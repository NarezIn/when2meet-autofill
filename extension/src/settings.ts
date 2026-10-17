/**
 * User settings, stored in chrome.storage.local.
 */

/** Hosted extraction service (Fly.io). */
export const HOSTED_SERVICE_URL = "https://screenshot2meet-api.fly.dev";
/** Local service for development (see README). */
export const LOCAL_SERVICE_URL = "http://127.0.0.1:8765";

export interface Settings {
  ocrEngine: "paddle" | "tesseract";
  /** PaddleOCR language codes, e.g. "en", "ch", "chinese_cht", "es". */
  ocrLanguages: string[];
  confidenceThreshold: number;
  /** Case-insensitive title substrings; matching events don't count as busy. */
  keywordRules: string[];
  bufferMinutes: number;
  minFreeBlockMinutes: number;
  allDayBusy: boolean;
  /** Calendar colours ("#rrggbb") the colour rules apply to; empty = rules off. */
  colorRules: string[];
  /** "exclude": events in these colours don't count as busy; "only": only they count. */
  colorRuleMode: "exclude" | "only";
  /** Request debug overlays and show them in the enlarged screenshot view. */
  developerMode: boolean;
  /** Which service to call. "local" only takes effect when developerMode is on. */
  serviceTarget: "hosted" | "local";
}

export const DEFAULT_SETTINGS: Settings = {
  ocrEngine: "paddle",
  ocrLanguages: ["en", "ch", "chinese_cht", "es"],
  confidenceThreshold: 0.7,
  keywordRules: ["Plan out Tomorrow"],
  bufferMinutes: 0,
  minFreeBlockMinutes: 15,
  allDayBusy: false,
  colorRules: [],
  colorRuleMode: "exclude",
  developerMode: false,
  serviceTarget: "hosted",
};

/**
 * Load settings, filling in defaults for anything missing.
 *
 * @returns {Promise<Settings>} The current settings.
 */
export async function loadSettings(): Promise<Settings> {
  const stored = (await chrome.storage.local.get("settings")).settings as Partial<Settings> | undefined;
  const merged = { ...DEFAULT_SETTINGS, ...stored } as Settings & { skipReview?: unknown };
  delete merged.skipReview; // removed setting; always fills right away now
  return merged;
}

/**
 * Save settings.
 *
 * @param {Settings} settings The settings to store.
 * @returns {Promise<void>}
 */
export async function saveSettings(settings: Settings): Promise<void> {
  await chrome.storage.local.set({ settings });
}

/**
 * Base URL of the extraction service to call.
 * Local is used only when Developer mode is on; otherwise always the hosted service.
 *
 * @param {Settings} settings The current settings.
 * @returns {string} Base URL with no trailing slash.
 */
export function serviceUrl(settings: Settings): string {
  return settings.developerMode && settings.serviceTarget === "local"
    ? LOCAL_SERVICE_URL
    : HOSTED_SERVICE_URL;
}

/**
 * OCR engine to request. The hosted service only has PaddleOCR, so Tesseract is used
 * only with the local service.
 *
 * @param {Settings} settings The current settings.
 * @returns {Settings["ocrEngine"]} The engine to send to the service.
 */
export function effectiveOcrEngine(settings: Settings): Settings["ocrEngine"] {
  return serviceUrl(settings) === LOCAL_SERVICE_URL ? settings.ocrEngine : "paddle";
}

/**
 * Ask Chrome for access to the local service. Must be called from a click/change handler.
 *
 * @returns {Promise<boolean>} True if access was granted (or already held).
 */
export function requestLocalAccess(): Promise<boolean> {
  return chrome.permissions.request({ origins: [`${LOCAL_SERVICE_URL}/*`] });
}