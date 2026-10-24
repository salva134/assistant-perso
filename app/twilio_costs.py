"""Prix réels des appels chez Twilio : les estimations faites à la fin des appels sont corrigées
dès que Twilio publie le prix (immédiat pour un appel reçu, parfois bien plus tard pour un sortant)."""

import logging

import httpx

from app import usage

logger = logging.getLogger(__name__)

API = "https://api.twilio.com/2010-04-01"

# Estimations provisoires, en dollars par minute entamée (numéro américain)
PRIX_MINUTE_RECU = 0.0085


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str):
        self._sid = account_sid
        self._http = httpx.AsyncClient(timeout=20, auth=(account_sid, auth_token))

    async def call_price(self, call_sid: str) -> float | None:
        """Prix facturé d'un appel en dollars, ou None tant que Twilio ne l'a pas calculé."""
        r = await self._http.get(f"{API}/Accounts/{self._sid}/Calls/{call_sid}.json")
        r.raise_for_status()
        prix = r.json().get("price")
        return abs(float(prix)) if prix not in (None, "") else None


async def reconcile(pool, twilio: TwilioClient) -> int:
    """Remplace les coûts Twilio encore estimés par leur prix réel. Renvoie le nombre corrigé."""
    corriges = 0
    for usage_id, call_sid in await usage.pending_estimates(pool, "twilio"):
        try:
            prix = await twilio.call_price(call_sid)
        except Exception:
            logger.exception("Prix Twilio illisible pour %s", call_sid)
            continue
        if prix is not None:
            await usage.settle(pool, usage_id, prix)
            corriges += 1
    if corriges:
        logger.info("%d coût(s) d'appel Twilio corrigé(s) avec le prix réel", corriges)
    return corriges
