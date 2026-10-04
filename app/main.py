from __future__ import annotations

import asyncio
import logging

import httpx
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bot import ElectionBot
from .candidates import CandidateDirectory
from .config import get_settings
from .g1_polls import G1PollClient
from .exterior import ExteriorBulletinClient, exterior_bulletin_loop
from .monitor import ElectionMonitorState, monitor_loop
from .poll_monitor import g1_poll_loop
from .result_service import ResultService, is_pre_election
from .storage import Storage
from .tse import TSEClient, VALID_UFS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

settings = get_settings()
storage = Storage(settings.database_path)
tse = TSEClient(settings)
g1_polls = G1PollClient()
candidates = CandidateDirectory(g1_polls)
results = ResultService(settings, tse, candidates)
election_bot = ElectionBot(settings, results, storage, g1_polls)
stop_event = asyncio.Event()
monitor_state = ElectionMonitorState()
exterior_client = ExteriorBulletinClient(settings)
monitor_task: asyncio.Task | None = None
g1_task: asyncio.Task | None = None
exterior_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global monitor_task, g1_task, exterior_task
    await storage.init()
    try:
        await asyncio.wait_for(
            candidates.list("presidente", "br", include_poll=False),
            timeout=10,
        )
        logging.getLogger(__name__).info("Cache inicial de candidaturas presidenciais aquecido")
    except Exception:
        logging.getLogger(__name__).exception("Falha aquecendo candidaturas presidenciais")
    tg_app = await election_bot.build()
    if tg_app:
        await tg_app.initialize()
        try:
            me = await tg_app.bot.get_me()
            logging.getLogger(__name__).info(
                "Telegram bot @%s inline_queries=%s",
                me.username or "",
                bool(getattr(me, "supports_inline_queries", False)),
            )
        except Exception:
            logging.getLogger(__name__).exception("Falha verificando configuração inline do bot")
        await tg_app.start()
        if tg_app.updater:
            await tg_app.updater.start_polling(drop_pending_updates=False)

    # Preflight the Telegram channel immediately. A missing admin permission must
    # be visible before the presidential totalization starts.
    if settings.channel_id:
        ready = await election_bot.check_channel_ready(force=True)
        if not ready:
            logging.getLogger(__name__).error(
                "CANAL NÃO PRONTO PARA O DIA DA ELEIÇÃO: %s",
                election_bot.channel_error,
            )

    monitor_task = asyncio.create_task(
        monitor_loop(
            settings,
            results,
            storage,
            election_bot,
            stop_event,
            monitor_state,
        )
    )
    g1_task = asyncio.create_task(
        g1_poll_loop(settings, g1_polls, storage, election_bot, stop_event)
    )
    exterior_task = asyncio.create_task(
        exterior_bulletin_loop(
            settings,
            storage,
            election_bot,
            exterior_client,
            stop_event,
        )
    )

    yield

    stop_event.set()
    for task in (monitor_task, g1_task, exterior_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if tg_app:
        if tg_app.updater:
            await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
    await tse.close()
    await candidates.close()
    await g1_polls.close()
    await exterior_client.close()


app = FastAPI(title="Resultado Eleições 2026", version="2.0.0", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(
        static_dir / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "mode": settings.election_mode,
        "telegram_configured": bool(settings.telegram_bot_token),
        "poll_seconds": settings.poll_seconds,
        "president_poll_seconds": settings.president_poll_seconds,
        "monitor": monitor_state.to_dict(),
        "channel": election_bot.channel_health(),
        "exterior_bu_enabled": settings.exterior_bu_enabled,
        "exterior_bu_poll_seconds": settings.exterior_bu_poll_seconds,
        "g1_daily_enabled": settings.g1_daily_enabled,
        "g1_daily_time": f"{settings.g1_daily_hour:02d}:{settings.g1_daily_minute:02d}",
        "timezone": settings.timezone,
    }


@app.get("/api/result")
async def api_result(
    scope: str = Query(default="br", min_length=2, max_length=2),
    office: str = Query(default="presidente"),
):
    scope = scope.lower()
    office = office.lower()
    if scope != "br" and scope not in VALID_UFS:
        raise HTTPException(status_code=400, detail="UF inválida")
    if office not in {"presidente", "governador", "senador", "federal", "estadual"}:
        raise HTTPException(status_code=400, detail="Cargo inválido")
    if office != "presidente" and scope == "br":
        raise HTTPException(status_code=400, detail="Este cargo exige uma UF")
    try:
        result, _ = await results.fetch(scope, office=office)
        data = result.to_dict()
        data["source_label"] = settings.source_label
        data["simulation"] = False
        data["pre_election"] = is_pre_election(result)
        data["source_url"] = settings.public_results_url
        return data
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Falha consultando TSE: {type(exc).__name__}",
        ) from exc


@app.get("/api/g1/poll")
async def api_g1_poll(
    office: str = Query(default="presidente"),
    scope: str = Query(default="br"),
    round_: int = Query(default=1, alias="round", ge=1, le=2),
    institute: str | None = Query(default=None),
    question: str | None = Query(default=None),
    force: bool = Query(default=False),
):
    try:
        result = await g1_polls.fetch(
            office,
            scope,
            round_,
            institute or None,
            question_code=question or None,
            force=force,
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Falha lendo pesquisas do G1")
        raise HTTPException(
            status_code=502,
            detail=f"Falha consultando G1: {type(exc).__name__}",
        ) from exc


@app.get("/api/g1/status")
async def api_g1_status():
    return {
        "daily_enabled": settings.g1_daily_enabled,
        "daily_time": f"{settings.g1_daily_hour:02d}:{settings.g1_daily_minute:02d}",
        "daily_institute": settings.g1_daily_institute,
        "monitor_minutes": settings.g1_monitor_minutes,
        "timezone": settings.timezone,
        "last_daily_date": await storage.get_state("g1:daily:presidente:datafolha"),
        "fingerprints": {
            "datafolha": await storage.get_state("g1:fingerprint:presidente:br:datafolha"),
            "quaest": await storage.get_state("g1:fingerprint:presidente:br:quaest"),
        },
    }


@app.get("/api/g1/catalog")
async def api_g1_catalog():
    try:
        return await g1_polls.catalog()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Falha consultando catálogo do G1: {type(exc).__name__}",
        ) from exc



@app.get("/api/location/reverse")
async def api_reverse_location(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    try:
        async with httpx.AsyncClient(
            timeout=8,
            follow_redirects=True,
            headers={"User-Agent": "ResultadoEleicoesBot/1.0 (Telegram Mini App)"},
        ) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "format": "jsonv2",
                    "lat": lat,
                    "lon": lon,
                    "zoom": 5,
                    "addressdetails": 1,
                },
                headers={"Accept-Language": "pt-BR"},
            )
            response.raise_for_status()
            payload = response.json()
        address = payload.get("address") or {}
        raw_code = (
            address.get("ISO3166-2-lvl4")
            or address.get("ISO3166-2-lvl3")
            or address.get("ISO3166-2-lvl6")
            or ""
        )
        uf = str(raw_code).split("-")[-1].lower().strip()
        if uf not in VALID_UFS:
            raise ValueError("UF não identificada.")
        return JSONResponse(
            {"uf": uf},
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Falha identificando a UF: {type(exc).__name__}",
        ) from exc


@app.get("/api/candidates")
async def api_candidates(
    office: str = Query(default="presidente"),
    scope: str = Query(default="br", min_length=2, max_length=2),
    include_poll: bool = Query(default=False),
):
    try:
        data = await candidates.list(office, scope, include_poll=include_poll)
        return JSONResponse(
            data,
            headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=300"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Falha lendo candidaturas 2026")
        raise HTTPException(
            status_code=502,
            detail=f"Falha consultando candidaturas: {type(exc).__name__}",
        ) from exc


@app.get("/api/candidates/{office}/{scope}/{candidate_id}")
async def api_candidate_detail(
    office: str,
    scope: str,
    candidate_id: str,
):
    try:
        data = await candidates.detail(office, scope, candidate_id)
        return JSONResponse(
            data,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Falha lendo detalhe de candidatura")
        raise HTTPException(
            status_code=502,
            detail=f"Falha consultando candidatura: {type(exc).__name__}",
        ) from exc
