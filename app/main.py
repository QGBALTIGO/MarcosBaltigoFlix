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
    specs = [
        ("presidente-br-datafolha", g1_url("presidente", "br", 1, "datafolha")),
        ("governador-ms", g1_url("governador", "ms", 1, None)),
        ("senador-ms", g1_url("senador", "ms", 1, None)),
        ("governador-sp-datafolha", g1_url("governador", "sp", 1, "datafolha")),
        ("senador-sp-datafolha", g1_url("senador", "sp", 1, "datafolha")),
    ]
    for label, url in specs:
        try:
            data = await g1_polls.debug(url)
            api_urls = [
                x for x in data.get("interesting_responses", [])
                if "/api/pesquisas-eleitorais/" in x
            ]
            payload_summary = []
            for item in data.get("api_payloads", []):
                payload = item.get("payload") or {}
                resultado = payload.get("resultado") or {}
                cenarios = resultado.get("cenarios") or []
                payload_summary.append({
                    "url": item.get("url"),
                    "scenario_count": len(cenarios),
                    "first_question": ((cenarios[0].get("pergunta") or {}).get("codigo") if cenarios else None),
                    "first_scenario_id": (cenarios[0].get("id") if cenarios else None),
                })
            logging.getLogger(__name__).warning(
                "G1_DISCOVERY %s",
                json.dumps({"label": label, "url": url, "api_urls": api_urls, "payloads": payload_summary}, ensure_ascii=False),
            )
        except Exception:
            logging.getLogger(__name__).exception("G1_DISCOVERY_FAILED %s", label)


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
