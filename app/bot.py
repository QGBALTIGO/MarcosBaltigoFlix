from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .config import Settings
from .formatter import UF_NAMES, format_result
from .storage import Storage, milestone_for
from .tse import TSEClient, VALID_UFS

log = logging.getLogger(__name__)


def result_keyboard(settings: Settings, subscribed: bool = False, scope: str = "br") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Atualizar", callback_data=f"result:{scope}")],
        [InlineKeyboardButton("Mato Grosso do Sul", callback_data="result:ms"), InlineKeyboardButton("São Paulo", callback_data="result:sp")],
        [InlineKeyboardButton("Rio de Janeiro", callback_data="result:rj"), InlineKeyboardButton("Minas Gerais", callback_data="result:mg")],
        [InlineKeyboardButton("Parar atualização" if subscribed else "Acompanhar aqui", callback_data=f"stop:{scope}" if subscribed else f"subscribe:{scope}")],
    ]
    if settings.webapp_url:
        rows.append([InlineKeyboardButton("Painel ao vivo", web_app=WebAppInfo(settings.webapp_url))])
    rows.append([InlineKeyboardButton("Abrir resultados do TSE", url=settings.public_results_url)])
    return InlineKeyboardMarkup(rows)


class ElectionBot:
    def __init__(self, settings: Settings, tse: TSEClient, storage: Storage):
        self.settings = settings
        self.tse = tse
        self.storage = storage
        self.application: Application | None = None

    def _is_configured_admin(self, update: Update) -> bool:
        if not self.settings.admin_ids:
            return True
        user_id = update.effective_user.id if update.effective_user else 0
        return user_id in self.settings.admin_ids

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
        app.add_handler(CallbackQueryHandler(self.callback))
        self.application = app
        return app

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        intro = (
            "<b>Eleições 2026 • Apuração</b>\n\n"
            "Acompanhe os resultados para Presidente usando dados publicados pelo Tribunal Superior Eleitoral. "
            "O bot apenas reproduz os dados oficiais; não faz projeções.\n\n"
            "<b>Comandos</b>\n"
            "/resultado — Brasil\n"
            "/estado MS — resultado presidencial em uma UF\n"
            "/acompanhar — mantém uma mensagem deste chat atualizada\n"
            "/parar — interrompe a atualização automática\n"
            "/alertas on|off — alertas de marcos de totalização\n"
            "/fonte — abre a fonte oficial"
        )
        if self.settings.is_simulation:
            intro += "\n\n⚠️ <b>O bot está em modo SIMULAÇÃO.</b> Os números atuais não são votos reais."
        await update.effective_message.reply_text(intro, parse_mode=ParseMode.HTML, reply_markup=result_keyboard(self.settings))

    async def _send_result(self, message, scope: str, edit: bool = False) -> None:
        try:
            result, _ = await self.tse.fetch(scope)
            text = format_result(result, self.settings)
            if edit:
                await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=result_keyboard(self.settings, scope=scope))
            else:
                await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=result_keyboard(self.settings, scope=scope))
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
        await self._send_result(update.effective_message, "br")

    async def estado(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_message.reply_text("Use /estado MS, /estado SP, /estado RJ etc.")
            return
        uf = context.args[0].lower().strip()
        if uf not in VALID_UFS:
            await update.effective_message.reply_text("UF inválida. Exemplo: /estado MS")
            return
        await self._send_result(update.effective_message, uf)

    async def acompanhar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard_live_control(update):
            return
        scope = "br"
        if context.args and context.args[0].lower() in VALID_UFS:
            scope = context.args[0].lower()
        result, _ = await self.tse.fetch(scope)
        sent = await update.effective_message.reply_text(
            format_result(result, self.settings),
            parse_mode=ParseMode.HTML,
            reply_markup=result_keyboard(self.settings, subscribed=True, scope=scope),
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
        if not await self._guard_live_control(update):
            return
        if not context.args or context.args[0].lower() not in {"on", "off"}:
            await update.effective_message.reply_text("Use /alertas on ou /alertas off. Os alertas são enviados quando a totalização cruza 10%, 25%, 50%, 75%, 90%, 95%, 99% e 100%.")
            return
        enabled = context.args[0].lower() == "on"
        await self.storage.set_alerts(update.effective_chat.id, enabled)
        await update.effective_message.reply_text("Alertas de marcos ativados." if enabled else "Alertas de marcos desativados.")

    async def fonte(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            f"<b>Fonte:</b> {self.settings.source_label}\n{self.settings.public_results_url}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            result, _ = await self.tse.fetch("br")
            await update.effective_message.reply_text(
                f"TSE acessível. Geração <code>{result.generation_id or '-'}</code>, atualização {result.totalization_time or '-'}, seções {result.sections_counted_pct:.2f}%.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            await update.effective_message.reply_text(f"Falha ao consultar TSE: {type(exc).__name__}")

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
            result, _ = await self.tse.fetch("br")
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=format_result(result, self.settings),
                parse_mode=ParseMode.HTML,
                reply_markup=result_keyboard(self.settings, subscribed=True, scope="br"),
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
        query = update.callback_query
        if not query:
            return
        await query.answer()
        data = query.data or ""
        if data.startswith("result:"):
            scope = data.split(":", 1)[1]
            try:
                result, _ = await self.tse.fetch(scope)
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
            if not await self._guard_live_control(update):
                return
            scope = data.split(":", 1)[1]
            if query.message:
                try:
                    result, _ = await self.tse.fetch(scope)
                    await self.storage.upsert_live(query.message.chat_id, query.message.message_id, scope, result.sections_counted_pct)
                    await query.edit_message_text(
                        format_result(result, self.settings),
                        parse_mode=ParseMode.HTML,
                        reply_markup=result_keyboard(self.settings, subscribed=True, scope=scope),
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
                    await query.edit_message_reply_markup(result_keyboard(self.settings, subscribed=False, scope=scope))
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
                        reply_markup=result_keyboard(self.settings, subscribed=True, scope=scope),
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
