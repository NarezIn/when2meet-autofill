"""
Evaluate the extractor on the fixtures in eval/expected/.

For each fixture, prints per-event time error (minutes), missed events, false
positives, and whether the low-confidence flags match. Exits non-zero unless
every fixture passes (no misses, no false positives, 0-minute errors, matching flags).

Usage:
    python eval/run.py [--debug eval/out] [--raw]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from screenshot2meet.cli import load_image
from screenshot2meet.extractor import BOTTOM_GAP_MIN, TOP_GAP_MIN, CvOcrExtractor
from screenshot2meet.models import EventContext, ExtractedEvent, parse_hhmm

ROOT = Path(__file__).resolve().parent.parent


def match(expected: list[dict], got: list[ExtractedEvent]) -> list[tuple[dict | None, ExtractedEvent | None]]:
    """
    Pair expected and extracted events greedily by smallest time error on the same day.

    Args:
        expected (list[dict]): Expected events {day, start, end, low}.
        got (list[ExtractedEvent]): Extracted events.

    Returns:
        list[tuple[dict | None, ExtractedEvent | None]]: Pairs; None marks a miss or a false positive.
    """
    candidates = []
    for i, exp in enumerate(expected):
        for j, ev in enumerate(got):
            if ev.day != exp["day"]:
                continue
            cost = abs(parse_hhmm(ev.start) - parse_hhmm(exp["start"])) + abs(parse_hhmm(ev.end) - parse_hhmm(exp["end"]))
            if cost <= 90:
                candidates.append((cost, i, j))
    used_e, used_g, pairs = set(), set(), []
    for cost, i, j in sorted(candidates):
        if i in used_e or j in used_g:
            continue
        used_e.add(i)
        used_g.add(j)
        pairs.append((expected[i], got[j]))
    pairs += [(exp, None) for i, exp in enumerate(expected) if i not in used_e]
    pairs += [(None, ev) for j, ev in enumerate(got) if j not in used_g]
    return pairs


def evaluate(case_path: Path, debug_dir: Path | None, raw: bool) -> bool:
    """
    Run one fixture and print its report.

    Args:
        case_path (Path): Expected-results JSON.
        debug_dir (Path | None): Where to write the debug overlay, if anywhere.
        raw (bool): Also print unsnapped edge times (for calibrating the block gaps).

    Returns:
        bool: Whether the fixture passed.
    """
    case = json.loads(case_path.read_text(encoding="utf-8"))
    image = load_image(ROOT / case["image"])
    ctx = EventContext()
    events, reports, analyses = CvOcrExtractor().extract_with_reports([image], ctx, debug=debug_dir is not None)
    report = reports[0]
    print(f"\n== {case['image']}")
    if report.error:
        print(f"   ERROR: {report.error}")
    print(f"   axis: {report.axis_labels} labels, rms {report.axis_residual_px}px; grid {report.grid}")
    for col in report.columns:
        print(f"   column {col.column}: {col.day} ({col.quality}) date={col.date} header={col.header_text!r}")
    if debug_dir is not None and report.debug_png:
        import base64

        debug_dir.mkdir(parents=True, exist_ok=True)
        out = debug_dir / f"{Path(case['image']).stem}.debug.png"
        out.write_bytes(base64.b64decode(report.debug_png))
        print(f"   debug overlay: {out}")

    if raw and analyses[0].axis:
        axis = analyses[0].axis
        print(f"   raw edges (gaps: top {TOP_GAP_MIN}, bottom {BOTTOM_GAP_MIN} min):")
        for ev in events:
            x0, y0, x1, y1 = ev.bbox
            top, bottom = axis.y_to_minutes(y0), axis.y_to_minutes(y1 + 1)
            print(f"     {ev.day} {ev.start}-{ev.end}  top={top:7.1f} bottom={bottom:7.1f}  h={y1 - y0 + 1}px  bbox={ev.bbox}")

    ok = True
    errors = []
    print(f"   {'expected':<20} {'got':<20} {'err(min)':>9}  {'low exp/got':<12} {'conf':>5}  title")
    for exp, ev in sorted(match(case["events"], events), key=lambda p: ((p[0] or {}).get("day", p[1].day if p[1] else ""), (p[0] or {}).get("start", p[1].start if p[1] else ""))):
        exp_s = f"{exp['day']} {exp['start']}-{exp['end']}" if exp else "-"
        got_s = f"{ev.day} {ev.start}-{ev.end}" if ev else "-"
        if exp and ev:
            err = abs(parse_hhmm(ev.start) - parse_hhmm(exp["start"])) + abs(parse_hhmm(ev.end) - parse_hhmm(exp["end"]))
            errors.append(err)
            flag_ok = exp["low"] == ev.low_confidence
            status = "" if err == 0 and flag_ok else "  <-- MISMATCH"
            ok &= err == 0 and flag_ok
            print(f"   {exp_s:<20} {got_s:<20} {err:>9}  {str(exp['low']):<5}/{str(ev.low_confidence):<6} {ev.confidence:>5.2f}  {ev.title or ''}{status}")
        elif exp:
            ok = False
            print(f"   {exp_s:<20} {'MISSED':<20}")
        else:
            ok = False
            print(f"   {'FALSE POSITIVE':<20} {got_s:<20} {'':>9}  {'':<12} {ev.confidence:>5.2f}  {ev.title or ''}")
    missed = sum(1 for exp, ev in match(case["events"], events) if ev is None)
    false_pos = sum(1 for exp, ev in match(case["events"], events) if exp is None)
    mean_err = sum(errors) / len(errors) if errors else 0
    print(f"   summary: {len(errors)} matched, {missed} missed, {false_pos} false positives, "
          f"mean error {mean_err:.1f} min -> {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> None:
    """
    Run every fixture in eval/expected and exit with the overall result.

    Returns:
        None
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", type=Path, help="Write debug overlays here.")
    parser.add_argument("--raw", action="store_true", help="Print unsnapped edge times.")
    args = parser.parse_args()
    results = [evaluate(p, args.debug, args.raw) for p in sorted((ROOT / "eval" / "expected").glob("*.json"))]
    print("\nALL PASS" if all(results) else "\nFAILED")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
