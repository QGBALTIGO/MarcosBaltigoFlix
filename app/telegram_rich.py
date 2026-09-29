from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

import httpx

from .formatter import UF_NAMES
from .g1_polls import G1Poll, G1PollChoice
from .models import ElectionResult
from .result_service import is_pre_election


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



def _int_br(value: int | float) -> str:
    return f"{int(value):,}".replace(",", ".")


def _result_pct(value: float) -> str:
    value = float(value or 0)
    if abs(value - round(value)) < 0.000001:
        return f"{int(round(value))}%"
    return f"{value:.2f}".replace(".", ",") + "%"


def build_president_result_rich_html(
    result: ElectionResult,
    *,
    panel_url: str = "",
    panel_web_app: bool = True,
    shared: bool = False,
) -> str:
    """Build the President result in the same native-table Rich Message style used by the channel."""
    place = UF_NAMES.get(result.scope, result.scope.upper())
    pre_election = is_pre_election(result)

    candidate_rows = [
        "<tr>"
        '<th align="left">Candidato</th>'
        '<th align="right">Votos</th>'
        '<th align="right">%</th>'
        "</tr>"
    ]
    for candidate in result.candidates:
        identity = []
        if candidate.number is not None:
            identity.append(str(candidate.number))
        if candidate.party:
            identity.append(candidate.party)
        candidate_cell = f"<b>{html.escape(candidate.ballot_name or candidate.name)}</b>"
        if identity:
            candidate_cell += "<br/>" + html.escape(" · ".join(identity))
        candidate_rows.append(
            "<tr>"
            f'<td align="left">{candidate_cell}</td>'
            f'<td align="right">{_int_br(candidate.votes)}</td>'
            f'<td align="right"><b>{_result_pct(candidate.percentage)}</b></td>'
            "</tr>"
        )

    sections_text = _result_pct(result.sections_counted_pct)
    if result.sections_total > 0:
        sections_text = (
            f"{_int_br(result.sections_counted)} / {_int_br(result.sections_total)}"
            f" · {_result_pct(result.sections_counted_pct)}"
        )

    totals = [
        ("Seções totalizadas", sections_text),
        ("Votos válidos", _int_br(result.valid_votes)),
        ("Em branco", _int_br(result.blank_votes)),
        ("Nulos", _int_br(result.null_votes)),
        ("Comparecimento", _int_br(result.turnout)),
        ("Abstenção", _int_br(result.abstention)),
    ]
    totals_rows = "".join(
        "<tr>"
        f'<td align="left">{html.escape(label)}</td>'
        f'<td align="right"><b>{html.escape(value)}</b></td>'
        "</tr>"
        for label, value in totals
    )

    if pre_election:
        status = (
            "<p>🕒 <b>Apuração ainda não iniciada.</b> "
            "As candidaturas estão carregadas e permanecem com 0 votos e 0% "
            "até a publicação oficial do TSE.</p>"
        )
        update_text = "Aguardando início da apuração oficial"
    else:
        status = (
            f"<p>🔄 <b>{_result_pct(result.sections_counted_pct)}</b> "
            "das seções totalizadas.</p>"
        )
        timestamp = " ".join(
            part for part in (result.totalization_date, result.totalization_time) if part
        )
        update_text = timestamp or "Atualização oficial disponível"

    if shared:
        # Telegram renders callback and URL buttons with different native fills in
        # shared Rich Messages. Keep them on separate rows so the callback's
        # neutral fill looks intentional instead of like a broken primary button.
        buttons = (
            '<tg-button-row align="center">'
            '<tg-button type="callback_data" data="president:refresh:br">'
            "🔄 Atualizar resultado</tg-button>"
            "</tg-button-row>"
        )
        if panel_url:
            buttons += (
                '<tg-button-row align="center">'
                f'<tg-button type="url" url="{html.escape(panel_url, quote=True)}">'
                "📊 Painel ao vivo</tg-button>"
                "</tg-button-row>"
            )
    else:
        buttons = (
            '<tg-button-row align="center">'
            '<tg-button type="callback_data" style="primary" data="president:refresh:br">'
            "🔄 Atualizar</tg-button>"
        )
        if panel_url:
            button_type = "web_app" if panel_web_app else "url"
            buttons += (
                f'<tg-button type="{button_type}" url="{html.escape(panel_url, quote=True)}">'
                "📊 Painel ao vivo</tg-button>"
            )
        buttons += "</tg-button-row>"
        buttons += (
            '<tg-button-row align="center">'
            '<tg-button type="callback_data" data="start:home">⬅️ Voltar</tg-button>'
            '<tg-button type="switch_inline_query_chosen_chat" query="presidente br" '
            'allow-user-chats allow-group-chats>📤 Compartilhar</tg-button>'
            "</tg-button-row>"
        )

    return "".join([
        "<h2>🗳️ Presidente • Brasil</h2>",
        f"<p><b>Eleições 2026 · {result.round}º turno · {html.escape(place)}</b></p>",
        "<p>Apuração oficial do Tribunal Superior Eleitoral (TSE).</p>",
        status,
        "<hr/>",
        '<table bordered striped compact><caption>Resultado presidencial</caption>',
        "".join(candidate_rows),
        "</table>",
        '<table compact><caption>Totalização</caption>',
        totals_rows,
        "</table>",
        f"<footer>Última atualização: {html.escape(update_text)} · Fonte: TSE.</footer>",
        buttons,
    ])



STATE_OFFICE_NAMES = {
    "federal": "Deputado Federal",
    "estadual": "Deputado Estadual",
    "senador": "Senador",
    "governador": "Governador",
}

STATE_RESULT_PAGE_SIZE = 8


def _state_office_name(office: str, scope: str) -> str:
    if office == "estadual" and scope.lower() == "df":
        return "Deputado Distrital"
    return STATE_OFFICE_NAMES.get(office, office.title())


def _state_result_page(
    result: ElectionResult,
    page: int,
    page_size: int = STATE_RESULT_PAGE_SIZE,
) -> tuple[list[Candidate], int, int]:
    page_size = max(1, int(page_size))
    total = len(result.candidates)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(0, int(page)), pages - 1)
    start = page * page_size
    return result.candidates[start:start + page_size], page, pages


def build_state_office_result_rich_html(
    result: ElectionResult,
    *,
    office: str,
    page: int = 0,
    page_size: int = STATE_RESULT_PAGE_SIZE,
    panel_url: str = "",
    panel_web_app: bool = True,
    shared: bool = False,
) -> str:
    """Build one paginated state-office result using Telegram native Rich tables."""
    scope = result.scope.lower()
    place = UF_NAMES.get(scope, scope.upper())
    office_name = _state_office_name(office, scope)
    rows_on_page, page, pages = _state_result_page(result, page, page_size)
    pre_election = is_pre_election(result)

    rows = [
        "<tr>"
        '<th align="left">Candidato</th>'
        '<th align="right">Votos</th>'
        '<th align="right">%</th>'
        "</tr>"
    ]
    for candidate in rows_on_page:
        identity: list[str] = []
        if candidate.number is not None:
            identity.append(str(candidate.number))
        if candidate.party:
            identity.append(candidate.party)
        candidate_cell = f"<b>{html.escape(candidate.ballot_name or candidate.name)}</b>"
        if identity:
            candidate_cell += "<br/>" + html.escape(" · ".join(identity))
        rows.append(
            "<tr>"
            f'<td align="left">{candidate_cell}</td>'
            f'<td align="right">{_int_br(candidate.votes)}</td>'
            f'<td align="right"><b>{_result_pct(candidate.percentage)}</b></td>'
            "</tr>"
        )

    sections_text = _result_pct(result.sections_counted_pct)
    if result.sections_total > 0:
        sections_text = (
            f"{_int_br(result.sections_counted)} / {_int_br(result.sections_total)}"
            f" · {_result_pct(result.sections_counted_pct)}"
        )

    if pre_election:
        status = (
            "<p>🕒 <b>Apuração ainda não iniciada.</b> "
            "As candidaturas registradas estão carregadas e permanecem com 0 votos e 0% "
            "até a publicação oficial do TSE.</p>"
        )
        update_text = "Aguardando início da apuração oficial"
    else:
        status = (
            f"<p>🔄 <b>{_result_pct(result.sections_counted_pct)}</b> "
            "das seções totalizadas.</p>"
        )
        timestamp = " ".join(
            part for part in (result.totalization_date, result.totalization_time) if part
        )
        update_text = timestamp or "Atualização oficial disponível"

    page_query = f"estado {scope} {office} {page}"

    buttons = ""
    if pages > 1:
        nav_buttons: list[str] = []
        if page > 0:
            nav_buttons.append(
                f'<tg-button type="callback_data" data="state:view:{scope}:{office}:{page - 1}">'
                "⬅️ Anterior</tg-button>"
            )
        if page < pages - 1:
            nav_buttons.append(
                f'<tg-button type="callback_data" data="state:view:{scope}:{office}:{page + 1}">'
                "Próxima ➡️</tg-button>"
            )
        if nav_buttons:
            buttons += f'<tg-button-row align="center">{"".join(nav_buttons)}</tg-button-row>'

    buttons += (
        '<tg-button-row align="center">'
        f'<tg-button type="callback_data" data="state:refresh:{scope}:{office}:{page}">'
        "🔄 Atualizar resultado</tg-button>"
        "</tg-button-row>"
    )

    if panel_url:
        button_type = "url" if shared or not panel_web_app else "web_app"
        buttons += (
            '<tg-button-row align="center">'
            f'<tg-button type="{button_type}" url="{html.escape(panel_url, quote=True)}">'
            "📊 Painel ao vivo</tg-button>"
            "</tg-button-row>"
        )

    if not shared:
        buttons += (
            '<tg-button-row align="center">'
            f'<tg-button type="callback_data" data="state:{scope}">⬅️ Voltar</tg-button>'
            f'<tg-button type="switch_inline_query_chosen_chat" query="{html.escape(page_query, quote=True)}" '
            'allow-user-chats allow-group-chats>📤 Compartilhar</tg-button>'
            "</tg-button-row>"
        )

    page_label = f"Página {page + 1} de {pages} · {len(result.candidates)} candidaturas"
    totals_rows = "".join([
        "<tr><td align="left">Seções totalizadas</td>"
        f'<td align="right"><b>{html.escape(sections_text)}</b></td></tr>',
        "<tr><td align="left">Votos válidos</td>"
        f'<td align="right"><b>{_int_br(result.valid_votes)}</b></td></tr>',
        "<tr><td align="left">Em branco</td>"
        f'<td align="right"><b>{_int_br(result.blank_votes)}</b></td></tr>',
        "<tr><td align="left">Nulos</td>"
        f'<td align="right"><b>{_int_br(result.null_votes)}</b></td></tr>',
    ])

    return "".join([
        f"<h2>🗳️ {html.escape(office_name)} • {html.escape(place)}</h2>",
        f"<p><b>Eleições 2026 · {result.round}º turno</b></p>",
        "<p>Apuração oficial do Tribunal Superior Eleitoral (TSE).</p>",
        status,
        "<hr/>",
        f'<table bordered striped compact><caption>{html.escape(page_label)}</caption>',
        "".join(rows),
        "</table>",
        '<table compact><caption>Totalização</caption>',
        totals_rows,
        "</table>",
        f"<footer>Última atualização: {html.escape(update_text)} · Fonte: TSE.</footer>",
        buttons,
    ])


async def _bot_api_post(
    *,
    token: str,
    method: str,
    payload: dict[str, Any],
) -> Any:
    if not token:
        raise RichMessageError("Token do Telegram não configurado.")

    url = f"https://api.telegram.org/bot{token}/{method}"
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
    return data.get("result")


async def edit_rich_html(
    *,
    token: str,
    rich_html: str,
    chat_id: str | int | None = None,
    message_id: int | None = None,
    inline_message_id: str | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "rich_message": {
            "html": rich_html,
            "skip_entity_detection": True,
        }
    }
    if inline_message_id:
        payload["inline_message_id"] = inline_message_id
    elif chat_id is not None and message_id is not None:
        payload["chat_id"] = chat_id
        payload["message_id"] = message_id
    else:
        raise RichMessageError("É necessário informar a mensagem a editar.")

    return await _bot_api_post(
        token=token,
        method="editMessageText",
        payload=payload,
    )


async def answer_inline_rich_query(
    *,
    token: str,
    inline_query_id: str,
    rich_html: str,
    result_id: str,
    title: str,
    description: str,
) -> Any:
    return await _bot_api_post(
        token=token,
        method="answerInlineQuery",
        payload={
            "inline_query_id": inline_query_id,
            "results": [
                {
                    "type": "article",
                    "id": result_id,
                    "title": title,
                    "description": description,
                    "input_message_content": {
                        "rich_message": {
                            "html": rich_html,
                            "skip_entity_detection": True,
                        }
                    },
                }
            ],
            "cache_time": 0,
            "is_personal": False,
        },
    )


async def send_rich_html(
    *,
    token: str,
    chat_id: str | int,
    rich_html: str,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Call Bot API sendRichMessage directly; PTB 22.x doesn't expose Rich Messages yet."""
    result = await _bot_api_post(
        token=token,
        method="sendRichMessage",
        payload={
            "chat_id": chat_id,
            "rich_message": {
                "html": rich_html,
                "skip_entity_detection": True,
            },
            "disable_notification": disable_notification,
        },
    )
    if not isinstance(result, dict):
        raise RichMessageError("Telegram não retornou a mensagem enviada.")
    return result
