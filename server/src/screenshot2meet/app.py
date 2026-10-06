"""FastAPI app serving the Screenshot2Meet extension on 127.0.0.1."""

from __future__ import annotations

import threading

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from . import __version__
from .extractor import CvOcrExtractor
from .models import EventContext, ExtractResponse, FillPlanRequest, FillPlanResponse
from .slots import compute_fill_plan

app = FastAPI(title="Screenshot2Meet", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
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


@app.post("/extract", response_model=ExtractResponse)
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
    try:
        ctx = EventContext.model_validate_json(context)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"bad context: {exc}") from exc
    images = []
    for upload in files:
        image = cv2.imdecode(np.frombuffer(upload.file.read(), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=422, detail=f"{upload.filename} is not an image")
        images.append(image)
    with _extract_lock:
        try:
            events, reports, _ = extractor.extract_with_reports(images, ctx, debug=debug)
        except RuntimeError as exc:  # e.g. Tesseract selected but not installed
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ExtractResponse(events=events, images=reports)


@app.post("/fill-plan", response_model=FillPlanResponse)
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
