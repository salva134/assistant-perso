"""Photos regardées par Marcus et contacts partagés retenus en mémoire."""

import asyncio
import base64
from types import SimpleNamespace

import pytest

from app import llm, main, memory
from app.memory import Operation, Souvenir

# --- Photos ---------------------------------------------------------------------------------


def test_la_photo_precede_le_texte_dans_le_message():
    image = llm.Image("QUJD", "image/jpeg")
    messages = llm.build_messages([], "C'est quoi ce menu ?", "samedi 24 octobre 2026, 12 h 00", [image])
    assert messages[-1]["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
        {"type": "text", "text": "[samedi 24 octobre 2026, 12 h 00]\nC'est quoi ce menu ?"},
    ]


def message_photo(legende="", photo=True, mime="image/jpeg", taille=100_000):
    reponses = []

    async def reply_text(texte):
        reponses.append(texte)

    fichier = SimpleNamespace(file_id="f1", file_size=taille)
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="petite", file_size=10), fichier] if photo else [],
        document=None if photo else SimpleNamespace(file_id="f1", file_size=taille, mime_type=mime),
        caption=legende, reply_text=reply_text,
    )
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=1)), reponses


def contexte_photo():
    telecharges = []

    async def get_file(file_id):
        telecharges.append(file_id)

        async def download_as_bytearray():
            return bytearray(b"\xff\xd8photo")

        return SimpleNamespace(download_as_bytearray=download_as_bytearray)

    return SimpleNamespace(bot=SimpleNamespace(get_file=get_file), bot_data={}), telecharges


@pytest.fixture
def capture(monkeypatch):
    recu = {}

    async def pas_de_plafond(update, context):
        return False

    async def answer(update, context, texte, entete="", images=(), texte_historique=None):
        recu.update(texte=texte, images=list(images), historique=texte_historique)

    monkeypatch.setattr(main, "over_budget", pas_de_plafond)
    monkeypatch.setattr(main, "answer", answer)
    return recu


def test_une_photo_avec_legende_part_a_claude(capture):
    update, _ = message_photo("Appelle le numéro sur l'affiche")
    context, telecharges = contexte_photo()
    asyncio.run(main.on_photo(update, context))
    assert telecharges == ["f1"]  # la plus grande version de la photo
    assert capture["texte"] == "Appelle le numéro sur l'affiche"
    assert capture["images"][0].media_type == "image/jpeg"
    assert base64.b64decode(capture["images"][0].data) == b"\xff\xd8photo"
    assert capture["historique"] == "[Photo envoyée] Appelle le numéro sur l'affiche"


def test_une_photo_sans_legende(capture):
    update, _ = message_photo("")
    asyncio.run(main.on_photo(update, contexte_photo()[0]))
    assert capture["texte"] == main.QUESTION_PHOTO_PAR_DEFAUT
    assert capture["historique"] == "[Photo envoyée]"


def test_image_envoyee_en_fichier(capture):
    update, _ = message_photo("menu", photo=False, mime="image/png")
    asyncio.run(main.on_photo(update, contexte_photo()[0]))
    assert capture["images"][0].media_type == "image/png"


def test_format_d_image_non_pris_en_charge(capture):
    update, reponses = message_photo(photo=False, mime="image/heic")
    asyncio.run(main.on_photo(update, contexte_photo()[0]))
    assert capture == {} and "JPG, PNG" in reponses[0]


def test_image_trop_lourde(capture):
    update, reponses = message_photo(photo=False, taille=9_000_000)
    asyncio.run(main.on_photo(update, contexte_photo()[0]))
    assert capture == {} and "trop lourde" in reponses[0]


# --- Contacts ---------------------------------------------------------------------------------


def test_un_contact_partage_est_retenu(monkeypatch):
    retenus, conversation, reponses = [], [], []

    async def remember_contact(pool, embed, nom, souvenir):
        retenus.append((nom, souvenir))

    async def save_message(pool, role, content):
        conversation.append((role, content))

    async def reply_text(texte):
        reponses.append(texte)

    monkeypatch.setattr(memory, "remember_contact", remember_contact)
    monkeypatch.setattr(memory, "save_message", save_message)
    contact = SimpleNamespace(first_name="Théo", last_name="Martin", phone_number="+33612345678")
    update = SimpleNamespace(message=SimpleNamespace(contact=contact, reply_text=reply_text))
    asyncio.run(main.on_contact(update, SimpleNamespace(bot_data={"pool": None, "embed": None})))
    assert retenus == [("Théo Martin", "Le numéro de téléphone de Théo Martin est le 06 12 34 56 78.")]
    assert conversation[0] == ("user", "[Contact partagé] Théo Martin : 06 12 34 56 78")
    assert reponses[0].startswith("📇 C'est noté : Théo Martin, 06 12 34 56 78.")


@pytest.fixture
def souvenirs(monkeypatch):
    etat = SimpleNamespace(existants=[], maj=[], ajouts=[])

    async def find_memories(pool, query):
        return [s for s in etat.existants if query in s.content]

    async def update_memory(pool, embed, memory_id, content, type_=None):
        etat.maj.append((memory_id, content))

    async def apply_operations(pool, embed, operations, source_id):
        etat.ajouts.extend(operations)

    monkeypatch.setattr(memory, "find_memories", find_memories)
    monkeypatch.setattr(memory, "update_memory", update_memory)
    monkeypatch.setattr(memory, "apply_operations", apply_operations)
    return etat


def test_nouveau_contact_ajoute(souvenirs):
    asyncio.run(memory.remember_contact(None, None, "Théo", "Le numéro de téléphone de Théo est le 06 12 34 56 78."))
    assert souvenirs.ajouts == [Operation("ajouter", 0, "profil", "Le numéro de téléphone de Théo est le 06 12 34 56 78.")]


def test_nouveau_numero_remplace_l_ancien(souvenirs):
    souvenirs.existants = [Souvenir(4, "profil", "Le numéro de téléphone de Théo est le 06 00 00 00 00.")]
    asyncio.run(memory.remember_contact(None, None, "Théo", "Le numéro de téléphone de Théo est le 06 12 34 56 78."))
    assert souvenirs.maj == [(4, "Le numéro de téléphone de Théo est le 06 12 34 56 78.")]
    assert souvenirs.ajouts == []
