from __future__ import annotations

import asyncio
import logging
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bot import ElectionBot
from .config import get_settings
from .monitor import monitor_loop
from .storage import Storage
from .tse import TSEClient, VALID_UFS
from .g1_polls import G1PollClient, g1_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Evita que URLs da Bot API (que contêm o token) apareçam nos logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

settings = get_settings()
storage = Storage(settings.database_path)
tse = TSEClient(settings)
election_bot = ElectionBot(settings, tse, storage)
g1_polls = G1PollClient()
stop_event = asyncio.Event()
monitor_task: asyncio.Task | None = None

async def _g1_startup_probe() -> None:
    try:
        data = await g1_polls.debug(g1_url("presidente", "br", 1, "datafolha"))
        lines = data.get("lines", [])
        start = next((i for i, x in enumerate(lines) if x.lower() == "total"), 0)
        compact = {
            "status": data.get("status"),
            "title": data.get("title"),
            "total_block": lines[start:start + 80],
            "percent_contexts": data.get("percent_contexts", [])[:30],
            "interesting_responses": data.get("interesting_responses", [])[:60],
            "scripts": data.get("scripts", [])[-20:],
        }
        logging.getLogger(__name__).warning("G1_PROBE %s", json.dumps(compact, ensure_ascii=False))
    except Exception:
        logging.getLogger(__name__).exception("G1_PROBE_FAILED")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global monitor_task
    await storage.init()
    tg_app = await election_bot.build()
    if tg_app:
        await tg_app.initialize()
        await tg_app.start()
        if tg_app.updater:
            await tg_app.updater.start_polling(drop_pending_updates=False)
    monitor_task = asyncio.create_task(monitor_loop(settings, tse, storage, election_bot, stop_event))
    asyncio.create_task(_g1_startup_probe())
    yield
    stop_event.set()
    if monitor_task:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
    if tg_app:
        if tg_app.updater:
            await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
    await tse.close()
    await g1_polls.close()


app = FastAPI(title="Eleições 2026 Bot", version="1.0.0", lifespan=lifespan)
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
        raise HTTPException(status_code=502, detail=f"Falha consultando TSE: {type(exc).__name__}") from exc


@app.get("/api/g1/poll")
async def api_g1_poll(
    office: str = Query(default="presidente"),
    scope: str = Query(default="br"),
    round_: int = Query(default=1, alias="round", ge=1, le=2),
    institute: str | None = Query(default=None),
    force: bool = Query(default=False),
):
    try:
        result = await g1_polls.fetch(office, scope, round_, institute, force=force)
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Falha lendo pesquisas do G1")
        raise HTTPException(status_code=502, detail=f"Falha consultando G1: {type(exc).__name__}") from exc


@app.get("/api/g1/debug")
async def api_g1_debug():
    # Diagnóstico temporário do carregamento dinâmico do especial do G1.
    url = g1_url("presidente", "br", 1, "datafolha")
    return await g1_polls.debug(url)
