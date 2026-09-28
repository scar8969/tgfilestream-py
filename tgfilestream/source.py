"""File source abstraction.

The original implementation was hard-wired to Telegram. Splitting the file
source behind this interface is what lets the whole stack run in demo mode
(no API credentials, deterministic fixtures) and keeps the web layer fully
unit-testable.
"""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from .util import get_file_name


class FileRef:
    """Resolved file reference handed to the download layer."""

    def __init__(self, peer_id: int, msg_id: int, file_name: str, size: int, mime_type: str) -> None:
        self.peer_id = peer_id
        self.msg_id = msg_id
        self.file_name = file_name
        self.size = size
        self.mime_type = mime_type


class FileSource:
    """Anything that can resolve a (peer, msg) reference to file bytes."""

    async def resolve(self, peer_id: int, msg_id: int) -> Optional[FileRef]:
        raise NotImplementedError

    async def download(
        self,
        ref: FileRef,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError


class DemoFileSource(FileSource):
    """In-memory file store used by DEMO_MODE and the test suite.

    Files are registered by (peer_id, msg_id); the bytes live in memory, so
    downloads are instant and deterministic. This is also the reference
    implementation the Telegram source is tested against.
    """

    def __init__(self) -> None:
        self._files: dict[tuple[int, int], tuple[bytes, str, str]] = {}

    def register(self, peer_id: int, msg_id: int, data: bytes, name: str, mime: str) -> None:
        self._files[(peer_id, msg_id)] = (data, name, mime)

    async def resolve(self, peer_id: int, msg_id: int) -> Optional[FileRef]:
        entry = self._files.get((peer_id, msg_id))
        if entry is None:
            return None
        data, name, mime = entry
        return FileRef(peer_id, msg_id, name, len(data), mime)

    async def download(
        self,
        ref: FileRef,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> AsyncGenerator[bytes, None]:
        entry = self._files.get((ref.peer_id, ref.msg_id))
        if entry is None:
            return
        data, _name, _mime = entry
        chunk = data[offset : offset + limit] if limit is not None else data[offset:]
        yield chunk


class TelegramFileSource(FileSource):
    """Streams files out of Telegram through the parallel transferrer."""

    def __init__(self, client, transferrer) -> None:
        self.client = client
        self.transferrer = transferrer

    async def resolve(self, peer_id: int, msg_id: int) -> Optional[FileRef]:
        message = await self.client.get_messages(entity=peer_id, ids=msg_id)
        if not message or not message.file:
            return None
        return FileRef(
            peer_id, msg_id, get_file_name(message), message.file.size, message.file.mime_type
        )

    async def download(
        self,
        ref: FileRef,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> AsyncGenerator[bytes, None]:
        async for chunk in self.transferrer.download_range(ref, offset=offset, limit=limit):
            yield chunk
