"""
Command-line entry point.

    screenshot2meet serve                      # run the local HTTP service (default)
    screenshot2meet extract IMG... [--debug DIR]  # print extracted events as JSON
    screenshot2meet download-models            # fetch the default OCR models
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def load_image(path: Path):  # -> np.ndarray
    """
    Read an image file as a BGR array (works with non-ASCII paths on Windows).

    Args:
        path (Path): Image file.

    Returns:
        np.ndarray: BGR image.

    Raises:
        ValueError: If the file can't be decoded.
    """
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"can't read image {path}")
    return image


def cmd_extract(args: argparse.Namespace) -> int:
    """
    Extract events from screenshots and print them as JSON.

    Args:
        args (argparse.Namespace): Parsed arguments (images, debug, dates, engine, languages).

    Returns:
        int: Process exit code.
    """
    from .extractor import CvOcrExtractor
    from .models import EventContext

    ctx = EventContext(
        mode="specific_dates" if args.dates else "days_of_week",
        dates=[dt.date.fromisoformat(d) for d in args.dates.split(",")] if args.dates else [],
        ocr_engine=args.engine,
        ocr_languages=args.languages.split(","),
    )
    images = [load_image(Path(p)) for p in args.images]
    events, reports, analyses = CvOcrExtractor().extract_with_reports(images, ctx, debug=args.debug is not None)
    if args.debug is not None:
        import base64

        out = Path(args.debug)
        out.mkdir(parents=True, exist_ok=True)
        for path, report in zip(args.images, reports):
            target = out / f"{Path(path).stem}.debug.png"
            target.write_bytes(base64.b64decode(report.debug_png or ""))
            print(f"debug overlay: {target}", file=sys.stderr)
    payload = {
        "events": [e.model_dump(mode="json") for e in events],
        "images": [r.model_dump(mode="json", exclude={"debug_png"}) for r in reports],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_download_models(args: argparse.Namespace) -> int:
    """
    Instantiate the OCR engine once so its models are downloaded and cached.

    Args:
        args (argparse.Namespace): Parsed arguments (engine, languages).

    Returns:
        int: Process exit code.
    """
    from .ocr import get_engine
    from .weekdays import weekday_table

    get_engine(args.engine, tuple(args.languages.split(",")))
    weekday_table()
    print("OCR models and weekday table are ready.")
    return 0


def main(argv: list[str] | None = None) -> None:
    """
    Parse arguments and run the requested command.

    Args:
        argv (list[str] | None): Arguments without the program name; defaults to sys.argv.

    Returns:
        None
    """
    parser = argparse.ArgumentParser(prog="screenshot2meet")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the local HTTP service (default).")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    ocr_args = argparse.ArgumentParser(add_help=False)
    ocr_args.add_argument("--engine", choices=["paddle", "tesseract"], default="paddle")
    ocr_args.add_argument("--languages", default="en,ch,chinese_cht,es", help="Comma-separated OCR languages.")

    extract = sub.add_parser("extract", parents=[ocr_args], help="Extract busy events from screenshots.")
    extract.add_argument("images", nargs="+")
    extract.add_argument("--debug", metavar="DIR", help="Write an annotated PNG per screenshot into DIR.")
    extract.add_argument("--dates", help="Specific-dates event: comma-separated ISO dates.")

    sub.add_parser("download-models", parents=[ocr_args], help="Download OCR models ahead of time.")

    args = parser.parse_args(argv)
    if args.command == "extract":
        raise SystemExit(cmd_extract(args))
    if args.command == "download-models":
        raise SystemExit(cmd_download_models(args))

    import uvicorn

    uvicorn.run("screenshot2meet.app:app", host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", 8765))


if __name__ == "__main__":
    main()
