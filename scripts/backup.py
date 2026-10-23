"""Sauvegarde manuelle de la mémoire de Marcus.

Usage : python scripts/backup.py [dossier]   (par défaut : backups/)
"""

import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from dotenv import load_dotenv  # noqa: E402

from app.backup import backup  # noqa: E402


def main() -> int:
    load_dotenv(RACINE / ".env")
    dossier = Path(sys.argv[1] if len(sys.argv) > 1 else RACINE / "backups")
    print("Sauvegarde :", backup(os.environ["DATABASE_URL"], dossier))
    return 0


if __name__ == "__main__":
    sys.exit(main())
