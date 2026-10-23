"""Lecture du .env : s'arrête avec un message clair si une variable manque."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent

MODELE = "claude-haiku-5-5"
# Variables indispensables pour la phase 1 ; les phases suivantes en ajouteront
REQUISES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "ANTHROPIC_API_KEY")


@dataclass(frozen=True)
class Config:
    # repr=False : un print(config) ou un log n'affiche jamais les secrets
    telegram_bot_token: str = field(repr=False)
    allowed_user_id: int
    anthropic_api_key: str = field(repr=False)
    timezone: str = "Europe/Paris"
    model: str = MODELE


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        load_dotenv(RACINE / ".env")
        env = os.environ

    manquantes = [nom for nom in REQUISES if not env.get(nom, "").strip()]
    if manquantes:
        raise SystemExit("Variable(s) manquante(s) dans .env : " + ", ".join(manquantes))

    user_id = env["TELEGRAM_ALLOWED_USER_ID"].strip()
    if not user_id.isdigit():
        raise SystemExit("TELEGRAM_ALLOWED_USER_ID doit être un nombre (ton identifiant Telegram).")

    return Config(
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"].strip(),
        allowed_user_id=int(user_id),
        anthropic_api_key=env["ANTHROPIC_API_KEY"].strip(),
        timezone=env.get("TIMEZONE", "").strip() or "Europe/Paris",
    )
