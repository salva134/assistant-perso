"""Démarrage du bot Telegram de Marcus. Lancer avec : python -m app.main"""

import asyncio
import datetime
import logging
import sys
from zoneinfo import ZoneInfo

import anthropic
import openai
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

from app import dates, llm, memory, usage
from app.config import load_config
from app.db import make_pool

logger = logging.getLogger("marcus")

TAILLE_HISTORIQUE = 20
LONGUEUR_MAX_TELEGRAM = 4096
MESSAGE_ERREUR = "Petit souci, réessaie dans un instant"


def is_authorized(update: Update, allowed_user_id: int) -> bool:
    user = update.effective_user
    return user is not None and user.id == allowed_user_id


def split_message(text: str, limit: int = LONGUEUR_MAX_TELEGRAM) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def parse_correction(args: str) -> tuple[str, str] | None:
    """« ancien -> nouveau » → (ancien, nouveau), ou None si la forme n'y est pas."""
    if "->" not in args:
        return None
    ancien, nouveau = (part.strip() for part in args.split("->", 1))
    return (ancien, nouveau) if ancien and nouveau else None


async def send_long(update: Update, text: str) -> None:
    for morceau in split_message(text):
        await update.message.reply_text(morceau)


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Liste blanche : passe avant tous les autres handlers et coupe le reste."""
    if not is_authorized(update, context.bot_data["config"].allowed_user_id):
        user = update.effective_user
        logger.info("Message ignoré de l'utilisateur %s", user.id if user else "inconnu")
        raise ApplicationHandlerStop


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Salut, c'est Marcus. Écris-moi ce que tu veux.")


async def learn_in_background(context, user_text, answer, source_id) -> None:
    data = context.bot_data
    pool = data["pool"]
    # Un apprentissage à la fois : deux messages rapprochés ne créent pas de doublon
    async with data["learn_lock"]:
        try:
            await memory.learn(
                pool,
                data["claude"],
                data["embed"],
                data["config"].model,
                user_text,
                answer,
                source_id,
                dates.format_day(dates.now(data["config"].timezone)),
                on_response=lambda r: usage.record_claude(pool, r),
            )
        except Exception:
            logger.exception("Échec de la mise à jour de la mémoire")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    config, pool = data["config"], data["pool"]
    user_text = update.message.text
    try:
        jour, _ = await usage.spent(pool)
        if jour >= config.daily_budget_usd:
            await update.message.reply_text(
                f"J'ai atteint le plafond de {usage.format_dollars(config.daily_budget_usd)}"
                " de dépense pour aujourd'hui. Je reprends demain."
            )
            return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        history = await memory.recent_messages(pool, TAILLE_HISTORIQUE)
        souvenirs = await memory.memories_for_prompt(pool, data["embed"], user_text)
        answer = await llm.reply(
            data["claude"],
            config.model,
            llm.build_system(data["system_prompt"], memory.format_for_prompt(souvenirs)),
            history,
            user_text,
            now_text=dates.format_now(dates.now(config.timezone)),
            on_response=lambda r: usage.record_claude(pool, r),
        )
        # L'échange n'entre en base qu'une fois la réponse obtenue
        source_id = await memory.save_message(pool, "user", user_text)
        await memory.save_message(pool, "assistant", answer)
    except Exception:
        logger.exception("Échec du traitement d'un message")
        await update.message.reply_text(MESSAGE_ERREUR)
        return
    await send_long(update, answer)
    context.application.create_task(
        learn_in_background(context, user_text, answer, source_id), update=update
    )


async def on_souvenirs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    souvenirs = await memory.all_memories(context.bot_data["pool"])
    if not souvenirs:
        await update.message.reply_text("Je n'ai encore aucun souvenir.")
        return
    await send_long(
        update,
        f"{len(souvenirs)} souvenir(s) :\n\n{memory.format_numbered(souvenirs)}\n\n"
        "Pour en effacer un : /oublie <numéro ou texte>\n"
        "Pour en corriger un : /corrige <numéro ou texte> -> <nouveau texte>",
    )


async def on_oublie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool = context.bot_data["pool"]
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Dis-moi quoi oublier : /oublie <numéro ou texte>")
        return
    trouves = await memory.find_memories(pool, query)
    if not trouves:
        await update.message.reply_text(f"Aucun souvenir ne correspond à « {query} ». Voir /souvenirs")
    elif len(trouves) > 1:
        await send_long(
            update,
            "Plusieurs souvenirs correspondent, donne le numéro :\n"
            + memory.format_numbered(trouves),
        )
    else:
        await memory.delete_memory(pool, trouves[0].id)
        await update.message.reply_text(f"Oublié : {trouves[0].content}")


async def on_corrige(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    correction = parse_correction(" ".join(context.args))
    if correction is None:
        await update.message.reply_text("Forme attendue : /corrige <numéro ou texte> -> <nouveau texte>")
        return
    ancien, nouveau = correction
    if memory.is_sensitive(nouveau):
        await update.message.reply_text("Je ne garde pas ce genre d'information (code, carte, mot de passe).")
        return
    trouves = await memory.find_memories(data["pool"], ancien)
    if not trouves:
        await update.message.reply_text(f"Aucun souvenir ne correspond à « {ancien} ». Voir /souvenirs")
    elif len(trouves) > 1:
        await send_long(
            update,
            "Plusieurs souvenirs correspondent, donne le numéro :\n"
            + memory.format_numbered(trouves),
        )
    else:
        await memory.update_memory(data["pool"], data["embed"], trouves[0].id, nouveau)
        await update.message.reply_text(f"Corrigé : {nouveau}")


async def on_couts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    jour, mois = await usage.spent(data["pool"])
    plafond = data["config"].daily_budget_usd
    await update.message.reply_text(
        f"Aujourd'hui : {usage.format_dollars(jour)} (plafond {usage.format_dollars(plafond)})\n"
        f"Ce mois-ci : {usage.format_dollars(mois)}"
    )


async def on_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Je ne lis que le texte pour l'instant.")


async def keep_database_awake(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Supabase gratuit se met en pause après 7 jours sans activité."""
    async with context.bot_data["pool"].connection() as conn:
        await conn.execute("select 1")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Erreur non gérée", exc_info=context.error)


COMMANDES = [
    ("souvenirs", "Ce que Marcus sait de toi"),
    ("oublie", "Effacer un souvenir : /oublie <numéro ou texte>"),
    ("corrige", "Corriger un souvenir : /corrige <ancien> -> <nouveau>"),
    ("couts", "Dépense du jour et du mois"),
]


async def post_init(app: Application) -> None:
    await app.bot_data["pool"].open(wait=True, timeout=15)
    # Menu « / » dans Telegram
    await app.bot.set_my_commands(COMMANDES)


async def post_shutdown(app: Application) -> None:
    await app.bot_data["pool"].close()


def build_application() -> Application:
    config = load_config()
    app = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    pool = make_pool(config.database_url)
    app.bot_data.update(
        config=config,
        pool=pool,
        claude=anthropic.AsyncAnthropic(api_key=config.anthropic_api_key, timeout=30.0),
        embed=memory.make_embedder(
            openai.AsyncOpenAI(api_key=config.openai_api_key, timeout=30.0), pool
        ),
        system_prompt=llm.load_system_prompt(),
        learn_lock=asyncio.Lock(),
    )
    app.add_handler(TypeHandler(Update, guard), group=-1)
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("souvenirs", on_souvenirs))
    app.add_handler(CommandHandler("oublie", on_oublie))
    app.add_handler(CommandHandler("corrige", on_corrige))
    app.add_handler(CommandHandler("couts", on_couts))
    app.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, on_text)
    )
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~filters.TEXT, on_unsupported))
    app.add_error_handler(on_error)
    app.job_queue.run_daily(
        keep_database_awake, time=datetime.time(4, 0, tzinfo=ZoneInfo(config.timezone))
    )
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s : %(message)s", level=logging.INFO
    )
    # httpx journalise chaque URL Telegram, et ces URL contiennent le token
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.platform == "win32":
        # psycopg en mode async ne fonctionne pas avec la boucle par défaut de Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app = build_application()
    logger.info("Marcus démarre (modèle %s)", app.bot_data["config"].model)
    # drop_pending_updates : on ne répond pas aux messages envoyés pendant que le bot était éteint
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
