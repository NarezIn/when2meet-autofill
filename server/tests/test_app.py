"""Tests for the service's upload limits and rate limiting (screenshot2meet.app, screenshot2meet.ratelimit)."""

from __future__ import annotations

from collections.abc import Iterator

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from screenshot2meet import app as app_module
from screenshot2meet.app import MAX_IMAGES, app, extract_limiter, fill_plan_limiter
from screenshot2meet.ratelimit import RateLimiter


@pytest.fixture(autouse=True)
def reset_limiters() -> Iterator[None]:
    """
    Clear the app's rate-limit counters before and after each test.

    Returns:
        Iterator[None]: Yields once while the test runs.
    """
    for limiter in (extract_limiter, fill_plan_limiter):
        limiter._hits.clear()
    yield
    for limiter in (extract_limiter, fill_plan_limiter):
        limiter._hits.clear()


def png(width: int, height: int) -> bytes:
    """
    Encode a blank grayscale PNG.

    Args:
        width (int): Image width in pixels.
        height (int): Image height in pixels.

    Returns:
        bytes: The PNG file.
    """
    ok, buf = cv2.imencode(".png", np.zeros((height, width), np.uint8))
    assert ok
    return buf.tobytes()


def test_limiter_blocks_after_limit_and_recovers() -> None:
    """The limiter allows `limit` hits per window, then reports the wait, then allows again."""
    limiter = RateLimiter("tests", limit=2, window_seconds=100)
    assert limiter.hit("1.2.3.4", now=0) is None
    assert limiter.hit("1.2.3.4", now=10) is None
    assert limiter.hit("1.2.3.4", now=20) == pytest.approx(80)
    assert limiter.hit("5.6.7.8", now=20) is None  # other IPs are counted separately
    assert limiter.hit("1.2.3.4", now=100.5) is None  # the hit at t=0 has left the window


def test_limiter_sweeps_idle_ips() -> None:
    """IPs with no requests inside the window are forgotten."""
    limiter = RateLimiter("tests", limit=5, window_seconds=10)
    limiter.hit("1.2.3.4", now=0)
    limiter.hit("5.6.7.8", now=15)
    assert "1.2.3.4" not in limiter._hits


def test_extract_rejects_too_many_files() -> None:
    """More than MAX_IMAGES screenshots is a 413 before anything is decoded."""
    files = [("files", (f"{i}.png", png(10, 10), "image/png")) for i in range(MAX_IMAGES + 1)]
    resp = TestClient(app).post("/extract", files=files)
    assert resp.status_code == 413
    assert str(MAX_IMAGES) in resp.json()["detail"]


def test_extract_rejects_large_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file over the byte limit is a 413."""
    monkeypatch.setattr(app_module, "MAX_FILE_BYTES", 100)
    resp = TestClient(app).post("/extract", files=[("files", ("big.png", png(200, 200), "image/png"))])
    assert resp.status_code == 413
    assert "big.png" in resp.json()["detail"]


def test_extract_rejects_too_many_pixels() -> None:
    """An image over the pixel cap is a 413, even though the file itself is small."""
    resp = TestClient(app).post("/extract", files=[("files", ("huge.png", png(5001, 5001), "image/png"))])
    assert resp.status_code == 413
    assert "megapixels" in resp.json()["detail"]


def test_extract_rejects_non_image() -> None:
    """A file that isn't an image is a 422."""
    resp = TestClient(app).post("/extract", files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert resp.status_code == 422


def test_extract_rate_limited_per_fly_client_ip() -> None:
    """After the hourly limit, the same Fly-Client-IP gets 429 with Retry-After; another IP doesn't."""
    client = TestClient(app)
    files = [("files", (f"{i}.png", png(10, 10), "image/png")) for i in range(MAX_IMAGES + 1)]
    for _ in range(extract_limiter.limit):
        assert client.post("/extract", files=files, headers={"Fly-Client-IP": "1.2.3.4"}).status_code == 413
    resp = client.post("/extract", files=files, headers={"Fly-Client-IP": "1.2.3.4"})
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0
    assert "Try again in" in resp.json()["detail"]
    assert client.post("/extract", files=files, headers={"Fly-Client-IP": "5.6.7.8"}).status_code == 413
