/**
 * Side panel: upload screenshots -> POST /extract -> review screen -> POST /fill-plan
 * -> fill When2meet through the content script; also undo and settings.
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
import { loadSettings, saveSettings, type Settings } from "./settings";
import { WEEKDAYS, type ImageReport, type ReviewedEvent, type W2MContext, type Weekday } from "./shared";

/** Expected events of fixtures/ios_calendar_mon_tue.png, for testing the fill path by hand. */
const SAMPLE_EVENTS: ReviewedEvent[] = [
  { day: "Mon", start: "09:00", end: "09:15", title: "早餐+处理邮件内容（除非紧急情况）", low_confidence: true, counts_as_busy: true },
  { day: "Mon", start: "09:30", end: "11:00", title: "Chikhany's office hours", low_confidence: false, counts_as_busy: true },
  { day: "Mon", start: "11:00", end: "12:15", title: "Comp…", low_confidence: false, counts_as_busy: true },
  { day: "Mon", start: "11:00", end: "12:15", title: "DMA…", low_confidence: false, counts_as_busy: true },
  { day: "Mon", start: "14:00", end: "15:00", title: "Sainin…", low_confidence: false, counts_as_busy: true },
  { day: "Mon", start: "14:15", end: "14:30", title: "Saining meeting", low_confidence: true, counts_as_busy: true },
  { day: "Mon", start: "22:30", end: "22:45", title: "Plan out Tomorrow", low_confidence: true, counts_as_busy: true },
  { day: "Tue", start: "09:00", end: "09:15", title: "早餐+处理邮件内容（除非紧急情况）", low_confidence: true, counts_as_busy: true },
  { day: "Tue", start: "09:30", end: "10:45", title: "Computer Graphic…", low_confidence: false, counts_as_busy: true },
  { day: "Tue", start: "14:00", end: "15:15", title: "Optimization in Lin…", low_confidence: false, counts_as_busy: true },
  { day: "Tue", start: "15:30", end: "16:45", title: "NLP CSCI-UA 469…", low_confidence: false, counts_as_busy: true },
  { day: "Tue", start: "22:30", end: "22:45", title: "Plan out Tomorrow", low_confidence: true, counts_as_busy: true },
];

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

let settings: Settings;
let events: ReviewedEvent[] = [];
let weekdayDates: Partial<Record<Weekday, string>> = {};
let busy = false;
/** Screenshots chosen for upload, with object URLs for previews. */
let shots: { file: File; url: string }[] = [];
/** Per-screenshot reports from the last extraction (debug overlays, columns). */
let reports: ImageReport[] = [];

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
 * Show a status or error message under the action buttons.
 *
 * @param {string} text The message.
 * @param {boolean} isError Whether to style it as an error.
 * @returns {void}
 */
function say(text: string, isError = false): void {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

/**
 * Whether a title matches one of the user's keyword rules.
 *
 * @param {string | null | undefined} title Event title.
 * @returns {boolean} True if the event should default to "doesn't count as busy".
 */
function matchesKeywordRule(title: string | null | undefined): boolean {
  const lower = (title ?? "").toLowerCase();
  return settings.keywordRules.some((rule) => rule.trim() && lower.includes(rule.trim().toLowerCase()));
}

/**
 * Persist the draft events for this browser session.
 *
 * @returns {Promise<void>}
 */
async function saveEvents(): Promise<void> {
  await chrome.storage.session.set({ events });
}

/**
 * Render the event list grouped by weekday.
 *
 * @returns {void}
 */
function renderEvents(): void {
  const container = $("events");
  container.replaceChildren();
  for (const day of WEEKDAYS) {
    const dayEvents = events
      .map((ev, index) => ({ ev, index }))
      .filter(({ ev }) => ev.day === day)
      .sort((a, b) => a.ev.start.localeCompare(b.ev.start));
    if (!dayEvents.length) continue;
    container.append(h("h3", {}, day));
    for (const { ev, index } of dayEvents) container.append(renderEvent(ev, index));
  }
  $("events-hint").hidden = events.length > 0;
  $("events-legend").hidden = !events.some((ev) => ev.confidence !== undefined);
}

/**
 * Render one editable event row.
 *
 * @param {ReviewedEvent} ev The event.
 * @param {number} index Its index in `events`.
 * @returns {HTMLElement} The row.
 */
function renderEvent(ev: ReviewedEvent, index: number): HTMLElement {
  const update = (patch: Partial<ReviewedEvent>): void => {
    events[index] = { ...events[index], ...patch };
    void saveEvents();
    renderEvents();
  };
  const busyBox = h("input", {
    type: "checkbox",
    checked: ev.counts_as_busy,
    title: "Counts as busy",
    onchange: (e: Event) => update({ counts_as_busy: (e.target as HTMLInputElement).checked }),
  });
  const timeInput = (field: "start" | "end"): HTMLElement =>
    h("input", {
      type: "time",
      step: 900,
      value: ev[field],
      ariaLabel: field,
      onchange: (e: Event) => update({ [field]: (e.target as HTMLInputElement).value }),
    });
  const row = h(
    "div",
    { className: `event row${ev.low_confidence ? " low" : ""}${ev.counts_as_busy ? "" : " not-busy"}` },
    h("label", { className: "row", title: "Counts as busy" }, busyBox),
    timeInput("start"),
    "–",
    timeInput("end"),
    ev.low_confidence ? h("span", { className: "badge", title: (ev.reasons ?? []).join("; ") }, "low") : "",
    ev.cut_off ? h("span", { className: "badge" }, `cut off (${ev.cut_off})`) : "",
    ev.confidence !== undefined
      ? h("span", { className: "conf", title: (ev.reasons ?? []).join("; ") || "no warnings" }, `${Math.round(ev.confidence * 100)}%`)
      : "",
    ev.source_image !== undefined ? h("span", { className: "conf" }, `shot ${ev.source_image + 1}`) : "",
    h("button", {
      className: "link",
      textContent: "Delete",
      onclick: () => {
        events.splice(index, 1);
        void saveEvents();
        renderEvents();
      },
    }),
  );
  if (ev.title) row.append(h("div", { className: "title", title: ev.title }, ev.title));
  if (ev.low_confidence && ev.reasons?.length) row.append(h("div", { className: "reasons" }, ev.reasons.join(" · ")));
  return row;
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
 * Run an async action with the buttons disabled and errors shown.
 *
 * @param {() => Promise<void>} action The work to do.
 * @returns {Promise<void>}
 */
async function guarded(action: () => Promise<void>): Promise<void> {
  if (busy) return;
  busy = true;
  $<HTMLButtonElement>("fill").disabled = true;
  $<HTMLButtonElement>("undo").disabled = true;
  $<HTMLButtonElement>("extract").disabled = true;
  try {
    await action();
  } catch (err) {
    say(err instanceof Error ? err.message : String(err), true);
  } finally {
    busy = false;
    $<HTMLButtonElement>("fill").disabled = false;
    $<HTMLButtonElement>("extract").disabled = shots.length === 0;
    await refreshStatus();
  }
}

/**
 * Fill When2meet from the current events: plan via the service, snapshot, apply, outline.
 *
 * @returns {Promise<void>}
 */
async function fill(): Promise<void> {
  const { tabId, ctx } = await signedInPage();
  say("Computing free slots…");
  const plan = await requestFillPlan(ctx, events, settings, weekdayDates);
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

  const lines = [`Done: +${result.added} / −${result.removed} slots in ${result.drags} drags.`];
  if (plan.low_confidence_slots.length) lines.push(`${plan.low_confidence_slots.length} low-confidence slots are outlined.`);
  if (plan.unmatched_events.length) lines.push(`${plan.unmatched_events.length} events fall on days this event doesn't include.`);
  if (result.mismatched.length) lines.push(`Warning: ${result.mismatched.length} slots didn't save as expected.`);
  say(lines.join("\n"), result.mismatched.length > 0);
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
  say(
    result.mismatched.length
      ? `Restored, but ${result.mismatched.length} slots didn't save as expected.`
      : `Restored previous availability (+${result.added} / −${result.removed}).`,
    result.mismatched.length > 0,
  );
}

/**
 * Update the service/page status chips and the Undo button.
 *
 * @returns {Promise<void>}
 */
async function refreshStatus(): Promise<void> {
  const serviceChip = $("service-status");
  const ok = await serviceHealthy();
  serviceChip.textContent = ok ? "Service: running" : "Service: not running";
  serviceChip.className = `chip ${ok ? "ok" : "bad"}`;

  const pageChip = $("page-status");
  let undoAvailable = false;
  try {
    const tab = await activeWhen2meetTab();
    if (!tab?.id) {
      pageChip.textContent = "When2meet: open an event";
      pageChip.className = "chip bad";
    } else {
      const ctx = await getContext(tab.id);
      const mode = ctx.mode === "days_of_week" ? "days of week" : "specific dates";
      pageChip.textContent = ctx.signedIn
        ? `When2meet: ${ctx.userName || "signed in"} · ${mode} · ${ctx.eventTz ?? (ctx.mode === "days_of_week" ? "wall clock" : "timezone unknown")}`
        : `When2meet: sign in first · ${mode}`;
      pageChip.className = `chip ${ctx.signedIn ? "ok" : "bad"}`;
      undoAvailable = ctx.signedIn && (await readUndoStack(ctx)).length > 0;
    }
  } catch (err) {
    pageChip.textContent = `When2meet: ${err instanceof Error ? err.message : String(err)}`;
    pageChip.className = "chip bad";
  }
  $<HTMLButtonElement>("undo").disabled = busy || !undoAvailable;
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
  field("skipReview").checked = settings.skipReview;

  form.addEventListener("change", () => {
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
      skipReview: field("skipReview").checked,
    };
    void saveSettings(settings);
  });
}

/**
 * Wire up the add-event form and the sample/clear buttons.
 *
 * @returns {void}
 */
function bindEventForm(): void {
  const form = $<HTMLFormElement>("add-event");
  const daySelect = form.elements.namedItem("day") as HTMLSelectElement;
  daySelect.append(...WEEKDAYS.map((d) => h("option", { value: d, textContent: d })));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const get = (name: string): string => (form.elements.namedItem(name) as HTMLInputElement).value;
    const date = get("date") || null;
    const day = date ? WEEKDAYS[(new Date(`${date}T12:00:00`).getDay() + 6) % 7] : (get("day") as Weekday);
    events.push({ day, date, start: get("start"), end: get("end"), low_confidence: false, counts_as_busy: true });
    void saveEvents();
    renderEvents();
  });
  $("load-sample").addEventListener("click", () => {
    events = SAMPLE_EVENTS.map((ev) => ({ ...ev, counts_as_busy: !matchesKeywordRule(ev.title) }));
    void saveEvents();
    renderEvents();
  });
  $("clear-events").addEventListener("click", () => {
    events = [];
    void saveEvents();
    renderEvents();
  });
}

/**
 * Show the chosen screenshots, or their debug overlays when the toggle is on.
 *
 * @returns {void}
 */
function renderImages(): void {
  const showDebug = $<HTMLInputElement>("show-debug").checked;
  const container = $("images");
  container.classList.toggle("thumbs-collapsed", !showDebug);
  container.replaceChildren(
    ...shots.map((shot, i) => {
      const report = reports[i];
      const src = showDebug && report?.debug_png ? `data:image/png;base64,${report.debug_png}` : shot.url;
      const caption = h(
        "div",
        { className: "caption" },
        h("span", {}, `Screenshot ${i + 1} · ${shot.file.name}`),
        report?.axis_residual_px != null
          ? h("span", {}, `${report.axis_labels} time labels, fit ±${report.axis_residual_px}px`)
          : "",
      );
      const box = h("div", { className: "shot" }, caption, h("img", { src, alt: `Screenshot ${i + 1}` }));
      if (report?.error) box.append(h("div", { className: "error" }, `Couldn't read this screenshot: ${report.error}`));
      return box;
    }),
  );
  $<HTMLButtonElement>("extract").disabled = busy || shots.length === 0;
  $("clear-images").hidden = shots.length === 0;
}

/**
 * List columns whose weekday had to be guessed, with a weekday picker for each.
 *
 * @returns {void}
 */
function renderColumns(): void {
  const guessed = reports.flatMap((r) => r.columns).filter((c) => c.quality === "guessed");
  $("columns-section").hidden = guessed.length === 0;
  $("columns").replaceChildren(
    ...guessed.map((col) => {
      const current =
        events.find((ev) => ev.source_image === col.source_image && ev.column === col.column)?.day ?? col.day;
      const select = h(
        "select",
        {
          onchange: (e: Event) => {
            const day = (e.target as HTMLSelectElement).value as Weekday;
            events = events.map((ev) =>
              ev.source_image === col.source_image && ev.column === col.column
                ? { ...ev, day, date: null, weekday_guessed: false }
                : ev,
            );
            void saveEvents();
            renderEvents();
          },
        },
        ...WEEKDAYS.map((d) => h("option", { value: d, textContent: d })),
      ) as HTMLSelectElement;
      select.value = current;
      const header = col.header_text ? ` (header read as “${col.header_text}”)` : "";
      return h("label", { className: "row" }, `Screenshot ${col.source_image + 1}, column ${col.column + 1}${header}: `, select);
    }),
  );
}

/**
 * Add screenshot files to the upload list.
 *
 * @param {Iterable<File>} files Files from the picker, a drop, or a paste.
 * @returns {void}
 */
function addFiles(files: Iterable<File>): void {
  for (const file of files) {
    if (file.type.startsWith("image/")) shots.push({ file, url: URL.createObjectURL(file) });
  }
  reports = [];
  renderImages();
  renderColumns();
}

/**
 * Send the screenshots to the service and show the extracted events for review.
 * With "skip review" on, fills When2meet right away.
 *
 * @returns {Promise<void>}
 */
async function extract(): Promise<void> {
  if (!shots.length) throw new Error("Add at least one screenshot first.");
  let ctx: W2MContext | null = null;
  const tab = await activeWhen2meetTab();
  if (tab?.id) ctx = await getContext(tab.id).catch(() => null);
  say(`Reading ${shots.length} screenshot${shots.length > 1 ? "s" : ""} (the first run loads the OCR models)…`);
  const result = await requestExtract(
    shots.map((s) => s.file),
    ctx,
    settings,
  );
  reports = result.images;
  events = result.events;
  await saveEvents();
  renderImages();
  renderColumns();
  renderEvents();
  const low = events.filter((ev) => ev.low_confidence).length;
  const failed = reports.filter((r) => r.error).length;
  const parts = [`Found ${events.length} events${low ? `, ${low} to double-check` : ""}.`];
  if (failed) parts.push(`${failed} screenshot${failed > 1 ? "s" : ""} couldn't be read.`);
  if (!ctx) parts.push("Open your When2meet event in this tab to fill it.");
  say(parts.join(" "), failed > 0);
  if (settings.skipReview && ctx?.signedIn && !failed) {
    await fill();
    say(`${parts.join(" ")}
${$("message").textContent}`, $("message").classList.contains("error"));
  }
}

/**
 * Wire up the file picker, drag-and-drop, paste, and the overlay toggle.
 *
 * @returns {void}
 */
function bindUpload(): void {
  const drop = $("drop");
  const input = $<HTMLInputElement>("files");
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
  $("show-debug").addEventListener("change", renderImages);
  $("clear-images").addEventListener("click", () => {
    shots.forEach((s) => URL.revokeObjectURL(s.url));
    shots = [];
    reports = [];
    renderImages();
    renderColumns();
  });
  $("extract").addEventListener("click", () => void guarded(extract));
}

/**
 * Initialise the panel.
 *
 * @returns {Promise<void>}
 */
async function init(): Promise<void> {
  settings = await loadSettings();
  events = ((await chrome.storage.session.get("events")).events as ReviewedEvent[] | undefined) ?? [];
  bindSettings();
  bindEventForm();
  bindUpload();
  renderEvents();
  renderImages();
  $("fill").addEventListener("click", () => void guarded(fill));
  $("undo").addEventListener("click", () => void guarded(undo));
  $("refresh").addEventListener("click", () => void refreshStatus());
  chrome.tabs.onActivated.addListener(() => void refreshStatus());
  chrome.tabs.onUpdated.addListener((_id, info) => {
    if (info.status === "complete") void refreshStatus();
  });
  await refreshStatus();
}

void init();
