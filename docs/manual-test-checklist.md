# Manual test checklist (extension)

Setup: run the service (`.venv\Scripts\screenshot2meet serve`), run `npm run build`
in `extension/`, and load `extension/dist` unpacked (Reload it after rebuilding).
Use the test events, or make your own:

- Specific dates: https://www.when2meet.com/?39090990-pwxVg
- Days of the week: https://www.when2meet.com/?39090991-uAXKc

`npm run e2e` automates most of this list (it uses the live test events), but
do the visual checks by hand.

## Status
- [ ] With the service stopped, the panel shows "Service: not running"; start it and click Refresh → "running".
- [ ] On a non-When2meet tab: "When2meet: open an event".
- [ ] On an event before sign-in: "sign in first"; after sign-in: your name, mode, and timezone
      ("America/New_York" for specific dates, "wall clock" for days of the week).

## Upload and review
- [ ] Choose, drag-and-drop, and paste (Ctrl+V) a screenshot; each one appears as a preview.
- [ ] "Read screenshots" with the fixture → "Found 12 events, 5 to double-check."
- [ ] Events are grouped Mon / Tue and sorted by time. The 09:00, 14:15 and 22:30 pills are
      highlighted "low", with their reasons shown underneath.
- [ ] "Plan out Tomorrow" is unchecked by default (keyword rule) and greyed out.
- [ ] Edit a start/end time → it persists after closing and reopening the panel.
- [ ] Delete an event → it disappears; uncheck "counts as busy" → the row greys out.
- [ ] Debug overlay toggle: shows the axis ticks, columns with weekday, grid bounds, and boxes
      labelled with times and confidence; toggling off shows the originals again.
- [ ] A screenshot without a readable time axis (e.g. a random photo) shows "Couldn't read this
      screenshot: …" and the other screenshots still work.
- [ ] Crop the fixture header off (no weekday text) → "Check weekdays" lists the columns with a
      pre-filled guess; changing a column's weekday moves its events to that day.

## Fill
- [ ] "Fill When2meet" on the specific-dates event: Mon/Tue are filled except the busy times;
      Wed–Fri are fully available. The group grid updates.
- [ ] The low-confidence cells (Mon 09:00, Mon 14:15, Tue 09:00) have a dashed orange outline.
- [ ] You can still click and drag on outlined cells (the outline doesn't block clicks).
- [ ] Reload the When2meet page: the availability is still there (it was saved), and the
      outlines are gone (expected; they're only drawn after a fill).
- [ ] Same on the days-of-week event.
- [ ] Specific-dates event spanning two weeks (e.g. Mon 10/5 – Fri 10/16): Fill asks
      "Which date is each weekday?"; pick dates and Fill again → only those dates change.

## Undo
- [ ] Set some availability by hand, Fill, then "Undo fill" → exactly your hand-set availability
      comes back, and the outlines disappear.
- [ ] Fill twice, then Undo twice → back to the state before the first fill.
- [ ] "Undo fill" is disabled when there is nothing to undo for this event.

## Settings
- [ ] Settings persist after closing the panel.
- [ ] Buffer 15 min → busy blocks grow by one slot on each side.
- [ ] Minimum free block 60 min → free gaps shorter than an hour stay unavailable.
- [ ] "Skip review and fill immediately" on → "Read screenshots" fills right away
      (the message shows both the extraction summary and the fill result).
- [ ] Confidence threshold 0.5 → the pills are no longer flagged low after re-reading.
- [ ] OCR engine = Tesseract without Tesseract installed → a clear error, not a hang.
