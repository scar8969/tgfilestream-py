"""Unit tests for the parallel transfer engine.

Uses a fake client whose ``get_file`` is a deterministic byte-slice function,
so the striping/ordering logic is verified without any network.
"""
import pytest

from tgfilestream.paralleltransfer import ParallelTransferrer
from tgfilestream.source import FileRef


class FakeClient:
    def __init__(self, data: bytes):
        self.data = data
        self.calls: list[tuple[int, int]] = []

    async def get_file(self, peer_id, msg_id, offset=0, limit=None):
        self.calls.append((offset, limit))
        return self.data[offset : offset + limit]


def make_ref(data: bytes, name="f.bin") -> FileRef:
    return FileRef(1001, 1, name, len(data), "application/octet-stream")


async def collect(agen):
    return b"".join([chunk async for chunk in agen])


@pytest.mark.asyncio
async def test_full_download_matches_source():
    data = bytes(range(256)) * 64  # 16 KB
    client = FakeClient(data)
    transferrer = ParallelTransferrer(client, max_connections=4)
    out = await collect(transferrer.download_range(make_ref(data)))
    assert out == data


@pytest.mark.asyncio
async def test_byte_range_subset():
    data = bytes(range(256)) * 64
    client = FakeClient(data)
    transferrer = ParallelTransferrer(client, max_connections=4)
    out = await collect(transferrer.download_range(make_ref(data), offset=1000, limit=5000))
    assert out == data[1000:6000]


@pytest.mark.asyncio
async def test_range_clamped_to_file_size():
    data = b"x" * 1000
    client = FakeClient(data)
    transferrer = ParallelTransferrer(client, max_connections=2)
    out = await collect(transferrer.download_range(make_ref(data), offset=900, limit=10**6))
    assert out == data[900:]


@pytest.mark.asyncio
async def test_empty_range_yields_nothing():
    data = b"x" * 1000
    client = FakeClient(data)
    transferrer = ParallelTransferrer(client, max_connections=2)
    out = await collect(transferrer.download_range(make_ref(data), offset=500, limit=0))
    assert out == b""


@pytest.mark.asyncio
async def test_single_connection_still_correct():
    data = bytes(range(256)) * 32
    client = FakeClient(data)
    transferrer = ParallelTransferrer(client, max_connections=1)
    out = await collect(transferrer.download_range(make_ref(data)))
    assert out == data


@pytest.mark.asyncio
async def test_parallelism_actually_used():
    """With max_connections=8 and a large file, more than one get_file call
    must be issued (i.e. the range is actually striped)."""
    data = bytes(range(256)) * 1024  # 256 KB -> 2 full parts
    client = FakeClient(data)
    transferrer = ParallelTransferrer(client, max_connections=8)
    await collect(transferrer.download_range(make_ref(data)))
    assert len(client.calls) > 1
    # Every stripe must be a full part except possibly the last.
    for offset, limit in client.calls[:-1]:
        assert limit == 128 * 1024
