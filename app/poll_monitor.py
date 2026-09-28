from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .bot import ElectionBot
from .config import Settings
from .g1_polls import G1PollClient
from .storage import Storage

log = logging.getLogger(__name__)


async def g1_poll_loop(
    settings: Settings,
    client: G1PollClient,
    bot: ElectionBot,
    storage: Storage,
    stop_event: asyncio.Event,
) -> None:
    if not settings.g1_daily_enabled and settings.g1_monitor_minutes <= 0:
        return

    specs = [
        ("presidente", "br", 1, "datafolha"),
        ("presidente", "br", 1, "quaest"),
    ]
    fingerprints: dict[str, str] = {}

    for office, scope, round_, institute in specs:
        key = f"{office}:{scope}:{round_}:{institute}"
        try:
            poll = await client.fetch(office, scope, round_, institute, force=True)
            state_key = f"g1:fingerprint:{key}"
            previous = await storage.get_state(state_key)
            if previous and previous != poll.fingerprint:
                await bot.publish_g1_poll(poll, headline="NOVA PESQUISA PUBLICADA NO G1")
            fingerprints[key] = poll.fingerprint
            await storage.set_state(state_key, poll.fingerprint)
            log.info(
                "Monitor G1 iniciado: %s, rodada %s, fingerprint %s",
                institute,
                poll.latest_date,
                poll.fingerprint,
            )
        except Exception:
            log.exception("Falha inicializando monitor G1: %s", key)

    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    daily_target = now.replace(
        hour=settings.g1_daily_hour,
        minute=settings.g1_daily_minute,
        second=0,
        microsecond=0,
    )
    if daily_target <= now:
        daily_target += timedelta(days=1)

    monitor_interval = max(5, settings.g1_monitor_minutes) * 60
    next_monitor = now + timedelta(seconds=monitor_interval)

    while not stop_event.is_set():
        now = datetime.now(tz)

        if settings.g1_monitor_minutes > 0 and now >= next_monitor:
            for office, scope, round_, institute in specs:
                key = f"{office}:{scope}:{round_}:{institute}"
                try:
                    poll = await client.fetch(office, scope, round_, institute, force=True)
                    previous = fingerprints.get(key)
                    if previous and previous != poll.fingerprint:
                        await bot.publish_g1_poll(
                            poll,
                            headline="NOVA PESQUISA PUBLICADA NO G1",
                        )
                    fingerprints[key] = poll.fingerprint
                    await storage.set_state(f"g1:fingerprint:{key}", poll.fingerprint)
                except Exception:
                    log.exception("Falha verificando nova pesquisa G1: %s", key)
            next_monitor = datetime.now(tz) + timedelta(seconds=monitor_interval)

        if settings.g1_daily_enabled and now >= daily_target:
            try:
                institute = settings.g1_daily_institute.lower()
                poll = await client.fetch("presidente", "br", 1, institute, force=True)
                sent = await bot.publish_g1_poll(
                    poll,
                    headline="BOLETIM DIÁRIO • ÚLTIMA PESQUISA DISPONÍVEL",
                )
                if sent:
                    await storage.set_state("g1:daily:last_date", now.date().isoformat())
                    log.info("Boletim diário G1 publicado para rodada %s", poll.latest_date)
                    daily_target += timedelta(days=1)
                else:
                    daily_target = datetime.now(tz) + timedelta(minutes=30)
            except Exception:
                log.exception("Falha publicando boletim diário G1")
                # Tenta novamente sem gerar uma sequência de mensagens duplicadas.
                daily_target = datetime.now(tz) + timedelta(minutes=30)

        delay = 60.0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
