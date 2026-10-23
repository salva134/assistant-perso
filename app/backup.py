"""Sauvegarde des tables de Marcus dans un fichier daté, gardé 8 semaines.

Format : JSON compressé (marcus-AAAA-MM-JJ.json.gz), lisible sans outil Postgres.
"""

import gzip
import json
import time
from datetime import date
from pathlib import Path

import psycopg

TABLES = ("messages", "memories", "reminders", "usage")
DUREE_CONSERVATION_JOURS = 8 * 7


def backup(database_url: str, dossier: Path) -> str:
    """Écrit la sauvegarde du jour, efface celles de plus de 8 semaines, renvoie un résumé."""
    dossier.mkdir(parents=True, exist_ok=True)
    sauvegarde = {}
    with psycopg.connect(database_url, connect_timeout=15) as conn:
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

    return f"{fichier} ({', '.join(f'{t} {len(sauvegarde[t])}' for t in TABLES)})"
