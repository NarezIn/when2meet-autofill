/**
 * User settings, stored in chrome.storage.local.
 */

export interface Settings {
  ocrEngine: "paddle" | "tesseract";
  /** PaddleOCR language codes, e.g. "en", "ch", "chinese_cht", "es". */
  ocrLanguages: string[];
  confidenceThreshold: number;
  /** Case-insensitive title substrings; matching events default to "doesn't count as busy". */
  keywordRules: string[];
  bufferMinutes: number;
  minFreeBlockMinutes: number;
  allDayBusy: boolean;
  skipReview: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  ocrEngine: "paddle",
  ocrLanguages: ["en", "ch", "chinese_cht", "es"],
  confidenceThreshold: 0.7,
  keywordRules: ["Plan out Tomorrow"],
  bufferMinutes: 0,
  minFreeBlockMinutes: 15,
  allDayBusy: false,
  skipReview: false,
};

/**
 * Load settings, filling in defaults for anything missing.
 *
 * @returns {Promise<Settings>} The current settings.
 */
export async function loadSettings(): Promise<Settings> {
  const stored = (await chrome.storage.local.get("settings")).settings as Partial<Settings> | undefined;
  return { ...DEFAULT_SETTINGS, ...stored };
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
