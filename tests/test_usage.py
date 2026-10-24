"""Coûts et plafond quotidien."""

import asyncio
from types import SimpleNamespace

import pytest

from app import main, usage


def test_cout_haiku():
    # 1 million de tokens en entrée + 1 million en sortie = 0,10 + 0,50 $
    assert usage.cost("claude-haiku-5-5", 1_000_000, 1_000_000) == 0.60


def test_cout_du_cache():
    assert usage.cost("claude-haiku-5-5", cache_read=1_000_000) == 0.01


def test_modele_inconnu_compte_au_prix_fort():
    assert usage.cost("modele-mystere", 1_000_000) == 2.00


def test_les_recherches_web_sont_comptees(monkeypatch):
    enregistre = {}

    async def record(pool, service, model, *args, cost_usd=None):
        enregistre["cout"] = cost_usd

    monkeypatch.setattr(usage, "record", record)
    reponse = SimpleNamespace(
        model="claude-haiku-5-5",
        usage=SimpleNamespace(
            input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=2),
        ),
    )
    asyncio.run(usage.record_claude(None, reponse))
    assert enregistre["cout"] == pytest.approx(0.10 + 2 * 0.01)


def test_format_dollars():
    assert usage.format_dollars(0.0123) == "0,0123 $"


def test_plafond_atteint_marcus_ne_repond_plus(monkeypatch):
    async def depense(_pool):
        return 1.2, 3.0

    async def interdit(*args, **kwargs):
        raise AssertionError("Claude ne doit pas être appelé au-delà du plafond")

    monkeypatch.setattr(usage, "spent", depense)
    monkeypatch.setattr(main.llm, "reply", interdit)
    reponses = []

    async def reply_text(texte):
        reponses.append(texte)

    update = SimpleNamespace(message=SimpleNamespace(text="Salut", reply_text=reply_text))
    context = SimpleNamespace(
        bot_data={"config": SimpleNamespace(daily_budget_usd=1.0), "pool": None}
    )
    asyncio.run(main.on_text(update, context))

    assert len(reponses) == 1
    assert "plafond" in reponses[0]
