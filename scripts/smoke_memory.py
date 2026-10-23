"""Vérification rapide avec les vrais services, sans rien écrire en base.

Usage : python scripts/smoke_memory.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from app import memory, usage  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import make_pool  # noqa: E402
from app.memory import Souvenir  # noqa: E402


async def main() -> None:
    config = load_config()
    pool = make_pool(config.database_url)
    await pool.open(wait=True, timeout=15)
    try:
        print("Messages en base :", len(await memory.recent_messages(pool, 20)))
        print("Souvenirs en base :", len(await memory.all_memories(pool)))
        jour, mois = await usage.spent(pool)
        print("Dépense :", usage.format_dollars(jour), "aujourd'hui,", usage.format_dollars(mois), "ce mois")
    finally:
        await pool.close()

    claude = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    faux = [Souvenir(1, "profil", "Hugo habite à Lyon.")]
    for user_text in ["Salut, ça va ?", "J'ai déménagé à Paris le mois dernier, et ma sœur s'appelle Léa."]:
        ops = await memory.extract_operations(
            claude, config.model, faux, user_text, "D'accord !", "vendredi 23 octobre 2026"
        )
        print(f"« {user_text} » ->", ops)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
