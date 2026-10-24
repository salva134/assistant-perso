"""Google Agenda : lecture des événements, outils, point du matin."""

import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import brief, calendar_google
from app.calendar_google import Evenement
from app.config import load_config
from app.llm import Toolbox

PARIS = "Europe/Paris"


def test_evenement_avec_heure():
    e = calendar_google.parse_event(
        {
            "summary": "Dentiste",
            "start": {"dateTime": "2026-10-29T14:00:00+01:00"},
            "end": {"dateTime": "2026-10-29T15:00:00+01:00"},
        }
    )
    assert e.titre == "Dentiste"
    assert e.debut == datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc)


def test_evenement_journee_entiere_et_sans_titre():
    e = calendar_google.parse_event({"id": "e1", "start": {"date": "2026-10-29"}, "end": {"date": "2026-10-30"}})
    assert e == Evenement("(sans titre)", None, None, "e1", date(2026, 10, 29))


def test_mise_en_forme_des_evenements():
    texte = calendar_google.format_events(
        [
            Evenement("Anniversaire de Léa", None, None),
            Evenement(
                "Dentiste",
                datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc),
                datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc),
            ),
        ],
        PARIS,
    )
    assert "- Toute la journée : Anniversaire de Léa" in texte
    assert "- 14 h 00 – 15 h 00 : Dentiste" in texte


def test_journee_vide():
    assert calendar_google.format_events([], PARIS) == "Rien de prévu."


class FauxAgenda:
    tz = PARIS

    def __init__(self):
        self.ajouts, self.jours = [], []

    async def events_of_day(self, jour):
        self.jours.append(jour)
        return [Evenement("Dentiste", datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc),
                          datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc))]

    async def add_event(self, titre, debut, fin):
        self.ajouts.append((titre, debut, fin))
        return {}


@pytest.fixture
def outils():
    toolbox, agenda = Toolbox(), FauxAgenda()
    calendar_google.add_tools(toolbox, agenda)
    return toolbox, agenda


def test_lire_un_jour(outils):
    toolbox, agenda = outils
    texte = asyncio.run(toolbox.handlers["list_events"]({"jour": "2026-10-29"}))
    assert agenda.jours == [date(2026, 10, 29)]
    assert texte.startswith("jeudi 29 octobre 2026 :")
    assert "Dentiste" in texte


def test_ajouter_dentiste_jeudi_14h(outils):
    toolbox, agenda = outils
    texte = asyncio.run(
        toolbox.handlers["add_event"]({"titre": "Dentiste", "debut": "2026-10-29T14:00", "fin": "2026-10-29T15:00"})
    )
    titre, debut, fin = agenda.ajouts[0]
    assert titre == "Dentiste"
    assert debut == datetime(2026, 10, 29, 14, 0, tzinfo=ZoneInfo(PARIS))
    assert "jeudi 29 octobre 2026 à 14 h 00" in texte


def test_fin_avant_debut_refusee(outils):
    toolbox, agenda = outils
    with pytest.raises(ValueError, match="après son début"):
        asyncio.run(
            toolbox.handlers["add_event"]({"titre": "x", "debut": "2026-10-29T15:00", "fin": "2026-10-29T14:00"})
        )
    assert agenda.ajouts == []


def test_point_du_matin_avec_agenda():
    matin = datetime(2026, 10, 29, 7, 30, tzinfo=ZoneInfo(PARIS))
    texte = brief.format_brief(matin, [], PARIS, agenda="- 14 h 00 – 15 h 00 : Dentiste")
    assert "📅 Ton agenda :\n- 14 h 00 – 15 h 00 : Dentiste" in texte


def test_google_actif_seulement_si_tout_est_rempli():
    base = {
        "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_USER_ID": "1", "ANTHROPIC_API_KEY": "a",
        "OPENAI_API_KEY": "o", "DATABASE_URL": "d",
    }
    assert not load_config(base).google_enabled
    assert not load_config({**base, "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "s"}).google_enabled
    complet = {**base, "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "s", "GOOGLE_REFRESH_TOKEN": "r"}
    config = load_config(complet)
    assert config.google_enabled
    assert "r'" not in repr(config) and "'s'" not in repr(config)
