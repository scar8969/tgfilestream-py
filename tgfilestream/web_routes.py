"""HTTP routes: streaming downloads, health check and the live dashboard."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiohttp import web

from .config import (
    link_ttl,
    one_time_links,
    request_limit,
    trust_forward_headers,
)
from .links import LinkRegistry
from .source import FileSource
from .telemetry import Telemetry
from .util import get_requester_ip

log = logging.getLogger(__name__)

routes = web.RouteTableDef()


class StreamLimiter:
    """Per-IP concurrency guard (the original's REQUEST_LIMIT)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._active: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, ip: str) -> bool:
        async with self._lock:
            if self._active.get(ip, 0) >= self.limit:
                return False
            self._active[ip] = self._active.get(ip, 0) + 1
            return True

    async def release(self, ip: str) -> None:
        async with self._lock:
            self._active[ip] = max(0, self._active.get(ip, 0) - 1)
            if self._active[ip] == 0:
                self._active.pop(ip, None)


def build_app(
    source: FileSource,
    links: LinkRegistry,
    telemetry: Telemetry,
    *,
    request_limit: int = request_limit,
    one_time: bool = one_time_links,
    ttl: Optional[int] = link_ttl,
    trust_headers: bool = trust_forward_headers,
) -> web.Application:
    """Assemble the aiohttp app with all routes wired to the given services."""
    limiter = StreamLimiter(request_limit)
    app = web.Application()
    app["source"] = source
    app["links"] = links
    app["telemetry"] = telemetry
    app["limiter"] = limiter
    app["one_time"] = one_time
    app["ttl"] = ttl
    app["trust_headers"] = trust_headers
    app.add_routes(routes)
    return app


def _parse_range(header: Optional[str], size: int) -> Optional[tuple[int, int]]:
    """Parse a single-range ``bytes=start-end`` header into (start, end).

    Supports absolute ranges (``bytes=100-200``), open-ended ranges
    (``bytes=100-``) and suffix ranges (``bytes=-500`` = last 500 bytes),
    per RFC 7233. Returns None when the header is absent, multi-range or
    not satisfiable against ``size``.
    """
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].strip()
    if "," in spec:
        return None  # multi-range not supported
    try:
        start_s, _, end_s = spec.partition("-")
        if start_s == "" and end_s != "":
            # Suffix range: last N bytes.
            n = int(end_s)
            if n <= 0:
                return None
            start = max(size - n, 0)
            end = size - 1
        elif start_s != "":
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        else:
            return None
    except ValueError:
        return None
    if start < 0 or end < start or start >= size:
        return None
    return start, min(end, size - 1)


@routes.get("/healthz")
async def healthz(request: web.Request) -> web.Response:
    telemetry: Telemetry = request.app["telemetry"]
    return web.json_response({"status": "ok", **await telemetry.snapshot()})


@routes.get("/")
async def index(request: web.Request) -> web.Response:
    telemetry: Telemetry = request.app["telemetry"]
    snap = await telemetry.snapshot()
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>tgfilestream dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{ --bg:#0b0f14; --panel:#12181f; --line:#1f2937; --txt:#e5e7eb; --dim:#9ca3af; --acc:#22d3ee; --ok:#34d399; --warn:#fbbf24; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--bg); color:var(--txt); font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; padding:24px; }}
h1 {{ font-size:18px; margin-bottom:4px; }}
.sub {{ color:var(--dim); margin-bottom:20px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:24px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
.card .k {{ color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.08em; }}
.card .v {{ font-size:22px; margin-top:4px; }}
.card .v.ok {{ color:var(--ok); }} .card .v.warn {{ color:var(--warn); }} .card .v.acc {{ color:var(--acc); }}
table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
th,td {{ text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); font-size:13px; }}
th {{ color:var(--dim); font-weight:500; text-transform:uppercase; font-size:11px; letter-spacing:.06em; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
tr:last-child td {{ border-bottom:none; }}
.empty {{ color:var(--dim); padding:16px; text-align:center; }}
#refresh {{ position:fixed; right:16px; bottom:16px; background:var(--acc); color:#04121a; border:none; border-radius:6px; padding:8px 14px; font:600 13px ui-monospace,monospace; cursor:pointer; }}
</style></head>
<body>
<h1>tgfilestream</h1>
<div class="sub">live transfer telemetry &mdash; auto-refreshes every 2s</div>
<div class="grid">
  <div class="card"><div class="k">active streams</div><div class="v acc" id="active">0</div></div>
  <div class="card"><div class="k">requests</div><div class="v" id="requests">0</div></div>
  <div class="card"><div class="k">served</div><div class="v ok" id="served">0</div></div>
  <div class="card"><div class="k">bytes served</div><div class="v" id="bytes">0</div></div>
  <div class="card"><div class="k">denied (429)</div><div class="v warn" id="denied">0</div></div>
  <div class="card"><div class="k">404 / 416</div><div class="v" id="errs">0 / 0</div></div>
</div>
<h2 style="font-size:14px;margin-bottom:8px">active streams</h2>
<table><thead><tr><th>file</th><th>client</th><th class="num">bytes</th><th class="num">elapsed</th><th class="num">rate</th></tr></thead>
<tbody id="rows"></tbody></table>
<div class="empty" id="empty">no active streams</div>
<button id="refresh">refresh</button>
<script>
async function tick() {{
  try {{
    const d = await (await fetch('/healthz')).json();
    document.getElementById('active').textContent = d.active_streams;
    document.getElementById('requests').textContent = d.total_requests;
    document.getElementById('served').textContent = d.total_served;
    document.getElementById('bytes').textContent = (d.total_bytes/1048576).toFixed(2)+' MiB';
    document.getElementById('denied').textContent = d.total_denied;
    document.getElementById('errs').textContent = d.total_404+' / '+d.total_416;
    const rows = document.getElementById('rows');
    rows.innerHTML = '';
    document.getElementById('empty').style.display = d.streams.length ? 'none' : '';
    for (const s of d.streams) {{
      const tr = document.createElement('tr');
      tr.innerHTML = '<td>'+s.file+'</td><td>'+s.ip+'</td><td class="num">'+(s.bytes/1048576).toFixed(2)+' MiB</td><td class="num">'+s.elapsed+'s</td><td class="num">'+s.rate_kbps+' KB/s</td>';
      rows.appendChild(tr);
    }}
  }} catch (e) {{ /* server restarting */ }}
}}
tick(); setInterval(tick, 2000);
document.getElementById('refresh').onclick = tick;
</script>
</body></html>"""
    return web.Response(text=html, content_type="text/html")


@routes.get(r"/{token:[A-Za-z0-9_-]+}/{name}")
async def download(request: web.Request) -> web.Response:
    app = request.app
    source: FileSource = app["source"]
    links: LinkRegistry = app["links"]
    telemetry: Telemetry = app["telemetry"]
    limiter: StreamLimiter = app["limiter"]

    token = request.match_info["token"]
    file_name = request.match_info["name"]
    ip = get_requester_ip(request, app["trust_headers"])

    record = await links.claim(token, one_time=app["one_time"])
    if record is None:
        telemetry.bump("total_404")
        return web.Response(status=404, text="404: Not Found")

    if record.file_name != file_name:
        telemetry.bump("total_404")
        return web.Response(status=404, text="404: Not Found")

    ref = await source.resolve(record.peer_id, record.msg_id)
    if ref is None:
        telemetry.bump("total_404")
        return web.Response(status=404, text="404: Not Found")

    size = ref.size
    range_parsed = _parse_range(request.headers.get("Range"), size)
    if request.headers.get("Range") and range_parsed is None:
        telemetry.bump("total_416")
        return web.Response(
            status=416,
            text="416: Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )

    start, end = range_parsed if range_parsed else (0, size - 1)
    length = end - start + 1

    if request.method == "HEAD":
        return web.Response(
            status=206 if length != size else 200,
            headers={
                "Content-Type": ref.mime_type,
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'attachment; filename="{file_name}"',
            },
        )

    if not await limiter.acquire(ip):
        telemetry.bump("total_429")
        return web.Response(status=429, text="429: Too Many Requests")

    telemetry.bump("total_requests")
    await telemetry.start_stream(token, file_name, ip)

    async def stream_response() -> web.StreamResponse:
        response = web.StreamResponse(
            status=206 if length != size else 200,
            headers={
                "Content-Type": ref.mime_type,
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'attachment; filename="{file_name}"',
            },
        )
        await response.prepare(request)
        sent = 0
        try:
            async for chunk in source.download(ref, offset=start, limit=length):
                await response.write(chunk)
                await telemetry.add_bytes(token, len(chunk))
                sent += len(chunk)
        finally:
            await limiter.release(ip)
            await telemetry.finish_stream(token)
        return response

    return await stream_response()
