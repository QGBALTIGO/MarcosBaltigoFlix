from __future__ import annotations

import html
from datetime import date

from .formatter import UF_NAMES
from .g1_polls import G1Poll, G1PollChoice


OFFICE_NAMES = {
    "presidente": "PRESIDENTE",
    "governador": "GOVERNADOR",
    "senador": "SENADOR",
}


def _pct(value: float) -> str:
    return f"{value:.0f}%" if float(value).is_integer() else f"{value:.1f}%".replace(".", ",")


def _date_br(value: str) -> str:
    try:
        return date.fromisoformat(value[:10]).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return value or "—"


def _delta(choice: G1PollChoice, latest_date: str) -> str:
    points = sorted((p for p in choice.history if p.date <= latest_date), key=lambda p: p.date)
    if len(points) < 2:
        return ""
    change = round(points[-1].percentage - points[-2].percentage, 1)
    if abs(change) < 0.05:
        return " • sem variação"
    sign = "+" if change > 0 else ""
    return f" • {sign}{change:.1f} p.p.".replace(".", ",")


def format_g1_poll(poll: G1Poll, headline: str | None = None, max_choices: int = 20) -> str:
    place = UF_NAMES.get(poll.scope, poll.scope.upper())
    title = headline or "PESQUISA ELEITORAL"
    lines = [
        f"<b>{html.escape(title)}</b>",
        f"<b>{OFFICE_NAMES.get(poll.office, poll.office.upper())} • {html.escape(place)}</b>",
        f"{poll.round}º turno • {html.escape(poll.institute)} • {html.escape(poll.question)}",
        "",
        f"<b>Última rodada disponível:</b> {_date_br(poll.latest_date)}",
        "<i>Pesquisa de intenção de voto — não é apuração.</i>",
        "",
    ]

    for choice in poll.choices[:max_choices]:
        identity = " - ".join(
            html.escape(value)
            for value in (choice.number, choice.party)
            if value
        )
        identity_line = f"\n<code>{identity}</code>" if identity else ""
        lines.append(
            f"<b>{html.escape(choice.name)}</b>{identity_line}\n"
            f"<b>{_pct(choice.percentage)}</b>{_delta(choice, poll.latest_date)}"
        )

    lines.extend(["", "<b>Informações da pesquisa</b>"])
    if poll.margin_error_points is not None:
        lines.append(f"Margem de erro: ±{poll.margin_error_points:g} p.p.")
    if poll.sample_size:
        lines.append(f"Amostra: {poll.sample_size:,} entrevistas".replace(",", "."))
    if poll.field_period:
        lines.append(f"Período de campo: {html.escape(poll.field_period)}")
    if poll.registrations:
        lines.append(f"Registro(s) no TSE: {html.escape(', '.join(poll.registrations))}")

    lines.extend([
        "",
        "<i>Percentuais e ordem das opções reproduzem os dados disponibilizados pela fonte.</i>",
        f"<b>Fonte:</b> G1 / {html.escape(poll.institute)}",
    ])
    return "\n".join(lines).strip()


def format_g1_history(poll: G1Poll, points: int = 5, max_choices: int = 8) -> str:
    lines = [
        f"<b>HISTÓRICO • {OFFICE_NAMES.get(poll.office, poll.office.upper())}</b>",
        f"{html.escape(UF_NAMES.get(poll.scope, poll.scope.upper()))} • {html.escape(poll.institute)}",
        "<i>Pesquisa eleitoral; não é apuração nem previsão.</i>",
        "",
    ]
    for choice in poll.choices[:max_choices]:
        recent = sorted(choice.history, key=lambda x: x.date, reverse=True)[:points]
        series = " • ".join(f"{_date_br(x.date)[:5]} {_pct(x.percentage)}" for x in recent)
        party = f" ({html.escape(choice.party)})" if choice.party else ""
        lines.append(f"<b>{html.escape(choice.name)}</b>{party}")
        lines.append(series or "Sem histórico")
        lines.append("")
    lines.append("<i>Fonte: G1.</i>")
    return "\n".join(lines).strip()
