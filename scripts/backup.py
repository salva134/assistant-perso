"""Sauvegarde des tables de Marcus dans un fichier daté, gardé 8 semaines.

Usage : python scripts/backup.py <dossier>
Format : JSON compressé (marcus-AAAA-MM-JJ.json.gz), lisible sans outil Postgres.
"""

import gzip
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
TABLES = ("messages", "memories", "reminders", "usage")
DUREE_CONSERVATION_JOURS = 8 * 7


def main() -> int:
    load_dotenv(RACINE / ".env")
    dossier = Path(sys.argv[1] if len(sys.argv) > 1 else RACINE / "backups")
    dossier.mkdir(parents=True, exist_ok=True)

    sauvegarde = {}
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
        for table in TABLES:
            # psycopg lit le type vector comme du texte : rien de spécial pour les embeddings
            cur = conn.execute(f"select * from {table} order by id")
            noms = [c.name for c in cur.description]
            sauvegarde[table] = [dict(zip(noms, ligne)) for ligne in cur.fetchall()]

    fichier = dossier / f"marcus-{date.today().isoformat()}.json.gz"
    with gzip.open(fichier, "wt", encoding="utf-8") as f:
        json.dump(sauvegarde, f, ensure_ascii=False, default=str)

    limite = time.time() - DUREE_CONSERVATION_JOURS * 86400
    for ancien in dossier.glob("marcus-*.json.gz"):
        if ancien.stat().st_mtime < limite:
            ancien.unlink()

    print(f"Sauvegarde : {fichier} ({', '.join(f'{t} {len(sauvegarde[t])}' for t in TABLES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
