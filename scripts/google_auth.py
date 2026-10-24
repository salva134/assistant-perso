"""Autorise Marcus à lire et remplir ton Google Agenda (à faire une seule fois).

Usage : python scripts/google_auth.py
Il faut GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET dans le .env. Une page Google s'ouvre :
choisis ton compte et accepte. Le jeton obtenu est écrit dans le .env (GOOGLE_REFRESH_TOKEN),
sans jamais être affiché.
"""

import sys
from pathlib import Path

from dotenv import dotenv_values
from google_auth_oauthlib.flow import InstalledAppFlow

RACINE = Path(__file__).resolve().parent.parent
ENV = RACINE / ".env"
SCOPE = "https://www.googleapis.com/auth/calendar.events"


def write_env(nom: str, valeur: str) -> None:
    lignes = ENV.read_text(encoding="utf-8-sig").splitlines()
    if any(ligne.startswith(f"{nom}=") for ligne in lignes):
        lignes = [f"{nom}={valeur}" if ligne.startswith(f"{nom}=") else ligne for ligne in lignes]
    else:
        lignes.append(f"{nom}={valeur}")
    ENV.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def main() -> int:
    env = dotenv_values(ENV)
    client_id, secret = env.get("GOOGLE_CLIENT_ID"), env.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not secret:
        print("Il manque GOOGLE_CLIENT_ID ou GOOGLE_CLIENT_SECRET dans le .env.")
        return 1
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=[SCOPE],
    )
    print("Une page Google va s'ouvrir dans ton navigateur : choisis ton compte et accepte.")
    # prompt=consent : Google renvoie toujours un refresh token, même si tu as déjà accepté
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", open_browser=True)
    if not creds.refresh_token:
        print("Google n'a pas renvoyé de jeton. Recommence.")
        return 1
    write_env("GOOGLE_REFRESH_TOKEN", creds.refresh_token)
    print("C'est bon : l'accès à Google Agenda est enregistré dans le .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
