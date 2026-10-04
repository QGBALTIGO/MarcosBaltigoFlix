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


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


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
    tse_state_election_code: int
    tse_cycle: str
    tse_president_cargo: str
    poll_seconds: int
    request_timeout: float
    database_path: str
    admin_ids: set[int]
    port: int
    webapp_url: str
    channel_id: str
    required_channel: str = ""
    bot_username: str = "ResultadoEleicoes_Bot"
    timezone: str = "America/Campo_Grande"
    g1_daily_enabled: bool = True
    g1_daily_hour: int = 9
    g1_daily_minute: int = 0
    g1_monitor_minutes: int = 15
    g1_daily_institute: str = "Datafolha"
    president_poll_seconds: float = 2.0
    exterior_bu_enabled: bool = True
    exterior_bu_poll_seconds: int = 90
    exterior_bu_source_url: str = "https://www.poder360.com.br/poder-eleicoes-2026/eleicoes-2026-exterior-resultados/"

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
    mode = os.getenv("ELECTION_MODE", "official").strip().lower()
    if mode not in {"simulation", "official"}:
        raise RuntimeError("ELECTION_MODE deve ser 'simulation' ou 'official'.")

    if mode == "simulation":
        defaults = {
            "base": "https://resultados-sim.tse.jus.br/simulado",
            "environment": "simulado2026",
            "election": 21270,
            "state_election": 21272,
        }
    else:
        defaults = {
            "base": "https://resultados.tse.jus.br",
            "environment": "oficial",
            "election": 6257,
            "state_election": 6259,
        }

    db_path = os.getenv("DATABASE_PATH", "data/election_bot.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        election_mode=mode,
        tse_base_url=os.getenv("TSE_BASE_URL", defaults["base"]).rstrip("/"),
        tse_environment=os.getenv("TSE_ENVIRONMENT", defaults["environment"]).strip("/"),
        tse_election_code=_int("TSE_ELECTION_CODE", defaults["election"]),
        tse_state_election_code=_int("TSE_STATE_ELECTION_CODE", defaults["state_election"]),
        tse_cycle=os.getenv("TSE_CYCLE", "ele2026").strip("/"),
        tse_president_cargo=os.getenv("TSE_PRESIDENT_CARGO", "0001").zfill(4),
        poll_seconds=max(5, _int("POLL_SECONDS", 10)),
        president_poll_seconds=min(10.0, max(1.0, _float("PRESIDENT_POLL_SECONDS", 2.0))),
        request_timeout=max(2.0, _float("REQUEST_TIMEOUT", 8.0)),
        database_path=db_path,
        admin_ids=_admin_ids(),
        port=_int("PORT", 8000),
        webapp_url=os.getenv("WEBAPP_URL", "").strip(),
        channel_id=os.getenv("CHANNEL_ID", "").strip(),
        required_channel=os.getenv("REQUIRED_CHANNEL", os.getenv("CHANNEL_ID", "")).strip(),
        bot_username=os.getenv("BOT_USERNAME", "ResultadoEleicoes_Bot").strip().lstrip("@") or "ResultadoEleicoes_Bot",
        timezone=os.getenv("TIMEZONE", "America/Campo_Grande").strip() or "America/Campo_Grande",
        g1_daily_enabled=_bool("G1_DAILY_ENABLED", True),
        g1_daily_hour=min(23, max(0, _int("G1_DAILY_HOUR", 9))),
        g1_daily_minute=min(59, max(0, _int("G1_DAILY_MINUTE", 0))),
        g1_monitor_minutes=max(5, _int("G1_MONITOR_MINUTES", 15)),
        g1_daily_institute=os.getenv("G1_DAILY_INSTITUTE", "Datafolha").strip() or "Datafolha",
        exterior_bu_enabled=_bool("EXTERIOR_BU_ENABLED", True),
        exterior_bu_poll_seconds=max(30, _int("EXTERIOR_BU_POLL_SECONDS", 90)),
        exterior_bu_source_url=os.getenv(
            "EXTERIOR_BU_SOURCE_URL",
            "https://www.poder360.com.br/poder-eleicoes-2026/eleicoes-2026-exterior-resultados/",
        ).strip(),
    )
