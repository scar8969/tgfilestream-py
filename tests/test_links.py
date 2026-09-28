"""Unit tests for the link registry (expiry + one-time semantics)."""
import asyncio
import time

import pytest

from tgfilestream.links import LinkRegistry


@pytest.mark.asyncio
async def test_create_and_claim():
    links = LinkRegistry()
    token, record = links.create(1001, 5, "a.bin", 100, "application/octet-stream")
    assert token
    assert record.peer_id == 1001
    claimed = await links.claim(token, one_time=False)
    assert claimed is not None
    assert claimed.msg_id == 5


@pytest.mark.asyncio
async def test_unknown_token():
    links = LinkRegistry()
    assert await links.claim("nope", one_time=False) is None


@pytest.mark.asyncio
async def test_expiry():
    links = LinkRegistry()
    token, _ = links.create(1001, 1, "a.bin", 10, "x", ttl=1)
    # claimable before expiry
    assert await links.claim(token, one_time=False) is not None
    time.sleep(1.1)
    assert await links.claim(token, one_time=False) is None


@pytest.mark.asyncio
async def test_expiry_zero_means_never():
    links = LinkRegistry()
    token, _ = links.create(1001, 1, "a.bin", 10, "x", ttl=0)
    assert await links.claim(token, one_time=False) is not None


@pytest.mark.asyncio
async def test_one_time_link_single_use():
    links = LinkRegistry()
    token, _ = links.create(1001, 1, "a.bin", 10, "x")
    assert await links.claim(token, one_time=True) is not None
    assert await links.claim(token, one_time=True) is None


@pytest.mark.asyncio
async def test_one_time_link_concurrent_burst_single_winner():
    """A burst of concurrent claims must never double-spend a one-time link."""
    links = LinkRegistry()
    token, _ = links.create(1001, 1, "a.bin", 10, "x")

    async def claim():
        return await links.claim(token, one_time=True)

    results = await asyncio.gather(*[claim() for _ in range(20)])
    assert sum(1 for r in results if r is not None) == 1


@pytest.mark.asyncio
async def test_purge_expired():
    links = LinkRegistry()
    t1, _ = links.create(1001, 1, "a.bin", 10, "x", ttl=1)
    t2, _ = links.create(1001, 2, "b.bin", 10, "x", ttl=0)
    time.sleep(1.1)
    assert links.purge_expired() == 1
    assert links.peek(t1) is None
    assert links.peek(t2) is not None
