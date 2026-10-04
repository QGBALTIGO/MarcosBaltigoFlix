from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from time import perf_counter
from zoneinfo import ZoneInfo

from .bot import ElectionBot
from .config import Settings
from .result_service import ResultService, is_pre_election
from .storage import Storage

log = logging.getLogger(__name__)

BRASILIA = ZoneInfo("America/Sao_Paulo")
ELECTION_DATES_2026 = {date(2026, 10, 4), date(2026, 10, 25)}


@dataclass(slots=True)
class ElectionMonitorState:
    running: bool = False
    last_president_poll_at: str = ""
    last_president_change_at: str = ""
    last_president_latency_ms: float | None = None
    last_generation_id: str = ""
    last_sections_pct: float = 0.0
    last_signature: tuple | None = None
    consecutive_failures: int = 0
    last_error: str = ""

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "last_president_poll_at": self.last_president_poll_at or None,
            "last_president_change_at": self.last_president_change_at or None,
            "last_president_latency_ms": self.last_president_latency_ms,
            "last_generation_id": self.last_generation_id or None,
            "last_sections_pct": self.last_sections_pct,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error or None,
        }


def _iso_now() -> str:
    return datetime.now(BRASILIA).isoformat()


def _result_signature(result) -> tuple:
    return (
        result.generation_id,
        result.totalization_date,
        result.totalization_time,
        int(result.sections_counted or 0),
        round(float(result.sections_counted_pct or 0), 6),
        int(result.total_votes or 0),
        int(result.valid_votes or 0),
        int(result.blank_votes or 0),
        int(result.null_votes or 0),
        tuple(
            (
                candidate.candidate_id,
                int(candidate.votes or 0),
                round(float(candidate.percentage or 0), 6),
            )
            for candidate in result.candidates
        ),
    )


def president_poll_interval(settings: Settings, now: datetime | None = None) -> float:
    now = now or datetime.now(BRASILIA)
    # TSE only releases presidential totalization from 17:00 Brasília.
    # Avoid hammering the not-yet-published EA20 URL with repeated 404s before then.
    if now.date() in ELECTION_DATES_2026:
        release = datetime.combine(now.date(), dt_time(17, 0), tzinfo=BRASILIA)
        if now < release:
            return 60.0
        return settings.president_poll_seconds
    return max(10.0, settings.president_poll_seconds)


async def _poll_other_live_scopes(
    results: ResultService,
    storage: Storage,
    bot: ElectionBot,
) -> None:
    live = await storage.list_live()
    scopes = sorted({item.scope for item in live if item.scope != "br"})
    if not scopes:
        return

    async def one(scope: str) -> None:
        try:
            result, changed = await results.fetch(scope)
            if changed:
                await bot.refresh_live_messages(scope, result)
        except Exception:
            log.exception("Falha atualizando escopo secundário %s", scope)

    await asyncio.gather(*(one(scope) for scope in scopes))


async def monitor_loop(
    settings: Settings,
    results: ResultService,
    storage: Storage,
    bot: ElectionBot,
    stop_event: asyncio.Event,
    state: ElectionMonitorState | None = None,
) -> None:
    state = state or ElectionMonitorState()
    state.running = True
    next_secondary = 0.0
    next_channel_reconcile = 0.0

    try:
        while not stop_event.is_set():
            cycle_started = perf_counter()
            try:
                result, _client_changed = await results.fetch("br", office="presidente")
                latency_ms = round((perf_counter() - cycle_started) * 1000, 2)
                state.last_president_poll_at = _iso_now()
                state.last_president_latency_ms = latency_ms
                state.last_generation_id = result.generation_id or ""
                state.last_sections_pct = float(result.sections_counted_pct or 0)
                state.consecutive_failures = 0
                state.last_error = ""

                # IMPORTANT: do not trust the shared TSE client's "changed" flag here.
                # The public WebApp also calls /api/result and can populate the same
                # cache between monitor ticks. The monitor owns its own signature so
                # user traffic can never "consume" a presidential update before the
                # channel sees it.
                signature = _result_signature(result)
                changed = state.last_signature is None or signature != state.last_signature
                state.last_signature = signature

                if changed:
                    state.last_president_change_at = state.last_president_poll_at
                    log.info(
                        "Mudança presidencial detectada: geração=%s seções=%.4f%% votos=%s",
                        result.generation_id or "-",
                        float(result.sections_counted_pct or 0),
                        int(result.total_votes or 0),
                    )
                    # Update all subscribed chats immediately.
                    await bot.refresh_live_messages("br", result)

                # Reconcile the official channel every 30s even without a new vote.
                # This detects a deleted/stale tracked message and recreates it.
                now_monotonic = asyncio.get_running_loop().time()
                reconcile = changed or now_monotonic >= next_channel_reconcile
                channel_ok = await bot.ensure_president_channel_message(
                    result,
                    changed=reconcile,
                )
                if reconcile:
                    next_channel_reconcile = now_monotonic + 30.0
                    log.info(
                        "Sincronização presidencial do canal: ok=%s changed=%s",
                        channel_ok,
                        changed,
                    )
            except Exception as exc:
                state.consecutive_failures += 1
                state.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("Falha no monitor prioritário da Presidência")

            now_monotonic = asyncio.get_running_loop().time()
            if now_monotonic >= next_secondary:
                try:
                    await _poll_other_live_scopes(results, storage, bot)
                except Exception:
                    log.exception("Falha no monitor de escopos secundários")
                next_secondary = now_monotonic + settings.poll_seconds

            interval = president_poll_interval(settings)
            # Right after 17:00 the CDN may take a short moment to expose EA20.
            # While the zero baseline is still active, cap retries at 5 seconds;
            # once the official file is live, switch to the configured 2-second loop.
            try:
                if interval <= settings.president_poll_seconds and is_pre_election(result):
                    interval = max(5.0, settings.president_poll_seconds)
            except UnboundLocalError:
                pass
            elapsed = perf_counter() - cycle_started
            delay = max(0.05, interval - elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
    finally:
        state.running = False
