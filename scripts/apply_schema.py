"""Applique sql/schema.sql sur la base (DATABASE_URL du .env). Peut être relancé sans risque.

Usage : python scripts/apply_schema.py
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
load_dotenv(RACINE / ".env")


def main() -> int:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL vide dans .env")
        return 1
    schema = (RACINE / "sql" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        conn.execute(schema)
        tables = conn.execute(
            "select table_name from information_schema.tables"
            " where table_schema = 'public' order by table_name"
        ).fetchall()
    print("Schéma appliqué. Tables :", ", ".join(t[0] for t in tables))
    return 0


if __name__ == "__main__":
    sys.exit(main())
