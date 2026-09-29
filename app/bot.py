from __future__ import annotations

import logging
import re
import unicodedata

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, InlineQueryHandler

from .config import Settings
from .formatter import UF_NAMES, format_result
from .g1_polls import G1Poll, G1PollClient
from .poll_formatter import format_g1_history, format_g1_poll
from .result_service import ResultService, is_pre_election
from .storage import Storage, milestone_for
from .telegram_rich import (
    RichMessageError,
    answer_inline_rich_query,
    answer_inline_rich_results,
    STATE_RESULT_PAGE_SIZE,
    build_g1_channel_rich_html,
    build_president_result_rich_html,
    build_state_inline_menu_rich_html,
    build_state_office_result_rich_html,
    edit_rich_html,
    send_rich_html,
)
from .tse import VALID_UFS

log = logging.getLogger(__name__)


def bot_panel_deep_link(settings: Settings) -> str:
    username = settings.bot_username.strip().lstrip("@") or "ResultadoEleicoes_Bot"
    return f"https://t.me/{username}?start=painel"


def start_message_text(settings: Settings) -> str:
    return (
        "<b>🗳️ Eleições 2026 • Apuração Oficial</b>\n\n"
        "Bem-vindo ao <b>Eleições 2026</b>.\n\n"
        "Acompanhe por aqui a apuração das eleições em todo o Brasil, com informações "
        "baseadas nos dados divulgados oficialmente pelo <b>Tribunal Superior Eleitoral (TSE)</b>.\n\n"
        "<blockquote>"
        "📊 Consulte resultados por cargo e estado\n"
        "🔄 Acompanhe a evolução da totalização\n"
        "🔔 Receba alertas durante a apuração\n"
        "🔎 Consulte pesquisas eleitorais disponíveis\n"
        "🏛️ Acesse informações sobre os candidatos"
        "</blockquote>\n\n"
        "<i><b>Transparência:</b> este bot é independente e não possui vínculo com partidos, "
        "candidatos ou campanhas eleitorais. Os dados de apuração são reproduzidos a partir "
        "das fontes oficiais, sem projeções próprias ou indicação de voto.</i>\n\n"
        "👇 <b>Escolha uma opção abaixo para começar.</b>"
    )


def start_keyboard(settings: Settings, external_chat: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if settings.webapp_url:
        if external_chat:
            rows.append([
                InlineKeyboardButton(
                    "📲 Abrir painel completo",
                    url=bot_panel_deep_link(settings),
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    "📲 Abrir painel completo",
                    web_app=WebAppInfo(settings.webapp_url),
                )
            ])

    rows.append([
        InlineKeyboardButton("🗳️ Presidente", callback_data="president:open:br"),
        InlineKeyboardButton("🗺️ Estados", callback_data="start:states"),
    ])
    return InlineKeyboardMarkup(rows)


def president_fallback_keyboard(
    settings: Settings,
    *,
    external_chat: bool = False,
) -> InlineKeyboardMarkup:
    panel_button = InlineKeyboardButton(
        "📊 Painel ao vivo",
        url=bot_panel_deep_link(settings) if external_chat or not settings.webapp_url else None,
        web_app=None if external_chat or not settings.webapp_url else WebAppInfo(settings.webapp_url),
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Atualizar", callback_data="president:refresh:br"),
            panel_button,
        ],
        [
            InlineKeyboardButton("⬅️ Voltar", callback_data="start:home"),
            InlineKeyboardButton("📤 Compartilhar", switch_inline_query="presidente br"),
        ],
    ])


def states_keyboard() -> InlineKeyboardMarkup:
    ufs = [
        ("AC", "ac"), ("AL", "al"), ("AP", "ap"),
        ("AM", "am"), ("BA", "ba"), ("CE", "ce"),
        ("DF", "df"), ("ES", "es"), ("GO", "go"),
        ("MA", "ma"), ("MT", "mt"), ("MS", "ms"),
        ("MG", "mg"), ("PA", "pa"), ("PB", "pb"),
        ("PR", "pr"), ("PE", "pe"), ("PI", "pi"),
        ("RJ", "rj"), ("RN", "rn"), ("RS", "rs"),
        ("RO", "ro"), ("RR", "rr"), ("SC", "sc"),
        ("SP", "sp"), ("SE", "se"), ("TO", "to"),
    ]
    rows = [
        [
            InlineKeyboardButton(label, callback_data=f"state:{scope}")
            for label, scope in ufs[index:index + 3]
        ]
        for index in range(0, len(ufs), 3)
    ]
    rows.append([InlineKeyboardButton("⬅️ Voltar", callback_data="start:home")])
    return InlineKeyboardMarkup(rows)


STATE_OFFICE_LABELS = {
    "federal": "Deputado Federal",
    "estadual": "Deputado Estadual",
    "senador": "Senador",
    "governador": "Governador",
}


INLINE_FLAG_BASE_URL = (
    "https://cdn.jsdelivr.net/gh/pierrelapalu/"
    "icones-bandeiras-br-uf@master/dist/square-rounded/png-200"
)
INLINE_BRAZIL_FLAG_FILE = "01-brasil-square-rounded.png"
INLINE_STATE_OPTIONS = (
    ("ac", "Acre", "02-acre-square-rounded.png"),
    ("al", "Alagoas", "03-alagoas-square-rounded.png"),
    ("ap", "Amapá", "04-amapa-square-rounded.png"),
    ("am", "Amazonas", "05-amazonas-square-rounded.png"),
    ("ba", "Bahia", "06-bahia-square-rounded.png"),
    ("ce", "Ceará", "07-ceara-square-rounded-v2.png"),
    ("df", "Distrito Federal", "08-distrito-federal-square-rounded.png"),
    ("es", "Espírito Santo", "09-espirito-santo-square-rounded-v2.png"),
    ("go", "Goiás", "10-goias-square-rounded.png"),
    ("ma", "Maranhão", "11-maranhao-square-rounded.png"),
    ("mt", "Mato Grosso", "12-mato-grosso-square-rounded.png"),
    ("ms", "Mato Grosso do Sul", "13-mato-grosso-do-sul-square-rounded.png"),
    ("mg", "Minas Gerais", "14-minas-gerais-square-rounded.png"),
    ("pa", "Pará", "15-para-square-rounded.png"),
    ("pb", "Paraíba", "16-paraiba-square-rounded-v2.png"),
    ("pr", "Paraná", "17-parana-square-rounded.png"),
    ("pe", "Pernambuco", "18-pernambuco-square-rounded.png"),
    ("pi", "Piauí", "19-piaui-square-rounded.png"),
    ("rj", "Rio de Janeiro", "20-rio-de-janeiro-square-rounded.png"),
    ("rn", "Rio Grande do Norte", "21-rio-grande-do-norte-square-rounded.png"),
    ("rs", "Rio Grande do Sul", "22-rio-grande-do-sul-square-rounded.png"),
    ("ro", "Rondônia", "23-rondonia-square-rounded.png"),
    ("rr", "Roraima", "24-roraima-square-rounded.png"),
    ("sc", "Santa Catarina", "25-santa-catarina-square-rounded.png"),
    ("sp", "São Paulo", "26-sao-paulo-square-rounded.png"),
    ("se", "Sergipe", "27-sergipe-square-rounded.png"),
    ("to", "Tocantins", "28-tocantins-square-rounded.png"),
)
INLINE_STATE_FLAG_FILES = {
    scope: filename for scope, _name, filename in INLINE_STATE_OPTIONS
}


def inline_flag_url(scope: str) -> str:
    filename = (
        INLINE_BRAZIL_FLAG_FILE
        if scope.lower() == "br"
        else INLINE_STATE_FLAG_FILES.get(scope.lower(), "")
    )
    return f"{INLINE_FLAG_BASE_URL}/{filename}" if filename else ""


def _fold_inline_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def inline_state_options(query: str) -> list[tuple[str, str, str]]:
    needle = _fold_inline_text(" ".join((query or "").strip().split()))
    if needle.startswith("estado "):
        needle = needle[7:].strip()
    if needle in {"", "estado", "estados"}:
        return list(INLINE_STATE_OPTIONS)

    # A two-letter UF is an exact selector. This prevents "sp" from also
    # matching the letters inside names such as "Espírito Santo".
    for item in INLINE_STATE_OPTIONS:
        scope, _name, _filename = item
        if needle == scope:
            return [item]

    matches: list[tuple[str, str, str]] = []
    for item in INLINE_STATE_OPTIONS:
        _scope, name, _filename = item
        if needle in _fold_inline_text(name):
            matches.append(item)
    return matches


def state_office_label(scope: str, office: str) -> str:
    if scope == "df" and office == "estadual":
        return "Deputado Distrital"
    return STATE_OFFICE_LABELS.get(office, office.title())


def parse_state_inline_query(query: str) -> tuple[str, str, int] | None:
    normalized = " ".join((query or "").strip().lower().split())
    match = re.fullmatch(
        r"(?:estado\s+)?([a-z]{2})\s+(federal|estadual|senador|governador)(?:\s+(\d+))?",
        normalized,
    )
    if not match:
        return None
    scope, office, raw_page = match.groups()
    if scope not in VALID_UFS:
        return None
    return scope, office, int(raw_page or 0)


def state_office_keyboard(scope: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏛️ Deputado Federal", callback_data=f"state:open:{scope}:federal:0"),
            InlineKeyboardButton(
                "🏢 Deputado Distrital" if scope == "df" else "🏢 Deputado Estadual",
                callback_data=f"state:open:{scope}:estadual:0",
            ),
        ],
        [
            InlineKeyboardButton("🗳️ Senador", callback_data=f"state:open:{scope}:senador:0"),
            InlineKeyboardButton("🏛️ Governador", callback_data=f"state:open:{scope}:governador:0"),
        ],
        [InlineKeyboardButton("⬅️ Estados", callback_data="start:states")],
    ])


def state_office_fallback_keyboard(
    settings: Settings,
    *,
    scope: str,
    office: str,
    page: int,
    pages: int,
    external_chat: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"state:view:{scope}:{office}:{page - 1}"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Próxima ➡️", callback_data=f"state:view:{scope}:{office}:{page + 1}"))
        if nav:
            rows.append(nav)

    rows.append([
        InlineKeyboardButton("🔄 Atualizar", callback_data=f"state:refresh:{scope}:{office}:{page}"),
        InlineKeyboardButton(
            "📊 Painel ao vivo",
            url=bot_panel_deep_link(settings) if external_chat or not settings.webapp_url else None,
            web_app=None if external_chat or not settings.webapp_url else WebAppInfo(settings.webapp_url),
        ),
    ])
    if not external_chat:
        rows.append([
            InlineKeyboardButton("⬅️ Voltar", callback_data=f"state:{scope}"),
            InlineKeyboardButton(
                "📤 Compartilhar",
                switch_inline_query=f"estado {scope} {office} {page}",
            ),
        ])
    return InlineKeyboardMarkup(rows)


def result_keyboard(
    settings: Settings,
    subscribed: bool = False,
    scope: str = "br",
    external_chat: bool = False,
) -> InlineKeyboardMarkup:
    if external_chat:
        rows = [[InlineKeyboardButton("Abrir resultados do TSE", url=settings.public_results_url)]]
        if settings.webapp_url:
            rows.insert(0, [InlineKeyboardButton("Painel ao vivo", url=bot_panel_deep_link(settings))])
        return InlineKeyboardMarkup(rows)

    rows = [
        [InlineKeyboardButton("Atualizar", callback_data=f"result:{scope}")],
        [InlineKeyboardButton("Mato Grosso do Sul", callback_data="result:ms"), InlineKeyboardButton("São Paulo", callback_data="result:sp")],
        [InlineKeyboardButton("Rio de Janeiro", callback_data="result:rj"), InlineKeyboardButton("Minas Gerais", callback_data="result:mg")],
        [InlineKeyboardButton("Parar atualização" if subscribed else "Acompanhar aqui", callback_data=f"stop:{scope}" if subscribed else f"subscribe:{scope}")],
        [InlineKeyboardButton("Pesquisas G1", callback_data="g1:presidente:br:datafolha")],
    ]
    if settings.webapp_url:
        rows.append([InlineKeyboardButton("Painel ao vivo", web_app=WebAppInfo(settings.webapp_url))])
    rows.append([InlineKeyboardButton("Abrir resultados do TSE", url=settings.public_results_url)])
    return InlineKeyboardMarkup(rows)


def poll_keyboard(settings: Settings, poll: G1Poll, external_chat: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("Abrir pesquisa no G1", url=poll.source_url)]]
    if not external_chat:
        institute = poll.institute.lower()
        rows.insert(0, [InlineKeyboardButton(
            "Ver histórico",
            callback_data=f"g1h:{poll.office}:{poll.scope}:{institute}",
        )])
    if settings.webapp_url:
        rows.append([
            InlineKeyboardButton(
                "Painel de pesquisas",
                url=bot_panel_deep_link(settings) if external_chat else None,
                web_app=None if external_chat else WebAppInfo(settings.webapp_url),
            )
        ])
    return InlineKeyboardMarkup(rows)


def polls_menu_keyboard(settings: Settings, external_chat: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Presidente • Datafolha", callback_data="g1:presidente:br:datafolha"),
            InlineKeyboardButton("Presidente • Quaest", callback_data="g1:presidente:br:quaest"),
        ],
        [
            InlineKeyboardButton("Governador • MS", callback_data="g1:governador:ms:auto"),
            InlineKeyboardButton("Senador • MS", callback_data="g1:senador:ms:auto"),
        ],
    ]
    if settings.webapp_url:
        rows.append([
            InlineKeyboardButton(
                "Ver todos os estados no painel",
                url=bot_panel_deep_link(settings) if external_chat else None,
                web_app=None if external_chat else WebAppInfo(settings.webapp_url),
            )
        ])
    return InlineKeyboardMarkup(rows)


class ElectionBot:
    def __init__(self, settings: Settings, results: ResultService, storage: Storage, g1_polls: G1PollClient):
        self.settings = settings
        self.results = results
        self.storage = storage
        self.g1_polls = g1_polls
        self.application: Application | None = None

    async def _record_user(self, update: Update) -> None:
        user = update.effective_user
        if not user:
            return
        try:
            await self.storage.record_user(user.id)
        except Exception:
            # User analytics must never block the election bot.
            log.exception("Falha registrando user_id=%s", user.id)

    def _is_configured_admin(self, update: Update) -> bool:
        if not self.settings.admin_ids:
            return True
        user_id = update.effective_user.id if update.effective_user else 0
        return user_id in self.settings.admin_ids

    def _required_channel_url(self) -> str:
        channel = self.settings.required_channel.strip()
        if channel.startswith("@"):
            return f"https://t.me/{channel[1:]}"
        return "https://t.me/ResultadoEleicoes"

    async def _is_required_channel_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if not self.settings.required_channel or self._is_configured_admin(update):
            return True
        user = update.effective_user
        if not user:
            return True
        try:
            member = await context.bot.get_chat_member(self.settings.required_channel, user.id)
            status = str(getattr(member, "status", "")).lower()
            if status in {"creator", "administrator", "member"}:
                return True
            if status == "restricted" and bool(getattr(member, "is_member", False)):
                return True
            return False
        except TelegramError as exc:
            log.warning("Não foi possível verificar inscrição no canal obrigatório: %s", exc)
            return False

    async def _guard_required_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        await self._record_user(update)
        if await self._is_required_channel_member(update, context):
            return True
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Entrar no canal oficial", url=self._required_channel_url())],
            [InlineKeyboardButton("✅ Verificar inscrição", callback_data="verify_subscription")],
        ])
        text = (
            "<b>📢 Acesso ao Resultado Eleições 2026</b>\n\n"
            "Para continuar, entre no nosso canal oficial: <b>@ResultadoEleicoes</b>.\n\n"
            "Por lá você acompanha avisos, atualizações e novidades do projeto.\n\n"
            "Depois de entrar, volte aqui e toque em <b>✅ Verificar inscrição</b> "
            "para liberar o acesso ao bot."
        )
        if update.effective_message:
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return False

    async def _guard_live_control(self, update: Update) -> bool:
        chat = update.effective_chat
        if chat and chat.type != "private" and not self._is_configured_admin(update):
            if update.callback_query:
                await update.callback_query.answer("Ação restrita aos administradores do bot.", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("Ação restrita aos administradores configurados.")
            return False
        return True

    async def build(self) -> Application | None:
        token = self.settings.telegram_bot_token
        if not token:
            log.warning("TELEGRAM_BOT_TOKEN ausente: API web inicia, bot Telegram fica desativado.")
            return None
        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("resultado", self.resultado))
        app.add_handler(CommandHandler("brasil", self.resultado))
        app.add_handler(CommandHandler("estado", self.estado))
        app.add_handler(CommandHandler("acompanhar", self.acompanhar))
        app.add_handler(CommandHandler("parar", self.parar))
        app.add_handler(CommandHandler("fonte", self.fonte))
        app.add_handler(CommandHandler("alertas", self.alertas))
        app.add_handler(CommandHandler("status", self.status))
        app.add_handler(CommandHandler("publicar", self.publicar))
        app.add_handler(CommandHandler("pesquisas", self.pesquisas))
        app.add_handler(CommandHandler("pesquisa", self.pesquisa))
        app.add_handler(CommandHandler("governador", self.governador))
        app.add_handler(CommandHandler("senador", self.senador))
        app.add_handler(CommandHandler("boletim", self.boletim))
        app.add_handler(InlineQueryHandler(self.inline_query))
        app.add_handler(CallbackQueryHandler(self.callback))
        self.application = app
        return app

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return

        if context.args and context.args[0].strip().lower() == "painel":
            if not self.settings.webapp_url:
                await update.effective_message.reply_text("O painel está temporariamente indisponível.")
                return
            await update.effective_message.reply_text(
                "<b>Painel completo • Eleições 2026</b>\n\n"
                "Abra o painel dentro do Telegram para consultar apuração e pesquisas eleitorais.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Abrir painel completo",
                        web_app=WebAppInfo(self.settings.webapp_url),
                    )
                ]]),
            )
            return

        await update.effective_message.reply_text(
            start_message_text(self.settings),
            parse_mode=ParseMode.HTML,
            reply_markup=start_keyboard(
                self.settings,
                external_chat=update.effective_chat.type != "private",
            ),
        )

    def _president_rich(
        self,
        result,
        *,
        shared: bool = False,
        external_chat: bool = False,
    ) -> str:
        if shared:
            panel_url = bot_panel_deep_link(self.settings)
            panel_web_app = False
        elif not external_chat and self.settings.webapp_url:
            panel_url = self.settings.webapp_url
            panel_web_app = True
        else:
            panel_url = bot_panel_deep_link(self.settings)
            panel_web_app = False
        return build_president_result_rich_html(
            result,
            panel_url=panel_url,
            panel_web_app=panel_web_app,
            shared=shared,
        )

    async def _send_president_rich(
        self,
        message,
        *,
        edit: bool = False,
        force: bool = False,
    ) -> None:
        try:
            result, _ = await self.results.fetch("br", force=force)
            external_chat = getattr(getattr(message, "chat", None), "type", "private") != "private"
            rich_html = self._president_rich(
                result,
                external_chat=external_chat,
            )
            if edit:
                await edit_rich_html(
                    token=self.settings.telegram_bot_token,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    rich_html=rich_html,
                )
            else:
                await send_rich_html(
                    token=self.settings.telegram_bot_token,
                    chat_id=message.chat_id,
                    rich_html=rich_html,
                )
        except Exception as exc:
            log.exception("Falha enviando resultado presidencial em Rich Message")
            try:
                result, _ = await self.results.fetch("br")
                text = format_result(result, self.settings)
                markup = president_fallback_keyboard(
                    self.settings,
                    external_chat=getattr(getattr(message, "chat", None), "type", "private") != "private",
                )
                if edit:
                    await message.edit_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                else:
                    await message.reply_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
            except Exception:
                log.exception("Falha também no fallback presidencial")
                if not edit:
                    await message.reply_text(
                        "Não consegui consultar a apuração presidencial agora. Tente novamente em instantes."
                    )

    def _state_office_rich(
        self,
        result,
        *,
        office: str,
        page: int,
        shared: bool = False,
        external_chat: bool = False,
    ) -> str:
        if shared:
            panel_url = bot_panel_deep_link(self.settings)
            panel_web_app = False
        elif not external_chat and self.settings.webapp_url:
            panel_url = self.settings.webapp_url
            panel_web_app = True
        else:
            panel_url = bot_panel_deep_link(self.settings)
            panel_web_app = False

        return build_state_office_result_rich_html(
            result,
            office=office,
            page=page,
            page_size=STATE_RESULT_PAGE_SIZE,
            panel_url=panel_url,
            panel_web_app=panel_web_app,
            shared=shared,
        )

    def _state_page_count(self, result) -> int:
        count = len(result.candidates)
        return max(1, (count + STATE_RESULT_PAGE_SIZE - 1) // STATE_RESULT_PAGE_SIZE)

    def _state_fallback_text(
        self,
        result,
        *,
        office: str,
        page: int,
    ) -> tuple[str, int, int]:
        pages = self._state_page_count(result)
        page = min(max(0, int(page)), pages - 1)
        start = page * STATE_RESULT_PAGE_SIZE
        candidates = result.candidates[start:start + STATE_RESULT_PAGE_SIZE]
        place = UF_NAMES.get(result.scope, result.scope.upper())
        title = state_office_label(result.scope, office)

        lines = [
            f"<b>🗳️ {title} • {place}</b>",
            f"<b>Eleições 2026 · {result.round}º turno</b>",
            "",
            f"Página <b>{page + 1}/{pages}</b> · {len(result.candidates)} candidaturas",
            "",
        ]
        for candidate in candidates:
            identity = " · ".join(
                value
                for value in (
                    str(candidate.number) if candidate.number is not None else "",
                    candidate.party,
                )
                if value
            )
            votes = f"{int(candidate.votes):,}".replace(",", ".")
            percentage = (
                f"{float(candidate.percentage):.2f}"
                .rstrip("0")
                .rstrip(".")
                .replace(".", ",")
            ) + "%"
            lines.append(
                f"<b>{candidate.ballot_name or candidate.name}</b>"
                + (f" · {identity}" if identity else "")
            )
            lines.append(f"{votes} votos · {percentage}")
            lines.append("")

        sections = (
            f"{float(result.sections_counted_pct):.2f}"
            .rstrip("0")
            .rstrip(".")
            .replace(".", ",")
        ) + "%"
        lines.extend([
            f"<b>Seções totalizadas:</b> {sections}",
            f"<b>Fonte:</b> {self.settings.source_label}",
        ])
        return "\n".join(lines), page, pages

    async def _send_state_office_rich(
        self,
        message,
        *,
        scope: str,
        office: str,
        page: int = 0,
        edit: bool = False,
        force: bool = False,
    ) -> None:
        try:
            result, _ = await self.results.fetch(
                scope,
                office=office,
                force=force,
            )
            external_chat = (
                getattr(getattr(message, "chat", None), "type", "private")
                != "private"
            )
            rich_html = self._state_office_rich(
                result,
                office=office,
                page=page,
                external_chat=external_chat,
            )
            if edit:
                await edit_rich_html(
                    token=self.settings.telegram_bot_token,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    rich_html=rich_html,
                )
            else:
                await send_rich_html(
                    token=self.settings.telegram_bot_token,
                    chat_id=message.chat_id,
                    rich_html=rich_html,
                )
            return
        except Exception:
            log.exception(
                "Falha enviando resultado estadual em Rich Message: %s/%s p%s",
                scope,
                office,
                page,
            )

        # Never leave a callback looking dead just because Rich Messages fail.
        try:
            result, _ = await self.results.fetch(scope, office=office)
            text, page, pages = self._state_fallback_text(
                result,
                office=office,
                page=page,
            )
            markup = state_office_fallback_keyboard(
                self.settings,
                scope=scope,
                office=office,
                page=page,
                pages=pages,
                external_chat=(
                    getattr(getattr(message, "chat", None), "type", "private")
                    != "private"
                ),
            )
            if edit:
                await message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
            else:
                await message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
        except Exception:
            log.exception(
                "Falha também no fallback estadual: %s/%s p%s",
                scope,
                office,
                page,
            )
            if edit:
                try:
                    await message.edit_text(
                        "Não consegui abrir este resultado agora. Tente novamente em instantes."
                    )
                except TelegramError:
                    pass
            else:
                await message.reply_text(
                    "Não consegui abrir este resultado agora. Tente novamente em instantes."
                )

    async def inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._record_user(update)
        inline = update.inline_query
        if not inline:
            return
        query = " ".join((inline.query or "").strip().lower().split())

        state_request = parse_state_inline_query(query)
        if state_request:
            scope, office, page = state_request
            try:
                result, _ = await self.results.fetch(scope, office=office, force=True)
                pages = self._state_page_count(result)
                page = min(max(0, page), pages - 1)
                rich_html = self._state_office_rich(
                    result,
                    office=office,
                    page=page,
                    shared=True,
                )
                place = UF_NAMES.get(scope, scope.upper())
                office_name = state_office_label(scope, office)
                status = (
                    "Aguardando início da apuração oficial"
                    if is_pre_election(result)
                    else f"{result.sections_counted_pct:.2f}% das seções totalizadas".replace(".", ",")
                )
                await answer_inline_rich_query(
                    token=self.settings.telegram_bot_token,
                    inline_query_id=inline.id,
                    rich_html=rich_html,
                    result_id=f"estado-{scope}-{office}-p{page}-2026",
                    title=f"{office_name} • {place}",
                    description=f"Página {page + 1}/{pages} • {status}",
                    thumbnail_url=inline_flag_url(scope),
                )
            except Exception:
                log.exception("Falha respondendo inline de estado/cargo")
                try:
                    await inline.answer([], cache_time=1)
                except TelegramError:
                    pass
            return

        if query in {"presidente", "presidente br", "br"}:
            try:
                result, _ = await self.results.fetch("br", force=True)
                rich_html = self._president_rich(result, shared=True)
                status = (
                    "Aguardando início da apuração oficial"
                    if is_pre_election(result)
                    else f"{result.sections_counted_pct:.2f}% das seções totalizadas".replace(".", ",")
                )
                await answer_inline_rich_query(
                    token=self.settings.telegram_bot_token,
                    inline_query_id=inline.id,
                    rich_html=rich_html,
                    result_id="presidente-br-2026",
                    title="Presidente • Brasil",
                    description=f"Apuração oficial 2026 • {status}",
                    thumbnail_url=inline_flag_url("br"),
                )
            except Exception:
                log.exception("Falha respondendo inline da apuração presidencial")
                try:
                    await inline.answer([], cache_time=1)
                except TelegramError:
                    pass
            return

        state_options = inline_state_options(query)
        if query == "" or state_options:
            rich_results: list[dict[str, str]] = []

            # With an empty query, President stays fixed as the first result.
            if query == "":
                try:
                    result, _ = await self.results.fetch("br", force=True)
                    status = (
                        "Aguardando início da apuração oficial"
                        if is_pre_election(result)
                        else f"{result.sections_counted_pct:.2f}% das seções totalizadas".replace(".", ",")
                    )
                    rich_results.append({
                        "rich_html": self._president_rich(result, shared=True),
                        "result_id": "presidente-br-2026",
                        "title": "Presidente • Brasil",
                        "description": f"Apuração oficial 2026 • {status}",
                        "thumbnail_url": inline_flag_url("br"),
                    })
                except Exception:
                    log.exception("Falha montando Presidente no catálogo inline")

            for scope, place, _filename in state_options:
                rich_results.append({
                    "rich_html": build_state_inline_menu_rich_html(scope),
                    "result_id": f"estado-{scope}-menu-2026",
                    "title": place,
                    "description": "Escolha o cargo • Eleições 2026",
                    "thumbnail_url": inline_flag_url(scope),
                })

            if rich_results:
                try:
                    await answer_inline_rich_results(
                        token=self.settings.telegram_bot_token,
                        inline_query_id=inline.id,
                        results=rich_results,
                        cache_time=0,
                        is_personal=False,
                    )
                except Exception:
                    log.exception("Falha respondendo catálogo inline de estados")
                    try:
                        await inline.answer([], cache_time=1)
                    except TelegramError:
                        pass
                return

        await inline.answer([], cache_time=1)

    async def _send_result(self, message, scope: str, edit: bool = False) -> None:
        try:
            result, _ = await self.results.fetch(scope)
            text = format_result(result, self.settings)
            external_chat = getattr(getattr(message, "chat", None), "type", "private") != "private"
            if edit:
                await message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=result_keyboard(self.settings, scope=scope, external_chat=external_chat),
                )
            else:
                await message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=result_keyboard(self.settings, scope=scope, external_chat=external_chat),
                )
        except Exception as exc:
            log.exception("Erro consultando TSE")
            text = f"Não consegui consultar os dados do TSE agora.\n<code>{type(exc).__name__}</code>"
            if edit:
                try:
                    await message.edit_text(text, parse_mode=ParseMode.HTML)
                except BadRequest:
                    pass
            else:
                await message.reply_text(text, parse_mode=ParseMode.HTML)

    async def resultado(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        await self._send_president_rich(update.effective_message)

    async def estado(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        if not context.args:
            await update.effective_message.reply_text("Use /estado MS, /estado SP, /estado RJ etc.")
            return
        uf = context.args[0].lower().strip()
        if uf not in VALID_UFS:
            await update.effective_message.reply_text("UF inválida. Exemplo: /estado MS")
            return
        await self._send_result(update.effective_message, uf)

    async def acompanhar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        if not await self._guard_live_control(update):
            return
        scope = "br"
        if context.args and context.args[0].lower() in VALID_UFS:
            scope = context.args[0].lower()
        result, _ = await self.results.fetch(scope)
        sent = await update.effective_message.reply_text(
            format_result(result, self.settings),
            parse_mode=ParseMode.HTML,
            reply_markup=result_keyboard(
                self.settings,
                subscribed=True,
                scope=scope,
                external_chat=update.effective_chat.type != "private",
            ),
        )
        await self.storage.upsert_live(update.effective_chat.id, sent.message_id, scope, result.sections_counted_pct)
        try:
            if update.effective_chat.type in {"group", "supergroup"}:
                await context.bot.pin_chat_message(update.effective_chat.id, sent.message_id, disable_notification=True)
        except TelegramError:
            pass
        await update.effective_message.reply_text(
            f"Atualização automática ativada para {UF_NAMES.get(scope, scope.upper())}. Vou editar a mensagem acima quando o TSE publicar novos dados."
        )

    async def parar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_live_control(update):
            return
        await self.storage.disable_live(update.effective_chat.id)
        await update.effective_message.reply_text("Atualização automática interrompida neste chat.")


    async def alertas(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        if not await self._guard_live_control(update):
            return
        if not context.args or context.args[0].lower() not in {"on", "off"}:
            await update.effective_message.reply_text("Use /alertas on ou /alertas off. Os alertas são enviados quando a totalização cruza 10%, 25%, 50%, 75%, 90%, 95%, 99% e 100%.")
            return
        enabled = context.args[0].lower() == "on"
        await self.storage.set_alerts(update.effective_chat.id, enabled)
        await update.effective_message.reply_text("Alertas de marcos ativados." if enabled else "Alertas de marcos desativados.")

    async def fonte(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        await update.effective_message.reply_text(
            f"<b>Fonte:</b> {self.settings.source_label}\n{self.settings.public_results_url}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        try:
            result, _ = await self.results.fetch("br")
            if is_pre_election(result):
                await update.effective_message.reply_text(
                    "<b>TSE configurado no ambiente oficial.</b>\n"
                    "A apuração ainda não foi publicada. As candidaturas reais já estão carregadas com <b>0 votos e 0%</b>.",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.effective_message.reply_text(
                    f"TSE acessível. Geração <code>{result.generation_id or '-'}</code>, atualização {result.totalization_time or '-'}, seções {result.sections_counted_pct:.2f}%.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as exc:
            await update.effective_message.reply_text(f"Falha ao consultar TSE: {type(exc).__name__}")

    async def pesquisas(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        await update.effective_message.reply_text(
            "<b>Pesquisas eleitorais 2026</b>\n\n"
            "Consulte pesquisas publicadas no especial do G1 para Presidente, Governador e Senador. "
            "Os dados são exibidos com instituto, data, margem de erro e registro no TSE quando disponíveis.\n\n"
            "<i>Pesquisa de intenção de voto não é apuração nem previsão de resultado.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=polls_menu_keyboard(
                self.settings,
                external_chat=update.effective_chat.type != "private",
            ),
        )

    def _poll_args(self, args: list[str]) -> tuple[str, str, str | None]:
        office = (args[0] if args else "presidente").lower().strip()
        if office not in {"presidente", "governador", "senador"}:
            raise ValueError("Cargo inválido. Use presidente, governador ou senador.")

        institute: str | None = None
        lowered = [x.lower().strip() for x in args[1:]]
        for candidate in lowered:
            if candidate in {"datafolha", "quaest"}:
                institute = candidate

        if office == "presidente":
            scope = next((x for x in lowered if x == "br" or x in VALID_UFS), "br")
            if institute is None:
                institute = "datafolha"
            return office, scope, institute

        scope = next((x for x in lowered if x in VALID_UFS), "")
        if not scope:
            raise ValueError(
                f"Informe a UF. Exemplo: /pesquisa {office} MS"
            )
        return office, scope, institute

    async def _send_poll(
        self,
        message,
        office: str,
        scope: str,
        institute: str | None,
        *,
        edit: bool = False,
        history: bool = False,
    ) -> None:
        try:
            poll = await self.g1_polls.fetch(office, scope, 1, institute)
            text = format_g1_history(poll) if history else format_g1_poll(poll)
            external_chat = getattr(getattr(message, "chat", None), "type", "private") != "private"
            markup = poll_keyboard(self.settings, poll, external_chat=external_chat)
            if edit:
                await message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
            else:
                await message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
        except Exception as exc:
            log.exception("Erro consultando pesquisa G1")
            text = (
                "Não consegui consultar essa pesquisa no G1 agora. "
                "A página pode não ter pesquisa disponível para esse cargo/UF/instituto.\n"
                f"<code>{type(exc).__name__}</code>"
            )
            if edit:
                try:
                    await message.edit_text(text, parse_mode=ParseMode.HTML)
                except BadRequest:
                    pass
            else:
                await message.reply_text(text, parse_mode=ParseMode.HTML)

    async def pesquisa(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        try:
            office, scope, institute = self._poll_args(context.args)
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"{exc}\n\n"
                "Exemplos:\n"
                "/pesquisa presidente\n"
                "/pesquisa presidente quaest\n"
                "/pesquisa governador MS\n"
                "/pesquisa governador SP datafolha\n"
                "/pesquisa senador MS"
            )
            return
        await self._send_poll(update.effective_message, office, scope, institute)

    async def governador(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        if not context.args:
            await update.effective_message.reply_text("Use /governador MS ou /governador SP datafolha.")
            return
        scope = context.args[0].lower().strip()
        institute = context.args[1].lower().strip() if len(context.args) > 1 else None
        if scope not in VALID_UFS:
            await update.effective_message.reply_text("UF inválida. Exemplo: /governador MS")
            return
        await self._send_poll(update.effective_message, "governador", scope, institute)

    async def senador(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_required_channel(update, context):
            return
        if not context.args:
            await update.effective_message.reply_text("Use /senador MS ou /senador RJ datafolha.")
            return
        scope = context.args[0].lower().strip()
        institute = context.args[1].lower().strip() if len(context.args) > 1 else None
        if scope not in VALID_UFS:
            await update.effective_message.reply_text("UF inválida. Exemplo: /senador MS")
            return
        await self._send_poll(update.effective_message, "senador", scope, institute)

    async def publish_g1_poll(self, poll: G1Poll, headline: str) -> bool:
        if not self.application or not self.settings.channel_id:
            log.warning("Canal ou aplicação Telegram indisponível para publicar pesquisa.")
            return False

        # Bot API 10.3 Rich Messages: usa tabela nativa/colunas no canal.
        try:
            rich_html = build_g1_channel_rich_html(
                poll,
                headline=headline,
                panel_url=bot_panel_deep_link(self.settings),
            )
            await send_rich_html(
                token=self.settings.telegram_bot_token,
                chat_id=self.settings.channel_id,
                rich_html=rich_html,
                disable_notification=False,
            )
            log.info("Pesquisa publicada como Rich Message no canal %s", self.settings.channel_id)
            return True
        except Exception:
            # Fallback compatível caso a API Rich esteja temporariamente indisponível.
            log.exception("Falha no Rich Message; usando mensagem clássica")
            try:
                await self.application.bot.send_message(
                    chat_id=self.settings.channel_id,
                    text=format_g1_poll(poll, headline=headline),
                    parse_mode=ParseMode.HTML,
                    reply_markup=poll_keyboard(self.settings, poll, external_chat=True),
                    disable_web_page_preview=True,
                )
                return True
            except TelegramError:
                log.exception("Falha publicando pesquisa no canal %s", self.settings.channel_id)
                return False

    async def boletim(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id if update.effective_user else 0
        if self.settings.admin_ids and user_id not in self.settings.admin_ids:
            await update.effective_message.reply_text("Comando restrito aos administradores configurados.")
            return
        try:
            poll = await self.g1_polls.fetch("presidente", "br", 1, "datafolha", force=True)
            ok = await self.publish_g1_poll(
                poll,
                headline="BOLETIM • ÚLTIMA PESQUISA DISPONÍVEL",
            )
            await update.effective_message.reply_text(
                "Boletim enviado ao canal." if ok else "Não consegui enviar o boletim ao canal."
            )
        except Exception as exc:
            log.exception("Falha preparando boletim G1")
            await update.effective_message.reply_text(f"Falha ao preparar boletim: {type(exc).__name__}")

    async def publicar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id if update.effective_user else 0
        if not self.settings.admin_ids:
            await update.effective_message.reply_text("O comando /publicar está desativado até ADMIN_IDS ser configurado no .env.")
            return
        if user_id not in self.settings.admin_ids:
            await update.effective_message.reply_text("Comando restrito aos administradores configurados.")
            return
        target = context.args[0] if context.args else self.settings.channel_id
        if not target:
            await update.effective_message.reply_text("Configure CHANNEL_ID ou use /publicar @SeuCanal / -1001234567890")
            return
        try:
            chat_id: str | int = int(target) if target.lstrip("-").isdigit() else target
            result, _ = await self.results.fetch("br")
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=format_result(result, self.settings),
                parse_mode=ParseMode.HTML,
                reply_markup=result_keyboard(self.settings, subscribed=True, scope="br", external_chat=True),
            )
            await self.storage.upsert_live(sent.chat_id, sent.message_id, "br", result.sections_counted_pct)
            try:
                await context.bot.pin_chat_message(sent.chat_id, sent.message_id, disable_notification=True)
            except TelegramError:
                pass
            await update.effective_message.reply_text("Mensagem de apuração publicada e vinculada para atualização automática.")
        except TelegramError as exc:
            await update.effective_message.reply_text(f"Não consegui publicar no destino: {exc}")

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._record_user(update)
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        if data == "verify_subscription":
            if await self._is_required_channel_member(update, context):
                await query.answer("Inscrição confirmada.")
                try:
                    await query.edit_message_text(
                        "<b>Inscrição confirmada.</b>\n\nAcesso ao bot liberado. Use /resultado para acompanhar a apuração.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=result_keyboard(self.settings),
                    )
                except BadRequest:
                    pass
            else:
                await query.answer("Ainda não encontrei sua inscrição em @ResultadoEleicoes.", show_alert=True)
            return
        await query.answer()
        if data == "president:open:br":
            if not await self._guard_required_channel(update, context):
                return
            if query.message:
                await self._send_president_rich(query.message, edit=True)
            return
        if data == "president:refresh:br":
            # Shared inline messages must stay refreshable inside groups/private chats,
            # even when the person pressing the button has never opened the bot.
            try:
                result, _ = await self.results.fetch("br", force=True)
                if query.inline_message_id:
                    await edit_rich_html(
                        token=self.settings.telegram_bot_token,
                        inline_message_id=query.inline_message_id,
                        rich_html=self._president_rich(result, shared=True),
                    )
                elif query.message:
                    external_chat = query.message.chat.type != "private"
                    await edit_rich_html(
                        token=self.settings.telegram_bot_token,
                        chat_id=query.message.chat_id,
                        message_id=query.message.message_id,
                        rich_html=self._president_rich(
                            result,
                            external_chat=external_chat,
                        ),
                    )
            except RichMessageError as exc:
                log.warning("Não foi possível atualizar Rich Message presidencial: %s", exc)
            return
        if data.startswith("state:open:"):
            if not await self._guard_required_channel(update, context):
                return
            _, _, scope, office, raw_page = data.split(":", 4)
            if scope not in VALID_UFS or office not in STATE_OFFICE_LABELS:
                return
            page = int(raw_page)
            if query.inline_message_id:
                try:
                    result, _ = await self.results.fetch(scope, office=office)
                    await edit_rich_html(
                        token=self.settings.telegram_bot_token,
                        inline_message_id=query.inline_message_id,
                        rich_html=self._state_office_rich(
                            result,
                            office=office,
                            page=page,
                            shared=True,
                        ),
                    )
                except Exception:
                    log.exception(
                        "Falha abrindo cargo em mensagem inline: %s/%s",
                        scope,
                        office,
                    )
            elif query.message:
                await self._send_state_office_rich(
                    query.message,
                    scope=scope,
                    office=office,
                    page=page,
                    edit=True,
                )
            return
        if data.startswith("state:view:") or data.startswith("state:refresh:"):
            _, action, scope, office, raw_page = data.split(":", 4)
            if scope not in VALID_UFS or office not in STATE_OFFICE_LABELS:
                return
            page = int(raw_page)
            force = action == "refresh"
            try:
                result, _ = await self.results.fetch(scope, office=office, force=force)
                if query.inline_message_id:
                    await edit_rich_html(
                        token=self.settings.telegram_bot_token,
                        inline_message_id=query.inline_message_id,
                        rich_html=self._state_office_rich(
                            result,
                            office=office,
                            page=page,
                            shared=True,
                        ),
                    )
                elif query.message:
                    await edit_rich_html(
                        token=self.settings.telegram_bot_token,
                        chat_id=query.message.chat_id,
                        message_id=query.message.message_id,
                        rich_html=self._state_office_rich(
                            result,
                            office=office,
                            page=page,
                            external_chat=query.message.chat.type != "private",
                        ),
                    )
            except RichMessageError as exc:
                log.warning("Não foi possível atualizar Rich Message de estado: %s", exc)
            return
        if data.startswith("state:") and data.count(":") == 1:
            scope = data.split(":", 1)[1]
            if scope not in VALID_UFS:
                return
            if query.inline_message_id:
                try:
                    await edit_rich_html(
                        token=self.settings.telegram_bot_token,
                        inline_message_id=query.inline_message_id,
                        rich_html=build_state_inline_menu_rich_html(scope),
                    )
                except RichMessageError as exc:
                    log.warning("Não foi possível voltar ao menu inline do estado: %s", exc)
            elif query.message:
                await query.edit_message_text(
                    f"<b>🗺️ {UF_NAMES.get(scope, scope.upper())}</b>\n\n"
                    "Escolha o cargo que deseja acompanhar:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=state_office_keyboard(scope),
                )
            return
        if data == "start:states":
            if query.message:
                await query.edit_message_text(
                    "<b>🗺️ Resultados por estado</b>\n\n"
                    "Escolha uma UF para selecionar o cargo.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=states_keyboard(),
                )
            return
        if data == "start:home":
            if query.message:
                await query.edit_message_text(
                    start_message_text(self.settings),
                    parse_mode=ParseMode.HTML,
                    reply_markup=start_keyboard(
                        self.settings,
                        external_chat=query.message.chat_id < 0,
                    ),
                )
            return
        if data.startswith("g1:"):
            if not await self._guard_required_channel(update, context):
                return
            _, office, scope, institute = data.split(":", 3)
            inst = None if institute == "auto" else institute
            if query.message:
                await self._send_poll(query.message, office, scope, inst, edit=True)
            return
        if data.startswith("g1h:"):
            if not await self._guard_required_channel(update, context):
                return
            _, office, scope, institute = data.split(":", 3)
            inst = None if institute in {"", "auto", "g1"} else institute
            if query.message:
                await self._send_poll(query.message, office, scope, inst, edit=True, history=True)
            return
        if data.startswith("result:"):
            if not await self._guard_required_channel(update, context):
                return
            scope = data.split(":", 1)[1]
            try:
                result, _ = await self.results.fetch(scope)
                await query.edit_message_text(
                    format_result(result, self.settings),
                    parse_mode=ParseMode.HTML,
                    reply_markup=result_keyboard(self.settings, scope=scope),
                )
            except BadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
            return
        if data.startswith("subscribe:"):
            if not await self._guard_required_channel(update, context):
                return
            if not await self._guard_live_control(update):
                return
            scope = data.split(":", 1)[1]
            if query.message:
                try:
                    result, _ = await self.results.fetch(scope)
                    await self.storage.upsert_live(query.message.chat_id, query.message.message_id, scope, result.sections_counted_pct)
                    await query.edit_message_text(
                        format_result(result, self.settings),
                        parse_mode=ParseMode.HTML,
                        reply_markup=result_keyboard(
                            self.settings,
                            subscribed=True,
                            scope=scope,
                            external_chat=query.message.chat_id < 0,
                        ),
                    )
                except BadRequest:
                    pass
            return
        if data.startswith("stop:"):
            if not await self._guard_live_control(update):
                return
            scope = data.split(":", 1)[1]
            if query.message:
                await self.storage.disable_live(query.message.chat_id)
                try:
                    await query.edit_message_reply_markup(
                        result_keyboard(
                            self.settings,
                            subscribed=False,
                            scope=scope,
                            external_chat=query.message.chat_id < 0,
                        )
                    )
                except BadRequest:
                    pass

    async def refresh_live_messages(self, scope: str, result) -> None:
        if not self.application:
            return
        for item in await self.storage.list_live():
            if item.scope != scope:
                continue
            try:
                try:
                    await self.application.bot.edit_message_text(
                        chat_id=item.chat_id,
                        message_id=item.message_id,
                        text=format_result(result, self.settings),
                        parse_mode=ParseMode.HTML,
                        reply_markup=result_keyboard(
                            self.settings,
                            subscribed=True,
                            scope=scope,
                            external_chat=item.chat_id < 0,
                        ),
                    )
                except BadRequest as exc:
                    if "message is not modified" not in str(exc).lower():
                        raise

                if item.alerts:
                    current = milestone_for(result.sections_counted_pct)
                    if current > item.last_milestone:
                        await self.application.bot.send_message(
                            chat_id=item.chat_id,
                            text=(
                                f"Marco de totalização: <b>{current}% das seções</b> no resultado "
                                f"de Presidente ({UF_NAMES.get(scope, scope.upper())}).\n"
                                f"Fonte: {self.settings.source_label}."
                            ),
                            parse_mode=ParseMode.HTML,
                            disable_notification=current < 100,
                        )
                        await self.storage.mark_milestone(item.chat_id, current)
            except (Forbidden, TelegramError) as exc:
                log.warning("Desativando chat %s após erro Telegram: %s", item.chat_id, exc)
                await self.storage.disable_live(item.chat_id)
