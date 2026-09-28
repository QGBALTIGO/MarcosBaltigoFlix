from __future__ import annotations

import asyncio
import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

settings = get_settings()
storage = Storage(settings.database_path)
tse = TSEClient(settings)
election_bot = ElectionBot(settings, tse, storage)
stop_event = asyncio.Event()
monitor_task: asyncio.Task | None = None


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
