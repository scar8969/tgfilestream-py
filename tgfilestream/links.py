"""Link registry with expiry and one-time semantics.

The original design handed out permanent, replayable URLs to anyone who had
the link. This module adds the two controls that were missing:

* ``LINK_TTL`` — links expire after N seconds (0 = never).
* ``ONE_TIME_LINKS`` — a link can be downloaded exactly once.

Both are enforced atomically at claim time, so a concurrent burst of
requests can never double-spend a one-time link.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LinkRecord:
    peer_id: int
    msg_id: int
    file_name: str
    file_size: int
    mime_type: str
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    used: bool = False


class LinkRegistry:
    """In-memory registry mapping opaque tokens to file references."""

    def __init__(self) -> None:
        self._links: dict[str, LinkRecord] = {}
        self._lock = asyncio.Lock()

    def create(
        self,
        peer_id: int,
        msg_id: int,
        file_name: str,
        file_size: int,
        mime_type: str,
        ttl: Optional[int] = None,
        token: Optional[str] = None,
    ) -> tuple[str, LinkRecord]:
        """Register a new link and return (token, record).

        ``token`` is normally generated; pass it explicitly for
        deterministic demo/test fixtures.
        """
        token = token or secrets.token_urlsafe(16)
        record = LinkRecord(
            peer_id=peer_id,
            msg_id=msg_id,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            expires_at=(time.time() + ttl) if ttl else None,
        )
        self._links[token] = record
        return token, record

    async def claim(self, token: str, one_time: bool) -> Optional[LinkRecord]:
        """Atomically fetch a link, enforcing expiry and one-time use.

        Returns None when the token is unknown, expired or already spent.
        """
        async with self._lock:
            record = self._links.get(token)
            if record is None:
                return None
            now = time.time()
            if record.expires_at is not None and now > record.expires_at:
                self._links.pop(token, None)
                return None
            if one_time and record.used:
                return None
            if one_time:
                record.used = True
            return record

    def peek(self, token: str) -> Optional[LinkRecord]:
        """Non-destructive lookup (used by the dashboard)."""
        return self._links.get(token)

    def purge_expired(self) -> int:
        """Remove expired links; returns how many were dropped."""
        now = time.time()
        expired = [t for t, r in self._links.items()
                   if r.expires_at is not None and now > r.expires_at]
        for t in expired:
            self._links.pop(t, None)
        return len(expired)

    def __len__(self) -> int:
        return len(self._links)
