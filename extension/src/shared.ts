/**
 * Types and message shapes shared by the side panel, the content script and the
 * MAIN-world page script.
 */

export type Weekday = "Mon" | "Tue" | "Wed" | "Thu" | "Fri" | "Sat" | "Sun";
export const WEEKDAYS: Weekday[] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export type EventMode = "specific_dates" | "days_of_week";

/** One cell of When2meet's "your availability" grid. */
export interface SlotCell {
  ts: number;
  col: number;
  row: number;
}

/** What the page tells us about the open When2meet event. */
export interface W2MContext {
  eventId: string;
  eventCode: string;
  eventName: string;
  signedIn: boolean;
  userId: number;
  userName: string;
  eventTz: string | null;
  mode: EventMode;
  slots: SlotCell[];
  /** Timestamps where the signed-in user is currently available. */
  available: number[];
}

/** Outcome of applying a target availability set. */
export interface ApplyResult {
  drags: number;
  added: number;
  removed: number;
  /** Slots that still don't match the target after one retry. */
  mismatched: number[];
}

/**
 * A busy event as shown/edited on the review screen and sent to POST /fill-plan.
 * Events from the extractor also carry the optional extraction fields.
 */
export interface ReviewedEvent {
  day: Weekday;
  date?: string | null;
  start: string;
  end: string;
  title?: string | null;
  low_confidence: boolean;
  counts_as_busy: boolean;
  all_day?: boolean;
  confidence?: number;
  reasons?: string[];
  cut_off?: "top" | "bottom" | null;
  source_image?: number;
  column?: number;
  minimum_height?: boolean;
  weekday_guessed?: boolean;
}

/** How one day column of one screenshot was identified (from POST /extract). */
export interface ColumnInfo {
  source_image: number;
  column: number;
  x0: number;
  x1: number;
  header_text: string;
  day: Weekday;
  date: string | null;
  quality: "exact" | "fuzzy" | "date" | "guessed";
}

/** Per-screenshot diagnostics from POST /extract. */
export interface ImageReport {
  source_image: number;
  width: number;
  height: number;
  axis_residual_px: number | null;
  axis_labels: number;
  grid: [number, number] | null;
  columns: ColumnInfo[];
  error: string | null;
  debug_png: string | null;
}

/** Response of POST /extract. */
export interface ExtractResponse {
  events: ReviewedEvent[];
  images: ImageReport[];
}

/** Response of POST /fill-plan. */
export interface FillPlan {
  available_slots: number[];
  low_confidence_slots: number[];
  ambiguous_weekdays: Partial<Record<Weekday, string[]>>;
  unmatched_events: number[];
}

/** Messages the side panel sends to the content script. */
export type PanelRequest =
  | { type: "getContext" }
  | { type: "apply"; available: number[] }
  | { type: "outline"; slots: number[] };

/** Envelope the content script returns for every PanelRequest. */
export type PanelResponse<T> = { ok: true; result: T } | { ok: false; error: string };

/** Requests the content script forwards to the MAIN-world script. */
export type PageRequest = { type: "getContext" } | { type: "apply"; available: number[] };

export const PAGE_REQUEST_EVENT = "s2m:request";
export const PAGE_RESPONSE_EVENT = "s2m:response";
