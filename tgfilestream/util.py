"""ID packing and small helpers shared across the package."""
from __future__ import annotations

import time
from typing import Optional

# Telegram peer IDs are 64-bit signed integers. We pack the peer and the
# message id into one URL-safe token so a download link is a single opaque
# path segment: /<token>/<filename>.
_PACK_BITS = 32
_PACK_MASK = (1 << _PACK_BITS) - 1


def pack_id(peer_id: int, msg_id: int) -> int:
    """Pack a (peer_id, msg_id) pair into a single 64-bit token."""
    peer = peer_id & _PACK_MASK
    msg = msg_id & _PACK_MASK
    return (peer << _PACK_BITS) | msg


def unpack_id(token: int) -> tuple[Optional[int], Optional[int]]:
    """Unpack a token back into (peer_id, msg_id), or (None, None) if invalid."""
    if token < 0 or token > (1 << 64) - 1:
        return None, None
    peer = token >> _PACK_BITS
    msg = token & _PACK_MASK
    if peer == 0 or msg == 0:
        return None, None
    return peer, msg


def get_file_name(message) -> str:
    """Best-effort file name for a Telegram message."""
    if message and message.file and message.file.name:
        return message.file.name
    if message and getattr(message, "media", None) and getattr(message.media, "document", None):
        attrs = getattr(message.media.document, "attributes", [])
        for attr in attrs:
            if hasattr(attr, "file_name") and attr.file_name:
                return attr.file_name
    return "file.bin"


def get_requester_ip(request, trust_forward_headers: bool) -> str:
    """Client IP, honouring X-Forwarded-For only when explicitly trusted."""
    if trust_forward_headers:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    peername = request.transport.get_extra_info("peername") if request.transport else None
    if peername:
        return str(peername[0])
    return "unknown"


def monotonic_ms() -> int:
    """Monotonic clock in milliseconds (for bandwidth/throughput math)."""
    return int(time.monotonic() * 1000)
