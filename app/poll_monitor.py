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

    for office in ("governador", "senador"):
        try:
            sample = await client.fetch(office, "ms", 1, None, force=True)
            log.info(
                "Smoke G1 estadual OK: %s MS, %s, rodada %s, %d opções",
                office,
                sample.institute,
                sample.latest_date,
                len(sample.choices),
            )
        except Exception:
            log.exception("Smoke G1 estadual falhou: %s MS", office)

    tz = ZoneInfo(settings.timezone)
    daily_institute_key = settings.g1_daily_institute.strip().lower()
    daily_key = f"g1:daily:presidente:{daily_institute_key}"
    layout_key = "g1:daily:layout_version"
    layout_version = "rich-table-v1"
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
                daily_state = await storage.get_state(daily_key)
                current_layout = await storage.get_state(layout_key)
                needs_layout_refresh = current_layout != layout_version
                if daily_state != today or needs_layout_refresh:
                    poll = await client.fetch(
                        "presidente", "br", 1, settings.g1_daily_institute, force=True
                    )
                    headline = (
                        "PESQUISA ELEITORAL • PRESIDENTE"
                        if needs_layout_refresh and daily_state == today
                        else "BOLETIM DIÁRIO • PESQUISA ELEITORAL"
                    )
                    if await bot.publish_g1_poll(poll, headline=headline):
                        await storage.set_state(daily_key, today)
                        await storage.set_state(layout_key, layout_version)
                        await storage.set_state(
                            f"g1:fingerprint:presidente:br:{daily_institute_key}",
                            poll.fingerprint,
                        )
                        log.info(
                            "Boletim diário G1 publicado no formato %s: %s, rodada %s",
                            layout_version,
                            poll.institute,
                            poll.latest_date,
                        )
                elif last_monitor == 0.0:
                    log.info(
                        "Boletim diário G1 já publicado em %s com layout %s",
                        today,
                        current_layout,
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
                            log.info(
                                "Nova pesquisa G1 publicada automaticamente: %s/%s, rodada %s",
                                institute,
                                office,
                                poll.latest_date,
                            )
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
