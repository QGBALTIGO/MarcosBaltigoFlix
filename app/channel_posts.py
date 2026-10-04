from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from telegram.error import TelegramError

from .formatter import UF_NAMES
from .models import ElectionResult
from .result_service import is_pre_election
from .telegram_rich import (
    RichMessageError,
    _int_br,
    _ordered_result_candidates,
    _result_pct,
    send_rich_html,
)

if TYPE_CHECKING:
    from .bot import ElectionBot

log = logging.getLogger(__name__)


def build_president_channel_rich_html(result: ElectionResult) -> str:
    """A broadcast is a dated result bulletin, never an interactive bot menu."""
    place = UF_NAMES.get(result.scope, result.scope.upper())
    rows = [
        '<tr><th align="left">Candidato</th>'
        '<th align="right">Votos</th><th align="right">%</th></tr>'
    ]
    for candidate in _ordered_result_candidates(result):
        identity = []
        if candidate.number is not None:
            identity.append(str(candidate.number))
        if candidate.party:
            identity.append(candidate.party)
        candidate_cell = f"<b>{html.escape(candidate.ballot_name or candidate.name)}</b>"
        if identity:
            candidate_cell += "<br/>" + html.escape(" · ".join(identity))
        rows.append(
            '<tr>'
            f'<td align="left">{candidate_cell}</td>'
            f'<td align="right">{_int_br(candidate.votes)}</td>'
            f'<td align="right"><b>{_result_pct(candidate.percentage)}</b></td>'
            '</tr>'
        )

    sections = _result_pct(result.sections_counted_pct)
    if result.sections_total > 0:
        sections = (
            f"{_int_br(result.sections_counted)} / {_int_br(result.sections_total)}"
            f" · {_result_pct(result.sections_counted_pct)}"
        )
    totals = (
        ("Seções totalizadas", sections),
        ("Votos válidos", _int_br(result.valid_votes)),
        ("Em branco", _int_br(result.blank_votes)),
        ("Nulos", _int_br(result.null_votes)),
        ("Comparecimento", _int_br(result.turnout)),
        ("Abstenção", _int_br(result.abstention)),
    )
    total_rows = "".join(
        f'<tr><td align="left">{html.escape(label)}</td>'
        f'<td align="right"><b>{html.escape(value)}</b></td></tr>'
        for label, value in totals
    )

    if is_pre_election(result):
        status = (
            "<p>🕒 <b>Apuração ainda não iniciada.</b> "
            "As candidaturas estão carregadas e permanecem com 0 votos e 0% "
            "até a publicação oficial do TSE.</p>"
        )
        timestamp = "Aguardando início da apuração oficial"
    else:
        status = (
            f"<p>🔄 <b>{_result_pct(result.sections_counted_pct)}</b> "
            "das seções totalizadas.</p>"
        )
        timestamp = " ".join(
            part for part in (result.totalization_date, result.totalization_time) if part
        ) or "Atualização oficial disponível"

    # Deliberately no tg-button, URL, callback, WebApp or reply_markup here.
    # Interactive controls are exclusive to the bot and shared inline messages.
    return "".join((
        "<h2>🗳️ Presidente • Brasil</h2>",
        f"<p><b>Eleições 2026 · {result.round}º turno · {html.escape(place)}</b></p>",
        "<p>Apuração oficial do Tribunal Superior Eleitoral (TSE).</p>",
        status,
        "<hr/>",
        '<table bordered striped compact><caption>Resultado presidencial</caption>',
        "".join(rows),
        "</table>",
        '<table compact><caption>Totalização</caption>',
        total_rows,
        "</table>",
        f"<footer>Última atualização: {html.escape(timestamp)} · Fonte: TSE.</footer>",
    ))


async def publish_president_channel_update(
    bot: ElectionBot,
    result: ElectionResult,
    *,
    force: bool = False,
) -> bool:
    """Publish the channel-only bulletin, retaining the existing deduplication keys."""
    if not bot.application or not bot.settings.channel_id:
        return False
    if not await bot.check_channel_ready():
        return False
    assert bot.channel_chat_id is not None

    fingerprint = bot._president_channel_fingerprint(result)
    state_key = "channel:president:last_post_signature"
    if not force and await bot.storage.get_state(state_key) == fingerprint:
        return True

    try:
        sent = await send_rich_html(
            token=bot.settings.telegram_bot_token,
            chat_id=bot.channel_chat_id,
            rich_html=build_president_channel_rich_html(result),
            disable_notification=False,
        )
        message_id = int(sent.get("message_id") or 0)
        if not message_id:
            raise RichMessageError("Telegram não retornou message_id da atualização presidencial.")
        await bot.storage.set_state(state_key, fingerprint)
        await bot.storage.set_state("channel:president:last_post_message_id", str(message_id))
        await bot.storage.set_state("channel:president:last_generation_id", str(result.generation_id or ""))
        bot.channel_last_update_at = datetime.now(timezone.utc).isoformat()
        log.info(
            "Nova atualização presidencial enviada ao canal sem botões: message_id=%s geração=%s seções=%.4f%% votos=%s",
            message_id,
            result.generation_id or "-",
            float(result.sections_counted_pct or 0),
            int(result.total_votes or 0),
        )
        return True
    except (RichMessageError, TelegramError) as exc:
        if "administrator rights" in str(exc).lower() or "not enough rights" in str(exc).lower():
            bot.channel_ready = False
        bot.channel_error = str(exc)
        log.error("Falha enviando atualização presidencial ao canal: %s", exc)
        return False
