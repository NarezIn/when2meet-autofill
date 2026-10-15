# Manual test checklist (extension)

Setup: run the service (`.venv\Scripts\screenshot2meet serve`), run `npm run build`
in `extension/`, and load `extension/dist` unpacked (Reload it after rebuilding).
Use the test events, or make your own:

- Specific dates: https://www.when2meet.com/?39090990-pwxVg
- Days of the week: https://www.when2meet.com/?39090991-uAXKc

`npm run e2e` automates most of this list (it uses the live test events), but
do the visual checks by hand.

## Status (banner + Settings → Connection)
- [ ] With the service stopped, a yellow banner says "Start the local service…"; start it and
      click "Check again" → the banner disappears.
- [ ] On a non-When2meet tab: banner "Open your When2meet event in this tab."
- [ ] On an event before sign-in: banner "Sign in with your name…"; after sign-in: no banner.
- [ ] Settings → Connection shows Service (Running), Signed in as (your name), Event type,
      and Timezone ("America/New_York" for specific dates, "Wall clock (no timezone)" for
      days of the week). Refresh re-checks.
- [ ] There is no status line or Refresh button at the top of the panel.

## Upload and screenshots
- [ ] Choose, drag-and-drop, and paste (Ctrl+V) a screenshot; each one appears as a thumbnail
      in upload order, with its file name and an × button.
- [ ] Thumbnails are never cropped (the whole phone screenshot is visible); 2 per row at
      320px panel width, 3 at 500px. Check with 1 and with 6 screenshots.
- [ ] × removes just that screenshot; "Remove all" removes all of them.
- [ ] Clicking a thumbnail opens the full-size view: it scrolls vertically, Previous/Next
      move between screenshots (disabled at the ends), Close and Esc close it.
- [ ] Developer mode off: the full-size view shows the original screenshot.
- [ ] Developer mode on, after Fill: the full-size view shows the axis ticks, columns with
      weekday, grid bounds, and boxes labelled with times and confidence. Before Fill it says
      the overlay appears after Fill.
- [ ] A screenshot without a readable time axis (e.g. a random photo): Fill stops, that
      thumbnail is outlined red with "Couldn't read this screenshot: …"; remove it and Fill works.
- [ ] Blank out the fixture's weekday header → Fill stops and that screenshot shows
      "Which day is this column?" with a pre-filled guess; pick a day and Fill again → those
      events land on the chosen day. The other screenshots show no prompt.
- [ ] There is no review list, no per-event edit/delete/"counts as busy" controls, no
      "Read screenshots" button, no "Debug overlay" toggle, and no "Skip review" setting.

## Fill
- [ ] "Fill When2meet" with the fixture on the specific-dates event reads and fills in one go:
      Mon/Tue are filled except the busy times; Wed–Fri are fully available. The group grid
      updates. "Plan out Tomorrow" (keyword rule) is not treated as busy.
- [ ] After filling, one line reads "Filled N slots · 3 uncertain slots outlined on the grid"
      next to "Undo fill".
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
- [ ] The summary line and "Undo fill" are hidden when there is nothing to undo for this event.

## Settings
- [ ] Settings persist after closing the panel.
- [ ] Buffer 15 min → busy blocks grow by one slot on each side.
- [ ] Minimum free block 60 min → free gaps shorter than an hour stay unavailable.
- [ ] Color rules: with screenshots added, "Find colors in my screenshots" shows one swatch per
      calendar color with an event count (fixture: red 2, light blue 2, yellow 3, purple 2, blue 3).
- [ ] Click the yellow swatch → it appears as a chip and the swatch greys out; × removes it.
- [ ] Switch off (ignore these colors) + yellow → Fill says "3 events left out by your color
      rules." and Tue 09:30–10:45 / 14:00–15:15 stay available.
- [ ] Switch on (only these colors) + yellow → only the yellow events block time.
- [ ] Changing color rules and filling again doesn't show "Reading screenshots…" (no re-read).
- [ ] Confidence threshold 0.5 → fewer (or no) cells are outlined after the next Fill.
- [ ] OCR engine = Tesseract without Tesseract installed → a clear error, not a hang.
