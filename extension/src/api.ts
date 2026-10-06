/**
 * Client for the local Screenshot2Meet service and for the When2meet tab.
 */
import type {
  ApplyResult,
  ExtractResponse,
  FillPlan,
  PanelRequest,
  PanelResponse,
  ReviewedEvent,
  W2MContext,
  Weekday,
} from "./shared";
import type { Settings } from "./settings";

export const SERVICE_URL = "http://127.0.0.1:8765";

/**
 * Check whether the local service is running.
 *
 * @returns {Promise<boolean>} True if GET /health answers ok.
 */
export async function serviceHealthy(): Promise<boolean> {
  try {
    const resp = await fetch(`${SERVICE_URL}/health`, { signal: AbortSignal.timeout(2000) });
    return resp.ok;
  } catch {
    return false;
  }
}

/**
 * POST JSON to the service and return the parsed response.
 *
 * @param {string} path Endpoint path, e.g. "/fill-plan".
 * @param {unknown} body Request body (JSON-serialisable) or FormData.
 * @returns {Promise<T>} Parsed JSON response.
 */
export async function postService<T>(path: string, body: unknown): Promise<T> {
  const isForm = body instanceof FormData;
  let resp: Response;
  try {
    resp = await fetch(`${SERVICE_URL}${path}`, {
      method: "POST",
      headers: isForm ? undefined : { "Content-Type": "application/json" },
      body: isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new Error(`Can't reach the local service at ${SERVICE_URL}. Is it running?`);
  }
  if (!resp.ok) throw new Error(`Service error ${resp.status}: ${await resp.text()}`);
  return (await resp.json()) as T;
}

/**
 * Distinct New York calendar dates covered by a specific-dates event's slots.
 *
 * @param {W2MContext} ctx The When2meet event context.
 * @returns {string[]} ISO dates, sorted.
 */
export function eventDates(ctx: W2MContext): string[] {
  if (ctx.mode !== "specific_dates") return [];
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return [...new Set(ctx.slots.map((s) => fmt.format(new Date(s.ts * 1000))))].sort();
}

/**
 * Send screenshots to the service for extraction.
 *
 * @param {File[]} files Screenshot files.
 * @param {W2MContext | null} ctx When2meet context, if a When2meet tab is open.
 * @param {Settings} settings User settings.
 * @returns {Promise<ExtractResponse>} Events and per-image reports (with debug overlays).
 */
export function requestExtract(files: File[], ctx: W2MContext | null, settings: Settings): Promise<ExtractResponse> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  form.append(
    "context",
    JSON.stringify({
      mode: ctx?.mode ?? "days_of_week",
      event_tz: ctx?.eventTz ?? null,
      dates: ctx ? eventDates(ctx) : [],
      ocr_engine: settings.ocrEngine,
      ocr_languages: settings.ocrLanguages,
      confidence_threshold: settings.confidenceThreshold,
      keyword_rules: settings.keywordRules,
    }),
  );
  form.append("debug", "true");
  return postService<ExtractResponse>("/extract", form);
}

/**
 * Ask the service which slots to mark available.
 *
 * @param {W2MContext} ctx The When2meet event context.
 * @param {ReviewedEvent[]} events Reviewed busy events.
 * @param {Settings} settings User settings.
 * @param {Partial<Record<Weekday, string>>} weekdayDates Weekday -> ISO date, for long specific-dates events.
 * @returns {Promise<FillPlan>} The plan.
 */
export function requestFillPlan(
  ctx: W2MContext,
  events: ReviewedEvent[],
  settings: Settings,
  weekdayDates: Partial<Record<Weekday, string>>,
): Promise<FillPlan> {
  return postService<FillPlan>("/fill-plan", {
    slots: ctx.slots.map((s) => s.ts),
    mode: ctx.mode,
    event_tz: ctx.eventTz,
    screenshot_tz: "America/New_York",
    events,
    settings: {
      buffer_minutes: settings.bufferMinutes,
      min_free_block_minutes: settings.minFreeBlockMinutes,
      all_day_busy: settings.allDayBusy,
    },
    weekday_dates: weekdayDates,
  });
}

/**
 * Find the active tab in this window if it is a When2meet page.
 *
 * @returns {Promise<chrome.tabs.Tab | null>} The tab, or null.
 */
export async function activeWhen2meetTab(): Promise<chrome.tabs.Tab | null> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url || !/^https:\/\/(www\.)?when2meet\.com\//.test(tab.url)) return null;
  return tab;
}

/**
 * Send a request to the content script in a tab.
 *
 * @param {number} tabId The When2meet tab.
 * @param {PanelRequest} request The request.
 * @returns {Promise<T>} The result.
 */
export async function sendToTab<T>(tabId: number, request: PanelRequest): Promise<T> {
  let response: PanelResponse<T> | undefined;
  try {
    response = await chrome.tabs.sendMessage<PanelRequest, PanelResponse<T>>(tabId, request);
  } catch {
    throw new Error("Can't talk to the When2meet page. Reload the tab and try again.");
  }
  if (!response) throw new Error("No answer from the When2meet page. Reload the tab.");
  if (!response.ok) throw new Error(response.error);
  return response.result;
}

/**
 * Read the event context from a When2meet tab.
 *
 * @param {number} tabId The When2meet tab.
 * @returns {Promise<W2MContext>} The context.
 */
export function getContext(tabId: number): Promise<W2MContext> {
  return sendToTab<W2MContext>(tabId, { type: "getContext" });
}

/**
 * Make the user's availability equal to the given slots.
 *
 * @param {number} tabId The When2meet tab.
 * @param {number[]} available Slot timestamps that should be available.
 * @returns {Promise<ApplyResult>} What changed.
 */
export function applyAvailability(tabId: number, available: number[]): Promise<ApplyResult> {
  return sendToTab<ApplyResult>(tabId, { type: "apply", available });
}

/**
 * Outline low-confidence cells (empty list clears outlines).
 *
 * @param {number} tabId The When2meet tab.
 * @param {number[]} slots Slot timestamps to outline.
 * @returns {Promise<void>}
 */
export async function outlineSlots(tabId: number, slots: number[]): Promise<void> {
  await sendToTab<null>(tabId, { type: "outline", slots });
}
