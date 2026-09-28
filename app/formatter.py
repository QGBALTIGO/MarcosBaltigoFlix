from __future__ import annotations

import html

from .config import Settings
from .models import ElectionResult

UF_NAMES = {
    "br": "Brasil", "ac": "Acre", "al": "Alagoas", "ap": "Amapá", "am": "Amazonas",
    "ba": "Bahia", "ce": "Ceará", "df": "Distrito Federal", "es": "Espírito Santo",
    "go": "Goiás", "ma": "Maranhão", "mt": "Mato Grosso", "ms": "Mato Grosso do Sul",
    "mg": "Minas Gerais", "pa": "Pará", "pb": "Paraíba", "pr": "Paraná", "pe": "Pernambuco",
    "pi": "Piauí", "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte", "rs": "Rio Grande do Sul",
    "ro": "Rondônia", "rr": "Roraima", "sc": "Santa Catarina", "sp": "São Paulo",
    "se": "Sergipe", "to": "Tocantins",
}


def n(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def p(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + "%"


def _progress_bar(value: float, size: int = 12) -> str:
    value = max(0.0, min(100.0, value))
    filled = round((value / 100) * size)
    return "█" * filled + "░" * (size - filled)


def _official_definition(result: ElectionResult) -> str:
    if result.mathematically_defined == "e":
        return "\n<b>Informação do TSE:</b> eleição matematicamente definida com eleito."
    if result.mathematically_defined == "s":
        return "\n<b>Informação do TSE:</b> eleição matematicamente definida para 2º turno."
    if result.final_totalization:
        return "\n<b>Totalização:</b> finalizada."
    return ""


def format_result(result: ElectionResult, settings: Settings, max_candidates: int = 20) -> str:
    place = UF_NAMES.get(result.scope, result.scope.upper())
    sim = "\n⚠️ <b>DADOS DE SIMULAÇÃO DO TSE — NÃO SÃO VOTOS REAIS</b>" if settings.is_simulation else ""
    lines = [
        f"<b>ELEIÇÕES 2026 • PRESIDENTE</b>",
        f"<b>{html.escape(place)}</b> • {result.round}º turno{sim}",
        "",
        f"<b>Seções totalizadas:</b> {p(result.sections_counted_pct)}",
        f"<code>{_progress_bar(result.sections_counted_pct)}</code>",
        f"{n(result.sections_counted)} de {n(result.sections_total)} seções",
        "",
    ]

    if not result.candidates:
        lines.append("A votação dos candidatos ainda não está disponível neste arquivo.")
    else:
        for cand in result.candidates[:max_candidates]:
            party = f" • {html.escape(cand.party)}" if cand.party else ""
            number = f"{cand.number} • " if cand.number else ""
            status = f" — <b>{html.escape(cand.official_status)}</b>" if cand.official_status else ""
            destination = ""
            if cand.vote_destination and cand.vote_destination.lower() not in {"válido", "valido"}:
                destination = f"\n   Destinação: {html.escape(cand.vote_destination)}"
            lines.extend([
                f"<b>{number}{html.escape(cand.ballot_name)}</b>{party}{status}",
                f"{n(cand.votes)} votos • <b>{p(cand.percentage)}</b>{destination}",
                "",
            ])

    lines.extend([
        f"<b>Votos válidos:</b> {n(result.valid_votes)}",
        f"<b>Brancos:</b> {n(result.blank_votes)}",
        f"<b>Nulos:</b> {n(result.null_votes)}",
        f"<b>Comparecimento:</b> {n(result.turnout)} ({p(result.turnout_pct)})",
        f"<b>Abstenção:</b> {n(result.abstention)} ({p(result.abstention_pct)})",
    ])
    definition = _official_definition(result)
    if definition:
        lines.append(definition)
    if result.no_elected_assignment:
        reasons = "; ".join(result.no_elected_reasons) or "motivo informado no arquivo oficial"
        lines.append(f"\n<b>TSE:</b> sem atribuição de eleito — {html.escape(reasons)}")

    timestamp = " ".join(x for x in [result.totalization_date, result.totalization_time] if x)
    lines.extend([
        "",
        f"<b>Atualização do TSE:</b> {html.escape(timestamp or 'aguardando')} ",
        f"<b>Fonte:</b> {html.escape(settings.source_label)}",
    ])
    return "\n".join(lines).strip()
