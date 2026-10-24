"""Le vrai SQL de Marcus, testé sur la base Supabase dans un schéma temporaire isolé.

Un schéma « test_xxxxxxxx » est créé avec les tables de sql/schema.sql, utilisé par ces tests,
puis supprimé : les vraies données de Marcus ne sont ni lues ni modifiées.
Ignoré si DATABASE_URL est absent (ou si SKIP_DB_TESTS=1).
"""

import asyncio
import os
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

from app import calls, memory, reminders, usage
from app.memory import Operation

RACINE = Path(__file__).resolve().parent.parent
URL = os.environ.get("DATABASE_URL") or dotenv_values(RACINE / ".env").get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not URL or os.environ.get("SKIP_DB_TESTS") == "1", reason="pas de base de données configurée"
)


@pytest.fixture(scope="module")
def schema():
    nom = f"test_{secrets.token_hex(4)}"
    with psycopg.connect(URL, autocommit=True, connect_timeout=15) as conn:
        conn.execute(f"create schema {nom}")
        conn.execute(f"set search_path to {nom}, public, extensions")
        conn.execute((RACINE / "sql" / "schema.sql").read_text(encoding="utf-8"))
    yield nom
    with psycopg.connect(URL, autocommit=True, connect_timeout=15) as conn:
        conn.execute(f"drop schema {nom} cascade")


class UneConnexion:
    """Se comporte comme le pool de Marcus, avec une seule connexion limitée au schéma de test."""

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


def avec_base(schema, scenario):
    async def principal():
        conn = await psycopg.AsyncConnection.connect(URL, autocommit=True, connect_timeout=15)
        try:
            await conn.execute(f"set search_path to {schema}, public, extensions")
            return await scenario(UneConnexion(conn))
        finally:
            await conn.close()

    if sys.platform == "win32":  # psycopg async ne fonctionne pas avec la boucle par défaut de Windows
        return asyncio.run(principal(), loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(principal())


async def faux_embed(texte):
    return [0.01] * 1536


# --- Messages et souvenirs ------------------------------------------------------------------


def test_historique_des_messages(schema):
    async def scenario(pool):
        for role, texte in [("user", "un"), ("assistant", "deux"), ("user", "trois")]:
            await memory.save_message(pool, role, texte)
        return await memory.recent_messages(pool, 2)

    assert avec_base(schema, scenario) == [
        {"role": "assistant", "content": "deux"}, {"role": "user", "content": "trois"}
    ]


def test_cycle_de_vie_d_un_souvenir(schema):
    async def scenario(pool):
        await memory.apply_operations(pool, faux_embed, [Operation("ajouter", 0, "profil", "Hugo habite à Lyon.")], None)
        [souvenir] = await memory.find_memories(pool, "Lyon")
        par_numero = await memory.find_memories(pool, f"n°{souvenir.id}")
        await memory.update_memory(pool, faux_embed, souvenir.id, "Hugo habite à Paris.")
        apres_correction = [s.content for s in await memory.all_memories(pool)]
        await memory.delete_memory(pool, souvenir.id)
        return par_numero, apres_correction, await memory.all_memories(pool)

    par_numero, apres_correction, final = avec_base(schema, scenario)
    assert par_numero[0].content == "Hugo habite à Lyon."
    assert apres_correction == ["Hugo habite à Paris."]
    assert final == []


def test_un_contact_partage_remplace_l_ancien_numero(schema):
    async def scenario(pool):
        await memory.remember_contact(pool, faux_embed, "Théo", "Le numéro de téléphone de Théo est le 06 00 00 00 00.")
        await memory.remember_contact(pool, faux_embed, "Théo", "Le numéro de téléphone de Théo est le 06 12 34 56 78.")
        return [s.content for s in await memory.all_memories(pool) if "Théo" in s.content]

    assert avec_base(schema, scenario) == ["Le numéro de téléphone de Théo est le 06 12 34 56 78."]


# --- Rappels ------------------------------------------------------------------------------------


def test_rappels(schema):
    maintenant = datetime.now(timezone.utc).replace(microsecond=0)

    async def scenario(pool):
        tard = await reminders.add(pool, "Garage", maintenant + timedelta(hours=5))
        tot = await reminders.add(pool, "Poubelles", maintenant + timedelta(hours=1), "hebdomadaire")
        ordre = [r.text for r in await reminders.pending(pool)]
        await reminders.move(pool, tot, maintenant + timedelta(days=7))
        deplace = await reminders.get(pool, tot)
        await reminders.mark_sent(pool, tard)
        envoye = await reminders.get(pool, tard)
        supprime_envoye = await reminders.delete(pool, tard)
        supprime = await reminders.delete(pool, tot)
        return ordre, deplace, envoye, supprime_envoye, supprime, await reminders.pending(pool)

    ordre, deplace, envoye, supprime_envoye, supprime, reste = avec_base(schema, scenario)
    assert ordre == ["Poubelles", "Garage"]  # du plus proche au plus lointain
    assert deplace.repetition == "hebdomadaire" and deplace.due_at == maintenant + timedelta(days=7)
    assert envoye is None  # un rappel envoyé n'est plus en attente
    assert supprime_envoye is False and supprime is True and reste == []


# --- Coûts -----------------------------------------------------------------------------------------


def test_couts_et_estimations(schema):
    async def scenario(pool):
        await usage.record(pool, "claude", "claude-haiku-5-5", 1_000_000, 0)  # 0,10 $
        await usage.record(pool, "twilio", "twilio-voice", cost_usd=0.32, ref="CA1", estime=True)
        avant = await usage.spent(pool), await usage.estimated_today(pool)
        [(usage_id, ref)] = await usage.pending_estimates(pool, "twilio")
        await usage.settle(pool, usage_id, 0.04)
        apres = await usage.spent(pool), await usage.estimated_today(pool), await usage.pending_estimates(pool, "twilio")
        return avant, ref, apres

    (avant_depense, avant_estime), ref, (apres_depense, apres_estime, attente) = avec_base(schema, scenario)
    assert avant_depense[0] == pytest.approx(0.42) and avant_estime == pytest.approx(0.32)
    assert ref == "CA1"
    assert apres_depense[0] == pytest.approx(0.14) and apres_estime == 0 and attente == []


# --- Appels -------------------------------------------------------------------------------------


def test_cycle_de_vie_d_un_appel_sortant(schema):
    async def scenario(pool):
        appel = await calls.create_pending(pool, "+33442000000", "Da Luigi", "Réserver", "J'appelle.", "")
        premier_clic = await calls.claim(pool, appel.id)
        second_clic = await calls.claim(pool, appel.id)  # double clic : refusé
        await calls.set_status(pool, appel.id, "en_cours", "vapi-1")
        en_cours = [a.id for a in await calls.in_progress(pool)]
        await calls.finish(pool, appel.id, "termine", "assistant-ended-call", "Réservé à 20 h.", "AI: …", 60)
        return (premier_clic, second_clic, en_cours, await calls.in_progress(pool), await calls.count_today(pool),
                await calls.recent_outgoing(pool), await calls.name_for(pool, "+33442000000"), appel.id)

    premier, second, en_cours, apres, du_jour, recents, nom, appel_id = avec_base(schema, scenario)
    assert premier is True and second is False
    assert en_cours == [appel_id] and apres == []
    assert du_jour == 1
    assert recents[0].destinataire == "Da Luigi" and recents[0].resume == "Réservé à 20 h."
    assert nom == "Da Luigi"


def test_un_appel_recu_n_est_enregistre_qu_une_fois(schema):
    async def scenario(pool):
        premier = await calls.record_inbound(pool, "vapi-entrant", "+33600000000", "06 00 00 00 00", "Théo", "…", 30, "fin")
        second = await calls.record_inbound(pool, "vapi-entrant", "+33600000000", "06 00 00 00 00", "Théo", "…", 30, "fin")
        connus = await calls.known_vapi_ids(pool, ["vapi-entrant", "inconnu"])
        return premier, second, connus, await calls.last_inbound_time(pool), await calls.count_today(pool)

    premier, second, connus, dernier, sortants = avec_base(schema, scenario)
    assert premier is True and second is False
    assert connus == {"vapi-entrant"}
    assert dernier is not None
    assert sortants <= 1  # les appels reçus ne comptent pas dans la limite d'appels passés


def test_les_vieilles_transcriptions_sont_effacees(schema):
    async def scenario(pool):
        appel = await calls.create_pending(pool, "+33442000001", "Vieux", "x", "x", "")
        await calls.finish(pool, appel.id, "termine", "fin", "résumé", "AI: vieux", 10)
        async with pool.connection() as conn:
            await conn.execute("update calls set created_at = now() - interval '8 days' where id = %s", (appel.id,))
            await calls.purge_old_transcripts(pool)
            cur = await conn.execute("select transcript, resume from calls where id = %s", (appel.id,))
            return await cur.fetchone()

    assert avec_base(schema, scenario) == (None, "résumé")  # le résumé reste
