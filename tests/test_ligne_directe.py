"""Ligne directe : pendant un appel, Marcus pose une question à Hugo et attend sa réponse."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from app import calls, calls_bot, hotline, main

PAYLOAD = {
    "message": {
        "type": "tool-calls",
        "call": {"id": "vapi-1", "customer": {"number": "+33442000000"}},
        "toolCallList": [
            {"id": "t1", "type": "function",
             "function": {"name": "demander_a_hugo", "arguments": {"question": "21 h au lieu de 20 h, ça va ?"}}},
        ],
    }
}


# --- Question et réponse ------------------------------------------------------------------


def test_hugo_repond_a_temps():
    async def scenario():
        data, envoyees = {}, []

        async def envoyer(q):
            envoyees.append(q.texte)
            asyncio.get_running_loop().call_later(0.05, hotline.answer, data, "  Oui, 21 h c'est parfait ")

        reponse = await hotline.ask(data, envoyer, "21 h au lieu de 20 h ?", "Da Luigi", delai=2)
        return envoyees, reponse, hotline.pending(data)

    envoyees, reponse, encore = asyncio.run(scenario())
    assert envoyees == ["21 h au lieu de 20 h ?"]
    assert reponse == "Oui, 21 h c'est parfait"
    assert encore is None
    assert "Oui, 21 h c'est parfait" in hotline.result_for_call(reponse)


def test_hugo_ne_repond_pas():
    async def scenario():
        data = {}

        async def envoyer(q):
            pass

        reponse = await hotline.ask(data, envoyer, "?", "Da Luigi", delai=0.05)
        # Une réponse tardive n'est plus prise en compte
        return reponse, hotline.answer(data, "trop tard")

    reponse, tardive = asyncio.run(scenario())
    assert reponse is None and tardive is False
    assert "reviendra vers eux" in hotline.result_for_call(None)


def test_pas_de_question_en_attente():
    assert hotline.answer({}, "oui") is False


def test_un_message_pendant_une_question_est_la_reponse_d_hugo(monkeypatch):
    async def scenario():
        data, reponses = {}, []
        data["question_hugo"] = hotline.Question("21 h ?", "Da Luigi")

        async def reply_text(texte):
            reponses.append(texte)

        async def interdit(*args, **kwargs):
            raise AssertionError("ne doit pas partir à Claude")

        monkeypatch.setattr(main, "answer", interdit)
        update = SimpleNamespace(message=SimpleNamespace(text="Oui vas-y", reply_text=reply_text))
        await main.on_text(update, SimpleNamespace(bot_data=data))
        return data["question_hugo"].reponse.result(), reponses

    resultat, reponses = asyncio.run(scenario())
    assert resultat == "Oui vas-y"
    assert "Je lui réponds" in reponses[0]


# --- Outil et requêtes Vapi ------------------------------------------------------------------


def test_definition_de_l_outil():
    outil = hotline.tool_definition("https://abc.trycloudflare.com", "jeton123")
    assert outil["function"]["name"] == "demander_a_hugo"
    assert outil["server"]["url"] == "https://abc.trycloudflare.com/vapi/jeton123"
    assert outil["server"]["headers"] == {"X-Marcus-Jeton": "jeton123"}
    assert outil["server"]["timeoutSeconds"] > hotline.DELAI_REPONSE_SECONDES
    # Marcus annonce lui-même qu'il vérifie : pas de phrase automatique en double
    assert [m["type"] for m in outil["messages"]] == ["request-response-delayed"]


def test_question_vide_donne_une_consigne_au_lieu_d_une_erreur():
    repondre = calls_bot.make_tool_answerer(SimpleNamespace(bot_data={}))
    resultat = asyncio.run(repondre("demander_a_hugo", {"question": "  "}, {}))
    assert "Il manque la question" in resultat


def test_lecture_des_appels_d_outil():
    assert hotline.tool_calls(PAYLOAD) == [("t1", "demander_a_hugo", {"question": "21 h au lieu de 20 h, ça va ?"})]
    en_texte = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "t2", "function": {"name": "x", "arguments": '{"question": "ok ?"}'}}]}}
    assert hotline.tool_calls(en_texte) == [("t2", "x", {"question": "ok ?"})]
    assert hotline.tool_calls({"message": {"type": "status-update"}}) == []
    assert hotline.caller_number(PAYLOAD) == "+33442000000"


def requete(chemin, entetes=None):
    async def scenario():
        recus = []

        async def repondre(nom, arguments, payload):
            recus.append((nom, arguments["question"]))
            return "Réponse d'Hugo : « oui »."

        client = TestClient(TestServer(hotline.make_web_app("bon-jeton", repondre)))
        await client.start_server()
        try:
            r = await client.post(chemin, json=PAYLOAD, headers=entetes or {})
            corps = await r.json() if r.status == 200 else None
            return r.status, corps, recus
        finally:
            await client.close()

    return asyncio.run(scenario())


def test_le_webhook_repond_avec_le_bon_jeton():
    status, corps, recus = requete("/vapi/bon-jeton")
    assert status == 200
    assert corps == {"results": [{"toolCallId": "t1", "result": "Réponse d'Hugo : « oui »."}]}
    assert recus == [("demander_a_hugo", "21 h au lieu de 20 h, ça va ?")]


def test_le_webhook_accepte_le_jeton_en_entete():
    status, _, recus = requete("/vapi/autre", {"X-Marcus-Jeton": "bon-jeton"})
    assert status == 200 and len(recus) == 1


def test_le_webhook_refuse_un_mauvais_jeton():
    status, _, recus = requete("/vapi/mauvais-jeton")
    assert status == 404 and recus == []


# --- Consignes des assistants vocaux --------------------------------------------------------


def un_appel():
    return calls.Appel(1, "+33442000000", "Da Luigi", "Réserver à 20 h", "J'appelle pour réserver.", "",
                       "en_attente", datetime.now(timezone.utc))


def test_l_assistant_d_appel_recoit_la_ligne_directe():
    outil = hotline.tool_definition("https://abc.trycloudflare.com", "j")
    a = calls.build_assistant(un_appel(), outils=[outil])
    assert a["model"]["tools"] == [outil]
    assert "demander_a_hugo" in a["model"]["messages"][0]["content"]


def test_sans_tunnel_pas_d_outil():
    a = calls.build_assistant(un_appel())
    assert "tools" not in a["model"]
    assert "qu'il reviendra vers eux" in a["model"]["messages"][0]["content"]


def test_l_accueil_recoit_aussi_la_ligne_directe():
    outil = hotline.tool_definition("https://abc.trycloudflare.com", "j")
    a = calls.build_inbound_assistant("", [], [outil])
    assert a["model"]["tools"] == [outil]
    assert "demander_a_hugo" in a["model"]["messages"][0]["content"]
