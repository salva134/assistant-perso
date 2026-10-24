"""Démarrage du bot Telegram de Marcus. Lancer avec : python -m app.main"""

import asyncio
import base64
import datetime
import logging
import os
import sys
from collections.abc import Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import openai
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app import brief, calendar_google, calls, calls_bot, dates, hotline, llm, memory, reminders, usage, voice
from app.backup import backup
from app.config import load_config
from app.db import make_pool

logger = logging.getLogger("marcus")

TAILLE_HISTORIQUE = 20
LONGUEUR_MAX_TELEGRAM = 4096
MESSAGE_ERREUR = "Petit souci, réessaie dans un instant"
ALERTE_APRES_ECHECS = 3


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


async def record_failure(context: ContextTypes.DEFAULT_TYPE, ou: str) -> None:
    """Au 3e échec d'affilée (réponse, vocal ou mémoire), Hugo est prévenu à part."""
    data = context.bot_data
    data["echecs"] = data.get("echecs", 0) + 1
    if data["echecs"] == ALERTE_APRES_ECHECS:
        try:
            await context.bot.send_message(
                data["config"].allowed_user_id,
                f"⚠️ {ALERTE_APRES_ECHECS} échecs d'affilée (dernier : {ou}). "
                "Si ça continue, préviens Claude Code : les détails sont dans les journaux.",
            )
        except Exception:
            logger.exception("Impossible d'envoyer l'alerte")


def record_success(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["echecs"] = 0


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
            await record_failure(context, "mémoire")


async def over_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Prévient Hugo et renvoie True si le plafond du jour est atteint."""
    plafond = context.bot_data["config"].daily_budget_usd
    jour, _ = await usage.spent(context.bot_data["pool"])
    if jour < plafond:
        return False
    await update.message.reply_text(
        f"J'ai atteint le plafond de {usage.format_dollars(plafond)}"
        " de dépense pour aujourd'hui. Je reprends demain."
    )
    return True


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Pendant un appel, Marcus attend peut-être la réponse d'Hugo à une question
    if hotline.answer(context.bot_data, update.message.text):
        await update.message.reply_text(f"✅ Je lui réponds : « {update.message.text.strip()} ».")
        return
    try:
        if await over_budget(update, context):
            return
    except Exception:
        logger.exception("Impossible de lire la dépense du jour")
        await update.message.reply_text(MESSAGE_ERREUR)
        return
    await answer(update, context, update.message.text)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    vocal = update.message.voice
    if vocal.duration > voice.DUREE_MAX_SECONDES:
        await update.message.reply_text("Ton vocal dépasse 5 minutes, je ne peux pas l'écouter. Coupe-le en plusieurs ?")
        return
    try:
        if await over_budget(update, context):
            return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        fichier = await context.bot.get_file(vocal.file_id)
        audio = bytes(await fichier.download_as_bytearray())
        texte = await voice.transcribe(data["openai"], audio)
        await usage.record_transcription(data["pool"], voice.TRANSCRIPTION_MODEL, vocal.duration)
    except Exception:
        logger.exception("Échec de la transcription d'un vocal")
        await update.message.reply_text(MESSAGE_ERREUR)
        await record_failure(context, "vocal")
        return
    if not texte:
        await update.message.reply_text("Je n'ai rien entendu dans ce vocal.")
        return
    if hotline.answer(data, texte):
        await update.message.reply_text(f"🎤 « {texte} »\n\n✅ Je lui réponds ça.")
        return
    # Hugo voit ce que Marcus a compris, puis la réponse
    await answer(update, context, texte, entete=f"🎤 « {texte} »\n\n")


async def keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Telegram efface « en train d'écrire… » au bout de 5 s : on le renvoie tant que Marcus
    travaille (une recherche web peut prendre 20 s)."""
    while True:
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:
            logger.debug("Indicateur de frappe non envoyé", exc_info=True)
        await asyncio.sleep(4)


async def answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    entete: str = "",
    images: Sequence[llm.Image] = (),
    texte_historique: str | None = None,
) -> None:
    """Répond à un message (écrit, transcrit ou photo) : souvenirs, Claude, base, apprentissage.

    texte_historique : ce qui est gardé en base à la place de user_text (« [Photo] … »)."""
    data = context.bot_data
    config, pool = data["config"], data["pool"]
    frappe = asyncio.create_task(keep_typing(context, update.effective_chat.id))
    try:
        history = await memory.recent_messages(pool, TAILLE_HISTORIQUE)
        souvenirs = await memory.memories_for_prompt(pool, data["embed"], user_text)
        reponse = await llm.reply(
            data["claude"],
            config.model,
            llm.build_system(data["system_prompt"], memory.format_for_prompt(souvenirs)),
            history,
            user_text,
            now_text=dates.format_now(dates.now(config.timezone)),
            on_response=lambda r: usage.record_claude(pool, r),
            toolbox=data["toolbox"],
            images=images,
        )
        # L'échange n'entre en base qu'une fois la réponse obtenue
        user_text = texte_historique or user_text
        source_id = await memory.save_message(pool, "user", user_text)
        await memory.save_message(pool, "assistant", reponse)
    except Exception:
        logger.exception("Échec du traitement d'un message")
        await update.message.reply_text(entete + MESSAGE_ERREUR)
        await record_failure(context, "réponse")
        return
    finally:
        frappe.cancel()
    record_success(context)
    await send_long(update, entete + reponse)
    context.application.create_task(
        learn_in_background(context, user_text, reponse, source_id), update=update
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


async def on_rappels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    rappels = await reminders.pending(data["pool"])
    texte = reminders.format_list(rappels, data["config"].timezone)
    if rappels:
        texte += "\n\nPour en annuler un, dis-moi simplement « annule le rappel n°… »."
    await send_long(update, texte)


async def morning_brief(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    tz = data["config"].timezone
    try:
        maintenant = dates.now(tz)
        du_jour = brief.todays(await reminders.pending(data["pool"]), maintenant, tz)
        agenda_du_jour = None
        if data["agenda"] is not None:
            try:
                evenements = await data["agenda"].events_of_day(maintenant.date())
                agenda_du_jour = calendar_google.format_events(evenements, tz)
            except Exception:
                logger.exception("Agenda indisponible pour le point du matin")
                agenda_du_jour = "(agenda indisponible ce matin)"
        await context.bot.send_message(
            data["config"].allowed_user_id,
            brief.format_brief(maintenant, du_jour, tz, agenda_du_jour),
        )
    except Exception:
        logger.exception("Échec du point du matin")
        await record_failure(context, "point du matin")


TAILLE_MAX_IMAGE = 4_500_000  # l'API Claude refuse les images de plus de 5 Mo
QUESTION_PHOTO_PAR_DEFAUT = "Regarde cette photo et dis-moi ce qui est utile pour moi (en bref)."


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Photo (ou image envoyée en fichier) : Marcus la regarde, avec la légende éventuelle."""
    message = update.message
    if message.photo:
        fichier_tg, type_mime = message.photo[-1], "image/jpeg"  # la plus grande taille
    else:
        fichier_tg, type_mime = message.document, message.document.mime_type or "image/jpeg"
    if type_mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        await message.reply_text("Je lis les photos JPG, PNG, GIF ou WebP. Envoie-la plutôt en photo.")
        return
    if (fichier_tg.file_size or 0) > TAILLE_MAX_IMAGE:
        await message.reply_text("Cette image est trop lourde pour moi : envoie-la en photo plutôt qu'en fichier.")
        return
    try:
        if await over_budget(update, context):
            return
        fichier = await context.bot.get_file(fichier_tg.file_id)
        contenu = bytes(await fichier.download_as_bytearray())
    except Exception:
        logger.exception("Téléchargement de la photo impossible")
        await message.reply_text(MESSAGE_ERREUR)
        return
    legende = (message.caption or "").strip()
    image = llm.Image(base64.standard_b64encode(contenu).decode("ascii"), type_mime)
    await answer(
        update, context, legende or QUESTION_PHOTO_PAR_DEFAUT, images=[image],
        texte_historique=f"[Photo envoyée] {legende}".strip(),
    )


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Contact partagé : Marcus retient son numéro (ou met à jour l'ancien)."""
    data = context.bot_data
    contact = update.message.contact
    nom = " ".join(p for p in (contact.first_name, contact.last_name) if p).strip() or "ce contact"
    try:
        numero = calls.format_number(calls.normalize_number(contact.phone_number))
    except ValueError:
        numero = contact.phone_number
    souvenir = f"Le numéro de téléphone de {nom} est le {numero}."
    try:
        await memory.remember_contact(data["pool"], data["embed"], nom, souvenir)
        await memory.save_message(data["pool"], "user", f"[Contact partagé] {nom} : {numero}")
        reponse = f"📇 C'est noté : {nom}, {numero}. Dis-moi qui c'est pour toi (ta mère, un ami…) et je le retiendrai aussi."
        await memory.save_message(data["pool"], "assistant", reponse)
    except Exception:
        logger.exception("Contact non enregistré")
        await update.message.reply_text(MESSAGE_ERREUR)
        return
    await update.message.reply_text(reponse)


async def on_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Je comprends le texte, les vocaux, les photos et les contacts, pas encore les autres fichiers.")


async def keep_database_awake(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Supabase gratuit se met en pause après 7 jours sans activité."""
    async with context.bot_data["pool"].connection() as conn:
        await conn.execute("select 1")


async def weekly_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.bot_data["config"]
    try:
        resume = await asyncio.to_thread(backup, config.database_url, Path(os.environ["BACKUP_DIR"]))
        logger.info("Sauvegarde : %s", resume)
    except Exception:
        logger.exception("Échec de la sauvegarde hebdomadaire")
        await record_failure(context, "sauvegarde")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Erreur non gérée", exc_info=context.error)


COMMANDES = [
    ("souvenirs", "Ce que Marcus sait de toi"),
    ("oublie", "Effacer un souvenir : /oublie <numéro ou texte>"),
    ("corrige", "Corriger un souvenir : /corrige <ancien> -> <nouveau>"),
    ("couts", "Dépense du jour et du mois"),
    ("rappels", "Rappels en attente"),
]


async def post_init(app: Application) -> None:
    await app.bot_data["pool"].open(wait=True, timeout=15)
    # Les rappels vivent en base : on les replanifie à chaque démarrage
    nombre = await reminders.reload_all(app.bot_data["pool"], app.job_queue)
    logger.info("%d rappel(s) en attente replanifié(s)", nombre)
    if app.bot_data["config"].calls_enabled:
        logger.info("%d appel(s) en cours repris", await calls_bot.resume_following(app))
        # Ligne directe : Vapi peut poser une question à Hugo pendant un appel (tunnel Cloudflare)
        try:
            app.bot_data["ligne"] = await hotline.start(calls_bot.make_tool_answerer(app))
        except Exception:
            logger.exception("Ligne directe non ouverte")
        try:
            await calls_bot.refresh_inbound(app.bot_data)
            logger.info("Assistant d'accueil en place sur le numéro de Marcus")
        except Exception:
            logger.exception("Assistant d'accueil non mis en place")
    # Menu « / » dans Telegram
    await app.bot.set_my_commands(COMMANDES)


async def post_shutdown(app: Application) -> None:
    await hotline.stop(app.bot_data.get("ligne"))
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
    openai_client = openai.AsyncOpenAI(api_key=config.openai_api_key, timeout=60.0)
    app.bot_data.update(
        config=config,
        pool=pool,
        claude=anthropic.AsyncAnthropic(api_key=config.anthropic_api_key, timeout=30.0),
        openai=openai_client,
        embed=memory.make_embedder(openai_client, pool),
        system_prompt=llm.load_system_prompt(),
        learn_lock=asyncio.Lock(),
    )
    toolbox = llm.Toolbox()
    # Recherche web chez Anthropic : trouver le numéro d'un restaurant, des horaires…
    toolbox.add_server_tool({
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 3,
        "user_location": {
            "type": "approximate",
            "city": config.home_city,
            "country": "FR",
            "timezone": config.timezone,
        },
    })
    reminders.add_tools(toolbox, pool, app.job_queue, config.timezone)
    agenda = None
    if config.google_enabled:
        agenda = calendar_google.GoogleCalendar(
            config.google_client_id,
            config.google_client_secret,
            config.google_refresh_token,
            config.timezone,
        )
        calendar_google.add_tools(toolbox, agenda)
    if config.calls_enabled:
        app.bot_data["vapi"] = calls.VapiClient(config.vapi_api_key, config.vapi_phone_number_id)
        calls.add_tools(toolbox, pool, calls_bot.make_on_prepared(app))
        app.add_handler(CallbackQueryHandler(calls_bot.on_button, pattern=r"^appel:"))
        app.add_handler(CallbackQueryHandler(calls_bot.on_question_button, pattern=r"^question:"))
    app.bot_data.update(toolbox=toolbox, agenda=agenda)
    app.add_handler(TypeHandler(Update, guard), group=-1)
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("souvenirs", on_souvenirs))
    app.add_handler(CommandHandler("oublie", on_oublie))
    app.add_handler(CommandHandler("corrige", on_corrige))
    app.add_handler(CommandHandler("couts", on_couts))
    app.add_handler(CommandHandler("rappels", on_rappels))
    app.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, on_text)
    )
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.VOICE, on_voice))
    app.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & (filters.PHOTO | filters.Document.IMAGE), on_photo)
    )
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.CONTACT, on_contact))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~filters.TEXT, on_unsupported))
    app.add_error_handler(on_error)
    fuseau = ZoneInfo(config.timezone)
    app.job_queue.run_daily(keep_database_awake, time=datetime.time(4, 0, tzinfo=fuseau))
    app.job_queue.run_daily(morning_brief, time=datetime.time(7, 30, tzinfo=fuseau))
    if config.calls_enabled:
        app.job_queue.run_daily(calls_bot.purge_job, time=datetime.time(4, 15, tzinfo=fuseau))
        # Appels reçus : vérifiés chaque minute ; accueil remis à jour chaque heure (mémoire qui évolue)
        app.job_queue.run_repeating(calls_bot.inbound_job, interval=60, first=30)
        app.job_queue.run_repeating(calls_bot.refresh_inbound_job, interval=3600, first=3600)
    # Sur le PC, Marcus fait lui-même sa sauvegarde du dimanche (sur un serveur, c'est systemd)
    if os.environ.get("BACKUP_DIR"):
        # days : 0 = dimanche pour python-telegram-bot
        app.job_queue.run_daily(weekly_backup, time=datetime.time(3, 30, tzinfo=fuseau), days=(0,))
    return app


def setup_logging() -> None:
    format_ = "%(asctime)s %(levelname)s %(name)s : %(message)s"
    fichier = os.environ.get("LOG_FILE")
    if fichier:
        # Lancé sans fenêtre (tâche Windows) : journal dans un fichier, 3 × 5 Mo au maximum
        handler = RotatingFileHandler(fichier, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        logging.basicConfig(format=format_, level=logging.INFO, handlers=[handler])
    else:
        logging.basicConfig(format=format_, level=logging.INFO)


def main() -> None:
    setup_logging()
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
