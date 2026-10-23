"""Connexion à la base Supabase (Postgres) via DATABASE_URL."""

from psycopg_pool import AsyncConnectionPool


def make_pool(database_url: str) -> AsyncConnectionPool:
    """Pool à ouvrir avec `await pool.open()` une fois la boucle asyncio lancée."""
    return AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=4,
        open=False,
        kwargs={"autocommit": True},
        # Le pooler Supabase coupe les connexions inactives : on vérifie avant de s'en servir
        check=AsyncConnectionPool.check_connection,
    )


def vector_literal(embedding: list[float]) -> str:
    """Format texte accepté par Postgres pour le type vector : '[0.1,0.2,…]'."""
    return "[" + ",".join(f"{x:.7f}" for x in embedding) + "]"
