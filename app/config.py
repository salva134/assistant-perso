"""Lecture du .env : s'arrête avec un message clair si une variable manque."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent

MODELE = "claude-haiku-5-5"
PLAFOND_JOUR_PAR_DEFAUT = 1.0
# Variables indispensables ; les phases suivantes en ajouteront
REQUISES = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USER_ID",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DATABASE_URL",
)


@dataclass(frozen=True)
class Config:
    # repr=False : un print(config) ou un log n'affiche jamais les secrets
    telegram_bot_token: str = field(repr=False)
    allowed_user_id: int
    anthropic_api_key: str = field(repr=False)
    openai_api_key: str = field(repr=False)
    database_url: str = field(repr=False)
    timezone: str = "Europe/Paris"
    daily_budget_usd: float = PLAFOND_JOUR_PAR_DEFAUT
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

    plafond = env.get("DAILY_BUDGET_USD", "").strip().replace(",", ".") or str(PLAFOND_JOUR_PAR_DEFAUT)
    try:
        plafond_usd = float(plafond)
    except ValueError:
        raise SystemExit("DAILY_BUDGET_USD doit être un nombre de dollars, par exemple 1 ou 0.5.")

    return Config(
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"].strip(),
        allowed_user_id=int(user_id),
        anthropic_api_key=env["ANTHROPIC_API_KEY"].strip(),
        openai_api_key=env["OPENAI_API_KEY"].strip(),
        database_url=env["DATABASE_URL"].strip(),
        timezone=env.get("TIMEZONE", "").strip() or "Europe/Paris",
        daily_budget_usd=plafond_usd,
    )
