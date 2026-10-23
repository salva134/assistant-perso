"""Journal des coûts des API et plafond de dépense quotidien."""

import logging

logger = logging.getLogger(__name__)

# Prix en dollars par million de tokens : entrée, sortie, lecture du cache, écriture du cache.
# Les prix du cache sont estimés (10 % et 125 % du prix d'entrée).
PRIX = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "text-embedding-3-small": (0.02, 0.0, 0.0, 0.0),
}
# Modèle inconnu : on compte au prix de Sonnet, pour ne jamais sous-estimer
PRIX_PAR_DEFAUT = PRIX["claude-sonnet-5-5"]

AUJOURD_HUI = "(now() at time zone 'Europe/Paris')::date"


def cost(model: str, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0) -> float:
    entree, sortie, lecture, ecriture = PRIX.get(model, PRIX_PAR_DEFAUT)
    total = (
        input_tokens * entree
        + output_tokens * sortie
        + cache_read * lecture
        + cache_write * ecriture
    )
    return total / 1_000_000


async def record(pool, service, model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    """Enregistre un appel ; une erreur ici ne doit jamais bloquer une réponse."""
    try:
        async with pool.connection() as conn:
            await conn.execute(
                "insert into usage (service, model, input_tokens, output_tokens,"
                " cache_read_tokens, cache_write_tokens, cost_usd)"
                " values (%s, %s, %s, %s, %s, %s, %s)",
                (
                    service,
                    model,
                    input_tokens,
                    output_tokens,
                    cache_read,
                    cache_write,
                    cost(model, input_tokens, output_tokens, cache_read, cache_write),
                ),
            )
    except Exception:
        logger.exception("Impossible d'enregistrer le coût d'un appel %s", model)


async def record_claude(pool, response) -> None:
    u = response.usage
    await record(
        pool,
        "claude",
        response.model,
        u.input_tokens,
        u.output_tokens,
        u.cache_read_input_tokens or 0,
        u.cache_creation_input_tokens or 0,
    )


async def spent(pool) -> tuple[float, float]:
    """Dépense du jour et du mois en cours (heure de Paris), en dollars."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"select coalesce(sum(cost_usd) filter (where day = {AUJOURD_HUI}), 0),"
            f" coalesce(sum(cost_usd), 0) from usage"
            f" where day >= date_trunc('month', {AUJOURD_HUI})"
        )
        jour, mois = await cur.fetchone()
    return float(jour), float(mois)


def format_dollars(montant: float) -> str:
    return f"{montant:.4f} $".replace(".", ",")
