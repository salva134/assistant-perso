"""Filtre d'ID : seul l'identifiant de la liste blanche passe."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from app.main import guard, is_authorized, split_message

HUGO = 7207428998


def fake_update(user_id):
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(effective_user=user)


def fake_context():
    return SimpleNamespace(bot_data={"config": SimpleNamespace(allowed_user_id=HUGO)})


def test_hugo_est_autorise():
    assert is_authorized(fake_update(HUGO), HUGO)


def test_un_inconnu_est_refuse():
    assert not is_authorized(fake_update(123456), HUGO)


def test_une_mise_a_jour_sans_utilisateur_est_refusee():
    assert not is_authorized(fake_update(None), HUGO)


def test_guard_bloque_un_inconnu():
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(guard(fake_update(123456), fake_context()))


def test_guard_laisse_passer_hugo():
    asyncio.run(guard(fake_update(HUGO), fake_context()))


def test_split_message_respecte_la_limite_telegram():
    morceaux = split_message("a" * 9000)
    assert [len(m) for m in morceaux] == [4096, 4096, 808]
    assert "".join(morceaux) == "a" * 9000
