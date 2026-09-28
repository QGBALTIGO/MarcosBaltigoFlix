from __future__ import annotations

import asyncio
import logging

from .bot import ElectionBot
from .config import Settings
from .storage import Storage
from .tse import TSEClient

log = logging.getLogger(__name__)


async def monitor_loop(settings: Settings, tse: TSEClient, storage: Storage, bot: ElectionBot, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            live = await storage.list_live()
            scopes = {item.scope for item in live} or {"br"}
            for scope in scopes:
                try:
                    result, changed = await tse.fetch(scope)
                    if changed and live:
                        await bot.refresh_live_messages(scope, result)
                except Exception:
                    log.exception("Falha atualizando escopo %s", scope)
        except Exception:
            log.exception("Falha no monitor")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.poll_seconds)
        except asyncio.TimeoutError:
            pass
