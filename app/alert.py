"""Envoie un message d'alerte à Hugo sur Telegram, hors du bot.

Usage : python -m app.alert "texte"
        python -m app.alert arret <code>   (message tout prêt, pour le script Windows)
"""

import sys

import httpx

from app.config import load_config


def message(args: list[str]) -> str:
    if len(args) == 2 and args[0] == "arret":
        return (
            f"Marcus s'est arrêté (code {args[1]}). Il redémarre dans 10 secondes. "
            "Les détails sont dans logs\\marcus.log."
        )
    return " ".join(args) or "Alerte sans détail."


def main() -> int:
    texte = message(sys.argv[1:])
    config = load_config()
    httpx.post(
        f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
        data={"chat_id": config.allowed_user_id, "text": f"⚠️ {texte}"},
        timeout=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
