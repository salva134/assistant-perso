"""Construction du prompt et boucle d'outils, avec un faux client Claude."""

import asyncio
from collections import deque
from types import SimpleNamespace

from app import llm


def texte(t):
    return SimpleNamespace(type="text", text=t)


def reponse(stop_reason, *blocs):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocs))


class FauxClient:
    """Renvoie les réponses prévues une à une et garde chaque requête reçue."""

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.requetes = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **params):
        # Copie : la liste messages continue d'évoluer après l'appel
        self.requetes.append({**params, "messages": list(params["messages"])})
        return self.reponses.pop(0)


def test_build_messages_ajoute_le_message_apres_l_historique():
    history = [
        {"role": "user", "content": "Salut"},
        {"role": "assistant", "content": "Salut Hugo"},
    ]
    messages = llm.build_messages(history, "Ça va ?")
    assert messages == history + [{"role": "user", "content": "Ça va ?"}]


def test_build_messages_retire_une_reponse_orpheline_en_tete():
    # Quand l'historique est tronqué, il peut commencer par une réponse de l'assistant
    history = deque(
        [{"role": "assistant", "content": "…"}, {"role": "user", "content": "A"}], maxlen=20
    )
    messages = llm.build_messages(history, "B")
    assert [m["role"] for m in messages] == ["user", "user"]


def test_build_messages_ne_modifie_pas_l_historique():
    history = [{"role": "user", "content": "Salut"}]
    llm.build_messages(history, "Re")
    assert history == [{"role": "user", "content": "Salut"}]


def test_build_messages_date_seulement_dans_le_dernier_message():
    history = [{"role": "user", "content": "Salut"}, {"role": "assistant", "content": "Salut !"}]
    messages = llm.build_messages(history, "On est quel jour ?", "jeudi 23 octobre 2026, 19 h 05")
    assert messages[0]["content"] == "Salut"
    assert messages[-1]["content"] == "[jeudi 23 octobre 2026, 19 h 05]\nOn est quel jour ?"


def test_build_system_met_les_souvenirs_en_cache():
    system = llm.build_system("CONSIGNES", "SOUVENIRS")
    assert [b["text"] for b in system] == ["CONSIGNES", "SOUVENIRS"]
    assert system[-1]["cache_control"] == {"type": "ephemeral"}


def test_le_prompt_systeme_presente_marcus():
    prompt = llm.load_system_prompt()
    assert "Marcus" in prompt
    assert "Hugo" in prompt


def test_reply_envoie_systeme_modele_et_renvoie_le_texte():
    client = FauxClient(reponse("end_turn", texte("Salut !")))
    resultat = asyncio.run(llm.reply(client, "claude-haiku-5-5", "SYSTEME", [], "Salut"))
    assert resultat == "Salut !"
    requete = client.requetes[0]
    assert requete["model"] == "claude-haiku-5-5"
    assert requete["system"] == "SYSTEME"
    assert requete["messages"] == [{"role": "user", "content": "Salut"}]
    assert "tools" not in requete  # aucun outil en phase 1


def test_reply_execute_un_outil_puis_renvoie_la_reponse(monkeypatch):
    async def heure(_entree):
        return "20 h 15"

    monkeypatch.setattr(llm, "TOOLS", [{"name": "heure", "input_schema": {"type": "object"}}])
    monkeypatch.setattr(llm, "TOOL_HANDLERS", {"heure": heure})
    appel = SimpleNamespace(type="tool_use", id="t1", name="heure", input={})
    client = FauxClient(reponse("tool_use", appel), reponse("end_turn", texte("Il est 20 h 15.")))

    resultat = asyncio.run(llm.reply(client, "m", "s", [], "Quelle heure ?"))

    assert resultat == "Il est 20 h 15."
    deuxieme = client.requetes[1]["messages"]
    assert deuxieme[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "20 h 15"}],
    }


def test_reply_signale_un_outil_inconnu(monkeypatch):
    monkeypatch.setattr(llm, "TOOLS", [{"name": "x"}])
    appel = SimpleNamespace(type="tool_use", id="t1", name="inexistant", input={})
    client = FauxClient(reponse("tool_use", appel), reponse("end_turn", texte("ok")))
    asyncio.run(llm.reply(client, "m", "s", [], "?"))
    resultat_outil = client.requetes[1]["messages"][-1]["content"][0]
    assert resultat_outil["is_error"] is True


def test_reply_s_arrete_apres_trop_de_tours(monkeypatch):
    monkeypatch.setattr(llm, "TOOLS", [{"name": "x"}])
    appel = SimpleNamespace(type="tool_use", id="t", name="x", input={})
    client = FauxClient(*[reponse("tool_use", appel)] * llm.MAX_TOURS_OUTILS)
    resultat = asyncio.run(llm.reply(client, "m", "s", [], "?"))
    assert len(client.requetes) == llm.MAX_TOURS_OUTILS
    assert "reformuler" in resultat
