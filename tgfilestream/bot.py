"""Telegram bot event handlers."""
from __future__ import annotations

import logging

from telethon import TelegramClient, events

from .config import group_chat_message, start_message
from .links import LinkRegistry
from .util import get_file_name, pack_id

log = logging.getLogger(__name__)


def register_handlers(client: TelegramClient, links: LinkRegistry, public_url: str) -> None:
    """Wire the bot's message handlers onto the client."""

    @client.on(events.NewMessage)
    async def handle_message(evt: events.NewMessage.Event) -> None:
        if not evt.is_private:
            await evt.reply(group_chat_message)
            return
        if not evt.file:
            await evt.reply(start_message)
            return

        peer_id = evt.chat_id
        msg_id = evt.id
        token, _record = links.create(
            peer_id=peer_id,
            msg_id=msg_id,
            file_name=get_file_name(evt),
            file_size=evt.file.size,
            mime_type=evt.file.mime_type or "application/octet-stream",
        )
        url = f"{public_url}/{token}/{get_file_name(evt)}"
        await evt.reply(f"Download: {url}")
        log.info("created link %s for msg %s in chat %s", token, msg_id, peer_id)
