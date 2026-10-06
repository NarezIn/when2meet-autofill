# How When2meet's "your availability" grid works

Inspected 2026-10-06 against two throwaway events created for this purpose
(event timezone America/New_York, 8 AM to midnight):

- Specific dates (Mon 10/5 to Fri 10/9): https://www.when2meet.com/?39090990-pwxVg
- Days of the week (Sun to Sat): https://www.when2meet.com/?39090991-uAXKc

Everything below comes from the page's own inline JavaScript and from the
`AvailabilityGrids.php` response. There are no external app scripts; all logic
is inline in the event page.

## Page globals (all top-level `var`, so they live on `window`; MAIN world only)

| Global | Meaning |
|---|---|
| `TimeOfSlot[i]` | Unix timestamp (seconds) of slot `i`. 15-minute slots. |
| `AvailableAtSlot[i]` | Array of person IDs available at slot `i`. |
| `UserID` | Signed-in person's ID; `0` when nobody is signed in. |
| `PeopleNames`, `PeopleIDs` | Participants. |
| `IsMouseDown`, `ChangeToAvailable`, `FromCol/ToCol/FromRow/ToRow` | Drag-selection state. |

The event ID and code are hard-coded inside `LoadAvailabilityGrids()` and the
page URL (`?<id>-<code>`). The event timezone appears as a string literal in
the `DOMContentLoaded` handler: `if (select.value != "America/New_York")`.

## Cells

Each cell in the "your availability" grid (`#YouGrid`, hidden until sign-in) is:

```html
<div id='YouTime1791201600' data-col="0" data-row="0" data-time="1791201600"
     onmousedown='SelectFromHere(event);' onmouseover='SelectToHere(event);'
     ontouchstart=... ontouchmove=... ontouchend=...
     style='...width:44px;height:9px;...background: #ffdede'>
```

- `id = "YouTime" + timestamp`, plus `data-time`, `data-col` (day column) and
  `data-row` (15-minute row).
- Colors: available `#339900`, unavailable `#ffdede`. These are set as inline
  styles by `ReColorIndividual()`.
- The group grid uses `GroupTime<timestamp>` cells.

## Selection and saving

1. `mousedown` on a cell calls `SelectFromHere(e)`. It reads `e.target`'s
   `data-time`/`data-col`/`data-row`, and sets
   `ChangeToAvailable = !(UserID in AvailableAtSlot[thatSlot])`. So a drag
   always sets the opposite of the **first** cell's state.
2. `mouseover` on a cell calls `SelectToHere(e)`, which moves the rectangle's
   far corner.
3. `document.onmouseup = SelectStop`. This applies the change to every cell in
   the **rectangle** (FromCol..ToCol × FromRow..ToRow), then makes one
   `fetch("SaveTimes.php", POST)` with:
   `person`, `event`, `slots` (comma-joined toggled timestamps), `availability`
   (a "0"/"1" string covering **every** slot in `TimeOfSlot` order), `password`
   (read from `#password`), and `ChangeToAvailable`.
   The fetch is fire-and-forget (`.then(console.log)`).

There is one save per drag, and a drag can only cover a rectangle.

Sign-in: `ProcessLogin()` POSTs to `ProcessLogin.php`, sets `UserID`, hides
`#SignIn`, and shows `#YouGrid`.

## Timestamps and the two event modes

**Specific dates.** `TimeOfSlot` holds real instants. `1791201600` is
2026-10-05 12:00 UTC = 08:00 EDT.

**Days of the week.** The timestamps sit in a fixed 1978 week:
`279705600` is Sun 1978-11-12 08:00 **UTC**. The slot's wall-clock time in the
*event's* timezone is encoded as if it were UTC: `day = (ts - 279705600) // 86400`
with Sun=0, and `minutes = (ts % 86400) / 60`.

Detect the mode from the timestamps. If every `TimeOfSlot` is below ~10^9, the
event is days-of-the-week; otherwise it uses specific dates. The headers
confirm it: "Oct 5 Mon" vs "Sun".

**Participant timezone.** The page fills a `#ParticipantTimeZone` select with
the browser's timezone. If that differs from the event timezone, it re-fetches
the grid HTML from `AvailabilityGrids.php` with `participantTimeZone`. I
fetched the specific-dates grid with New York and with Los Angeles: the cell
`data-time` values and `TimeOfSlot` are **identical**, and only the row labels
shift (8 AM to 5 AM). So cell identity is timezone-independent, and we never
parse the labels.

**Days-of-the-week events have no timezone.** Their page doesn't contain the
`select.value != "<tz>"` literal. Instead, the `DOMContentLoaded` handler
hard-codes the participant timezone to `UTC`, so the grid always shows the
UTC-encoded wall clock as is. (If you call `AvailabilityGrids.php` yourself
with another timezone, the labels shift, e.g. 8 AM shows as "3:00 AM" for New
York, but the page itself never does that.) We therefore treat
days-of-the-week slots as plain wall clock and match screenshot times
(America/New_York) to them directly. `eventTz` is `null` in this mode.

## Proposed fill strategy

**Drive the page's own drag mechanism from a MAIN-world script.** No direct
POSTs.

1. **Read state (MAIN world)** from `TimeOfSlot`, `AvailableAtSlot`, `UserID`,
   the event timezone literal, and the mode. Require `UserID != 0`; otherwise
   tell the user to sign in first. A content script running in the ISOLATED
   world forwards this to the side panel.
2. **Map slots to wall-clock times.** In specific-dates mode, the side panel
   / service converts each timestamp into an America/New_York datetime (the
   screenshot timezone) and matches by date. In days-of-the-week mode, it
   decodes the event-timezone wall clock as above. If the event timezone isn't
   New York, it converts NY → event timezone using the upcoming week's date for
   DST.
3. **Snapshot for undo**: the set of timestamps where `UserID` is available,
   stored in `chrome.storage.session` keyed by `eventId:UserID`, taken before
   any change.
4. **Apply a target set** (used by both fill and undo):
   - Diff the target against the current state. That gives `toAdd` (currently
     unavailable, should be available) and `toRemove`.
   - Group each set into maximal vertical runs within a column (same
     `data-col`, consecutive `data-row`). Every cell in a run needs the same
     change, so the run's first cell has the opposite state. Its `mousedown`
     sets `ChangeToAvailable` correctly.
   - Per run: dispatch `mousedown` on the first cell, `mouseover` on the last
     cell, then `mouseup` on `document`. The page's own handlers run exactly
     as for a real drag.
   - **Serialize saves.** Each `SaveTimes.php` call sends the *full*
     availability string, so two saves that arrive out of order could
     overwrite a newer state with an older one. The MAIN-world script wraps
     `window.fetch` to track in-flight `SaveTimes.php` requests and waits for
     each one to finish before starting the next drag.
   - **Verify** afterwards by re-reading `AvailableAtSlot`, and retry any
     mismatched runs once.
5. **Low-confidence outlines.** Inject a stylesheet with
   `.s2m-lowconf { outline: 2px dashed #f5a623; outline-offset: -2px; }` and
   add that class to the affected `YouTime…` cells. An outline takes no layout
   space and doesn't intercept the mouse. `ReColorIndividual()` only rewrites
   background and border colors, so it survives repaints. The outlines are
   cleared on undo or on the next fill.
6. **Undo** applies the snapshot through the same mechanism as step 4.
7. **Specific-dates events longer than 7 days.** If a weekday appears more
   than once, the side panel asks which date each screenshot weekday maps to.

**Fallback (only if the simulated drag proves unreliable):** set
`AvailableAtSlot` in MAIN world and make one `SaveTimes.php` POST with the
same parameters `SelectStop` sends, then call `ReColorIndividual()` and
`ReColorGroup()`.

## Verified (2026-10-06, test user "simon-claude")

- **The server stores the full `availability` string** and ignores `slots` /
  `ChangeToAvailable`. A POST with `slots` = slots 0,1 and an `availability`
  string marking only slot 5 left exactly slot 5 available. The last save to
  arrive wins, so serializing saves is required.
- **Synthetic `mousedown`/`mouseover`/`mouseup` events dispatched from a
  MAIN-world script drive the page's handlers**, and the result is saved.
  Tested end to end with real Chrome and the unpacked extension on both test
  events: the server state after fill matched the expected slots exactly, the
  outlined cells were exactly the low-confidence ones and stayed the click
  target, and undo restored the pre-fill state (including a pre-existing range
  set by a real mouse drag). Fill took under 1 s for 12-15 drags.
