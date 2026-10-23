"""Lecture du .env : messages clairs et secrets jamais affichés."""

import pytest

from app.config import load_config

ENV_OK = {
    "TELEGRAM_BOT_TOKEN": "123:secret-telegram",
    "TELEGRAM_ALLOWED_USER_ID": "7207428998",
    "ANTHROPIC_API_KEY": "sk-ant-secret",
}


def test_config_complete():
    config = load_config(ENV_OK)
    assert config.allowed_user_id == 7207428998
    assert config.model == "claude-haiku-5-5"
    assert config.timezone == "Europe/Paris"


def test_variable_manquante_arrete_avec_son_nom():
    env = {**ENV_OK, "ANTHROPIC_API_KEY": ""}
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        load_config(env)


def test_identifiant_non_numerique_refuse():
    with pytest.raises(SystemExit, match="nombre"):
        load_config({**ENV_OK, "TELEGRAM_ALLOWED_USER_ID": "@hugo"})


def test_repr_ne_montre_pas_les_secrets():
    texte = repr(load_config(ENV_OK))
    assert "secret" not in texte
