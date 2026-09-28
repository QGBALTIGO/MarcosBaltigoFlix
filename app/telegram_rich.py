from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

import httpx

from .formatter import UF_NAMES
from .g1_polls import G1Poll, G1PollChoice


OFFICE_NAMES = {
    "presidente": "Presidente",
    "governador": "Governador",
    "senador": "Senador",
}


class RichMessageError(RuntimeError):
    pass


def _pct(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}%"
    return f"{value:.1f}".replace(".", ",") + "%"


def _date_br(value: str) -> str:
    try:
        return date.fromisoformat(value[:10]).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return value or "—"


def _delta(choice: G1PollChoice, latest_date: str) -> str:
    points = sorted(
        (point for point in choice.history if point.date <= latest_date),
        key=lambda point: point.date,
    )
    if len(points) < 2:
        return "—"
    change = round(points[-1].percentage - points[-2].percentage, 1)
    if abs(change) < 0.05:
        return "—"
    sign = "+" if change > 0 else "−"
    absolute = f"{abs(change):.1f}".replace(".", ",")
    return f"{sign}{absolute} p.p."


def _candidate_rows(poll: G1Poll) -> tuple[list[G1PollChoice], list[G1PollChoice]]:
    candidates: list[G1PollChoice] = []
    other: list[G1PollChoice] = []
    for choice in poll.choices:
        if choice.party:
            candidates.append(choice)
        else:
            other.append(choice)
    return candidates, other


def build_g1_channel_rich_html(
    poll: G1Poll,
    *,
    headline: str,
    panel_url: str = "",
) -> str:
    """Build a Telegram Bot API 10.3 Rich Message using native table columns."""
    place = UF_NAMES.get(poll.scope, poll.scope.upper())
    office = OFFICE_NAMES.get(poll.office, poll.office.title())
    candidates, other = _candidate_rows(poll)

    rows = [
        "<tr>"
        '<th align="left">Candidato</th>'
        '<th align="right">Pesquisa</th>'
        '<th align="right">Variação</th>'
        "</tr>"
    ]
    for choice in candidates:
        candidate_name = html.escape(choice.name)
        identity_parts = []
        if choice.number:
            identity_parts.append(html.escape(choice.number))
        if choice.party:
            identity_parts.append(html.escape(choice.party))
        identity = " - ".join(identity_parts)

        candidate_cell = f"<b>{candidate_name}</b>"
        if identity:
            candidate_cell += f"<br/>{identity}"

        rows.append(
            "<tr>"
            f'<td align="left">{candidate_cell}</td>'
            f'<td align="right"><b>{_pct(choice.percentage)}</b></td>'
            f'<td align="right">{html.escape(_delta(choice, poll.latest_date))}</td>'
            "</tr>"
        )

    secondary_rows = []
    for choice in other:
        secondary_rows.append(
            "<tr>"
            f'<td align="left">{html.escape(choice.name)}</td>'
            f'<td align="right"><b>{_pct(choice.percentage)}</b></td>'
            "</tr>"
        )

    details: list[str] = []
    if poll.sample_size:
        sample = f"{poll.sample_size:,}".replace(",", ".")
        details.append(f"<b>Amostra:</b> {sample} entrevistas")
    if poll.field_period:
        details.append(f"<b>Campo:</b> {html.escape(poll.field_period)}")
    if poll.margin_error_points is not None:
        details.append(f"<b>Margem de erro:</b> ±{poll.margin_error_points:g} p.p.")

    confidence = ""
    if poll.methodology:
        match = re.search(r"confian(?:ça|ca)[^0-9]{0,40}(\d{2,3})%", poll.methodology, re.I)
        if match:
            confidence = match.group(1) + "%"
    if confidence:
        details.append(f"<b>Nível de confiança:</b> {confidence}")

    if poll.registrations:
        label = "Registro no TSE" if len(poll.registrations) == 1 else "Registros no TSE"
        details.append(f"<b>{label}:</b> " + html.escape(", ".join(poll.registrations)))

    details_html = "<br/>".join(details) if details else "Metodologia disponível na fonte G1."

    buttons = []
    if panel_url:
        buttons.append(
            f'<tg-button type="url" style="primary" url="{html.escape(panel_url, quote=True)}">'
            "Painel completo</tg-button>"
        )
    buttons.append(
        f'<tg-button type="url" url="{html.escape(poll.source_url, quote=True)}">'
        "Fonte G1</tg-button>"
    )
    button_row = f'<tg-button-row align="center">{"".join(buttons)}</tg-button-row>'

    secondary_table = ""
    if secondary_rows:
        secondary_table = (
            "<h4>Outras respostas</h4>"
            "<table compact>"
            '<tr><th align="left">Opção</th><th align="right">%</th></tr>'
            + "".join(secondary_rows)
            + "</table>"
        )

    return "".join([
        f"<h2>{html.escape(headline)}</h2>",
        f"<p><b>{html.escape(office)} · {html.escape(place)} · {poll.round}º turno</b></p>",
        f"<p>{html.escape(poll.institute)} · {html.escape(poll.question)} · "
        f"rodada de <b>{_date_br(poll.latest_date)}</b></p>",
        "<hr/>",
        '<table bordered striped compact><caption>Intenção de voto</caption>',
        "".join(rows),
        "</table>",
        secondary_table,
        "<details><summary>Metodologia e registro</summary>",
        f"<p>{details_html}</p>",
        "</details>",
        "<footer>Pesquisa de intenção de voto. Não é apuração nem previsão de resultado. "
        f"Fonte: G1 / {html.escape(poll.institute)}.</footer>",
        button_row,
    ])


async def send_rich_html(
    *,
    token: str,
    chat_id: str | int,
    rich_html: str,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Call Bot API sendRichMessage directly; PTB 22.x doesn't expose Bot API 10.3 Rich Messages yet."""
    if not token:
        raise RichMessageError("Token do Telegram não configurado.")

    url = f"https://api.telegram.org/bot{token}/sendRichMessage"
    payload = {
        "chat_id": chat_id,
        "rich_message": {
            "html": rich_html,
            "skip_entity_detection": True,
        },
        "disable_notification": disable_notification,
    }

    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        response = await client.post(url, json=payload)

    try:
        data = response.json()
    except ValueError as exc:
        raise RichMessageError(
            f"Telegram retornou HTTP {response.status_code} sem JSON."
        ) from exc

    if not response.is_success or not data.get("ok"):
        description = str(data.get("description") or f"HTTP {response.status_code}")
        raise RichMessageError(description)

    result = data.get("result")
    if not isinstance(result, dict):
        raise RichMessageError("Telegram não retornou a mensagem enviada.")
    return result
