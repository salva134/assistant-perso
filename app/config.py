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
    # Ville d'Hugo : oriente la recherche web (« le restaurant le plus proche »…)
    home_city: str = "Aubagne"
    daily_budget_usd: float = PLAFOND_JOUR_PAR_DEFAUT
    model: str = MODELE
    # Google Agenda : facultatif, actif seulement si les trois valeurs sont remplies
    google_client_id: str = field(default="", repr=False)
    google_client_secret: str = field(default="", repr=False)
    google_refresh_token: str = field(default="", repr=False)

    # Appels : facultatifs, actifs si la clé Vapi et l'identifiant du numéro sont remplis
    vapi_api_key: str = field(default="", repr=False)
    vapi_phone_number_id: str = ""

    # Twilio : sert à lire le prix réel des appels (facultatif)
    twilio_account_sid: str = ""
    twilio_auth_token: str = field(default="", repr=False)

    @property
    def calls_enabled(self) -> bool:
        return bool(self.vapi_api_key and self.vapi_phone_number_id)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)


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
        raise SystemExit("DAILY_BUDGET_USD doit être un nombre de dollars, par exemple 1 ou 0.5.") from None

    return Config(
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"].strip(),
        allowed_user_id=int(user_id),
        anthropic_api_key=env["ANTHROPIC_API_KEY"].strip(),
        openai_api_key=env["OPENAI_API_KEY"].strip(),
        database_url=env["DATABASE_URL"].strip(),
        timezone=env.get("TIMEZONE", "").strip() or "Europe/Paris",
        home_city=env.get("HOME_CITY", "").strip() or "Aubagne",
        daily_budget_usd=plafond_usd,
        google_client_id=env.get("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=env.get("GOOGLE_CLIENT_SECRET", "").strip(),
        google_refresh_token=env.get("GOOGLE_REFRESH_TOKEN", "").strip(),
        vapi_api_key=env.get("VAPI_API_KEY", "").strip(),
        vapi_phone_number_id=env.get("VAPI_PHONE_NUMBER_ID", "").strip(),
        twilio_account_sid=env.get("TWILIO_ACCOUNT_SID", "").strip(),
        twilio_auth_token=env.get("TWILIO_AUTH_TOKEN", "").strip(),
    )
