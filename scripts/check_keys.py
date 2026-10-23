"""Phase 0 — vérifie que chaque clé API du .env répond.

Usage : python scripts/check_keys.py
Chaque service est testé indépendamment ; un service sans clé est signalé « à remplir ».
Les clés ne sont jamais affichées.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
load_dotenv(RACINE / ".env")

OK, ECHEC, MANQUE = "OK", "ÉCHEC", "À REMPLIR"


def env(nom: str) -> str:
    return os.getenv(nom, "").strip()


def test_telegram() -> tuple[str, str]:
    import httpx

    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        return MANQUE, "TELEGRAM_BOT_TOKEN vide"
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    data = r.json()
    if not data.get("ok"):
        return ECHEC, data.get("description", f"HTTP {r.status_code}")
    detail = f"bot @{data['result']['username']}"
    user_id = env("TELEGRAM_ALLOWED_USER_ID")
    if not user_id.isdigit():
        return ECHEC, f"{detail}, mais TELEGRAM_ALLOWED_USER_ID doit être un nombre"
    return OK, f"{detail}, liste blanche {user_id}"


def test_claude() -> tuple[str, str]:
    import anthropic

    if not env("ANTHROPIC_API_KEY"):
        return MANQUE, "ANTHROPIC_API_KEY vide"
    modele = "claude-haiku-5-5"
    client = anthropic.Anthropic()
    try:
        reponse = client.messages.create(
            model=modele,
            max_tokens=256,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Réponds juste « ok »."}],
        )
    except anthropic.AuthenticationError:
        return ECHEC, "clé invalide"
    except anthropic.PermissionDeniedError:
        return ECHEC, "clé sans permission (crédit ou plafond ?)"
    except anthropic.NotFoundError:
        return ECHEC, f"modèle introuvable : {modele}"
    except anthropic.APIStatusError as e:
        return ECHEC, f"HTTP {e.status_code} : {e.message}"
    except anthropic.APIConnectionError:
        return ECHEC, "pas de connexion réseau"
    texte = "".join(b.text for b in reponse.content if b.type == "text").strip()
    return OK, f"{modele} répond « {texte[:40]} »"


def test_openai() -> tuple[str, str]:
    import openai

    if not env("OPENAI_API_KEY"):
        return MANQUE, "OPENAI_API_KEY vide"
    client = openai.OpenAI()
    try:
        r = client.embeddings.create(model="text-embedding-3-small", input="test")
    except openai.AuthenticationError:
        return ECHEC, "clé invalide"
    except openai.APIStatusError as e:
        return ECHEC, f"HTTP {e.status_code} : {e.message}"
    except openai.APIConnectionError:
        return ECHEC, "pas de connexion réseau"
    dim = len(r.data[0].embedding)
    if dim != 1536:
        return ECHEC, f"embedding de {dim} dimensions au lieu de 1536"
    return OK, "embedding de 1536 dimensions"


def test_supabase() -> tuple[str, str]:
    import psycopg

    url = env("DATABASE_URL")
    if not url:
        return MANQUE, "DATABASE_URL vide"
    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            conn.execute("select 1")
            vector = conn.execute(
                "select count(*) from pg_extension where extname = 'vector'"
            ).fetchone()[0]
            tables = conn.execute(
                "select count(*) from information_schema.tables where table_schema = 'public'"
                " and table_name in ('messages', 'memories', 'reminders', 'usage')"
            ).fetchone()[0]
    except psycopg.OperationalError as e:
        # Le message d'erreur de psycopg ne contient pas le mot de passe
        return ECHEC, str(e).strip().splitlines()[-1][:150]
    if not vector:
        return ECHEC, "connexion OK, mais extension vector absente"
    if tables < 4:
        return ECHEC, f"{tables}/4 tables : lancer python scripts/apply_schema.py"
    return OK, "connexion OK, extension vector, 4/4 tables"


def test_vapi() -> tuple[str, str]:
    import httpx

    cle = env("VAPI_API_KEY")
    if not cle:
        return MANQUE, "VAPI_API_KEY vide (test téléphonie, après la phase 1)"
    r = httpx.get(
        "https://api.vapi.ai/assistant",
        headers={"Authorization": f"Bearer {cle}"},
        timeout=10,
    )
    if r.status_code == 401:
        return ECHEC, "clé refusée"
    if r.status_code != 200:
        return ECHEC, f"HTTP {r.status_code} : {r.text[:120]}"
    return OK, f"{len(r.json())} assistant(s)"


TESTS = {
    "Telegram": test_telegram,
    "Claude": test_claude,
    "OpenAI": test_openai,
    "Supabase": test_supabase,
    "Vapi": test_vapi,
}


def main() -> int:
    if not (RACINE / ".env").exists():
        print("Pas de fichier .env : copie .env.example en .env et remplis-le.\n")
    resultats = []
    for nom, test in TESTS.items():
        try:
            statut, detail = test()
        except Exception as e:  # un service qui plante ne doit pas bloquer les autres
            statut, detail = ECHEC, type(e).__name__
        resultats.append(statut)
        print(f"{nom:<10} {statut:<10} {detail}")
    return 0 if all(s == OK for s in resultats) else 1


if __name__ == "__main__":
    sys.exit(main())
