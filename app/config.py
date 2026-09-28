from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        try:
            out.add(int(part.strip()))
        except ValueError:
            pass
    return out


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    election_mode: str
    tse_base_url: str
    tse_environment: str
    tse_election_code: int
    tse_cycle: str
    tse_president_cargo: str
    poll_seconds: int
    request_timeout: float
    database_path: str
    admin_ids: set[int]
    port: int
    webapp_url: str
    channel_id: str

    @property
    def is_simulation(self) -> bool:
        return self.election_mode == "simulation"

    @property
    def source_label(self) -> str:
        return "Simulado oficial do TSE" if self.is_simulation else "Tribunal Superior Eleitoral (TSE)"

    @property
    def public_results_url(self) -> str:
        if self.is_simulation:
            return "https://resultados-sim.tse.jus.br/simulado/simulado2026/app/index.html"
        return "https://resultados.tse.jus.br/"


def get_settings() -> Settings:
    mode = os.getenv("ELECTION_MODE", "simulation").strip().lower()
    if mode not in {"simulation", "official"}:
        raise RuntimeError("ELECTION_MODE deve ser 'simulation' ou 'official'.")

    if mode == "simulation":
        defaults = {
            "base": "https://resultados-sim.tse.jus.br/simulado",
            "environment": "simulado2026",
            "election": 21270,
        }
    else:
        defaults = {
            "base": "https://resultados.tse.jus.br",
            "environment": "oficial",
            "election": 6257,
        }

    db_path = os.getenv("DATABASE_PATH", "data/election_bot.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        election_mode=mode,
        tse_base_url=os.getenv("TSE_BASE_URL", defaults["base"]).rstrip("/"),
        tse_environment=os.getenv("TSE_ENVIRONMENT", defaults["environment"]).strip("/"),
        tse_election_code=_int("TSE_ELECTION_CODE", defaults["election"]),
        tse_cycle=os.getenv("TSE_CYCLE", "ele2026").strip("/"),
        tse_president_cargo=os.getenv("TSE_PRESIDENT_CARGO", "0001").zfill(4),
        poll_seconds=max(10, _int("POLL_SECONDS", 20)),
        request_timeout=max(3.0, _float("REQUEST_TIMEOUT", 12.0)),
        database_path=db_path,
        admin_ids=_admin_ids(),
        port=_int("PORT", 8000),
        webapp_url=os.getenv("WEBAPP_URL", "").strip(),
        channel_id=os.getenv("CHANNEL_ID", "").strip(),
    )
