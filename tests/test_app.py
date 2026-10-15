"""Runs the Streamlit page headlessly with prepared test data."""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stores  # noqa: E402
import test_stores  # noqa: E402

APP = str(ROOT / "app.py")


@pytest.fixture
def data_file(tmp_path, monkeypatch):
    out, _ = test_stores.prepare_data.build(test_stores.RAW, post=test_stores.fake_ban_post, min_rows=1)
    path = tmp_path / "stores.csv"
    out.to_csv(path, index=False)
    monkeypatch.setattr(stores, "DATA_FILE", path)
    monkeypatch.setattr(stores, "find_places", lambda a, http=None, limit=5: [
        {"label": a, "latitude": 48.8566, "longitude": 2.3522, "postcode": "75001", "city": "Paris", "score": 0.9}])
    from streamlit import cache_data
    cache_data.clear()
    return path


def test_search_shows_nearest_store_and_keeps_address_in_link(data_file):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    at.text_input[0].set_value("10 rue de Rivoli, Paris")
    at.button[0].click().run()
    assert not at.exception
    assert any("**HYPER A**" in m.value and "km" in m.value for m in at.markdown)
    assert len(at.dataframe) == 1
    assert dict(at.query_params) == {"address": "10 rue de Rivoli, Paris"}


def test_bookmarked_link_searches_by_itself(data_file):
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["address"] = "10 rue de Rivoli, Paris"
    at.run()
    assert not at.exception
    assert len(at.dataframe) == 1


def test_missing_data_file_shows_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, "DATA_FILE", tmp_path / "absent.csv")
    from streamlit import cache_data
    cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("prepare_data.py" in e.value for e in at.error)


def test_page_lists_drives_only_and_links_to_directions(data_file):
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["address"] = "10 rue de Rivoli, Paris"
    at.run()
    assert not at.exception
    assert len(at.radio) == 0  # no "accepted at" toggle: the page is about drives
    table = at.dataframe[0].value
    assert list(table.columns) == ["Distance", "Store", "Format", "Address", "Directions"]
    assert table["Directions"].iloc[0].startswith("https://www.google.com/maps/dir/?api=1&destination=")
    assert table["Directions"].iloc[0].endswith("&origin=10%20rue%20de%20Rivoli%2C%20Paris")  # the address as text, not coordinates


def test_ambiguous_address_asks_instead_of_guessing(data_file, monkeypatch):
    places = [
        {"label": "Rue Richard-Lenoir 35000 Rennes", "latitude": 48.11, "longitude": -1.68, "postcode": "35000", "city": "Rennes", "score": 0.7},
        {"label": "Rue Richard Lenoir 75011 Paris", "latitude": 48.8566, "longitude": 2.3522, "postcode": "75011", "city": "Paris", "score": 0.68},
    ]
    monkeypatch.setattr(stores, "find_places", lambda a, http=None, limit=5: places)
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["address"] = "rue richard lenoir"
    at.run()
    assert not at.exception
    assert any("Several places match" in w.value for w in at.warning)
    assert len(at.dataframe) == 0  # nothing is searched until the visitor picks
    assert at.radio[0].options == ["Rue Richard-Lenoir 35000 Rennes", "Rue Richard Lenoir 75011 Paris"]
    at.radio[0].set_value("Rue Richard Lenoir 75011 Paris").run()
    assert not at.exception
    assert dict(at.query_params) == {"address": "Rue Richard Lenoir 75011 Paris"}
