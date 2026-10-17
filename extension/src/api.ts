/**
 * Client for the Screenshot2Meet service (hosted or local) and for the When2meet tab.
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
import { loadSettings, serviceUrl, type Settings } from "./settings";

/** Upload limits for /extract; keep in sync with MAX_IMAGES / MAX_FILE_BYTES in server/src/screenshot2meet/app.py. */
export const MAX_IMAGES = 5;
export const MAX_IMAGE_MB = 10;

/**
 * Check whether the selected service (hosted or local) is reachable.
 *
 * @returns {Promise<boolean>} True if GET /health answers ok.
 */
export async function serviceHealthy(): Promise<boolean> {
  const base = serviceUrl(await loadSettings());
  try {
    // 15s: a stopped Fly Machine needs a few seconds to wake up and load OCR models
    const resp = await fetch(`${base}/health`, { signal: AbortSignal.timeout(15000) });
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
  const base = serviceUrl(await loadSettings());
  const isForm = body instanceof FormData;
  let resp: Response;
  try {
    resp = await fetch(`${base}${path}`, {
      method: "POST",
      headers: isForm ? undefined : { "Content-Type": "application/json" },
      body: isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new Error(`Can't reach the Screenshot2Meet service at ${base}. Check your connection and try again.`);
  }
  if (!resp.ok) throw new Error(await errorMessage(resp));
  return (await resp.json()) as T;
}

/**
 * Turn a failed service response into a message for the user. Limit errors (413 too large,
 * 429 too many requests) carry a plain-language `detail` from the service, shown as is.
 *
 * @param {Response} resp The non-ok response.
 * @returns {Promise<string>} The message to show.
 */
async function errorMessage(resp: Response): Promise<string> {
  const text = await resp.text();
  if (resp.status === 413 || resp.status === 429) {
    try {
      const detail = (JSON.parse(text) as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
    } catch {
      // not JSON: fall through to the generic messages
    }
    return resp.status === 429
      ? "Too many requests from your network. Try again later."
      : "Those screenshots are too large to send.";
  }
  return `Service error ${resp.status}: ${text}`;
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
 * @returns {Promise<ExtractResponse>} Events and per-image reports (with debug overlays in developer mode).
 */
export async function requestExtract(files: File[], ctx: W2MContext | null, settings: Settings): Promise<ExtractResponse> {
  // Same limits the service enforces; checking here saves an upload (and a rate-limit hit).
  if (files.length > MAX_IMAGES) {
    throw new Error(`Use at most ${MAX_IMAGES} screenshots at a time. Remove ${files.length - MAX_IMAGES} and try again.`);
  }
  const tooBig = files.find((f) => f.size > MAX_IMAGE_MB * 1024 * 1024);
  if (tooBig) throw new Error(`${tooBig.name} is larger than ${MAX_IMAGE_MB} MB.`);
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
  form.append("debug", String(settings.developerMode));
  return postService<ExtractResponse>("/extract", form);
}

/**
 * Ask the service which slots to mark available.
 *
 * @param {W2MContext} ctx The When2meet event context.
 * @param {ReviewedEvent[]} events Busy events (from extraction, with any weekday corrections).
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
      color_rules: settings.colorRules,
      color_rule_mode: settings.colorRuleMode,
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
