"""Appels téléphoniques passés par Marcus via Vapi (numéro Twilio importé dans Vapi).

Déroulé : Claude prépare l'appel avec l'outil prepare_call → Hugo voit un récapitulatif avec
deux boutons → « Appeler » lance l'appel → suivi toutes les 15 s → compte rendu sur Telegram.
"""

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app.llm import Toolbox

logger = logging.getLogger(__name__)

API = "https://api.vapi.ai"
APPELS_MAX_PAR_JOUR = 5
VALIDITE_RECAPITULATIF = timedelta(minutes=10)
INTERVALLE_SUIVI_SECONDES = 15
DUREE_MAX_APPEL_SECONDES = 500

# Chaîne écoute → réflexion → voix, réglée pour le français.
# (GPT Realtime a été essayé le 24/10/2026 : accent anglais, phrases coupées.)
TRANSCRIPTION = {
    "provider": "deepgram",
    "model": "nova-3",
    "language": "fr",
    # Silence (ms) avant de considérer qu'un morceau de phrase est fini : 10 ms par défaut coupe trop tôt
    "endpointing": 300,
}
# Claude Sonnet 5 : le plus rapide (0,9 s avant le premier mot) et le plus malin d'un comparatif
# du 24/10/2026 contre GPT-4.1 (1,6 s), GPT-5.5 (1,7 s), GPT-5.6 Terra (1,3 s), Claude Haiku 5.5 (1,0 s).
# Pas de « temperature » : les modèles Claude récents la refusent.
MODELE = {"provider": "anthropic", "model": "claude-sonnet-5", "maxTokens": 250}
# Voix française native de Cartesia ; d'autres sont listées dans VOIX_FRANCAISES
VOIX_FRANCAISES = {
    "marc": "ce74c4da-4aee-435d-bc6d-81d1a9367e12",  # Conversational Buddy
    "fabien": "9d216805-e52e-4b1e-966a-6447df592a5d",  # naturel, chaleureux
    "henri": "d9f4af15-c402-4f50-bbda-d8823d028d6a",  # naturel, clair
    "antoine": "0418348a-0ca2-4e90-9986-800fb8b3bbc0",
}
# Henri : choisi par Hugo le 24/10/2026 après comparaison de Marc, Fabien et Henri
VOIX = {"provider": "cartesia", "model": "sonic-3.5", "voiceId": VOIX_FRANCAISES["henri"]}
# Tour de parole : laisser finir les phrases (adresses, numéros), mais s'arrêter dès qu'on le coupe
PRISE_DE_PAROLE = {
    "waitSeconds": 0.6,
    "smartEndpointingPlan": {"provider": "vapi"},  # recommandé par Vapi hors anglais
    "transcriptionEndpointingPlan": {
        "onPunctuationSeconds": 0.3,
        "onNoPunctuationSeconds": 1.5,
        "onNumberSeconds": 1.0,
    },
}
INTERRUPTION = {"numWords": 0, "voiceSeconds": 0.25, "backoffSeconds": 1.0}

# Façon de parler commune aux appels passés et reçus
STYLE_ORAL = (
    "Comment parler : en français de tous les jours, comme une personne sympathique et détendue "
    "au téléphone. Phrases courtes, une idée à la fois, avec des mots simples (« d'accord », "
    "« parfait », « super, merci »). Écoute vraiment ce qu'on te dit et réponds à ça, pas à un "
    "script. Si on te coupe la parole, arrête-toi et écoute. Ne répète jamais ta présentation. "
    "Écris tous les nombres en toutes lettres, comme on les dit à l'oral : heures (« vingt heures »), "
    "prix (« vingt-cinq euros cinquante »), code postal (« treize mille quatre cents »), numéro de "
    "rue (« deux cent quarante-cinq »), téléphone deux chiffres par deux (« zéro six, douze, trente-quatre… »)."
)

# Prix Twilio estimés depuis un numéro américain, en dollars par minute entamée
PRIX_MINUTE_MOBILE = 0.16
PRIX_MINUTE_FIXE = 0.05


class AppelErreur(Exception):
    pass


@dataclass
class Appel:
    id: int
    numero: str
    destinataire: str
    objectif: str
    ouverture: str
    details: str
    status: str
    created_at: datetime
    vapi_id: str | None = None


# --- Numéros ------------------------------------------------------------------------


def normalize_number(texte: str) -> str:
    """« 06 12 34 56 78 », « 0033 6… » ou « +33 6… » → « +33612345678 »."""
    brut = re.sub(r"[\s.\-()/]", "", texte)
    if brut.startswith("00"):
        brut = "+" + brut[2:]
    elif re.fullmatch(r"0[1-9]\d{8}", brut):
        brut = "+33" + brut[1:]
    if not re.fullmatch(r"\+[1-9]\d{6,14}", brut):
        raise ValueError(f"Numéro de téléphone illisible : « {texte} ».")
    return brut


def format_number(numero: str) -> str:
    """« +33612345678 » → « 06 12 34 56 78 » pour un numéro français, sinon tel quel."""
    if re.fullmatch(r"\+33[1-9]\d{8}", numero):
        local = "0" + numero[3:]
        return " ".join(local[i : i + 2] for i in range(0, 10, 2))
    return numero


def is_mobile(numero: str) -> bool:
    return bool(re.match(r"\+33[67]", numero))


def estimated_twilio_cost(numero: str, secondes: int) -> float:
    minutes = max(1, math.ceil(secondes / 60))
    return minutes * (PRIX_MINUTE_MOBILE if is_mobile(numero) else PRIX_MINUTE_FIXE)


# --- Base -------------------------------------------------------------------------------

COLONNES = "id, numero, destinataire, objectif, ouverture, details, status, created_at, vapi_id"


async def create_pending(pool, numero, destinataire, objectif, ouverture, details) -> Appel:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"insert into calls (numero, destinataire, objectif, ouverture, details)"
            f" values (%s, %s, %s, %s, %s) returning {COLONNES}",
            (numero, destinataire, objectif, ouverture, details),
        )
        return Appel(*await cur.fetchone())


async def get(pool, call_id: int) -> Appel | None:
    async with pool.connection() as conn:
        cur = await conn.execute(f"select {COLONNES} from calls where id = %s", (call_id,))
        row = await cur.fetchone()
    return Appel(*row) if row else None


async def set_status(pool, call_id: int, status: str, vapi_id: str | None = None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update calls set status = %s, vapi_id = coalesce(%s, vapi_id) where id = %s",
            (status, vapi_id, call_id),
        )


async def claim(pool, call_id: int) -> bool:
    """Passe l'appel de « en_attente » à « lancement » en une seule requête : deux clics
    rapprochés sur « Appeler » ne peuvent pas lancer deux appels."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "update calls set status = 'lancement' where id = %s and status = 'en_attente' returning id",
            (call_id,),
        )
        return await cur.fetchone() is not None


async def finish(pool, call_id, status, ended_reason, resume, transcript, duree) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update calls set status = %s, ended_reason = %s, resume = %s, transcript = %s,"
            " duree_secondes = %s, ended_at = now() where id = %s",
            (status, ended_reason, resume, transcript, duree, call_id),
        )


async def count_today(pool) -> int:
    """Appels réellement lancés par Marcus aujourd'hui (heure de Paris)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select count(*) from calls where direction = 'sortant' and status in ('en_cours', 'termine', 'echec')"
            " and (created_at at time zone 'Europe/Paris')::date = (now() at time zone 'Europe/Paris')::date"
        )
        return (await cur.fetchone())[0]


async def in_progress(pool) -> list[Appel]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"select {COLONNES} from calls where direction = 'sortant' and status = 'en_cours' order by id"
        )
        return [Appel(*row) for row in await cur.fetchall()]


@dataclass(frozen=True)
class AppelRecent:
    destinataire: str
    numero: str
    objectif: str
    resume: str
    created_at: datetime


async def recent_outgoing(pool, jours: int = 7, nombre: int = 5) -> list[AppelRecent]:
    """Derniers appels passés par Marcus : si on le rappelle, il sait de quoi on parle."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select destinataire, numero, objectif, coalesce(resume, ''), created_at from calls"
            " where direction = 'sortant' and status = 'termine'"
            " and created_at > now() - make_interval(days => %s) order by id desc limit %s",
            (jours, nombre),
        )
        return [AppelRecent(*row) for row in await cur.fetchall()]


async def last_inbound_time(pool) -> datetime | None:
    async with pool.connection() as conn:
        cur = await conn.execute("select max(created_at) from calls where direction = 'entrant'")
        return (await cur.fetchone())[0]


async def name_for(pool, numero: str) -> str | None:
    """Nom sous lequel Marcus a déjà appelé ce numéro (« Pizzeria Da Luigi »…)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select destinataire from calls where direction = 'sortant' and numero = %s order by id desc limit 1",
            (numero,),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def known_vapi_ids(pool, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    async with pool.connection() as conn:
        cur = await conn.execute("select vapi_id from calls where vapi_id = any(%s)", (ids,))
        return {row[0] for row in await cur.fetchall()}


async def record_inbound(pool, vapi_id, numero, appelant, resume, transcript, duree, raison) -> bool:
    """Enregistre un appel reçu. Renvoie False s'il l'était déjà (pas de double notification)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "insert into calls (vapi_id, numero, destinataire, objectif, ouverture, status, direction,"
            " ended_reason, resume, transcript, duree_secondes, ended_at)"
            " values (%s, %s, %s, 'Appel reçu', '', 'termine', 'entrant', %s, %s, %s, %s, now())"
            " on conflict (vapi_id) do nothing returning id",
            (vapi_id, numero, appelant, raison, resume, transcript, duree),
        )
        return await cur.fetchone() is not None


async def purge_old_transcripts(pool) -> None:
    """Les transcriptions contiennent les propos d'autres personnes : effacées après 7 jours."""
    async with pool.connection() as conn:
        await conn.execute(
            "update calls set transcript = null where transcript is not null"
            " and created_at < now() - interval '7 days'"
        )


# --- Assistant vocal ---------------------------------------------------------------


def first_message(ouverture: str) -> str:
    return f"Bonjour, c'est Marcus, l'assistant IA d'Hugo. {ouverture.strip()}"


CONSIGNE_LIGNE_DIRECTE = (
    "Tu peux joindre Hugo en direct avec l'outil demander_a_hugo : il reçoit ta question et répond "
    "en quelques secondes. Sers-t'en pour une décision importante qui n'est ni dans la demande ni dans "
    "sa mémoire (autre heure, autre jour, prix plus élevé, choix entre deux options), une ou deux fois "
    "par appel au plus. Pendant l'attente, reste naturel ; ensuite, reprends avec sa réponse."
)


def build_assistant(appel: Appel, memoire: str = "", voix: dict = VOIX, outils: list[dict] | None = None) -> dict:
    """memoire : ce que Marcus sait d'Hugo (souvenirs), pour répondre et prendre les devants."""
    consignes = (
        f"Tu es Marcus, l'assistant IA d'Hugo, et tu téléphones de sa part à : {appel.destinataire}.\n"
        f"But de l'appel : {appel.objectif}\n"
        f"Précisions d'Hugo pour cet appel : {appel.details or 'aucune.'}\n\n"
        f"{memoire or 'Tu ne sais rien de plus sur Hugo.'}\n\n"
        f"{STYLE_ORAL}\n"
        "Une chose à la fois, comme une vraie personne : dis d'abord ce que tu veux en une phrase, "
        "puis donne les détails (adresse, heure, paiement…) quand on te les demande, ou un par un. "
        "Ne déballe jamais tout d'un coup.\n\n"
        "Prends les devants avec ce que tu sais d'Hugo : s'il y a une question dont la réponse est dans "
        "sa mémoire ou découle de ses habitudes (son adresse, ses goûts, ses allergies, l'heure qu'il "
        "préfère, le nom pour la commande), réponds directement, sans dire que tu dois vérifier. "
        "Tu peux aussi proposer de toi-même ce qui l'arrangerait. Ne donne que ce qui sert à cet appel.\n\n"
        "Les questions imprévues :\n"
        "- Petit choix de tous les jours (sauce, boisson, couverts, sur place ou à emporter) : "
        "décide avec bon sens, selon ses goûts si tu les connais. Par exemple « Oui, une sauce "
        "pimentée, volontiers » ou « Non merci, ça ira ».\n"
        + (
            f"- {CONSIGNE_LIGNE_DIRECTE} S'il ne répond pas, dis qu'il reviendra vers eux.\n"
            if outils
            else "- Décision importante qui n'est ni dans la demande ni dans sa mémoire (prix nettement plus "
            "élevé, autre jour, acompte) : dis gentiment que tu vérifies avec Hugo et qu'il reviendra vers eux.\n"
        )
        + "- N'invente jamais un fait sur Hugo (adresse, numéro, nom) que tu ne connais pas.\n\n"
        "Pour finir : récapitule en une phrase ce qui est convenu, remercie, dis au revoir et "
        "raccroche. Si tu tombes sur un répondeur, laisse un message court avec le but de l'appel, "
        "puis raccroche."
    )
    return voice_assistant("Marcus appel", first_message(appel.ouverture), consignes, voix, outils)


def voice_assistant(
    nom: str, premier_message: str, consignes: str, voix: dict = VOIX, outils: list[dict] | None = None
) -> dict:
    """Réglages communs à tous les assistants vocaux de Marcus (voix, écoute, tour de parole)."""
    modele = {**MODELE, "messages": [{"role": "system", "content": consignes}]}
    if outils:
        modele["tools"] = outils
    return {
        "name": nom,
        "firstMessage": premier_message,
        "model": modele,
        "voice": voix,
        "transcriber": TRANSCRIPTION,
        "startSpeakingPlan": PRISE_DE_PAROLE,
        "stopSpeakingPlan": INTERRUPTION,
        "backgroundDenoisingEnabled": True,
        "endCallFunctionEnabled": True,
        "maxDurationSeconds": DUREE_MAX_APPEL_SECONDES,
        # Seule la transcription sert au compte rendu : pas d'enregistrement audio
        "artifactPlan": {"recordingEnabled": False},
    }


# --- Accueil : quand quelqu'un appelle le numéro de Marcus ---------------------------

NOM_ACCUEIL = "Marcus accueil"
PREMIER_MESSAGE_ACCUEIL = "Bonjour, vous êtes bien sur la ligne d'Hugo. C'est Marcus, son assistant IA. Je vous écoute ?"


def format_recent_calls(recents: list[AppelRecent], tz: str = "Europe/Paris") -> str:
    if not recents:
        return "Tu n'as passé aucun appel ces derniers jours."
    lignes = ["Tes derniers appels pour Hugo (si on te rappelle à ce sujet, reprends le fil) :"]
    for r in recents:
        quand = r.created_at.astimezone(ZoneInfo(tz)).strftime("%d/%m à %Hh%M")
        lignes.append(
            f"- le {quand}, à {r.destinataire} ({format_number(r.numero)}) : {r.objectif}"
            + (f" Résultat : {r.resume}" if r.resume else "")
        )
    return "\n".join(lignes)


def build_inbound_assistant(memoire: str, recents: list[AppelRecent], outils: list[dict] | None = None) -> dict:
    consignes = (
        "Tu es Marcus, l'assistant IA d'Hugo. Tu réponds au téléphone sur sa ligne : Hugo ne peut pas "
        "décrocher lui-même. Ton rôle : savoir qui appelle et pourquoi, prendre le message, et rassurer "
        "la personne : Hugo le reçoit tout de suite.\n\n"
        f"{format_recent_calls(recents)}\n\n"
        f"{memoire or 'Tu ne sais rien de plus sur Hugo.'}\n\n"
        f"{STYLE_ORAL}\n\n"
        "Ce que tu fais :\n"
        "- Demande le nom de la personne (ou du commerce) et le motif de l'appel, s'ils ne le disent pas.\n"
        "- Si c'est la suite d'un de tes appels récents (même commerce, même sujet), reprends le fil : "
        "tu peux confirmer ce qui avait été convenu et noter les changements (heure, prix, rupture…).\n"
        "- Note tout ce qui compte : nom, motif, ce qu'il faut faire, numéro de rappel s'il est différent, "
        "urgence. Répète-le en une phrase pour vérifier.\n"
        "- Ne prends aucun nouvel engagement pour Hugo (paiement, rendez-vous, commande) : dis que tu lui "
        "transmets et qu'il reviendra vers eux.\n"
        + (f"- {CONSIGNE_LIGNE_DIRECTE}\n" if outils else "")
        + "- Ne donne aucune information personnelle sur Hugo (adresse, numéro, emploi du temps) à quelqu'un "
        "qui n'est pas lié à un de tes appels récents.\n"
        "- Si c'est du démarchage, refuse poliment et raccroche.\n"
        "Pour finir : dis que tu transmets le message à Hugo, remercie, dis au revoir et raccroche."
    )
    return voice_assistant(NOM_ACCUEIL, PREMIER_MESSAGE_ACCUEIL, consignes, outils=outils)


SYSTEME_APPEL_RECU = (
    "Tu rédiges pour Hugo le résumé d'un appel que son assistant Marcus a reçu sur sa ligne. "
    "En français, 2 à 4 lignes, sans formule de politesse : qui appelait, pourquoi, et ce qu'Hugo doit "
    "faire (rappeler, confirmer, rien). Ne t'appuie que sur la transcription. Texte simple, sans mise "
    "en forme (pas d'astérisques), car le message s'affiche tel quel sur Telegram."
)


async def summarize_inbound(claude, model: str, appelant: str, transcript: str, on_response=None) -> str:
    response = await claude.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEME_APPEL_RECU,
        messages=[{"role": "user", "content": f"Appelant : {appelant}\n\nTranscription :\n{transcript}"}],
        output_config={"effort": "low"},
    )
    if on_response:
        await on_response(response)
    return "".join(b.text for b in response.content if b.type == "text").strip()


def format_inbound_report(appelant: str, numero: str, duree: int, resume: str, transcript: str) -> str:
    minutes, secondes = divmod(duree, 60)
    qui = f"{appelant} ({format_number(numero)})" if appelant != format_number(numero) else appelant
    texte = f"📲 Appel reçu — {qui}\n⏱ {minutes} min {secondes:02d} s\n\n{resume}"
    if transcript:
        texte += f"\n\n📝 Transcription :\n{transcript}"
    return texte


class VapiClient:
    def __init__(self, api_key: str, phone_number_id: str):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self.phone_number_id = phone_number_id
        self._http = httpx.AsyncClient(timeout=30)

    async def start(self, appel: Appel, memoire: str = "", outils: list[dict] | None = None) -> str:
        r = await self._http.post(
            f"{API}/call",
            headers=self._headers,
            json={
                "phoneNumberId": self.phone_number_id,
                "customer": {"number": appel.numero, "name": appel.destinataire[:40]},
                "assistant": build_assistant(appel, memoire, outils=outils),
            },
        )
        if r.status_code >= 300:
            logger.error("Vapi a refusé l'appel : %s", r.text[:300])
            raise AppelErreur(f"Vapi a refusé l'appel ({r.status_code}).")
        return r.json()["id"]

    async def get(self, vapi_id: str) -> dict:
        r = await self._http.get(f"{API}/call/{vapi_id}", headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def calls_since(self, depuis: datetime) -> list[dict]:
        """Appels du numéro de Marcus créés après `depuis`, reçus comme passés."""
        r = await self._http.get(
            f"{API}/call",
            headers=self._headers,
            params={
                "phoneNumberId": self.phone_number_id,
                "createdAtGt": depuis.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "limit": 50,
            },
        )
        r.raise_for_status()
        return r.json()

    async def set_inbound_assistant(self, config: dict) -> str:
        """Crée ou met à jour l'assistant d'accueil, et le place sur le numéro de Marcus."""
        r = await self._http.get(f"{API}/assistant", headers=self._headers)
        r.raise_for_status()
        existant = next((a["id"] for a in r.json() if a.get("name") == config["name"]), None)
        if existant:
            r = await self._http.patch(f"{API}/assistant/{existant}", headers=self._headers, json=config)
        else:
            r = await self._http.post(f"{API}/assistant", headers=self._headers, json=config)
        r.raise_for_status()
        assistant_id = r.json()["id"]
        r = await self._http.patch(
            f"{API}/phone-number/{self.phone_number_id}", headers=self._headers, json={"assistantId": assistant_id}
        )
        r.raise_for_status()
        return assistant_id


def call_duration(data: dict) -> int:
    debut, fin = data.get("startedAt"), data.get("endedAt")
    if not debut or not fin:
        return 0
    d = datetime.fromisoformat(debut.replace("Z", "+00:00"))
    f = datetime.fromisoformat(fin.replace("Z", "+00:00"))
    return max(0, round((f - d).total_seconds()))


def call_transcript(data: dict) -> str:
    return ((data.get("artifact") or {}).get("transcript") or data.get("transcript") or "").strip()


# Raisons de fin qui veulent dire que personne n'a vraiment parlé avec Marcus
FINS_SANS_CONVERSATION = {
    "customer-did-not-answer": "personne n'a décroché",
    "customer-busy": "la ligne était occupée",
    "voicemail": "je suis tombé sur le répondeur",
    "twilio-failed-to-connect-call": "l'appel n'a pas pu aboutir",
}


def explain_end(reason: str | None) -> str | None:
    if not reason:
        return None
    if reason in FINS_SANS_CONVERSATION:
        return FINS_SANS_CONVERSATION[reason]
    if "error" in reason:
        return f"l'appel a échoué ({reason})"
    return None


# --- Messages pour Hugo ---------------------------------------------------------------


def format_recap(appel: Appel) -> str:
    lignes = [
        "📞 Appel prêt — je n'appelle qu'après ta validation.",
        "",
        f"👤 {appel.destinataire}",
        f"☎️ {format_number(appel.numero)}",
        f"🎯 {appel.objectif}",
        f"🗣 Je commencerai par : « {first_message(appel.ouverture)} »",
    ]
    if appel.details:
        lignes.append(f"ℹ️ Infos que je peux donner : {appel.details}")
    return "\n".join(lignes)


def format_report(appel: Appel, duree: int, resume: str, transcript: str) -> str:
    minutes, secondes = divmod(duree, 60)
    texte = (
        f"📞 Appel terminé — {appel.destinataire} ({format_number(appel.numero)})\n"
        f"⏱ {minutes} min {secondes:02d} s\n\n{resume}"
    )
    if transcript:
        texte += f"\n\n📝 Transcription :\n{transcript}"
    return texte


SYSTEME_COMPTE_RENDU = (
    "Tu rédiges pour Hugo le compte rendu d'un appel que son assistant Marcus a passé pour lui. "
    "En français, 2 à 4 lignes, sans formule de politesse : l'objectif est-il atteint, ce qui a été "
    "convenu (heure, nombre de personnes, prix…), et ce qu'Hugo doit éventuellement faire ensuite. "
    "Ne t'appuie que sur la transcription. Texte simple, sans mise en forme (pas d'astérisques), "
    "car le message s'affiche tel quel sur Telegram."
)


async def summarize(claude, model: str, appel: Appel, transcript: str, on_response=None) -> str:
    response = await claude.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEME_COMPTE_RENDU,
        messages=[{
            "role": "user",
            "content": f"Objectif de l'appel : {appel.objectif}\n\nTranscription :\n{transcript}",
        }],
        output_config={"effort": "low"},
    )
    if on_response:
        await on_response(response)
    return "".join(b.text for b in response.content if b.type == "text").strip()


# --- Outil pour Claude --------------------------------------------------------------------

DEFINITION = {
    "name": "prepare_call",
    "description": (
        "Prépare un appel téléphonique que Marcus passera lui-même pour Hugo (un proche, un "
        "restaurant, un commerce…). L'appel ne part pas tout de suite : Hugo reçoit un récapitulatif "
        "et le valide avec un bouton. Si tu ne connais pas le numéro, demande-le à Hugo avant."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "numero": {"type": "string", "description": "Numéro à appeler, par exemple 06 12 34 56 78."},
            "destinataire": {"type": "string", "description": "Qui on appelle, par exemple « Maman » ou « Pizzeria Luigi »."},
            "objectif": {"type": "string", "description": "Ce que l'appel doit obtenir ou transmettre, en une phrase."},
            "ouverture": {
                "type": "string",
                "description": "Phrase dite juste après « Bonjour, c'est Marcus, l'assistant IA d'Hugo. », "
                "par exemple « J'appelle de sa part pour réserver une table ce soir. »",
            },
            "details": {
                "type": "string",
                "description": "Informations utiles que Marcus peut donner (nombre de personnes, horaires, "
                "nom de réservation…), ou une chaîne vide.",
            },
        },
        "required": ["numero", "destinataire", "objectif", "ouverture", "details"],
        "additionalProperties": False,
    },
}


def add_tools(toolbox: Toolbox, pool, on_prepared) -> None:
    """on_prepared(appel) est appelé quand un appel attend la validation d'Hugo (envoi des boutons)."""

    async def prepare_call(entree: dict) -> str:
        numero = normalize_number(entree["numero"])
        if await count_today(pool) >= APPELS_MAX_PAR_JOUR:
            raise ValueError(f"Limite de {APPELS_MAX_PAR_JOUR} appels par jour atteinte.")
        appel = await create_pending(
            pool,
            numero,
            entree["destinataire"].strip() or format_number(numero),
            entree["objectif"].strip(),
            entree["ouverture"].strip(),
            entree["details"].strip(),
        )
        await on_prepared(appel)
        return (
            f"Récapitulatif de l'appel n°{appel.id} envoyé à Hugo avec les boutons Appeler / Annuler. "
            "L'appel ne partira que s'il clique sur Appeler : ne dis pas qu'il est lancé."
        )

    toolbox.add(DEFINITION, prepare_call)


def is_expired(appel: Appel) -> bool:
    return datetime.now(timezone.utc) - appel.created_at > VALIDITE_RECAPITULATIF
