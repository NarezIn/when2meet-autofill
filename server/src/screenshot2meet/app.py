"""FastAPI app serving the Screenshot2Meet extension (locally or hosted)."""

from __future__ import annotations

import io
import threading

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from . import __version__
from .extractor import CvOcrExtractor
from .models import EventContext, ExtractResponse, FillPlanRequest, FillPlanResponse
from .ratelimit import RateLimiter
from .slots import compute_fill_plan

# Upload limits for /extract; keep in sync with MAX_IMAGES / MAX_IMAGE_MB in extension/src/api.ts.
MAX_IMAGES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
# Decoded size cap: a small PNG can decode to gigabytes. 25 MP still fits a 5K screenshot.
MAX_PIXELS = 25_000_000

extract_limiter = RateLimiter("screenshot reads", limit=10, window_seconds=3600)
fill_plan_limiter = RateLimiter("fill requests", limit=60, window_seconds=3600)

app = FastAPI(title="Screenshot2Meet", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

extractor = CvOcrExtractor()
# PaddleOCR pipelines are not safe to call from several threads at once.
_extract_lock = threading.Lock()


@app.get("/health")
def health() -> dict[str, str]:
    """
    Report that the service is running.

    Returns:
        dict[str, str]: {"status": "ok", "version": <package version>}.
    """
    return {"status": "ok", "version": __version__}


def decode_upload(upload: UploadFile) -> np.ndarray:
    """
    Read one uploaded screenshot, enforcing the file-size and pixel limits before decoding.

    Args:
        upload (UploadFile): The uploaded file.

    Returns:
        np.ndarray: The BGR image.

    Raises:
        HTTPException: 413 if the file or image is too large, 422 if it is not an image.
    """
    data = upload.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{upload.filename} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.",
        )
    try:
        width, height = Image.open(io.BytesIO(data)).size  # reads the header only
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"{upload.filename} is not an image") from exc
    if width * height > MAX_PIXELS:
        raise HTTPException(
            status_code=413,
            detail=f"{upload.filename} is {width}×{height} pixels; the limit is "
            f"{MAX_PIXELS // 1_000_000} megapixels.",
        )
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=422, detail=f"{upload.filename} is not an image")
    return image


@app.post("/extract", response_model=ExtractResponse, dependencies=[Depends(extract_limiter.dependency())])
def extract(
    files: list[UploadFile] = File(..., description="Calendar screenshots."),
    context: str = Form("{}", description="EventContext as JSON."),
    debug: bool = Form(False, description="Include a debug overlay PNG per screenshot."),
) -> ExtractResponse:
    """
    Extract busy events from uploaded calendar screenshots.

    Args:
        files (list[UploadFile]): Screenshots (PNG/JPEG).
        context (str): JSON-encoded EventContext (mode, dates, OCR settings, threshold, keyword rules).
        debug (bool): Whether to return annotated PNGs.

    Returns:
        ExtractResponse: Merged events and per-image reports.
    """
    if len(files) > MAX_IMAGES:
        raise HTTPException(status_code=413, detail=f"Send at most {MAX_IMAGES} screenshots at a time.")
    try:
        ctx = EventContext.model_validate_json(context)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"bad context: {exc}") from exc
    images = [decode_upload(upload) for upload in files]
    with _extract_lock:
        try:
            events, reports, _ = extractor.extract_with_reports(images, ctx, debug=debug)
        except RuntimeError as exc:  # e.g. Tesseract selected but not installed
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ExtractResponse(events=events, images=reports)


@app.post("/fill-plan", response_model=FillPlanResponse, dependencies=[Depends(fill_plan_limiter.dependency())])
def fill_plan(req: FillPlanRequest) -> FillPlanResponse:
    """
    Compute which When2meet slots should be marked available.

    Args:
        req (FillPlanRequest): Slot timestamps, event mode/timezone, reviewed events, settings.

    Returns:
        FillPlanResponse: Available slots, low-confidence slots, weekday ambiguity.
    """
    try:
        return compute_fill_plan(req)
    except (ValueError, KeyError) as exc:  # bad timezone names and the like
        raise HTTPException(status_code=422, detail=str(exc)) from exc
