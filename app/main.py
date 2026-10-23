"""Démarrage du bot Telegram de Marcus. Lancer avec : python -m app.main"""

import logging
from collections import deque

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app import llm
from app.config import load_config

logger = logging.getLogger("marcus")

TAILLE_HISTORIQUE = 20
LONGUEUR_MAX_TELEGRAM = 4096
MESSAGE_ERREUR = "Petit souci, réessaie dans un instant"


def is_authorized(update: Update, allowed_user_id: int) -> bool:
    user = update.effective_user
    return user is not None and user.id == allowed_user_id


def split_message(text: str, limit: int = LONGUEUR_MAX_TELEGRAM) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Liste blanche : passe avant tous les autres handlers et coupe le reste."""
    if not is_authorized(update, context.bot_data["config"].allowed_user_id):
        user = update.effective_user
        logger.info("Message ignoré de l'utilisateur %s", user.id if user else "inconnu")
        raise ApplicationHandlerStop


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Salut, c'est Marcus. Écris-moi ce que tu veux.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    user_text = update.message.text
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        answer = await llm.reply(
            data["claude"], data["config"].model, data["system_prompt"], data["history"], user_text
        )
    except Exception:
        logger.exception("Échec de l'appel à Claude")
        await update.message.reply_text(MESSAGE_ERREUR)
        return
    # L'échange n'entre dans l'historique qu'une fois la réponse obtenue
    data["history"].extend(
        [{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}]
    )
    for morceau in split_message(answer):
        await update.message.reply_text(morceau)


async def on_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Je ne lis que le texte pour l'instant.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Erreur non gérée", exc_info=context.error)


def build_application() -> Application:
    config = load_config()
    app = Application.builder().token(config.telegram_bot_token).build()
    app.bot_data.update(
        config=config,
        claude=anthropic.AsyncAnthropic(api_key=config.anthropic_api_key, timeout=30.0),
        system_prompt=llm.load_system_prompt(),
        history=deque(maxlen=TAILLE_HISTORIQUE),
    )
    app.add_handler(TypeHandler(Update, guard), group=-1)
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, on_text)
    )
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~filters.TEXT, on_unsupported))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s : %(message)s", level=logging.INFO
    )
    # httpx journalise chaque URL Telegram, et ces URL contiennent le token
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = build_application()
    logger.info("Marcus démarre (modèle %s)", app.bot_data["config"].model)
    # drop_pending_updates : on ne répond pas aux messages envoyés pendant que le bot était éteint
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
