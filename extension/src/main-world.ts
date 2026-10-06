/**
 * Runs in When2meet's MAIN world so it can read the page's globals
 * (TimeOfSlot, AvailableAtSlot, UserID) and drive its own drag-selection.
 *
 * It never posts to When2meet itself: every change goes through the page's
 * SelectFromHere / SelectToHere / SelectStop handlers via synthetic mouse events.
 * See docs/when2meet-dom.md.
 */
import {
  PAGE_REQUEST_EVENT,
  PAGE_RESPONSE_EVENT,
  type ApplyResult,
  type PageRequest,
  type SlotCell,
  type W2MContext,
} from "./shared";

/** The When2meet page globals we rely on. */
interface W2MWindow extends Window {
  TimeOfSlot: number[];
  AvailableAtSlot: number[][];
  UserID: number;
  PeopleIDs: number[];
  PeopleNames: string[];
  __s2mFetchHooked?: boolean;
}

const page = window as unknown as W2MWindow;
const SAVE_TIMEOUT_MS = 15000;

/** Promise for the most recent SaveTimes.php request, set by the fetch hook. */
let lastSave: Promise<string> | null = null;

/**
 * Wrap window.fetch so we can wait for each SaveTimes.php request to finish.
 * The server stores the full availability string from each save, so saves
 * must not overlap or an older one could land last.
 *
 * @returns {void}
 */
function installFetchHook(): void {
  if (page.__s2mFetchHooked) return;
  page.__s2mFetchHooked = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const promise = original(input, init);
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url.includes("SaveTimes.php")) {
      lastSave = promise.then((resp) => {
        if (!resp.ok) throw new Error(`SaveTimes.php returned HTTP ${resp.status}`);
        return resp.clone().text();
      });
    }
    return promise;
  };
}

/**
 * Find the event timezone from the page's inline script
 * (`if (select.value != "America/New_York")`).
 *
 * @returns {string | null} IANA timezone name, or null if not found.
 */
function findEventTimezone(): string | null {
  for (const script of Array.from(document.scripts)) {
    const match = /select\.value\s*!=\s*"([A-Za-z_]+\/[A-Za-z_\/+-]+|UTC)"/.exec(script.text);
    if (match) return match[1];
  }
  return null;
}

/**
 * Get the "your availability" cell for a slot timestamp.
 *
 * @param {number} ts Slot timestamp.
 * @returns {HTMLElement | null} The cell, or null if it isn't on the page.
 */
function cellFor(ts: number): HTMLElement | null {
  return document.getElementById(`YouTime${ts}`);
}

/**
 * List all slots with their grid position.
 *
 * @returns {SlotCell[]} One entry per slot that has a cell on the page.
 */
function readSlots(): SlotCell[] {
  const out: SlotCell[] = [];
  for (const ts of page.TimeOfSlot ?? []) {
    const cell = cellFor(ts);
    if (!cell) continue;
    out.push({ ts, col: Number(cell.dataset.col), row: Number(cell.dataset.row) });
  }
  return out;
}

/**
 * Timestamps where the signed-in user is currently available.
 *
 * @returns {number[]} Slot timestamps.
 */
function readAvailable(): number[] {
  const out: number[] = [];
  page.TimeOfSlot.forEach((ts, i) => {
    if (page.UserID && page.AvailableAtSlot[i]?.includes(page.UserID)) out.push(ts);
  });
  return out;
}

/**
 * Collect everything the side panel needs to know about the event.
 *
 * @returns {W2MContext} The event context.
 */
function readContext(): W2MContext {
  if (!Array.isArray(page.TimeOfSlot) || !Array.isArray(page.AvailableAtSlot)) {
    throw new Error("This doesn't look like a When2meet event page.");
  }
  const [eventId = "", eventCode = ""] = location.search.replace(/^\?/, "").split("-");
  const nameDiv = document.getElementById("NewEventNameDiv");
  const eventName = (nameDiv?.firstChild?.textContent ?? "").trim();
  const userId = Number(page.UserID) || 0;
  const userIndex = (page.PeopleIDs ?? []).indexOf(userId);
  const slots = readSlots();
  const isDaysOfWeek = slots.length > 0 && Math.max(...slots.map((s) => s.ts)) < 1_000_000_000;
  return {
    eventId,
    eventCode,
    eventName,
    signedIn: userId !== 0,
    userId,
    userName: userIndex >= 0 ? page.PeopleNames[userIndex] : "",
    eventTz: findEventTimezone(),
    mode: isDaysOfWeek ? "days_of_week" : "specific_dates",
    slots,
    available: userId ? readAvailable() : [],
  };
}

/**
 * Split a set of slots into vertical runs: same column, consecutive rows.
 *
 * @param {SlotCell[]} cells Cells that all need the same change.
 * @returns {SlotCell[][]} Runs, each sorted top to bottom.
 */
function verticalRuns(cells: SlotCell[]): SlotCell[][] {
  const sorted = [...cells].sort((a, b) => a.col - b.col || a.row - b.row);
  const runs: SlotCell[][] = [];
  for (const cell of sorted) {
    const run = runs[runs.length - 1];
    const prev = run?.[run.length - 1];
    if (prev && prev.col === cell.col && prev.row + 1 === cell.row) run.push(cell);
    else runs.push([cell]);
  }
  return runs;
}

/**
 * Reject after a delay unless the given promise settles first.
 *
 * @param {Promise<T>} promise The promise to wait for.
 * @param {number} ms Timeout in milliseconds.
 * @returns {Promise<T>} The promise's result.
 */
function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timed out waiting for When2meet to save.")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

/**
 * Perform one drag over a vertical run using the page's own handlers, then
 * wait for the resulting save to complete.
 *
 * @param {SlotCell[]} run Cells in one column, top to bottom, all needing the same change.
 * @returns {Promise<void>} Resolves when When2meet has saved.
 */
async function dragRun(run: SlotCell[]): Promise<void> {
  const first = cellFor(run[0].ts);
  const last = cellFor(run[run.length - 1].ts);
  if (!first || !last) throw new Error("A grid cell disappeared while filling.");
  lastSave = null;
  const init: MouseEventInit = { bubbles: true, cancelable: true, view: window, button: 0 };
  first.dispatchEvent(new MouseEvent("mousedown", init));
  last.dispatchEvent(new MouseEvent("mouseover", init));
  document.dispatchEvent(new MouseEvent("mouseup", init));
  // The fetch hook sets lastSave synchronously inside SelectStop; reading it through
  // a function call keeps TypeScript from narrowing it to null.
  const save = currentSave();
  if (!save) throw new Error("When2meet didn't save the change. Are you signed in?");
  await withTimeout(save, SAVE_TIMEOUT_MS);
}

/**
 * Return the pending save promise recorded by the fetch hook.
 *
 * @returns {Promise<string> | null} The latest save, or null if none happened.
 */
function currentSave(): Promise<string> | null {
  return lastSave;
}

/**
 * Change the user's availability to exactly the target set, one serialized drag per run.
 *
 * @param {number[]} target Timestamps that should end up available.
 * @returns {Promise<{drags: number, added: number, removed: number}>} Work done.
 */
async function applyOnce(target: number[]): Promise<{ drags: number; added: number; removed: number }> {
  const want = new Set(target);
  const have = new Set(readAvailable());
  const cells = readSlots();
  const toAdd = cells.filter((c) => want.has(c.ts) && !have.has(c.ts));
  const toRemove = cells.filter((c) => !want.has(c.ts) && have.has(c.ts));
  const runs = [...verticalRuns(toAdd), ...verticalRuns(toRemove)];
  for (const run of runs) await dragRun(run);
  return { drags: runs.length, added: toAdd.length, removed: toRemove.length };
}

/**
 * Apply a target availability, verify it, and retry mismatches once.
 *
 * @param {number[]} target Timestamps that should end up available.
 * @returns {Promise<ApplyResult>} Summary including any slots still wrong.
 */
async function applyTarget(target: number[]): Promise<ApplyResult> {
  if (!page.UserID) throw new Error("Sign in on the When2meet page first.");
  installFetchHook();
  const first = await applyOnce(target);
  const second = await applyOnce(target);
  const want = new Set(target);
  const have = new Set(readAvailable());
  const mismatched = readSlots()
    .map((c) => c.ts)
    .filter((ts) => want.has(ts) !== have.has(ts));
  return {
    drags: first.drags + second.drags,
    added: first.added,
    removed: first.removed,
    mismatched,
  };
}

/**
 * Handle one request from the content script and post the response back.
 *
 * @param {Event} event The s2m:request CustomEvent; detail is a JSON string {id, request}.
 * @returns {Promise<void>}
 */
async function onRequest(event: Event): Promise<void> {
  const { id, request } = JSON.parse((event as CustomEvent<string>).detail) as { id: string; request: PageRequest };
  let response: { id: string; ok: boolean; result?: unknown; error?: string };
  try {
    const result = request.type === "getContext" ? readContext() : await applyTarget(request.available);
    response = { id, ok: true, result };
  } catch (err) {
    response = { id, ok: false, error: err instanceof Error ? err.message : String(err) };
  }
  document.dispatchEvent(new CustomEvent(PAGE_RESPONSE_EVENT, { detail: JSON.stringify(response) }));
}

document.addEventListener(PAGE_REQUEST_EVENT, (e) => void onRequest(e));
