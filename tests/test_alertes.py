"""Alerte Telegram après 3 échecs d'affilée."""

import asyncio
from types import SimpleNamespace

from app.main import record_failure, record_success


def faux_context(envois):
    async def send_message(chat_id, texte):
        envois.append((chat_id, texte))

    return SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        bot_data={"config": SimpleNamespace(allowed_user_id=42)},
    )


def test_alerte_au_troisieme_echec_seulement():
    envois = []
    context = faux_context(envois)
    for _ in range(2):
        asyncio.run(record_failure(context, "réponse"))
    assert envois == []
    asyncio.run(record_failure(context, "mémoire"))
    assert len(envois) == 1
    assert envois[0][0] == 42
    assert "mémoire" in envois[0][1]
    asyncio.run(record_failure(context, "réponse"))
    assert len(envois) == 1  # pas de nouvelle alerte à chaque échec suivant


def test_un_succes_remet_le_compteur_a_zero():
    envois = []
    context = faux_context(envois)
    for _ in range(2):
        asyncio.run(record_failure(context, "réponse"))
    record_success(context)
    for _ in range(2):
        asyncio.run(record_failure(context, "réponse"))
    assert envois == []
