"""Tests for stores.py and prepare_data.py with made-up data (no network)."""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_data  # noqa: E402
import stores  # noqa: E402

PARIS = (48.8566, 2.3522)
LYON = (45.7640, 4.8357)

RAW = pd.DataFrame(
    [
        # Magasin, CP, Ville, Adresse, Format, En Magasin, En Drive
        ["HYPER A", "93100", "Montreuil", "1 Rue A", "Hypermarché", "OUI", "OUI"],
        ["MARKET B", "75020", "Paris", "2 Rue B", "CARREFOUR MARKET", "OUI", ""],
        ["CITY C", "75011", "Paris", "3 Rue C", "CARREFOUR CITY", "OUI", "OUI"],
        ["HYPER FAR", "69000", "Lyon", "4 Rue D", "Hypermarché", "OUI", "OUI"],
        ["NO COORDS", "00000", "Nowhere", "", "Hypermarché", "OUI", "OUI"],
    ],
    columns=stores.COLUMNS,
)
COORDS = {  # what the fake address service answers, by Magasin
    "HYPER A": (48.8600, 2.4400, 0.95),
    "MARKET B": (48.8650, 2.4000, 0.9),
    "CITY C": (48.8600, 2.3800, 0.9),
    "HYPER FAR": (45.7500, 4.8500, 0.9),
    "NO COORDS": (46.0, 2.0, 0.2),  # weak match
}


class FakeResp:
    def __init__(self, status=200, text="", data=None):
        self.status_code, self.text, self._data = status, text, data

    def json(self):
        return self._data


def fake_ban_post(url, files=None, data=None, timeout=None):
    """Mimics the bulk endpoint: same rows back, plus latitude, longitude, result_score."""
    sent = pd.read_csv(io.BytesIO(files["data"][1]), dtype=str, keep_default_na=False)
    by_postcode = {**dict(zip(RAW["Code Postal"], RAW["Magasin"])), "01000": "HYPER A"}
    rows = []
    for _, r in sent.iterrows():
        lat, lon, score = COORDS[by_postcode[r["Code Postal"]]]
        rows.append({**r.to_dict(), "latitude": lat, "longitude": lon, "result_score": score})
    return FakeResp(text=pd.DataFrame(rows).to_csv(index=False))


# ---------- stores.py ----------

def test_haversine_known_distances():
    assert stores.haversine_km(*PARIS, *PARIS) == pytest.approx(0)
    assert stores.haversine_km(*PARIS, *LYON) == pytest.approx(392, abs=3)


@pytest.mark.parametrize(
    "fmt,kind",
    [
        ("Hypermarché", "Hypermarché"), ("MARKET", "Market"), ("CARREFOUR MARKET", "Market"),
        ("Market (PROVENCIA)", "Market"), ("CARREFOUR CITY", "City"), ("CARREFOUR EXPRESS", "Express"),
        ("CARREFOUR CONTACT", "Contact"), ("CARREFOUR MONTAGNE", "Montagne"), ("AUTRES ENSEIGNES", "Other"),
    ],
)
def test_store_kind(fmt, kind):
    assert stores.store_kind(fmt) == kind


class Http:
    def __init__(self, features):
        self.features = features

    def get(self, url, params=None, timeout=None):
        self.params = params
        return FakeResp(data={"features": self.features})


def feat(label, postcode, city, score, lon=2.37, lat=48.86):
    return {"properties": {"label": label, "postcode": postcode, "city": city, "score": score},
            "geometry": {"coordinates": [lon, lat]}}


def test_find_places_ok_and_not_found():
    http = Http([feat("1 Rue X 75011 Paris", "75011", "Paris", 0.9)])
    p = stores.find_places("x", http=http)[0]
    assert (p["latitude"], p["longitude"], p["label"]) == (48.86, 2.37, "1 Rue X 75011 Paris")
    assert http.params["limit"] == 5
    with pytest.raises(stores.AddressError):
        stores.find_places("zzzz", http=Http([]))


RICHARD = [
    feat("Rue Richard-Lenoir 35000 Rennes", "35000", "Rennes", 0.7),
    feat("Rue Richard Lenoir 75011 Paris", "75011", "Paris", 0.68),
    feat("Rue Richard Lenoir 93100 Montreuil", "93100", "Montreuil", 0.66),
    feat("Rue Du Bruit 12345 Nowhere", "12345", "Nowhere", 0.1),  # noise
]


def test_needs_choice_when_no_postcode_and_several_places():
    places = stores.find_places("rue richard lenoir", http=Http(RICHARD))
    assert stores.needs_choice("rue richard lenoir", places)


def test_no_choice_when_a_postcode_was_typed():
    places = stores.find_places("rue richard lenoir 75011", http=Http(RICHARD))
    assert not stores.needs_choice("rue richard lenoir 75011", places)


def test_no_choice_when_only_one_place_scores_well():
    places = stores.find_places("x", http=Http([RICHARD[0], RICHARD[3]]))
    assert not stores.needs_choice("x", places)


def test_no_choice_for_a_plain_town_name():
    places = stores.find_places("Rennes", http=Http([feat("Rennes", "35000", "Rennes", 0.9), RICHARD[1]]))
    assert not stores.needs_choice("rennes", places)


def prepared(tmp_path):
    out, _ = prepare_data.build(RAW, post=fake_ban_post, min_rows=1)
    path = tmp_path / "stores.csv"
    out.to_csv(path, index=False)
    return stores.load_stores(path)


def test_load_missing_file_and_columns(tmp_path):
    with pytest.raises(stores.DataMissing):
        stores.load_stores(tmp_path / "nope.csv")
    RAW.to_csv(tmp_path / "raw.csv", index=False)  # no latitude/longitude yet
    with pytest.raises(stores.DataMissing):
        stores.load_stores(tmp_path / "raw.csv")


def test_nearest_filters_and_order(tmp_path):
    df = prepared(tmp_path)
    res = stores.nearest(df, *PARIS, accepts="drive", kinds=["Hypermarché", "Market"], radius_km=50, limit=10)
    assert list(res["Magasin"]) == ["HYPER A"]  # MARKET B has no Drive, FAR is out of radius, NO COORDS left out
    res = stores.nearest(df, *PARIS, accepts="store", kinds=None, radius_km=50)
    assert list(res["Magasin"]) == ["CITY C", "MARKET B", "HYPER A"]  # nearest first
    assert res["distance_km"].is_monotonic_increasing
    res = stores.nearest(df, *PARIS, accepts="drive", kinds=["Hypermarché"], radius_km=None)
    assert list(res["Magasin"]) == ["HYPER A", "HYPER FAR"]
    assert len(stores.nearest(df, *PARIS, accepts="store", limit=2)) == 2


# ---------- prepare_data.py ----------

def test_prepare_blanks_weak_matches_and_keeps_text_postcodes():
    raw = RAW.copy()
    raw.loc[0, "Code Postal"] = "1000"  # lost its leading zero in some export
    raw["Distance"] = "12.3"  # a home-based column that must not travel
    raw["IDF"] = "OUI"
    out, stats = prepare_data.build(raw, post=fake_ban_post, min_rows=1)
    assert list(out.columns) == stores.COLUMNS + ["latitude", "longitude", "geocode_score"]
    assert out.loc[0, "Code Postal"] == "01000"
    weak = out[out["Magasin"] == "NO COORDS"].iloc[0]
    assert pd.isna(weak["latitude"]) and pd.isna(weak["longitude"])
    assert stats["rows"] == 5 and stats["with_coordinates"] == 4


def test_prepare_stops_on_short_input_and_missing_columns():
    with pytest.raises(SystemExit):
        prepare_data.build(RAW, post=fake_ban_post)  # default minimum is about 2,000 rows
    with pytest.raises(SystemExit):
        prepare_data.clean(RAW.drop(columns=["Format"]))


def test_prepare_stops_when_address_service_fails():
    with pytest.raises(SystemExit):
        prepare_data.build(RAW, post=lambda *a, **k: FakeResp(status=503), min_rows=1)


def test_expand_address_spells_out_abbreviations():
    f = prepare_data.expand_address
    assert f("215 Bvd Ferdinand De Lesseps") == "215 Boulevard Ferdinand De Lesseps"
    assert f("16 Route Nationale (Rn25)") == "16 Route Nationale"
    assert f("Rd 1005") == "Route Départementale 1005"
    assert f("147 Chaussee St Pierre") == "147 Chaussee Saint Pierre"
    assert f("106-108 Av Ch De Gaulle") == "106-108 Avenue Ch De Gaulle"
    assert f("Place Du Beffroi") == "Place Du Beffroi"


def test_expanded_address_is_sent_but_the_original_is_kept():
    sent = {}
    def post(url, files=None, data=None, timeout=None):
        sent.setdefault("csv", files["data"][1].decode())  # the first request
        return fake_ban_post(url, files=files, data=data, timeout=timeout)
    raw = RAW.copy()
    raw.loc[0, "Adresse"] = "1 Bvd A"
    out, _ = prepare_data.build(raw, post=post, min_rows=1)
    assert "1 Boulevard A" in sent["csv"]
    assert out.loc[0, "Adresse"] == "1 Bvd A"


def service(answers):
    """Fake bulk endpoint: answers[(use_postcode, Ville)] = (score, city) or nothing at all."""
    calls = []
    def post(url, files=None, data=None, timeout=None):
        sent = pd.read_csv(io.BytesIO(files["data"][1]), dtype=str, keep_default_na=False)
        calls.append(("postcode" in data, len(sent)))
        rows = []
        for _, r in sent.iterrows():
            a = answers.get(("postcode" in data, r["Ville"]))
            row = {**r.to_dict(), "latitude": "", "longitude": "", "result_score": "", "result_city": ""}
            if a:
                row.update(latitude=48.0, longitude=2.0, result_score=a[0], result_city=a[1])
            rows.append(row)
        return FakeResp(text=pd.DataFrame(rows).to_csv(index=False))
    post.calls = calls
    return post


def geo(rows, answers):
    df = pd.DataFrame(rows, columns=["Adresse", "Ville", "Code Postal"])
    post = service(answers)
    return prepare_data.geocode_all(df, post=post), post


def test_geocode_accepts_a_lower_score_only_for_the_same_town():
    res, _ = geo(
        [["Rue A", "Amiens", "80000"], ["Rue B", "Gueux", "51390"]],
        {(True, "Amiens"): (0.45, "Amiens"), (True, "Gueux"): (0.45, "Reims")},
    )
    assert res["latitude"].notna().tolist() == [True, False]


def test_geocode_retries_without_the_postcode_and_needs_the_same_town():
    res, post = geo(
        [["Rue A", "Antony", "92002"], ["Rue B", "Albi", "81000"], ["Rue C", "Brioude", "43100"]],
        {(False, "Antony"): (0.8, "Antony"), (False, "Albi"): (0.9, "Toulouse"), (True, "Brioude"): (0.9, "Brioude")},
    )
    assert res["latitude"].notna().tolist() == [True, False, True]
    assert post.calls == [(True, 3), (False, 2)]  # only the two unmatched rows are retried
    assert prepare_data._norm("Saint-Étienne") == prepare_data._norm("ST ETIENNE")
