"""Appels à Claude : prompt système, historique et boucle d'outils (tool use)."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

PROMPT_SYSTEME = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
MAX_TOKENS = 4096
# Garde-fou : une boucle d'outils qui ne s'arrête pas coûterait cher
MAX_TOURS_OUTILS = 5

Handler = Callable[[dict], Awaitable[str]]


@dataclass
class Toolbox:
    """Outils proposés au modèle : définitions envoyées à l'API, et pour chaque nom
    la fonction async qui l'exécute et renvoie du texte (une exception = erreur pour le modèle)."""

    definitions: list[dict] = field(default_factory=list)
    handlers: dict[str, Handler] = field(default_factory=dict)

    def add(self, definition: dict, handler: Handler) -> None:
        self.definitions.append(definition)
        self.handlers[definition["name"]] = handler

    def add_server_tool(self, definition: dict) -> None:
        """Outil exécuté chez Anthropic (ex. recherche web) : rien à faire de notre côté."""
        self.definitions.append(definition)


def load_system_prompt(path: Path = PROMPT_SYSTEME) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_system(system_prompt: str, memories_text: str) -> list[dict]:
    """Consignes + souvenirs. Le cache couvre les deux : il ne se renouvelle
    que lorsque la mémoire change, pas à chaque message."""
    return [
        {"type": "text", "text": system_prompt},
        {"type": "text", "text": memories_text, "cache_control": {"type": "ephemeral"}},
    ]


@dataclass(frozen=True)
class Image:
    data: str  # base64
    media_type: str  # image/jpeg, image/png…


def build_messages(
    history: Sequence[dict], user_text: str, now_text: str | None = None, images: Sequence[Image] = ()
) -> list[dict]:
    """Historique récent + nouveau message, au format de l'API.

    La date et l'heure vont dans le dernier message seulement : le début du prompt
    reste identique d'un message à l'autre, ce qui permet le cache.
    Les images (photo envoyée par Hugo) précèdent le texte dans ce dernier message.
    """
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    # L'API attend une conversation qui commence par l'utilisateur
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    texte = f"[{now_text}]\n{user_text}" if now_text else user_text
    if images:
        contenu = [
            {"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.data}}
            for i in images
        ] + [{"type": "text", "text": texte}]
    else:
        contenu = texte
    messages.append({"role": "user", "content": contenu})
    return messages


async def run_tool(block, toolbox: Toolbox) -> dict:
    handler = toolbox.handlers.get(block.name)
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
    system: str | list[dict],
    history: Sequence[dict],
    user_text: str,
    now_text: str | None = None,
    on_response: Callable[[object], Awaitable[None]] | None = None,
    toolbox: Toolbox | None = None,
    images: Sequence[Image] = (),
) -> str:
    """Envoie le message à Claude, exécute les outils demandés, renvoie le texte final.

    on_response est appelé après chaque réponse de l'API (journal des coûts).
    """
    toolbox = toolbox or Toolbox()
    messages = build_messages(history, user_text, now_text, images)
    params = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        # Effort bas : réponses rapides pour une discussion de tous les jours
        "output_config": {"effort": "low"},
    }
    if toolbox.definitions:
        params["tools"] = toolbox.definitions

    for _ in range(MAX_TOURS_OUTILS):
        response = await client.messages.create(messages=messages, **params)
        if on_response:
            await on_response(response)
        if response.stop_reason == "pause_turn":
            # Une recherche web longue a été mise en pause : on renvoie tel quel, l'API reprend
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason != "tool_use":
            break
        # La réponse complète (avec les blocs tool_use) repart telle quelle
        messages.append({"role": "assistant", "content": response.content})
        resultats = [await run_tool(b, toolbox) for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": resultats})
    else:
        logger.warning("Boucle d'outils arrêtée après %d tours", MAX_TOURS_OUTILS)
        return "Je n'ai pas réussi à aller au bout, tu peux reformuler ?"

    if response.stop_reason == "refusal":
        return "Je ne peux pas répondre à ça."
    texte = "".join(b.text for b in response.content if b.type == "text").strip()
    return texte or "…"
