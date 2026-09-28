from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .bot import ElectionBot
from .config import Settings
from .g1_polls import G1PollClient
from .storage import Storage

log = logging.getLogger(__name__)


async def g1_poll_loop(
    settings: Settings,
    client: G1PollClient,
    storage: Storage,
    bot: ElectionBot,
    stop_event: asyncio.Event,
) -> None:
    if not settings.g1_daily_enabled and settings.g1_monitor_minutes <= 0:
        return

    tz = ZoneInfo(settings.timezone)
    daily_key = "g1:daily:presidente:datafolha"
    specs = [
        ("presidente", "br", 1, "datafolha"),
        ("presidente", "br", 1, "quaest"),
    ]
    last_monitor = 0.0

    while not stop_event.is_set():
        now = datetime.now(tz)
        today = now.date().isoformat()
        due = (now.hour, now.minute) >= (settings.g1_daily_hour, settings.g1_daily_minute)

        if settings.g1_daily_enabled and settings.channel_id and due:
            try:
                if await storage.get_state(daily_key) != today:
                    poll = await client.fetch(
                        "presidente", "br", 1, settings.g1_daily_institute, force=True
                    )
                    if await bot.publish_g1_poll(
                        poll,
                        headline="BOLETIM DIÁRIO • ÚLTIMA PESQUISA DISPONÍVEL",
                    ):
                        await storage.set_state(daily_key, today)
                        await storage.set_state(
                            "g1:fingerprint:presidente:br:datafolha",
                            poll.fingerprint,
                        )
            except Exception:
                log.exception("Falha publicando boletim diário de pesquisas")

        interval = max(5, settings.g1_monitor_minutes) * 60
        if time.monotonic() - last_monitor >= interval:
            last_monitor = time.monotonic()
            for office, scope, round_, institute in specs:
                state_key = f"g1:fingerprint:{office}:{scope}:{institute}"
                try:
                    poll = await client.fetch(
                        office, scope, round_, institute, force=True
                    )
                    previous = await storage.get_state(state_key)
                    if previous is None:
                        await storage.set_state(state_key, poll.fingerprint)
                    elif previous != poll.fingerprint:
                        if await bot.publish_g1_poll(
                            poll,
                            headline="NOVA PESQUISA PUBLICADA NO G1",
                        ):
                            await storage.set_state(state_key, poll.fingerprint)
                except Exception:
                    log.exception(
                        "Falha verificando nova pesquisa G1: %s/%s",
                        institute,
                        office,
                    )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=45)
        except asyncio.TimeoutError:
            pass
