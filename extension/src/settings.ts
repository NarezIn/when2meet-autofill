/**
 * User settings, stored in chrome.storage.local.
 */

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
