"""Carte Cadeau store finder: data loading, geocoding and ranking (no Streamlit here)."""

import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BAN_URL = "https://api-adresse.data.gouv.fr/search/"
EARTH_RADIUS_KM = 6371.0088
MIN_PLACE_SCORE = 0.4  # candidates scoring lower are noise
DATA_FILE = Path(__file__).parent / "data" / "stores.csv"
COLUMNS = ["Magasin", "Code Postal", "Ville", "Adresse", "Format", "En Magasin", "En Drive"]
KINDS = ["Hypermarché", "Market", "City", "Express", "Contact", "Montagne", "Other"]


class DataMissing(Exception):
    """The prepared store file is absent or has the wrong shape."""


class AddressError(Exception):
    """The address could not be turned into coordinates."""


def store_kind(fmt: str) -> str:
    """Group the PDF's Format values: 'Hypermarché', 'MARKET', 'CARREFOUR MARKET', 'Market (PROVENCIA)'..."""
    f = (fmt or "").lower()
    if "hyper" in f:
        return "Hypermarché"
    if "market" in f:
        return "Market"
    for word, kind in (("city", "City"), ("express", "Express"), ("contact", "Contact"), ("montagne", "Montagne")):
        if word in f:
            return kind
    return "Other"


def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line distance in km. Accepts numbers or numpy/pandas arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def find_places(address: str, http=requests, limit: int = 5) -> list:
    """Address -> up to `limit` candidate places, best first, from the French government
    address service (free, no key). Each: label, latitude, longitude, postcode, city, score."""
    try:
        r = http.get(BAN_URL, params={"q": address, "limit": limit}, timeout=15)
    except requests.RequestException as e:
        raise AddressError(f"address service unreachable ({e.__class__.__name__})") from e
    if r.status_code >= 400:
        raise AddressError(f"address service answered HTTP {r.status_code}")
    places = []
    for f in r.json().get("features", []):
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
        places.append({
            "label": p.get("label", address), "latitude": lat, "longitude": lon,
            "postcode": p.get("postcode", ""), "city": p.get("city", ""), "score": p.get("score") or 0,
        })
    if not places:
        raise AddressError("Address not found. Try a street address or a town in France.")
    return places


def needs_choice(address: str, places: list) -> bool:
    """True when the typed address names no postcode and could be in several places, so the
    visitor must pick instead of the app guessing."""
    if re.search(r"\b\d{5}\b", address):  # a postcode was typed: the first match is the one
        return False
    near = [p for p in places if p["score"] >= MIN_PLACE_SCORE]
    if len(near) < 2:
        return False
    if _plain(address) == _plain(near[0]["city"]):  # just a town name, matched exactly
        return False
    return len({p["postcode"] for p in near}) > 1


def load_stores(path=None) -> pd.DataFrame:
    """Read the prepared file (see prepare_data.py) and add kind / in_store / drive columns."""
    path = Path(path) if path else DATA_FILE
    if not path.exists():
        raise DataMissing(f"{path.name} not found. Run prepare_data.py first (see README).")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in COLUMNS + ["latitude", "longitude"] if c not in df.columns]
    if missing:
        raise DataMissing(f"Columns missing in {path.name}: {', '.join(missing)}. Run prepare_data.py.")
    df["kind"] = df["Format"].map(store_kind)
    df["in_store"] = df["En Magasin"].str.upper().eq("OUI")
    df["drive"] = df["En Drive"].str.upper().eq("OUI")
    for c in ("latitude", "longitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def nearest(df, lat, lon, *, accepts="drive", kinds=None, radius_km=None, limit=10) -> pd.DataFrame:
    """Stores accepting the card (at the Drive or in store), nearest first.

    Stores without usable coordinates are left out: a wrong position is worse than none.
    """
    d = df.dropna(subset=["latitude", "longitude"])
    d = d[d["drive"]] if accepts == "drive" else d[d["in_store"]]
    if kinds:
        d = d[d["kind"].isin(kinds)]
    d = d.assign(distance_km=haversine_km(lat, lon, d["latitude"], d["longitude"]))
    if radius_km is not None:
        d = d[d["distance_km"] <= radius_km]
    return d.sort_values("distance_km").head(limit).reset_index(drop=True)
