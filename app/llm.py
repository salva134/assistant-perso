"""Appels à Claude : prompt système, historique et boucle d'outils (tool use)."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

PROMPT_SYSTEME = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
MAX_TOKENS = 4096
# Garde-fou : une boucle d'outils qui ne s'arrête pas coûterait cher
MAX_TOURS_OUTILS = 5

# Outils proposés au modèle. Vide en phase 1 ; chaque phase en ajoutera
# (rappels, agenda, appels). TOOLS contient les définitions envoyées à l'API,
# TOOL_HANDLERS associe chaque nom d'outil à une fonction async qui renvoie du texte.
TOOLS: list[dict] = []
TOOL_HANDLERS: dict[str, Callable[[dict], Awaitable[str]]] = {}


def load_system_prompt(path: Path = PROMPT_SYSTEME) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_messages(history: Sequence[dict], user_text: str) -> list[dict]:
    """Historique récent + nouveau message, au format de l'API."""
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    # L'API attend une conversation qui commence par l'utilisateur
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    messages.append({"role": "user", "content": user_text})
    return messages


async def run_tool(block) -> dict:
    handler = TOOL_HANDLERS.get(block.name)
    if handler is None:
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Outil inconnu : {block.name}",
            "is_error": True,
        }
    try:
        resultat = await handler(block.input)
    except Exception as e:
        logger.exception("L'outil %s a échoué", block.name)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Erreur : {e}",
            "is_error": True,
        }
    return {"type": "tool_result", "tool_use_id": block.id, "content": resultat}


async def reply(
    client: anthropic.AsyncAnthropic,
    model: str,
    system_prompt: str,
    history: Sequence[dict],
    user_text: str,
) -> str:
    """Envoie le message à Claude, exécute les outils demandés, renvoie le texte final."""
    messages = build_messages(history, user_text)
    params = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system_prompt,
        # Effort bas : réponses rapides pour une discussion de tous les jours
        "output_config": {"effort": "low"},
    }
    if TOOLS:
        params["tools"] = TOOLS

    for _ in range(MAX_TOURS_OUTILS):
        response = await client.messages.create(messages=messages, **params)
        if response.stop_reason != "tool_use":
            break
        # La réponse complète (avec les blocs tool_use) repart telle quelle
        messages.append({"role": "assistant", "content": response.content})
        resultats = [await run_tool(b) for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": resultats})
    else:
        logger.warning("Boucle d'outils arrêtée après %d tours", MAX_TOURS_OUTILS)
        return "Je n'ai pas réussi à aller au bout, tu peux reformuler ?"

    if response.stop_reason == "refusal":
        return "Je ne peux pas répondre à ça."
    texte = "".join(b.text for b in response.content if b.type == "text").strip()
    return texte or "…"
