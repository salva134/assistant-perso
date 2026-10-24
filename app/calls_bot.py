"""Côté Telegram des appels : récapitulatif avec boutons, lancement, suivi et compte rendu."""

import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from app import calls, hotline, memory, usage

logger = logging.getLogger(__name__)

# Au-delà, on arrête de suivre un appel qui ne se termine pas. Compté depuis le lancement
# de l'appel chez Vapi : Vapi le coupe de lui-même après DUREE_MAX_APPEL_SECONDES.
SUIVI_MAX = timedelta(seconds=calls.DUREE_MAX_APPEL_SECONDES) + timedelta(minutes=10)


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
    if not await calls.claim(pool, call_id):
        await query.answer("Cet appel a déjà été traité.")
        return

    await query.answer("J'appelle…")
    try:
        # Toute la mémoire d'Hugo accompagne l'appel : Marcus peut répondre et improviser
        memoire = memory.format_for_prompt(await memory.all_memories(pool))
        vapi_id = await data["vapi"].start(appel, memoire, call_tools(data))
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
        if datetime.now(timezone.utc) - launch_time(appel, etat) > SUIVI_MAX:
            context.job.schedule_removal()
            await calls.finish(pool, appel.id, "echec", "suivi-abandonne", None, None, None)
            await context.bot.send_message(
                data["config"].allowed_user_id,
                f"⚠️ Je n'ai pas eu la fin de l'appel à {appel.destinataire}. Regarde dans Vapi.",
            )
        return

    context.job.schedule_removal()
    await report(context, appel, etat)


def launch_time(appel: calls.Appel, etat: dict) -> datetime:
    """Heure de lancement chez Vapi. Le récapitulatif a pu être créé jusqu'à 10 minutes avant."""
    if etat.get("createdAt"):
        return datetime.fromisoformat(etat["createdAt"].replace("Z", "+00:00"))
    return appel.created_at


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

    echange = calls.format_exchange(etat, calls.short_name(appel.destinataire))
    texte = calls.format_report(appel, duree, resume, echange)
    for i in range(0, len(texte), 4096):
        await context.bot.send_message(data["config"].allowed_user_id, texte[i : i + 4096])
    if duree and (appel.vapi_id or etat.get("id")):
        await send_recording(context, appel.vapi_id or etat["id"], appel.destinataire)
    # Le compte rendu rejoint la conversation : Hugo peut ensuite demander « c'était combien ? »
    await memory.save_message(pool, "assistant", f"[Compte rendu de l'appel à {appel.destinataire}] {resume}")
    logger.info("Appel n°%s terminé (%s)", appel.id, raison)
    # Si ce commerce rappelle, l'accueil connaît maintenant cet appel
    try:
        await refresh_inbound(data)
    except Exception:
        logger.exception("Mise à jour de l'assistant d'accueil impossible")


def call_tools(bot_data: dict) -> list[dict]:
    """Outils donnés aux assistants vocaux : la ligne directe avec Hugo, si le tunnel est ouvert."""
    ligne = bot_data.get("ligne")
    return [hotline.tool_definition(ligne.url, ligne.jeton)] if ligne else []


async def refresh_inbound(bot_data: dict) -> None:
    """Met à jour l'assistant d'accueil avec la mémoire d'Hugo et les derniers appels passés."""
    pool = bot_data["pool"]
    memoire = memory.format_for_prompt(await memory.all_memories(pool))
    config = calls.build_inbound_assistant(memoire, await calls.recent_outgoing(pool), call_tools(bot_data))
    await bot_data["vapi"].set_inbound_assistant(config)


# --- Ligne directe : questions à Hugo pendant un appel ---------------------------------


def question_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("👍 Oui", callback_data="question:Oui"),
            InlineKeyboardButton("👎 Non", callback_data="question:Non"),
        ]]
    )


def make_tool_answerer(app: Application):
    """Répond aux outils appelés par Vapi pendant un appel (pour l'instant : demander_a_hugo)."""

    async def repondre(nom: str, arguments: dict, payload: dict) -> str:
        if nom != hotline.NOM_OUTIL:
            raise ValueError(f"Outil inconnu : {nom}")
        question = (arguments.get("question") or "").strip()
        if not question:
            # Pas d'erreur : une consigne claire permet au modèle de se reprendre aussitôt
            return "Il manque la question : rappelle l'outil en écrivant ta question complète pour Hugo."
        data = app.bot_data
        appelant = await caller_name(data["pool"], payload)

        async def envoyer(q: hotline.Question) -> None:
            await app.bot.send_message(
                data["config"].allowed_user_id,
                f"❓ En ligne avec {q.appelant}, on me demande :\n\n{q.texte}\n\n"
                f"Réponds-moi ici, par écrit ou en vocal, dans les {hotline.DELAI_REPONSE_SECONDES} secondes.",
                reply_markup=question_buttons(),
            )

        reponse = await hotline.ask(data, envoyer, question, appelant)
        if reponse is None:
            await app.bot.send_message(
                data["config"].allowed_user_id,
                "⌛ Trop tard pour cette question : je leur ai dit que tu reviendrais vers eux.",
            )
        logger.info("Question à Hugo pendant l'appel avec %s : %s", appelant, "répondue" if reponse else "sans réponse")
        return hotline.result_for_call(reponse)

    return repondre


async def caller_name(pool, payload: dict) -> str:
    """Avec qui Marcus est en ligne : le destinataire d'un appel passé, ou le nom connu de l'appelant."""
    appel_vapi = (payload.get("message") or {}).get("call") or {}
    async with pool.connection() as conn:
        cur = await conn.execute("select destinataire from calls where vapi_id = %s", (appel_vapi.get("id"),))
        row = await cur.fetchone()
    if row:
        return row[0]
    numero = hotline.caller_number(payload)
    return await calls.name_for(pool, numero) or (calls.format_number(numero) if numero else "un correspondant")


async def on_question_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    reponse = (query.data or "").split(":", 1)[-1]
    if hotline.answer(context.bot_data, reponse):
        await query.answer("Transmis")
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(f"✅ Je lui réponds : « {reponse} ».")
    else:
        await query.answer("Trop tard, l'appel a continué sans ta réponse.")
        await query.edit_message_reply_markup(None)


async def refresh_inbound_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await refresh_inbound(context.bot_data)
    except Exception:
        logger.exception("Mise à jour de l'assistant d'accueil impossible")


# On regarde les appels reçus sur cette fenêtre (rattrape ceux reçus pendant que Marcus était éteint)
FENETRE_APPELS_RECUS = timedelta(days=2)


async def inbound_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chaque minute : un appel reçu vient de se terminer ? Résumé à Hugo sur Telegram."""
    data = context.bot_data
    pool = data["pool"]
    try:
        depuis = max(
            (await calls.last_inbound_time(pool)) or datetime.min.replace(tzinfo=timezone.utc),
            datetime.now(timezone.utc) - FENETRE_APPELS_RECUS,
        )
        appels = await data["vapi"].calls_since(depuis - timedelta(minutes=1))
        termines = [a for a in appels if a.get("type") == "inboundPhoneCall" and a.get("status") == "ended"]
        deja_vus = await calls.known_vapi_ids(pool, [a["id"] for a in termines])
        for etat in termines:
            if etat["id"] not in deja_vus:
                await report_inbound(context, etat)
    except Exception:
        logger.exception("Suivi des appels reçus impossible")


async def report_inbound(context: ContextTypes.DEFAULT_TYPE, etat: dict) -> None:
    data = context.bot_data
    pool = data["pool"]
    numero = (etat.get("customer") or {}).get("number") or "numéro masqué"
    appelant = calls.format_number(numero)
    # Un numéro déjà appelé n'est qu'un indice : ce n'est pas forcément la même personne qui rappelle
    deja_appele = await calls.name_for(pool, numero)
    transcript = calls.call_transcript(etat)
    duree = calls.call_duration(etat)
    if len(transcript) < 40:
        resume = "La personne a raccroché sans laisser de message."
    else:
        try:
            resume = await calls.summarize_inbound(
                data["claude"], data["config"].model, appelant, transcript, deja_appele,
                on_response=lambda r: usage.record_claude(pool, r),
            )
        except Exception:
            logger.exception("Résumé de l'appel reçu impossible")
            resume = "Je n'ai pas réussi à résumer l'appel : lis la transcription."
    # L'insertion échoue sans bruit si un autre passage l'a déjà traité : pas de double message
    if not await calls.record_inbound(
        pool, etat["id"], numero, appelant, resume, transcript or None, duree, etat.get("endedReason")
    ):  # appelant = le numéro : le nom réel est dans le résumé
        return
    if etat.get("cost"):
        await usage.record(pool, "vapi", "vapi-call", cost_usd=float(etat["cost"]))
    echange = calls.format_exchange(etat, "Appelant")
    texte = calls.format_inbound_report(numero, duree, resume, echange, deja_appele)
    for i in range(0, len(texte), 4096):
        await context.bot.send_message(data["config"].allowed_user_id, texte[i : i + 4096])
    if duree:
        await send_recording(context, etat["id"], f"appel reçu {appelant}")
    await memory.save_message(pool, "assistant", f"[Appel reçu de {appelant}] {resume}")
    logger.info("Appel reçu de %s signalé", appelant)


async def send_recording(context: ContextTypes.DEFAULT_TYPE, vapi_id: str, destinataire: str) -> None:
    """Envoie l'enregistrement audio de l'appel à Hugo, juste après le compte rendu."""
    data = context.bot_data
    try:
        audio = await data["vapi"].recording(vapi_id)
        if audio is None:
            await context.bot.send_message(
                data["config"].allowed_user_id, "🎧 L'enregistrement audio n'est pas disponible pour cet appel."
            )
            return
        await context.bot.send_audio(
            data["config"].allowed_user_id,
            audio=audio,
            filename=calls.recording_filename(destinataire, datetime.now(timezone.utc)),
            title=f"Appel — {calls.short_name(destinataire)}",
            performer="Marcus",
        )
    except Exception:
        logger.exception("Envoi de l'enregistrement de l'appel %s impossible", vapi_id)


async def resume_following(app: Application) -> int:
    """Au démarrage : reprend le suivi des appels restés « en cours »."""
    en_cours = await calls.in_progress(app.bot_data["pool"])
    for appel in en_cours:
        follow(app.job_queue, appel.id)
    return len(en_cours)


async def purge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await calls.purge_old_transcripts(context.bot_data["pool"])
