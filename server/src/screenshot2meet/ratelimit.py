"""Per-client-IP sliding-window rate limits, kept in memory on each machine."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    """
    Best guess at the caller's IP address.

    Behind Fly.io every request arrives from Fly's proxy, which puts the real address in
    the Fly-Client-IP header (and overwrites any value the client sent).

    Args:
        request (Request): The incoming request.

    Returns:
        str: The client IP, or "unknown" if there is none.
    """
    return request.headers.get("fly-client-ip") or (request.client.host if request.client else "unknown")


class RateLimiter:
    """Allows at most `limit` requests per client IP in any `window_seconds`-long window."""

    def __init__(self, name: str, limit: int, window_seconds: float) -> None:
        """
        Create a limiter.

        Args:
            name (str): What is being limited, used in the error message (e.g. "screenshot reads").
            limit (int): Maximum requests per IP per window.
            window_seconds (float): Window length in seconds.
        """
        self.name = name
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = float("-inf")

    def hit(self, key: str, now: float | None = None) -> float | None:
        """
        Record one request for `key` if it is under the limit.

        Args:
            key (str): The client identifier (IP address).
            now (float | None): Current monotonic time; defaults to time.monotonic().

        Returns:
            float | None: None if allowed, otherwise seconds until the next request is allowed.
        """
        now = time.monotonic() if now is None else now
        cutoff = now - self.window
        with self._lock:
            self._sweep(now, cutoff)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return hits[0] - cutoff
            hits.append(now)
            return None

    def _sweep(self, now: float, cutoff: float) -> None:
        """
        Drop IPs with no recent requests, at most once per window, so memory stays bounded.

        Args:
            now (float): Current monotonic time.
            cutoff (float): Requests at or before this time are outside the window.

        Returns:
            None
        """
        if now - self._last_sweep < self.window:
            return
        self._last_sweep = now
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]

    def dependency(self) -> Callable[[Request], None]:
        """
        A FastAPI dependency that raises 429 (with Retry-After) once the caller is over the limit.

        Returns:
            Callable[[Request], None]: The dependency function.
        """

        def check(request: Request) -> None:
            """
            Count this request against the caller's limit.

            Args:
                request (Request): The incoming request.

            Returns:
                None

            Raises:
                HTTPException: 429 if the caller is over the limit.
            """
            wait = self.hit(client_ip(request))
            if wait is None:
                return
            minutes = max(1, math.ceil(wait / 60))
            raise HTTPException(
                status_code=429,
                detail=f"Too many {self.name} from your network. Try again in {minutes} "
                f"minute{'s' if minutes != 1 else ''}.",
                headers={"Retry-After": str(math.ceil(wait))},
            )

        return check
