from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bot import ElectionBot
from .candidates import CandidateDirectory
from .config import get_settings
from .g1_polls import G1PollClient
from .monitor import monitor_loop
from .poll_monitor import g1_poll_loop
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
election_bot = ElectionBot(settings, tse, storage, g1_polls)
stop_event = asyncio.Event()
monitor_task: asyncio.Task | None = None
g1_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global monitor_task, g1_task
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
        await tg_app.start()
        if tg_app.updater:
            await tg_app.updater.start_polling(drop_pending_updates=False)

    monitor_task = asyncio.create_task(
        monitor_loop(settings, tse, storage, election_bot, stop_event)
    )
    g1_task = asyncio.create_task(
        g1_poll_loop(settings, g1_polls, storage, election_bot, stop_event)
    )

    yield

    stop_event.set()
    for task in (monitor_task, g1_task):
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


app = FastAPI(title="Resultado Eleições 2026", version="2.0.0", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "mode": settings.election_mode,
        "telegram_configured": bool(settings.telegram_bot_token),
        "poll_seconds": settings.poll_seconds,
        "g1_daily_enabled": settings.g1_daily_enabled,
        "g1_daily_time": f"{settings.g1_daily_hour:02d}:{settings.g1_daily_minute:02d}",
        "timezone": settings.timezone,
    }


@app.get("/api/result")
async def api_result(scope: str = Query(default="br", min_length=2, max_length=2)):
    scope = scope.lower()
    if scope != "br" and scope not in VALID_UFS:
        raise HTTPException(status_code=400, detail="UF inválida")
    try:
        result, _ = await tse.fetch(scope)
        data = result.to_dict()
        data["source_label"] = settings.source_label
        data["simulation"] = settings.is_simulation
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
            headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=900"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Falha lendo detalhe de candidatura")
        raise HTTPException(
            status_code=502,
            detail=f"Falha consultando candidatura: {type(exc).__name__}",
        ) from exc
