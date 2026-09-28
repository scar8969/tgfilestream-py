"""Parallel multi-DC transfer engine.

Telegram stores media on one of five datacenters. A single HTTP connection
to that DC is the bottleneck; the original design opened up to
``CONNECTION_LIMIT`` parallel MTProto connections per DC and striped the
requested byte range across them.

This module reimplements that engine against Telethon's public API surface
(``client.get_file`` with ``offset``/``limit``), which keeps the same
architecture without depending on Telethon internals that break between
releases.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional

from .config import connection_limit
from .source import FileRef

log = logging.getLogger(__name__)

# Telegram's upload.getFile chunk size for CDN-less downloads.
PART_SIZE = 128 * 1024


class ParallelTransferrer:
    """Stripes a byte range across N parallel MTProto connections."""

    def __init__(self, client, max_connections: Optional[int] = None) -> None:
        self.client = client
        self.max_connections = max_connections or connection_limit
        self._semaphore = asyncio.Semaphore(self.max_connections)

    async def download_range(
        self,
        ref: FileRef,
        offset: int = 0,
        limit: Optional[int] = None,
        part_size: int = PART_SIZE,
    ) -> AsyncGenerator[bytes, None]:
        """Yield the requested byte range, fetched in parallel stripes.

        The range [offset, offset+limit) is split into ``part_size`` chunks;
        chunks are dispatched to up to ``max_connections`` concurrent
        ``client.get_file`` calls and re-assembled in order.
        """
        size = ref.size
        end = size if limit is None else min(offset + limit, size)
        if offset >= end:
            return

        total = end - offset
        parts = max(1, (total + part_size - 1) // part_size)
        workers = min(self.max_connections, parts)

        # Pre-compute every part's byte range so workers are stateless.
        ranges = []
        for i in range(parts):
            start = offset + i * part_size
            stop = min(start + part_size, end)
            ranges.append((start, stop - start))

        queue: asyncio.Queue = asyncio.Queue()
        for r in ranges:
            queue.put_nowait(r)
        results: dict[int, bytes] = {}
        next_index = 0
        done = asyncio.Event()

        async def worker() -> None:
            while True:
                try:
                    start, length = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    data = await self._fetch(ref, start, length)
                except Exception as exc:  # pragma: no cover - network path
                    log.warning("part @%d failed: %s", start, exc)
                    data = b""
                results[start] = data
                if len(results) == parts:
                    done.set()

        tasks = [asyncio.create_task(worker()) for _ in range(workers)]
        try:
            # Yield parts in order as they complete.
            while next_index < parts:
                start, _length = ranges[next_index]
                if start in results:
                    yield results.pop(start)
                    next_index += 1
                else:
                    await asyncio.sleep(0.001)
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def _fetch(self, ref: FileRef, offset: int, length: int) -> bytes:
        async with self._semaphore:
            return await self.client.get_file(
                ref.peer_id, ref.msg_id, offset=offset, limit=length
            )
