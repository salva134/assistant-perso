"""Ligne directe pendant un appel : Marcus pose une question à Hugo sur Telegram et attend sa réponse.

Vapi appelle l'outil « demander_a_hugo » → requête HTTPS vers un tunnel Cloudflare → petit serveur
web local (127.0.0.1) dans le processus de Marcus → message Telegram à Hugo → sa réponse (texte,
vocal ou bouton) repart vers l'appel. Sans réponse sous 45 s, Marcus dit qu'Hugo rappellera.
"""

import asyncio
import hmac
import json
import logging
import queue
import re
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

from aiohttp import web

logger = logging.getLogger(__name__)

PORT = 8787
DELAI_REPONSE_SECONDES = 45
NOM_OUTIL = "demander_a_hugo"
CHEMINS_CLOUDFLARED = [
    "cloudflared",
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
]
URL_TUNNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


# --- Question en attente de la réponse d'Hugo ------------------------------------------


@dataclass
class Question:
    texte: str
    appelant: str
    reponse: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


def pending(bot_data: dict) -> Question | None:
    q = bot_data.get("question_hugo")
    return q if q is not None and not q.reponse.done() else None


def answer(bot_data: dict, texte: str) -> bool:
    """Transmet la réponse d'Hugo à la question en cours. False s'il n'y en a pas."""
    q = pending(bot_data)
    if q is None:
        return False
    q.reponse.set_result(texte.strip())
    return True


PAS_DE_REPONSE = (
    "Hugo n'a pas répondu à temps. Dis gentiment que tu vérifies avec lui et qu'il "
    "reviendra vers eux, puis continue l'appel."
)


async def ask(bot_data: dict, envoyer, texte: str, appelant: str, delai: float = DELAI_REPONSE_SECONDES) -> str | None:
    """Envoie la question à Hugo (via `envoyer`) et attend sa réponse. None s'il ne répond pas à temps."""
    if pending(bot_data):
        return None
    q = Question(texte, appelant)
    bot_data["question_hugo"] = q
    await envoyer(q)
    try:
        return await asyncio.wait_for(asyncio.shield(q.reponse), delai)
    except TimeoutError:
        q.reponse.cancel()
        return None


def result_for_call(reponse: str | None) -> str:
    if reponse is None:
        return PAS_DE_REPONSE
    return (
        f"Réponse d'Hugo : « {reponse} ». Reprends la conversation avec cette réponse, naturellement. "
        "Transmets-la telle quelle : n'invente ni raison ni détail qu'il n'a pas donnés."
    )


# --- Outil donné à l'assistant vocal --------------------------------------------------


def tool_definition(url: str, jeton: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": NOM_OUTIL,
            "description": (
                "Pose une question à Hugo en direct (il reçoit un message et répond en quelques "
                "secondes). À utiliser pour une décision importante qui n'est ni dans la demande "
                "ni dans sa mémoire : autre heure ou autre jour, prix plus élevé, choix entre deux "
                "options, information que toi seul ne peux pas donner. Une ou deux fois par appel au plus."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "La question pour Hugo, courte et complète, par exemple « Ils "
                        "n'ont plus de place à 20 h, mais à 21 h oui. Ça te va ? »",
                    }
                },
                "required": ["question"],
            },
        },
        # Le jeton est dans l'adresse et dans un en-tête : Vapi n'ajoute aucune authentification
        # aux assistants créés pour un appel, c'est à nous de vérifier
        "server": {
            "url": f"{url}/vapi/{jeton}",
            "headers": {"X-Marcus-Jeton": jeton},
            "timeoutSeconds": DELAI_REPONSE_SECONDES + 15,
        },
        # Pas de message « request-start » : Marcus annonce déjà lui-même qu'il vérifie
        "messages": [
            {
                "type": "request-response-delayed",
                "content": "Il regarde, encore une petite seconde.",
                "timingMilliseconds": 15000,
            },
        ],
    }


# --- Serveur web local et tunnel Cloudflare --------------------------------------------


def tool_calls(payload: dict) -> list[tuple[str, str, dict]]:
    """(id, nom, arguments) de chaque appel d'outil dans une requête Vapi « tool-calls »."""
    message = payload.get("message") or {}
    if message.get("type") != "tool-calls":
        return []
    resultat = []
    for appel in message.get("toolCallList") or []:
        fonction = appel.get("function") or {}
        arguments = fonction.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        resultat.append((appel.get("id", ""), fonction.get("name", ""), arguments))
    return resultat


def caller_number(payload: dict) -> str:
    appel = (payload.get("message") or {}).get("call") or {}
    return (appel.get("customer") or {}).get("number") or ""


def make_web_app(jeton: str, repondre) -> web.Application:
    """repondre(nom_outil, arguments, payload) -> str : traite un appel d'outil."""

    async def handler(request: web.Request) -> web.Response:
        jeton_url = request.match_info.get("jeton", "")
        jeton_entete = request.headers.get("X-Marcus-Jeton", "")
        if not (hmac.compare_digest(jeton_url, jeton) or hmac.compare_digest(jeton_entete, jeton)):
            logger.warning("Requête refusée sur le webhook (jeton invalide)")
            return web.Response(status=404)
        try:
            payload = await request.json()
        except ValueError:
            return web.json_response({"results": []})
        resultats = []
        for call_id, nom, arguments in tool_calls(payload):
            try:
                resultats.append({"toolCallId": call_id, "result": await repondre(nom, arguments, payload)})
            except Exception as e:
                logger.exception("Outil %s en échec", nom)
                resultats.append({"toolCallId": call_id, "error": f"Erreur : {e}"})
        return web.json_response({"results": resultats})

    app = web.Application()
    app.router.add_post("/vapi/{jeton}", handler)
    return app


@dataclass
class Ligne:
    url: str
    jeton: str
    runner: web.AppRunner
    tunnel: subprocess.Popen


def find_cloudflared() -> str | None:
    for chemin in CHEMINS_CLOUDFLARED:
        trouve = shutil.which(chemin)
        if trouve:
            return trouve
    return None


def kill_orphans() -> None:
    """Un arrêt brutal de Marcus laisse tourner son cloudflared : on nettoie avant d'en lancer un."""
    if shutil.which("taskkill"):
        subprocess.run(
            ["taskkill", "/IM", "cloudflared.exe", "/F"],
            capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def _watch(tunnel: subprocess.Popen, urls: queue.Queue) -> None:
    """Lit les journaux de cloudflared (dans un fil à part) : donne l'adresse du tunnel, puis
    continue à les lire, sinon son tampon se remplit et il se bloque."""
    donnee = False
    for ligne in tunnel.stderr:
        if not donnee:
            trouve = URL_TUNNEL.search(ligne)
            if trouve:
                urls.put(trouve.group(0))
                donnee = True
    if not donnee:
        urls.put(None)


async def start(repondre) -> Ligne | None:
    """Lance le serveur local et le tunnel. None si cloudflared manque ou si le tunnel échoue."""
    cloudflared = find_cloudflared()
    if cloudflared is None:
        logger.warning("cloudflared introuvable : pas de questions à Hugo pendant les appels")
        return None
    await asyncio.to_thread(kill_orphans)
    jeton = secrets.token_urlsafe(24)  # nouveau à chaque démarrage
    runner = web.AppRunner(make_web_app(jeton, repondre), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    # Popen dans un fil plutôt qu'asyncio : la boucle utilisée sous Windows (pour psycopg)
    # ne sait pas lancer de programme externe
    tunnel = await asyncio.to_thread(
        subprocess.Popen,
        [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    urls: queue.Queue = queue.Queue()
    threading.Thread(target=_watch, args=(tunnel, urls), daemon=True).start()
    try:
        url = await asyncio.to_thread(urls.get, True, 30)
    except queue.Empty:
        url = None
    if url is None:
        logger.error("Tunnel Cloudflare non obtenu")
        tunnel.kill()
        await runner.cleanup()
        return None
    logger.info("Ligne directe prête (tunnel Cloudflare)")
    return Ligne(url, jeton, runner, tunnel)


async def stop(ligne: Ligne | None) -> None:
    if ligne is None:
        return
    if ligne.tunnel.poll() is None:
        ligne.tunnel.kill()
    await ligne.runner.cleanup()
