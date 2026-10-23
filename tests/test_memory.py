"""Mémoire : opérations d'extraction, données sensibles, mise en forme pour le prompt."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import memory
from app.memory import Operation, Souvenir

SOUVENIRS = [
    Souvenir(1, "profil", "Hugo habite à Lyon."),
    Souvenir(2, "preference", "Le plat préféré d'Hugo est la tartiflette."),
]


def ops(*operations):
    return {"operations": [dict(zip(("action", "id", "type", "content"), o)) for o in operations]}


# --- parse_operations --------------------------------------------------------


def test_rien_a_retenir():
    assert memory.parse_operations({"operations": []}, {1, 2}) == []


def test_ajout():
    resultat = memory.parse_operations(ops(("ajouter", 0, "profil", "Sa mère s'appelle Martine.")), {1})
    assert resultat == [Operation("ajouter", 0, "profil", "Sa mère s'appelle Martine.")]


def test_modification_d_un_souvenir_existant():
    resultat = memory.parse_operations(ops(("modifier", 1, "profil", "Hugo habite à Paris.")), {1, 2})
    assert resultat == [Operation("modifier", 1, "profil", "Hugo habite à Paris.")]


def test_suppression_d_un_souvenir_existant():
    resultat = memory.parse_operations(ops(("supprimer", 2, "preference", "")), {1, 2})
    assert resultat == [Operation("supprimer", 2, "preference", "")]


def test_numero_inconnu_ignore():
    assert memory.parse_operations(ops(("modifier", 99, "fait", "x"), ("supprimer", 98, "fait", "")), {1}) == []


def test_ajout_vide_ignore():
    assert memory.parse_operations(ops(("ajouter", 0, "fait", "  ")), set()) == []


def test_type_ou_action_inventes_ignores():
    assert memory.parse_operations(ops(("retenir", 0, "fait", "x"), ("ajouter", 0, "secret", "x")), set()) == []


def test_trois_operations_au_maximum():
    cinq = ops(*[("ajouter", 0, "fait", f"Fait {i}") for i in range(5)])
    assert len(memory.parse_operations(cinq, set())) == 3


def test_donnee_sensible_jamais_retenue():
    resultat = memory.parse_operations(
        ops(
            ("ajouter", 0, "fait", "Le mot de passe de sa box est soleil123."),
            ("ajouter", 0, "fait", "Sa carte : 4970 1012 3456 7890."),
            ("ajouter", 0, "fait", "Le chat d'Hugo s'appelle Pixel."),
        ),
        set(),
    )
    assert [o.content for o in resultat] == ["Le chat d'Hugo s'appelle Pixel."]


# --- Données sensibles -------------------------------------------------------


@pytest.mark.parametrize(
    "texte",
    [
        "Mon mot de passe Gmail est azerty",
        "le code PIN de ma carte c'est 1234",
        "Le digicode de l'immeuble est 4B12",
        "Carte 4970-1012-3456-7890",
        "IBAN FR76 3000 6000 0112 3456 7890 189",
        "mon mdp c'est truc",
    ],
)
def test_sensible(texte):
    assert memory.is_sensitive(texte)


@pytest.mark.parametrize(
    "texte",
    [
        "Hugo a rendez-vous chez le dentiste le 12 novembre 2026 à 14 h.",
        "Son numéro de portable est le 06 12 34 56 78.",
        "Il préfère réserver vers 20 h pour 4 personnes.",
    ],
)
def test_pas_sensible(texte):
    assert not memory.is_sensitive(texte)


# --- Mise en forme -----------------------------------------------------------


def test_prompt_sans_souvenir():
    assert "rien" in memory.format_for_prompt([])


def test_prompt_range_les_souvenirs_par_type():
    texte = memory.format_for_prompt(SOUVENIRS + [Souvenir(3, "fait", "Il a un chat.")])
    assert texte.index("Profil") < texte.index("Préférences") < texte.index("Faits")
    assert "- Hugo habite à Lyon." in texte
    assert "n°" not in texte  # les numéros ne servent qu'aux commandes


def test_liste_numerotee():
    texte = memory.format_numbered(SOUVENIRS)
    assert "n°1 — Hugo habite à Lyon." in texte
    assert "n°2 — Le plat préféré" in texte


# --- Extraction avec un faux client Claude ------------------------------------


class FauxClaude:
    def __init__(self, data, stop_reason="end_turn"):
        self.data, self.stop_reason, self.requetes = data, stop_reason, []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **params):
        self.requetes.append(params)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(self.data))],
        )


def test_extraction_envoie_souvenirs_et_echange():
    client = FauxClaude(ops(("modifier", 1, "profil", "Hugo habite à Paris.")))
    resultat = asyncio.run(
        memory.extract_operations(
            client, "claude-haiku-5-5", SOUVENIRS, "J'ai déménagé à Paris", "Noté !", "jeudi 23 octobre 2026"
        )
    )
    assert resultat == [Operation("modifier", 1, "profil", "Hugo habite à Paris.")]
    requete = client.requetes[0]
    entree = requete["messages"][0]["content"]
    assert "n°1 — Hugo habite à Lyon." in entree
    assert "Hugo : J'ai déménagé à Paris" in entree
    assert "jeudi 23 octobre 2026" in entree
    assert requete["output_config"]["format"]["type"] == "json_schema"


def test_extraction_interrompue_ne_renvoie_rien():
    client = FauxClaude({}, stop_reason="max_tokens")
    resultat = asyncio.run(memory.extract_operations(client, "m", [], "a", "b", "aujourd'hui"))
    assert resultat == []
