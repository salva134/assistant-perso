"""Mémoire de Marcus : messages et souvenirs en base, extraction après chaque échange."""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app import usage
from app.db import vector_literal

logger = logging.getLogger(__name__)

PROMPT_EXTRACTION = Path(__file__).resolve().parent.parent / "prompts" / "extraction.md"
EMBEDDING_MODEL = "text-embedding-3-small"

TYPES = ("profil", "preference", "fait", "evenement")
TITRES = {"profil": "Profil", "preference": "Préférences", "fait": "Faits", "evenement": "Événements"}
ACTIONS = ("ajouter", "modifier", "supprimer")

# Sous ce nombre, tous les souvenirs partent dans le prompt ; au-delà, recherche par similarité
SEUIL_TOUT_DANS_LE_PROMPT = 300
NB_PROCHES = 8
MAX_OPERATIONS = 3

Embedder = Callable[[str], Awaitable[list[float]]]


@dataclass(frozen=True)
class Souvenir:
    id: int
    type: str
    content: str


@dataclass(frozen=True)
class Operation:
    action: str
    id: int
    type: str
    content: str


# --- Données sensibles : jamais en mémoire ---------------------------------

MOTIFS_SENSIBLES = [
    re.compile(r"\b(?:\d[ .-]?){13,19}\b"),  # numéro de carte bancaire
    re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}", re.IGNORECASE),  # IBAN
    re.compile(
        r"mot de passe|password|\bmdp\b|\bcode (?:pin|secret|d'accès|confidentiel)"
        r"|digicode|\bcvv\b|cryptogramme|identifiant de connexion",
        re.IGNORECASE,
    ),
]


def is_sensitive(text: str) -> bool:
    return any(m.search(text) for m in MOTIFS_SENSIBLES)


# --- Messages ---------------------------------------------------------------


async def save_message(pool, role: str, content: str) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "insert into messages (role, content) values (%s, %s) returning id", (role, content)
        )
        return (await cur.fetchone())[0]


async def recent_messages(pool, n: int = 20) -> list[dict]:
    """Les n derniers messages, du plus ancien au plus récent."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select role, content from (select id, role, content from messages"
            " order by id desc limit %s) m order by id",
            (n,),
        )
        return [{"role": r, "content": c} for r, c in await cur.fetchall()]


# --- Lecture des souvenirs --------------------------------------------------


def _souvenirs(rows) -> list[Souvenir]:
    return [Souvenir(*row) for row in rows]


async def all_memories(pool) -> list[Souvenir]:
    async with pool.connection() as conn:
        cur = await conn.execute("select id, type, content from memories order by id")
        return _souvenirs(await cur.fetchall())


async def memories_for_prompt(pool, embed: Embedder, text: str) -> list[Souvenir]:
    tous = await all_memories(pool)
    if len(tous) < SEUIL_TOUT_DANS_LE_PROMPT:
        return tous
    # Mémoire trop grande : profil et préférences toujours, puis les faits les plus proches
    toujours = [s for s in tous if s.type in ("profil", "preference")]
    vecteur = vector_literal(await embed(text))
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select id, type, content from memories"
            " where type in ('fait', 'evenement') and embedding is not null"
            " order by embedding <=> %s::vector limit %s",
            (vecteur, NB_PROCHES),
        )
        return toujours + _souvenirs(await cur.fetchall())


def format_for_prompt(souvenirs: list[Souvenir]) -> str:
    if not souvenirs:
        return "Tu ne sais encore rien d'Hugo en dehors de cette conversation."
    lignes = ["Ce que tu sais d'Hugo (ta mémoire à long terme) :"]
    for type_ in TYPES:
        du_type = [s for s in souvenirs if s.type == type_]
        if du_type:
            lignes.append(f"\n{TITRES[type_]} :")
            lignes += [f"- {s.content}" for s in du_type]
    return "\n".join(lignes)


def format_numbered(souvenirs: list[Souvenir]) -> str:
    """Liste numérotée, pour /souvenirs et pour l'extraction."""
    if not souvenirs:
        return "(aucun souvenir)"
    lignes = []
    for type_ in TYPES:
        du_type = [s for s in souvenirs if s.type == type_]
        if du_type:
            lignes.append(f"{TITRES[type_]} :")
            lignes += [f"  n°{s.id} — {s.content}" for s in du_type]
    return "\n".join(lignes)


# --- Extraction après chaque échange ----------------------------------------

SCHEMA_EXTRACTION = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "id": {"type": "integer"},
                    "type": {"type": "string", "enum": list(TYPES)},
                    "content": {"type": "string"},
                },
                "required": ["action", "id", "type", "content"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["operations"],
    "additionalProperties": False,
}


def parse_operations(data: dict, existing_ids: set[int]) -> list[Operation]:
    """Ne garde que les opérations valides, sans donnée sensible, 3 au maximum."""
    valides = []
    for op in data.get("operations", []):
        action, type_ = op.get("action"), op.get("type")
        content = (op.get("content") or "").strip()
        id_ = op.get("id") or 0
        if action not in ACTIONS or type_ not in TYPES:
            continue
        if action in ("modifier", "supprimer") and id_ not in existing_ids:
            logger.warning("Opération %s ignorée : souvenir n°%s inconnu", action, id_)
            continue
        if action in ("ajouter", "modifier"):
            if not content:
                continue
            if is_sensitive(content):
                logger.warning("Souvenir refusé : donnée sensible")
                continue
        valides.append(Operation(action, id_, type_, content))
    return valides[:MAX_OPERATIONS]


async def extract_operations(
    claude, model: str, souvenirs: list[Souvenir], user_text: str, answer: str,
    today: str, on_response=None,
) -> list[Operation]:
    entree = (
        f"Date du jour : {today}\n\n"
        f"Souvenirs enregistrés :\n{format_numbered(souvenirs)}\n\n"
        f"Dernier échange :\nHugo : {user_text}\nMarcus : {answer}"
    )
    response = await claude.messages.create(
        model=model,
        max_tokens=2048,
        system=PROMPT_EXTRACTION.read_text(encoding="utf-8").strip(),
        messages=[{"role": "user", "content": entree}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA_EXTRACTION}},
    )
    if on_response:
        await on_response(response)
    if response.stop_reason != "end_turn":
        logger.warning("Extraction interrompue (%s)", response.stop_reason)
        return []
    texte = next(b.text for b in response.content if b.type == "text")
    return parse_operations(json.loads(texte), {s.id for s in souvenirs})


async def apply_operations(pool, embed: Embedder, operations: list[Operation], source_id: int | None):
    for op in operations:
        if op.action == "supprimer":
            await delete_memory(pool, op.id)
            logger.info("Souvenir n°%s supprimé", op.id)
        elif op.action == "modifier":
            await update_memory(pool, embed, op.id, op.content, op.type)
            logger.info("Souvenir n°%s modifié", op.id)
        else:
            vecteur = vector_literal(await embed(op.content))
            async with pool.connection() as conn:
                await conn.execute(
                    "insert into memories (type, content, embedding, source_message_id)"
                    " values (%s, %s, %s::vector, %s)",
                    (op.type, op.content, vecteur, source_id),
                )
            logger.info("Nouveau souvenir (%s)", op.type)


async def learn(pool, claude, embed: Embedder, model, user_text, answer, source_id, today, on_response=None):
    """Après un échange : met la mémoire à jour. Tourne en arrière-plan."""
    souvenirs = await all_memories(pool)
    operations = await extract_operations(
        claude, model, souvenirs, user_text, answer, today, on_response
    )
    await apply_operations(pool, embed, operations, source_id)


# --- Commandes /oublie et /corrige -------------------------------------------


async def find_memories(pool, query: str) -> list[Souvenir]:
    """Par numéro (« 12 » ou « n°12 ») ou par morceau de texte."""
    query = query.strip()
    numero = re.fullmatch(r"(?:n°|#)?\s*(\d+)", query)
    async with pool.connection() as conn:
        if numero:
            cur = await conn.execute(
                "select id, type, content from memories where id = %s", (int(numero.group(1)),)
            )
        else:
            cur = await conn.execute(
                "select id, type, content from memories where content ilike %s order by id",
                (f"%{query}%",),
            )
        return _souvenirs(await cur.fetchall())


async def delete_memory(pool, memory_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute("delete from memories where id = %s", (memory_id,))


async def update_memory(pool, embed: Embedder, memory_id: int, content: str, type_: str | None = None):
    vecteur = vector_literal(await embed(content))
    async with pool.connection() as conn:
        await conn.execute(
            "update memories set content = %s, type = coalesce(%s, type),"
            " embedding = %s::vector, updated_at = now() where id = %s",
            (content, type_, vecteur, memory_id),
        )


def make_embedder(openai_client, pool) -> Embedder:
    async def embed(text: str) -> list[float]:
        r = await openai_client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        await usage.record(pool, "openai", EMBEDDING_MODEL, input_tokens=r.usage.prompt_tokens)
        return r.data[0].embedding

    return embed
