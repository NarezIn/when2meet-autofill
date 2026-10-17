/**
 * Side panel: upload screenshots -> "Fill When2meet" (POST /extract, a weekday prompt only
 * if a column's weekday was guessed, POST /fill-plan, fill through the content script)
 * -> one-line summary with Undo. Also the problem banner, settings, and the enlarged view.
 */
import {
  activeWhen2meetTab,
  applyAvailability,
  getContext,
  outlineSlots,
  requestExtract,
  requestFillPlan,
  serviceHealthy,
} from "./api";
import { loadSettings, LOCAL_SERVICE_URL, requestLocalAccess, saveSettings, serviceUrl, type Settings } from "./settings";
import { WEEKDAYS, type ImageReport, type ReviewedEvent, type W2MContext, type Weekday } from "./shared";

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

/** Result of reading the current screenshots, reused until the screenshots or settings change. */
interface Extraction {
  /** Cache key: screenshot list + extraction settings + event context. */
  key: string;
  events: ReviewedEvent[];
  reports: ImageReport[];
  /** Whether the user has seen the weekday prompt for guessed columns. */
  weekdaysConfirmed: boolean;
}

let settings: Settings;
let weekdayDates: Partial<Record<Weekday, string>> = {};
let busy = false;
/** Screenshots chosen for upload, in upload order, with object URLs for previews. */
let shots: { id: number; file: File; url: string }[] = [];
let nextShotId = 0;
let extraction: Extraction | null = null;
/** Index into `shots` shown in the enlarged view, or null when it is closed. */
let viewerIndex: number | null = null;

/**
 * Create an element with attributes and children.
 *
 * @param {string} tag Tag name.
 * @param {Record<string, unknown>} attrs Properties to assign (e.g. className, value, onclick).
 * @param {(Node | string)[]} children Child nodes or text.
 * @returns {HTMLElement} The element.
 */
function h(tag: string, attrs: Record<string, unknown> = {}, ...children: (Node | string)[]): HTMLElement {
  const el = document.createElement(tag);
  Object.assign(el, attrs);
  el.append(...children);
  return el;
}

/**
 * Show a progress or error message under the Fill button.
 *
 * @param {string} text The message ("" clears it).
 * @param {boolean} isError Whether to style it as an error.
 * @returns {void}
 */
function say(text: string, isError = false): void {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

/**
 * Show the one-line fill summary next to the Undo button.
 *
 * @param {string} text Summary text.
 * @returns {void}
 */
function showResult(text: string): void {
  $("result-text").textContent = text;
  $("result").hidden = false;
}

/**
 * Show the weekday -> date picker for a long specific-dates event.
 *
 * @param {Partial<Record<Weekday, string[]>>} ambiguous Candidate dates per weekday.
 * @returns {void}
 */
function renderAmbiguity(ambiguous: Partial<Record<Weekday, string[]>>): void {
  const fields = $("ambiguity-fields");
  fields.replaceChildren();
  for (const [day, dates] of Object.entries(ambiguous) as [Weekday, string[]][]) {
    const select = h(
      "select",
      { onchange: (e: Event) => (weekdayDates[day] = (e.target as HTMLSelectElement).value) },
      ...dates.map((d) => h("option", { value: d, textContent: d })),
    ) as HTMLSelectElement;
    weekdayDates[day] = weekdayDates[day] ?? dates[0];
    select.value = weekdayDates[day]!;
    fields.append(h("label", { className: "row" }, `${day}: `, select));
  }
  $("ambiguity").hidden = false;
}

/**
 * Storage key for this event's undo stack.
 *
 * @param {W2MContext} ctx The event context.
 * @returns {string} The key.
 */
function undoKey(ctx: W2MContext): string {
  return `undo:${ctx.eventId}:${ctx.userId}`;
}

/**
 * Read the undo stack (snapshots of previous availability) for this event.
 *
 * @param {W2MContext} ctx The event context.
 * @returns {Promise<number[][]>} Snapshots, oldest first.
 */
async function readUndoStack(ctx: W2MContext): Promise<number[][]> {
  const key = undoKey(ctx);
  return ((await chrome.storage.session.get(key))[key] as number[][] | undefined) ?? [];
}

/**
 * Write the undo stack for this event.
 *
 * @param {W2MContext} ctx The event context.
 * @param {number[][]} stack Snapshots, oldest first.
 * @returns {Promise<void>}
 */
async function writeUndoStack(ctx: W2MContext, stack: number[][]): Promise<void> {
  await chrome.storage.session.set({ [undoKey(ctx)]: stack });
}

/**
 * Storage key for the summaries of this event's fills (one per undo snapshot).
 *
 * @param {W2MContext} ctx The event context.
 * @returns {string} The key.
 */
function summaryKey(ctx: W2MContext): string {
  return `summary:${ctx.eventId}:${ctx.userId}`;
}

/**
 * Read the fill summaries for this event, parallel to the undo stack.
 *
 * @param {W2MContext} ctx The event context.
 * @returns {Promise<string[]>} Summaries, oldest first.
 */
async function readSummaries(ctx: W2MContext): Promise<string[]> {
  const key = summaryKey(ctx);
  return ((await chrome.storage.session.get(key))[key] as string[] | undefined) ?? [];
}

/**
 * Write the fill summaries for this event.
 *
 * @param {W2MContext} ctx The event context.
 * @param {string[]} summaries Summaries, oldest first.
 * @returns {Promise<void>}
 */
async function writeSummaries(ctx: W2MContext, summaries: string[]): Promise<void> {
  await chrome.storage.session.set({ [summaryKey(ctx)]: summaries });
}

/**
 * Get the When2meet tab and its context, requiring a signed-in user.
 *
 * @returns {Promise<{tabId: number, ctx: W2MContext}>} The tab id and context.
 */
async function signedInPage(): Promise<{ tabId: number; ctx: W2MContext }> {
  const tab = await activeWhen2meetTab();
  if (!tab?.id) throw new Error("Open a When2meet event in this tab first.");
  const ctx = await getContext(tab.id);
  if (!ctx.signedIn) throw new Error("Sign in with your name on the When2meet page first.");
  return { tabId: tab.id, ctx };
}

/**
 * Enable or disable the Fill button for the current state.
 *
 * @returns {void}
 */
function updateFillButton(): void {
  $<HTMLButtonElement>("fill").disabled = busy || shots.length === 0;
}

/**
 * Run an async action with the buttons disabled and errors shown.
 *
 * @param {() => Promise<void>} action The work to do.
 * @returns {Promise<void>}
 */
async function guarded(action: () => Promise<void>): Promise<void> {
  if (busy) return;
  busy = true;
  updateFillButton();
  $<HTMLButtonElement>("undo").disabled = true;
  try {
    await action();
  } catch (err) {
    say(err instanceof Error ? err.message : String(err), true);
  } finally {
    busy = false;
    // Re-check first so Undo is settled before Fill re-enables (Fill enabled = panel idle).
    await refreshStatus();
    updateFillButton();
  }
}

/**
 * Cache key for an extraction: changes when the screenshots, the settings that affect
 * reading, or the event's mode/dates change.
 *
 * @param {W2MContext} ctx The event context.
 * @returns {string} The key.
 */
function extractionKey(ctx: W2MContext): string {
  return JSON.stringify({
    shots: shots.map((s) => s.id),
    engine: settings.ocrEngine,
    languages: settings.ocrLanguages,
    threshold: settings.confidenceThreshold,
    rules: settings.keywordRules,
    debug: settings.developerMode,
    event: ctx.eventId,
    mode: ctx.mode,
  });
}

/**
 * Columns whose weekday the extractor had to guess.
 *
 * @param {Extraction} ex The extraction.
 * @returns {ImageReport["columns"]} The guessed columns.
 */
function guessedColumns(ex: Extraction): ImageReport["columns"] {
  return ex.reports.flatMap((r) => r.columns).filter((c) => c.quality === "guessed");
}

/**
 * Read the screenshots for this event, or reuse the last reading if nothing changed.
 *
 * @param {W2MContext} ctx The event context.
 * @returns {Promise<Extraction>} The reading.
 */
async function ensureExtraction(ctx: W2MContext): Promise<Extraction> {
  const key = extractionKey(ctx);
  if (extraction?.key !== key) {
    say(`Reading ${shots.length} screenshot${shots.length > 1 ? "s" : ""} (the first run loads the OCR models)…`);
    const result = await requestExtract(
      shots.map((s) => s.file),
      ctx,
      settings,
    );
    extraction = { key, events: result.events, reports: result.images, weekdaysConfirmed: false };
    renderImages();
    renderFoundColors();
  }
  return extraction;
}

/**
 * Read the screenshots (or reuse the last reading), then plan and fill When2meet.
 * Stops early to ask for a weekday if one couldn't be read, or for dates if the
 * event spans more than one week; clicking Fill again continues.
 *
 * @returns {Promise<void>}
 */
async function fill(): Promise<void> {
  if (!shots.length) throw new Error("Add at least one screenshot first.");
  const { tabId, ctx } = await signedInPage();
  $("result").hidden = true;

  extraction = await ensureExtraction(ctx);

  const failed = extraction.reports.filter((r) => r.error).length;
  if (failed) {
    throw new Error(
      `${failed} screenshot${failed > 1 ? "s" : ""} couldn't be read (marked in red above). ` +
        "Remove or replace it, then click Fill again.",
    );
  }
  if (!extraction.weekdaysConfirmed && guessedColumns(extraction).length) {
    extraction.weekdaysConfirmed = true;
    renderImages();
    say("Pick the day for the highlighted column above, then click Fill again.");
    return;
  }

  say("Computing free slots…");
  const plan = await requestFillPlan(ctx, extraction.events, settings, weekdayDates);
  if (Object.keys(plan.ambiguous_weekdays).length) {
    renderAmbiguity(plan.ambiguous_weekdays);
    say("Pick a date for each weekday above, then click Fill again.");
    return;
  }
  $("ambiguity").hidden = true;

  const stack = await readUndoStack(ctx);
  stack.push(ctx.available);
  await writeUndoStack(ctx, stack);

  say(`Filling ${plan.available_slots.length} free slots…`);
  const result = await applyAvailability(tabId, plan.available_slots);
  await outlineSlots(tabId, plan.low_confidence_slots);

  const summary = [`Filled ${plan.available_slots.length} slots`];
  const uncertain = plan.low_confidence_slots.length;
  if (uncertain) summary.push(`${uncertain} to check`);
  showResult(summary.join(" · "));
  await writeSummaries(ctx, [...(await readSummaries(ctx)), summary.join(" · ")].slice(-stack.length));

  const warnings: string[] = [];
  if (plan.color_skipped.length) {
    const n = plan.color_skipped.length;
    warnings.push(`${n} event${n > 1 ? "s" : ""} left out by your color rules.`);
  }
  if (plan.unmatched_events.length) {
    warnings.push(`${plan.unmatched_events.length} events fall on days this event doesn't include.`);
  }
  if (result.mismatched.length) warnings.push(`Warning: ${result.mismatched.length} slots didn't save as expected.`);
  say(warnings.join("\n"), result.mismatched.length > 0);
}

/**
 * Restore the availability saved before the most recent fill.
 *
 * @returns {Promise<void>}
 */
async function undo(): Promise<void> {
  const { tabId, ctx } = await signedInPage();
  const stack = await readUndoStack(ctx);
  const snapshot = stack.pop();
  if (!snapshot) throw new Error("Nothing to undo for this event.");
  say("Restoring your previous availability…");
  const result = await applyAvailability(tabId, snapshot);
  await outlineSlots(tabId, []);
  await writeUndoStack(ctx, stack);
  await writeSummaries(ctx, (await readSummaries(ctx)).slice(0, -1));
  $("result").hidden = true;
  say(
    result.mismatched.length
      ? `Restored, but ${result.mismatched.length} slots didn't save as expected.`
      : "Restored your previous availability.",
    result.mismatched.length > 0,
  );
}

/**
 * Set one Connection value in Settings.
 *
 * @param {string} id Element id of the value cell.
 * @param {string} text Value text.
 * @param {"ok" | "bad" | ""} state Colouring.
 * @returns {void}
 */
function setConn(id: string, text: string, state: "ok" | "bad" | "" = ""): void {
  const el = $(id);
  el.textContent = text;
  el.className = state;
}

/**
 * Show the warning banner with one problem, or hide it.
 *
 * @param {string | null} text What's wrong and what to do; null hides the banner.
 * @returns {void}
 */
function setBanner(text: string | null): void {
  $("banner").hidden = text === null;
  $("banner-text").textContent = text ?? "";
}

/**
 * Re-check the service and the When2meet tab: update the Connection rows, the
 * problem banner, and the Undo button.
 *
 * @returns {Promise<void>}
 */
async function refreshStatus(): Promise<void> {
  const ok = await serviceHealthy();
  const local = serviceUrl(settings) === LOCAL_SERVICE_URL;
  setConn("conn-service", ok ? "Running" : local ? "Not running" : "Unreachable", ok ? "ok" : "bad");

  let pageProblem: string | null = null;
  let undoAvailable = false;
  let lastSummary: string | undefined;
  try {
    const tab = await activeWhen2meetTab();
    if (!tab?.id) {
      pageProblem = "Open your When2meet event in this tab.";
      setConn("conn-user", "No When2meet event open", "bad");
      setConn("conn-mode", "–");
      setConn("conn-tz", "–");
    } else {
      const ctx = await getContext(tab.id);
      setConn("conn-user", ctx.signedIn ? ctx.userName || "(signed in)" : "Not signed in", ctx.signedIn ? "ok" : "bad");
      setConn("conn-mode", ctx.mode === "days_of_week" ? "Days of the week" : "Specific dates");
      setConn(
        "conn-tz",
        ctx.eventTz ?? (ctx.mode === "days_of_week" ? "Wall clock (no timezone)" : "Unknown"),
      );
      if (!ctx.signedIn) pageProblem = "Sign in with your name on the When2meet page.";
      undoAvailable = ctx.signedIn && (await readUndoStack(ctx)).length > 0;
      if (undoAvailable) lastSummary = (await readSummaries(ctx)).at(-1);
    }
  } catch (err) {
    pageProblem = err instanceof Error ? err.message : String(err);
    setConn("conn-user", "Can't read the When2meet page", "bad");
    setConn("conn-mode", "–");
    setConn("conn-tz", "–");
  }

  setBanner(
    ok
      ? pageProblem
      : local
        ? "Start the local service: run screenshot2meet serve (see the README)."
        : "Can't reach the Screenshot2Meet service. Check your internet connection and try again.",
  );
  $<HTMLButtonElement>("undo").disabled = busy || !undoAvailable;
  if (!undoAvailable) {
    $("result").hidden = true;
  } else if ($("result").hidden) {
    showResult(lastSummary ?? "Your last fill can be undone");
  }
}

/**
 * Fill the settings form from `settings` and save on every change.
 *
 * @returns {void}
 */
function bindSettings(): void {
  const form = $<HTMLFormElement>("settings");
  const field = (name: string): HTMLInputElement => form.elements.namedItem(name) as HTMLInputElement;
  field("ocrEngine").value = settings.ocrEngine;
  field("ocrLanguages").value = settings.ocrLanguages.join(", ");
  field("confidenceThreshold").value = String(settings.confidenceThreshold);
  (form.elements.namedItem("keywordRules") as HTMLTextAreaElement).value = settings.keywordRules.join("\n");
  field("bufferMinutes").value = String(settings.bufferMinutes);
  field("minFreeBlockMinutes").value = String(settings.minFreeBlockMinutes);
  field("allDayBusy").checked = settings.allDayBusy;
  field("colorOnly").checked = settings.colorRuleMode === "only";
  field("developerMode").checked = settings.developerMode;
  field("useLocalService").checked = settings.serviceTarget === "local";
  $("local-service-row").hidden = !settings.developerMode;

  // Ask for the optional localhost permission while we still have the click gesture.
  field("useLocalService").addEventListener("change", () => {
    if (!field("useLocalService").checked) return;
    void requestLocalAccess().then((granted) => {
      if (granted) return;
      field("useLocalService").checked = false;
      form.dispatchEvent(new Event("change"));
    });
  });

  form.addEventListener("change", () => {
    const previousUrl = serviceUrl(settings);
    settings = {
      ocrEngine: field("ocrEngine").value as Settings["ocrEngine"],
      ocrLanguages: field("ocrLanguages").value.split(",").map((s) => s.trim()).filter(Boolean),
      confidenceThreshold: Number(field("confidenceThreshold").value),
      keywordRules: (form.elements.namedItem("keywordRules") as HTMLTextAreaElement).value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      bufferMinutes: Number(field("bufferMinutes").value),
      minFreeBlockMinutes: Number(field("minFreeBlockMinutes").value),
      allDayBusy: field("allDayBusy").checked,
      colorRules: settings.colorRules,
      colorRuleMode: field("colorOnly").checked ? "only" : "exclude",
      developerMode: field("developerMode").checked,
      serviceTarget: field("useLocalService").checked ? "local" : "hosted",
    };
    $("local-service-row").hidden = !settings.developerMode;
    void saveSettings(settings);
    if (serviceUrl(settings) !== previousUrl) void refreshStatus();
    renderViewer();
    renderColorRules();
  });
  $("color-add").addEventListener("click", () => addColorRule($<HTMLInputElement>("color-pick").value));
  $("find-colors").addEventListener("click", () => void guarded(findColors));
  renderColorRules();
  renderFoundColors();
  $("refresh").addEventListener("click", () => void refreshStatus());
}

/**
 * Distance between two "#rrggbb" colours in RGB space (for de-duplicating swatches only;
 * the service does the real matching in CIE Lab).
 *
 * @param {string} a First colour.
 * @param {string} b Second colour.
 * @returns {number} Euclidean RGB distance.
 */
function rgbDistance(a: string, b: string): number {
  const parse = (hex: string): number[] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const [pa, pb] = [parse(a), parse(b)];
  return Math.hypot(pa[0] - pb[0], pa[1] - pb[1], pa[2] - pb[2]);
}

/**
 * Save the settings after a colour rule change and redraw the colour lists.
 *
 * @returns {void}
 */
function colorRulesChanged(): void {
  void saveSettings(settings);
  renderColorRules();
  renderFoundColors();
}

/**
 * Add a colour to the colour rules (ignored if it is already there).
 *
 * @param {string} color "#rrggbb".
 * @returns {void}
 */
function addColorRule(color: string): void {
  const hex = color.toLowerCase();
  if (settings.colorRules.some((c) => rgbDistance(c, hex) < 6)) return;
  settings = { ...settings, colorRules: [...settings.colorRules, hex] };
  colorRulesChanged();
}

/**
 * Draw the chosen rule colours (each with a remove button) and the mode hint.
 *
 * @returns {void}
 */
function renderColorRules(): void {
  $("color-chips").replaceChildren(
    ...settings.colorRules.map((color) => {
      const swatch = h("span", { className: "swatch" });
      swatch.style.background = color;
      return h(
        "span",
        { className: "chip", title: color },
        swatch,
        color,
        h("button", {
          type: "button",
          textContent: "×",
          ariaLabel: `Remove ${color}`,
          onclick: () => {
            settings = { ...settings, colorRules: settings.colorRules.filter((c) => c !== color) };
            colorRulesChanged();
          },
        }),
      );
    }),
  );
  const none = settings.colorRules.length === 0;
  $("color-empty").hidden = !none;
  $("color-mode-hint").textContent = none
    ? "Color rules are off until you add a color."
    : settings.colorRuleMode === "only"
      ? "Only events in these colors count as busy. Everything else is ignored."
      : "Events in these colors don't count as busy.";
}

/**
 * Draw the distinct event colours found in the screenshots as clickable swatches.
 *
 * @returns {void}
 */
function renderFoundColors(): void {
  const groups: { color: string; titles: string[] }[] = [];
  for (const ev of extraction?.events ?? []) {
    if (!ev.color) continue;
    const group = groups.find((g) => rgbDistance(g.color, ev.color!) < 12);
    if (group) group.titles.push(ev.title ?? "(no title)");
    else groups.push({ color: ev.color, titles: [ev.title ?? "(no title)"] });
  }
  $("found-colors").replaceChildren(
    ...groups.map(({ color, titles }) => {
      const chosen = settings.colorRules.some((c) => rgbDistance(c, color) < 12);
      const examples = [...new Set(titles)].slice(0, 3).join(", ");
      const swatch = h("span", { className: "swatch" });
      swatch.style.background = color;
      return h(
        "button",
        {
          type: "button",
          className: "chip",
          disabled: chosen,
          title: `${color}: ${examples}${chosen ? " (already added)" : ""}`,
          onclick: () => addColorRule(color),
        },
        swatch,
        `${titles.length} event${titles.length > 1 ? "s" : ""}`,
      );
    }),
  );
  $("find-colors").hidden = groups.length > 0;
}

/**
 * Read the screenshots (without filling) so their colours can be picked.
 *
 * @returns {Promise<void>}
 */
async function findColors(): Promise<void> {
  if (!shots.length) throw new Error("Add screenshots first, then find their colors.");
  const { ctx } = await signedInPage();
  await ensureExtraction(ctx);
  say("Colors found. Click one under Settings → Color rules to add it.");
}

/**
 * Inline "Which day is this column?" prompt for one screenshot's guessed columns.
 *
 * @param {number} index The screenshot's position in `shots`.
 * @returns {HTMLElement | null} The prompt, or null if nothing needs asking.
 */
function weekdayPrompt(index: number): HTMLElement | null {
  if (!extraction?.weekdaysConfirmed) return null;
  const columns = guessedColumns(extraction).filter((c) => c.source_image === index);
  if (!columns.length) return null;
  const prompt = h("div", { className: "weekday-prompt" }, h("p", {}, `Which day is ${columns.length > 1 ? "each" : "this"} column?`));
  for (const col of columns) {
    const current =
      extraction.events.find((ev) => ev.source_image === col.source_image && ev.column === col.column)?.day ?? col.day;
    const select = h(
      "select",
      {
        ariaLabel: `Column ${col.column + 1} weekday`,
        onchange: (e: Event) => {
          const day = (e.target as HTMLSelectElement).value as Weekday;
          extraction!.events = extraction!.events.map((ev) =>
            ev.source_image === col.source_image && ev.column === col.column
              ? { ...ev, day, date: null, weekday_guessed: false }
              : ev,
          );
        },
      },
      ...WEEKDAYS.map((d) => h("option", { value: d, textContent: d })),
    ) as HTMLSelectElement;
    select.value = current;
    const title = col.header_text ? `Header read as “${col.header_text}”` : "No header text found";
    prompt.append(h("label", { title }, `Column ${col.column + 1}`, select));
  }
  return prompt;
}

/** Small "x" icon for the remove buttons (static markup, no user data). */
const X_ICON =
  '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" ' +
  'stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

/**
 * Render the screenshot thumbnails in upload order: full image (never cropped), then the
 * file name with a remove button below it, then any read error or weekday prompt.
 *
 * @returns {void}
 */
function renderImages(): void {
  $("images").replaceChildren(
    ...shots.map((shot, i) => {
      const report = extraction?.reports[i];
      const prompt = weekdayPrompt(i);
      const remove = h("button", {
        type: "button",
        className: "thumb-remove",
        title: "Remove",
        ariaLabel: `Remove ${shot.file.name}`,
        onclick: () => removeShot(i),
      });
      remove.innerHTML = X_ICON;
      const card = h(
        "figure",
        { className: `thumb${report?.error ? " has-error" : ""}${prompt ? " needs-input" : ""}` },
        h(
          "button",
          { type: "button", className: "thumb-open", title: "Enlarge", ariaLabel: `Enlarge ${shot.file.name}`, onclick: () => openViewer(i) },
          h("img", { src: shot.url, alt: `Screenshot ${i + 1}: ${shot.file.name}` }),
        ),
        h("figcaption", {}, h("span", { className: "thumb-name", title: shot.file.name }, shot.file.name), remove),
      );
      if (report?.error) card.append(h("div", { className: "error" }, `Couldn't read this screenshot: ${report.error}`));
      if (prompt) card.append(prompt);
      return card;
    }),
  );
  updateFillButton();
  $("clear-images").hidden = shots.length === 0;
}

/**
 * Add screenshot files to the end of the upload list.
 *
 * @param {Iterable<File>} files Files from the picker, a drop, or a paste.
 * @returns {void}
 */
function addFiles(files: Iterable<File>): void {
  let added = false;
  for (const file of files) {
    if (!file.type.startsWith("image/")) continue;
    shots.push({ id: nextShotId++, file, url: URL.createObjectURL(file) });
    added = true;
  }
  if (!added) return;
  extraction = null;
  say("");
  renderImages();
  renderFoundColors();
}

/**
 * Remove one screenshot.
 *
 * @param {number} index Its position in `shots`.
 * @returns {void}
 */
function removeShot(index: number): void {
  URL.revokeObjectURL(shots[index].url);
  shots.splice(index, 1);
  extraction = null;
  if (viewerIndex !== null) closeViewer();
  renderImages();
  renderFoundColors();
}

/**
 * Open the enlarged view on one screenshot.
 *
 * @param {number} index Its position in `shots`.
 * @returns {void}
 */
function openViewer(index: number): void {
  viewerIndex = index;
  $("viewer").hidden = false;
  document.body.classList.add("viewer-open");
  renderViewer();
  $("viewer-close").focus();
}

/**
 * Close the enlarged view.
 *
 * @returns {void}
 */
function closeViewer(): void {
  viewerIndex = null;
  $("viewer").hidden = true;
  document.body.classList.remove("viewer-open");
}

/**
 * Move the enlarged view to the previous or next screenshot.
 *
 * @param {number} step -1 for previous, +1 for next.
 * @returns {void}
 */
function stepViewer(step: number): void {
  if (viewerIndex === null) return;
  const next = viewerIndex + step;
  if (next < 0 || next >= shots.length) return;
  viewerIndex = next;
  renderViewer();
}

/**
 * Draw the enlarged view: the screenshot, or its detection overlay in developer mode.
 *
 * @returns {void}
 */
function renderViewer(): void {
  if (viewerIndex === null) return;
  const shot = shots[viewerIndex];
  const overlay = extraction?.reports[viewerIndex]?.debug_png;
  const showOverlay = settings.developerMode && !!overlay;
  const img = $<HTMLImageElement>("viewer-img");
  img.src = showOverlay ? `data:image/png;base64,${overlay}` : shot.url;
  img.alt = `Screenshot ${viewerIndex + 1}: ${shot.file.name}`;
  $("viewer-title").textContent = `${viewerIndex + 1} / ${shots.length} · ${shot.file.name}`;
  $<HTMLButtonElement>("viewer-prev").disabled = viewerIndex === 0;
  $<HTMLButtonElement>("viewer-next").disabled = viewerIndex === shots.length - 1;
  const note = $("viewer-note");
  note.hidden = !settings.developerMode;
  note.textContent = showOverlay
    ? "Developer mode: detected time axis, day columns, and events with times and confidence."
    : "Developer mode: the detection overlay appears after you click Fill When2meet.";
  $("viewer-scroll").scrollTop = 0;
}

/**
 * Wire up the file picker, drag-and-drop, paste, and the enlarged view.
 *
 * @returns {void}
 */
function bindUpload(): void {
  const drop = $("drop");
  const input = $<HTMLInputElement>("files");
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    addFiles(input.files ?? []);
    input.value = "";
  });
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    addFiles(e.dataTransfer?.files ?? []);
  });
  document.addEventListener("paste", (e) => addFiles(e.clipboardData?.files ?? []));
  $("clear-images").addEventListener("click", () => {
    shots.forEach((s) => URL.revokeObjectURL(s.url));
    shots = [];
    extraction = null;
    renderImages();
    renderFoundColors();
  });

  $("viewer-prev").addEventListener("click", () => stepViewer(-1));
  $("viewer-next").addEventListener("click", () => stepViewer(1));
  $("viewer-close").addEventListener("click", closeViewer);
  document.addEventListener("keydown", (e) => {
    if (viewerIndex === null) return;
    if (e.key === "Escape") closeViewer();
    else if (e.key === "ArrowLeft") stepViewer(-1);
    else if (e.key === "ArrowRight") stepViewer(1);
  });
}

/**
 * Initialise the panel.
 *
 * @returns {Promise<void>}
 */
async function init(): Promise<void> {
  settings = await loadSettings();
  bindSettings();
  bindUpload();
  renderImages();
  $("fill").addEventListener("click", () => void guarded(fill));
  $("undo").addEventListener("click", () => void guarded(undo));
  $("banner-retry").addEventListener("click", () => void refreshStatus());
  $("settings-toggle").addEventListener("click", () => {
    const toggle = $("settings-toggle");
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    $("settings-section").hidden = !open;
  });
  chrome.tabs.onActivated.addListener(() => void refreshStatus());
  chrome.tabs.onUpdated.addListener((_id, info) => {
    if (info.status === "complete") void refreshStatus();
  });
  await refreshStatus();
}

void init();
