"""Telegram glue for the Wallbit confirmation flow.

Mirror of ``whatsappbot/wallbit_handlers``: the channel-agnostic lifecycle
(preview text, execute, cancel, button-id parsing) lives in
``wallbit.confirmation_actions``. This module only handles the
python-telegram-bot-specific work — building the InlineKeyboardMarkup
and ack'ing the CallbackQuery.
"""
from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import sync_to_async
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from users.models import User
from wallbit.confirmation_actions import (
    CANCEL_PREFIX,
    CONFIRM_PREFIX,
    handle_button_press,
    summary_text,
)

logger = logging.getLogger(__name__)


def _build_keyboard(decision_id: int, two_step: bool) -> InlineKeyboardMarkup:
    confirm_label = "✅ Confirmar 2x" if two_step else "✅ Confirmar"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    confirm_label,
                    callback_data=f"{CONFIRM_PREFIX}{decision_id}",
                ),
                InlineKeyboardButton(
                    "🚫 Cancelar",
                    callback_data=f"{CANCEL_PREFIX}{decision_id}",
                ),
            ]
        ]
    )


async def send_confirmation_buttons(
    bot: Bot,
    chat_id: int | str,
    decision_id: int,
    preview: dict[str, Any],
    two_step: bool = False,
) -> bool:
    """Send the confirmation prompt to a Telegram chat using the Bot instance."""
    body_text = summary_text(preview, two_step)
    keyboard = _build_keyboard(decision_id, two_step)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=body_text[:4096],
            reply_markup=keyboard,
        )
    except TelegramError as exc:
        logger.warning("telegram send_confirmation_buttons failed: %s", exc)
        return False
    logger.info(
        "Sent telegram confirmation buttons for decision %s to chat %s",
        decision_id, chat_id,
    )
    return True


@sync_to_async
def _resolve_user_and_dispatch(callback_data: str, telegram_chat_id: str) -> str:
    """Synchronous DB lookup wrapped for the async handler."""
    user = User.objects.filter(
        platform="telegram", external_id=telegram_chat_id
    ).first()
    if user is None:
        return "No reconocí tu cuenta en Tresqu."
    return handle_button_press(callback_data, user)


async def wallbit_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE  # noqa: ARG001
) -> None:
    """python-telegram-bot CallbackQueryHandler for ``wallbit_*`` callbacks.

    Registered with ``pattern=r"^wallbit_(confirm|cancel)_"`` so it only
    fires for our own buttons (the generic ``debug_callback`` keeps the
    others).
    """
    query = update.callback_query
    if not query or not query.data:
        return
    # Always answer to clear Telegram's "loading" state on the button.
    try:
        await query.answer()
    except TelegramError:
        pass

    telegram_chat_id = str(query.from_user.id) if query.from_user else ""
    if not telegram_chat_id:
        return

    reply = await _resolve_user_and_dispatch(query.data, telegram_chat_id)
    if not reply:
        return

    try:
        # Drop the keyboard from the original message so the user can't
        # double-tap, then send the textual outcome as a new message.
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    try:
        await query.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
    except TelegramError as exc:
        logger.warning("telegram callback reply failed: %s", exc)


__all__ = [
    "send_confirmation_buttons",
    "wallbit_callback_handler",
]
