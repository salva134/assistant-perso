"""Commandes Telegram et dates en français."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app import dates
from app.main import parse_correction


def test_corrige_avec_fleche():
    assert parse_correction("habite à Lyon -> Hugo habite à Paris.") == (
        "habite à Lyon",
        "Hugo habite à Paris.",
    )


def test_corrige_par_numero():
    assert parse_correction("12 -> Hugo a deux chats.") == ("12", "Hugo a deux chats.")


def test_corrige_sans_fleche_refuse():
    assert parse_correction("habite à Lyon Paris") is None


def test_corrige_incomplet_refuse():
    assert parse_correction("-> Paris") is None


def test_date_en_francais():
    dt = datetime(2026, 10, 23, 19, 5, tzinfo=ZoneInfo("Europe/Paris"))
    assert dates.format_now(dt) == "vendredi 23 octobre 2026, 19 h 05 (heure de Paris)"
