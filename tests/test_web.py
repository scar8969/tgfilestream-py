"""End-to-end HTTP tests: range requests, one-time links, rate limiting,
HEAD support and the dashboard/health endpoints."""
import pytest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tgfilestream.links import LinkRegistry
from tgfilestream.source import DemoFileSource
from tgfilestream.telemetry import Telemetry
from tgfilestream.web_routes import build_app

DATA = bytes(range(256)) * 64  # 16 KB


def make_source():
    source = DemoFileSource()
    source.register(1001, 1, DATA, "movie.bin", "video/mp4")
    source.register(1001, 2, b"tiny", "tiny.txt", "text/plain")
    return source


def make_app(**kwargs):
    source = make_source()
    links = LinkRegistry()
    telemetry = Telemetry()
    app = build_app(source, links, telemetry, **kwargs)
    return app, source, links


async def create_client(app) -> TestClient:
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client


@pytest.fixture
async def client():
    app, _source, links = make_app()
    token, _ = links.create(1001, 1, "movie.bin", len(DATA), "video/mp4")
    c = await create_client(app)
    c.token = token
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_full_download(client):
    resp = await client.get(f"/{client.token}/movie.bin")
    assert resp.status == 200
    body = await resp.read()
    assert body == DATA
    assert resp.headers["Content-Length"] == str(len(DATA))
    assert resp.headers["Accept-Ranges"] == "bytes"


@pytest.mark.asyncio
async def test_byte_range_request(client):
    resp = await client.get(f"/{client.token}/movie.bin", headers={"Range": "bytes=0-1023"})
    assert resp.status == 206
    body = await resp.read()
    assert body == DATA[0:1024]
    assert resp.headers["Content-Range"] == f"bytes 0-1023/{len(DATA)}"


@pytest.mark.asyncio
async def test_open_ended_range(client):
    resp = await client.get(f"/{client.token}/movie.bin", headers={"Range": "bytes=100-"})
    assert resp.status == 206
    body = await resp.read()
    assert body == DATA[100:]


@pytest.mark.asyncio
async def test_suffix_range(client):
    resp = await client.get(f"/{client.token}/movie.bin", headers={"Range": "bytes=-500"})
    assert resp.status == 206
    body = await resp.read()
    assert body == DATA[-500:]


@pytest.mark.asyncio
async def test_unsatisfiable_range(client):
    resp = await client.get(f"/{client.token}/movie.bin", headers={"Range": "bytes=99999999-"})
    assert resp.status == 416
    assert resp.headers["Content-Range"] == f"bytes */{len(DATA)}"


@pytest.mark.asyncio
async def test_head_request(client):
    resp = await client.head(f"/{client.token}/movie.bin")
    assert resp.status == 200
    assert resp.headers["Content-Length"] == str(len(DATA))
    assert await resp.read() == b""


@pytest.mark.asyncio
async def test_unknown_token_404(client):
    resp = await client.get("/doesnotexist/movie.bin")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_wrong_filename_404(client):
    resp = await client.get(f"/{client.token}/other.bin")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_one_time_link_single_use_http():
    app, _source, links = make_app(one_time=True)
    token, _ = links.create(1001, 1, "movie.bin", len(DATA), "video/mp4")
    c = await create_client(app)
    try:
        assert (await c.get(f"/{token}/movie.bin")).status == 200
        assert (await c.get(f"/{token}/movie.bin")).status == 404
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_expired_link_404():
    import time
    app, _source, links = make_app()
    token, _ = links.create(1001, 1, "movie.bin", len(DATA), "video/mp4", ttl=1)
    c = await create_client(app)
    try:
        assert (await c.get(f"/{token}/movie.bin")).status == 200
        time.sleep(1.1)
        assert (await c.get(f"/{token}/movie.bin")).status == 404
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_rate_limit_429():
    """REQUEST_LIMIT=1: a second request from the same IP while the first is
    still streaming must be rejected with 429."""
    import asyncio

    class SlowSource(DemoFileSource):
        async def download(self, ref, offset=0, limit=None):
            data = self._files[(ref.peer_id, ref.msg_id)][0]
            chunk = data[offset : offset + limit] if limit is not None else data[offset:]
            for i in range(0, len(chunk), 256):
                await asyncio.sleep(0.02)  # keep the stream open long enough
                yield chunk[i : i + 256]

    source = SlowSource()
    source.register(1001, 1, DATA, "movie.bin", "video/mp4")
    links = LinkRegistry()
    token, _ = links.create(1001, 1, "movie.bin", len(DATA), "video/mp4")
    telemetry = Telemetry()
    app = build_app(source, links, telemetry, request_limit=1)
    c = await create_client(app)
    try:
        r1 = await c.get(f"/{token}/movie.bin")
        assert r1.status == 200
        r2 = await c.get(f"/{token}/movie.bin")
        assert r2.status == 429
        await r1.read()
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_healthz_and_dashboard():
    app, _source, links = make_app()
    token, _ = links.create(1001, 1, "movie.bin", len(DATA), "video/mp4")
    c = await create_client(app)
    try:
        await c.get(f"/{token}/movie.bin")
        health = await c.get("/healthz")
        assert health.status == 200
        data = await health.json()
        assert data["status"] == "ok"
        assert data["total_served"] == 1
        assert data["total_requests"] >= 1
        assert data["total_bytes"] == len(DATA)

        index = await c.get("/")
        assert index.status == 200
        html = await index.text()
        assert "tgfilestream dashboard" in html
        assert "active streams" in html
    finally:
        await c.close()
