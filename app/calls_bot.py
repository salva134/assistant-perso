"""Côté Telegram des appels : récapitulatif avec boutons, lancement, suivi et compte rendu."""

import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from app import calls, memory, usage

logger = logging.getLogger(__name__)

# Au-delà, on arrête de suivre un appel qui ne se termine pas (Vapi coupe à 5 minutes)
SUIVI_MAX = timedelta(minutes=15)


def buttons(call_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Appeler", callback_data=f"appel:oui:{call_id}"),
            InlineKeyboardButton("❌ Annuler", callback_data=f"appel:non:{call_id}"),
        ]]
    )


def make_on_prepared(app: Application):
    async def on_prepared(appel: calls.Appel) -> None:
        await app.bot.send_message(
            app.bot_data["config"].allowed_user_id, calls.format_recap(appel), reply_markup=buttons(appel.id)
        )

    return on_prepared


def parse_callback(data: str) -> tuple[str, int] | None:
    """« appel:oui:12 » → (« oui », 12)."""
    parties = data.split(":")
    if len(parties) != 3 or parties[0] != "appel" or parties[1] not in ("oui", "non") or not parties[2].isdigit():
        return None
    return parties[1], int(parties[2])


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    choix = parse_callback(query.data or "")
    if choix is None:
        await query.answer()
        return
    reponse, call_id = choix
    data = context.bot_data
    pool = data["pool"]
    appel = await calls.get(pool, call_id)
    if appel is None or appel.status != "en_attente":
        await query.answer("Cet appel a déjà été traité.")
        await query.edit_message_reply_markup(None)
        return
    recap = calls.format_recap(appel)

    if reponse == "non":
        await calls.set_status(pool, call_id, "annule")
        await query.answer("Appel annulé")
        await query.edit_message_text(f"{recap}\n\n❌ Annulé.")
        return
    if calls.is_expired(appel):
        await calls.set_status(pool, call_id, "annule")
        await query.answer("Trop tard")
        await query.edit_message_text(f"{recap}\n\n⌛ Récapitulatif expiré (10 minutes) : redemande-moi l'appel.")
        return

    refus = await refusal_reason(context)
    if refus:
        await query.answer(refus, show_alert=True)
        return

    await query.answer("J'appelle…")
    try:
        # Toute la mémoire d'Hugo accompagne l'appel : Marcus peut répondre et improviser
        memoire = memory.format_for_prompt(await memory.all_memories(pool))
        vapi_id = await data["vapi"].start(appel, memoire)
    except Exception:
        logger.exception("Lancement de l'appel n°%s impossible", call_id)
        await calls.set_status(pool, call_id, "echec")
        await query.edit_message_text(f"{recap}\n\n⚠️ L'appel n'a pas pu partir. Réessaie dans un instant.")
        return
    await calls.set_status(pool, call_id, "en_cours", vapi_id)
    await query.edit_message_text(f"{recap}\n\n📞 Appel en cours… Je t'envoie le compte rendu à la fin.")
    follow(context.job_queue, call_id)


async def refusal_reason(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    data = context.bot_data
    pool = data["pool"]
    if await calls.in_progress(pool):
        return "Un appel est déjà en cours : attends son compte rendu."
    if await calls.count_today(pool) >= calls.APPELS_MAX_PAR_JOUR:
        return f"Limite de {calls.APPELS_MAX_PAR_JOUR} appels par jour atteinte."
    jour, _ = await usage.spent(pool)
    if jour >= data["config"].daily_budget_usd:
        return "Le plafond de dépense du jour est atteint."
    return None


def follow(job_queue, call_id: int) -> None:
    job_queue.run_repeating(
        check_job,
        interval=calls.INTERVALLE_SUIVI_SECONDES,
        first=calls.INTERVALLE_SUIVI_SECONDES,
        data=call_id,
        name=f"appel-{call_id}",
    )


async def check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toutes les 15 s : l'appel est-il terminé ? Si oui, compte rendu à Hugo."""
    data = context.bot_data
    pool = data["pool"]
    appel = await calls.get(pool, context.job.data)
    if appel is None or appel.status != "en_cours" or not appel.vapi_id:
        context.job.schedule_removal()
        return
    try:
        etat = await data["vapi"].get(appel.vapi_id)
    except Exception:
        logger.exception("Suivi de l'appel n°%s impossible", appel.id)
        return
    if etat.get("status") != "ended":
        if datetime.now(timezone.utc) - appel.created_at > SUIVI_MAX:
            context.job.schedule_removal()
            await calls.finish(pool, appel.id, "echec", "suivi-abandonne", None, None, None)
            await context.bot.send_message(
                data["config"].allowed_user_id,
                f"⚠️ Je n'ai pas eu la fin de l'appel à {appel.destinataire}. Regarde dans Vapi.",
            )
        return

    context.job.schedule_removal()
    await report(context, appel, etat)


async def report(context: ContextTypes.DEFAULT_TYPE, appel: calls.Appel, etat: dict) -> None:
    data = context.bot_data
    pool = data["pool"]
    raison = etat.get("endedReason")
    duree = calls.call_duration(etat)
    transcript = calls.call_transcript(etat)
    sans_conversation = calls.explain_end(raison)

    if sans_conversation and len(transcript) < 40:
        resume, status = f"Pas de conversation : {sans_conversation}.", "echec"
    else:
        status = "termine"
        try:
            resume = await calls.summarize(
                data["claude"], data["config"].model, appel, transcript or "(vide)",
                on_response=lambda r: usage.record_claude(pool, r),
            )
        except Exception:
            logger.exception("Compte rendu de l'appel n°%s impossible", appel.id)
            resume = "Je n'ai pas réussi à rédiger le compte rendu : lis la transcription."

    await calls.finish(pool, appel.id, status, raison, resume, transcript or None, duree)
    if etat.get("cost"):
        await usage.record(pool, "vapi", "vapi-call", cost_usd=float(etat["cost"]))
    if duree:
        await usage.record(pool, "twilio", "twilio-voice", cost_usd=calls.estimated_twilio_cost(appel.numero, duree))

    texte = calls.format_report(appel, duree, resume, transcript)
    for i in range(0, len(texte), 4096):
        await context.bot.send_message(data["config"].allowed_user_id, texte[i : i + 4096])
    logger.info("Appel n°%s terminé (%s)", appel.id, raison)


async def resume_following(app: Application) -> int:
    """Au démarrage : reprend le suivi des appels restés « en cours »."""
    en_cours = await calls.in_progress(app.bot_data["pool"])
    for appel in en_cours:
        follow(app.job_queue, appel.id)
    return len(en_cours)


async def purge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await calls.purge_old_transcripts(context.bot_data["pool"])
