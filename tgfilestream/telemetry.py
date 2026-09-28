"""Live transfer telemetry for the dashboard.

Tracks active streams, bytes served, per-IP concurrency and per-DC
connection-pool usage. Everything is monotonic-clock based so the numbers
are meaningful even when the system clock jumps.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class StreamStats:
    token: str
    file_name: str
    ip: str
    started: float = field(default_factory=time.monotonic)
    bytes_sent: int = 0
    last_activity: float = field(default_factory=time.monotonic)


class Telemetry:
    def __init__(self) -> None:
        self._streams: Dict[str, StreamStats] = {}
        self._lock = asyncio.Lock()
        self.total_bytes = 0
        self.total_requests = 0
        self.total_served = 0
        self.total_denied = 0
        self.total_404 = 0
        self.total_416 = 0
        self.total_429 = 0
        self.started_at = time.monotonic()

    async def start_stream(self, token: str, file_name: str, ip: str) -> None:
        async with self._lock:
            self._streams[token] = StreamStats(token=token, file_name=file_name, ip=ip)
            self.total_requests += 1

    async def add_bytes(self, token: str, n: int) -> None:
        async with self._lock:
            stream = self._streams.get(token)
            if stream is not None:
                stream.bytes_sent += n
                stream.last_activity = time.monotonic()
            self.total_bytes += n

    async def finish_stream(self, token: str) -> None:
        async with self._lock:
            self._streams.pop(token, None)
            self.total_served += 1

    def bump(self, attr: str) -> None:
        setattr(self, attr, getattr(self, attr) + 1)

    async def snapshot(self) -> dict:
        """Dashboard-friendly snapshot of all counters + active streams."""
        async with self._lock:
            now = time.monotonic()
            streams = [
                {
                    "file": s.file_name,
                    "ip": s.ip,
                    "bytes": s.bytes_sent,
                    "elapsed": round(now - s.started, 1),
                    "rate_kbps": round(
                        s.bytes_sent / 1024 / max(now - s.started, 0.001), 1
                    ),
                }
                for s in self._streams.values()
            ]
            return {
                "uptime_s": round(now - self.started_at, 1),
                "active_streams": len(streams),
                "streams": streams,
                "total_requests": self.total_requests,
                "total_served": self.total_served,
                "total_bytes": self.total_bytes,
                "total_denied": self.total_denied,
                "total_404": self.total_404,
                "total_416": self.total_416,
                "total_429": self.total_429,
            }
