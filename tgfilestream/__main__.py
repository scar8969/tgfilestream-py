"""Package entry points: ``python -m tgfilestream`` and the ``tgfilestream`` console script."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiohttp import web

from . import config
from .bot import register_handlers
from .links import LinkRegistry
from .paralleltransfer import ParallelTransferrer
from .source import DemoFileSource, TelegramFileSource
from .telemetry import Telemetry
from .web_routes import build_app


def _setup_logging() -> None:
    level = logging.DEBUG if config.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _build_demo_source() -> DemoFileSource:
    """Seed the demo file store with a few deterministic files."""
    source = DemoFileSource()
    source.register(
        1001, 1,
        b"demo file one: " + bytes(range(256)) * 40,
        "demo_one.bin", "application/octet-stream",
    )
    source.register(
        1001, 2,
        b"demo file two: " + bytes(range(256)) * 80,
        "demo_two.bin", "application/octet-stream",
    )
    source.register(
        1001, 3,
        b"hello from tgfilestream demo\n" * 200,
        "demo_hello.txt", "text/plain",
    )
    return source


def _seed_demo_links(links: LinkRegistry, source: DemoFileSource) -> None:
    """Pre-register links with FIXED tokens so the demo is reproducible
    (the README's curl commands work verbatim)."""
    for i, ((peer, msg), (data, name, mime)) in enumerate(source._files.items(), start=1):
        links.create(peer, msg, name, len(data), mime, ttl=config.link_ttl or None,
                     token=f"demo{i}")


async def _run() -> None:
    _setup_logging()
    log = logging.getLogger("tgfilestream")

    links = LinkRegistry()
    telemetry = Telemetry()

    if config.demo_mode:
        source = _build_demo_source()
        _seed_demo_links(links, source)
        log.info("DEMO_MODE: serving seeded in-memory files, no Telegram connection needed")
    else:
        if not config.api_id or not config.api_hash:
            print("Set TG_API_ID and TG_API_HASH (https://my.telegram.org/apps), or run with DEMO_MODE=1")
            sys.exit(1)
        from telethon import TelegramClient

        client = TelegramClient(config.session_name, config.api_id, config.api_hash)
        transferrer = ParallelTransferrer(client)
        source = TelegramFileSource(client, transferrer)
        register_handlers(client, links, str(config.public_url))
        await client.start()
        log.info("Telegram client started as %s", (await client.get_me()).username)
        await client.run_until_disconnected()

    app = build_app(
        source, links, telemetry,
        request_limit=config.request_limit,
        one_time=config.one_time_links,
        ttl=config.link_ttl or None,
        trust_headers=config.trust_forward_headers,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port)
    await site.start()
    log.info("HTTP server on http://%s:%s", config.host, config.port)

    if config.demo_mode:
        # Keep the event loop alive in demo mode.
        while True:
            await asyncio.sleep(3600)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
