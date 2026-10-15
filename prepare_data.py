"""Turn Carrefour's Carte Cadeau acceptance list into data/stores.csv (adds coordinates).

Usage:
    python3 prepare_data.py list.pdf     the PDF from "Trouver mon magasin" on
                                         https://www.carrefour.fr/services/cartes-cadeaux
    python3 prepare_data.py --fetch      download that PDF first (may be refused from some networks)
    python3 prepare_data.py list.csv     an already parsed list (columns Magasin, Code Postal, Ville,
                                         Adresse, Format, En Magasin, En Drive)

Any extra CSV column (for example a distance measured from someone's home) is dropped.
Coordinates come from the French government address service (BAN), in one free request.
A store whose address is matched with low confidence gets no coordinates and is left out of
the rankings, instead of being placed in the wrong spot.
"""

import io
import json
import re
import sys
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
import requests

import pdf_source
from stores import COLUMNS

BAN_CSV_URL = "https://api-adresse.data.gouv.fr/search/csv/"
OUT_DIR = Path(__file__).parent / "data"
MIN_ROWS = 2000  # the list had about 4,000 rows; far fewer means the export is incomplete
MIN_SCORE = 0.5
LOOSE_SCORE = 0.35  # accepted only when the service returns the same town as the list


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in COLUMNS if c not in raw.columns]
    if missing:
        raise SystemExit(f"Missing columns in the input: {', '.join(missing)}")
    df = raw[COLUMNS].copy()
    for c in COLUMNS:
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["Code Postal"] = df["Code Postal"].str.zfill(5)  # keep leading zeros (01000, not 1000)
    return df.reset_index(drop=True)


ABBREVIATIONS = {  # as written in the PDF -> full word, so the address service can match them
    "bvd": "Boulevard", "bd": "Boulevard", "bld": "Boulevard", "av": "Avenue", "ave": "Avenue",
    "rte": "Route", "rd": "Route Départementale", "rn": "Route Nationale", "pl": "Place",
    "imp": "Impasse", "fg": "Faubourg", "fbg": "Faubourg", "cc": "Centre Commercial",
    "crs": "Cours", "esp": "Esplanade", "sq": "Square", "chem": "Chemin", "dr": "Docteur",
    "st": "Saint", "ste": "Sainte",
}
# "Ch" is left alone: it can mean Charles, Chemin or Champs.


def expand_address(addr: str) -> str:
    """Spell out abbreviations and drop bracketed notes. Only used for the lookup, never stored."""
    addr = re.sub(r"\([^)]*\)", " ", str(addr))
    words = [ABBREVIATIONS.get(w.lower(), w) for w in re.split(r"(?<=\w)\.?\s+", addr.strip())]
    return " ".join(w for w in words if w)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).replace("saint", "st").strip()


def _lookup(df: pd.DataFrame, post, use_postcode: bool) -> pd.DataFrame:
    """One bulk request -> score, lat, lon and the town the service matched, one row per df row."""
    upload = df[["Adresse", "Ville", "Code Postal"]].copy()
    upload["Adresse"] = upload["Adresse"].map(expand_address)
    upload.insert(0, "id", range(len(upload)))
    form = {"columns": ["Adresse", "Ville"]}
    if use_postcode:
        form["postcode"] = "Code Postal"
    r = post(
        BAN_CSV_URL,
        files={"data": ("stores.csv", upload.to_csv(index=False).encode("utf-8"), "text/csv")},
        data=form,
        timeout=300,
    )
    if r.status_code >= 400:
        raise SystemExit(f"Address service answered HTTP {r.status_code}")
    out = pd.read_csv(io.StringIO(r.text), dtype=str, keep_default_na=False)
    out = out.set_index(out["id"].astype(int)).reindex(range(len(df)))
    city = out["result_city"] if "result_city" in out else pd.Series("", index=out.index)
    return pd.DataFrame({
        "score": pd.to_numeric(out["result_score"], errors="coerce"),
        "lat": pd.to_numeric(out["latitude"], errors="coerce"),
        "lon": pd.to_numeric(out["longitude"], errors="coerce"),
        "same_town": [bool(_norm(c)) and _norm(c) == _norm(v) for c, v in zip(city.fillna(""), df["Ville"])],
    })


def _accepted(r: pd.DataFrame, min_score: float, loose_score: float) -> pd.Series:
    has_point = r["lat"].notna() & r["lon"].notna() & r["score"].notna()
    return has_point & ((r["score"] >= min_score) | ((r["score"] >= loose_score) & r["same_town"]))


def geocode_all(df: pd.DataFrame, post=requests.post, min_score: float = MIN_SCORE,
                loose_score: float = LOOSE_SCORE) -> pd.DataFrame:
    """Add latitude, longitude and geocode_score columns (blank when the match is weak).

    Pass 1 searches inside the postcode printed in the PDF. A weaker score is accepted when the
    service returns the same town as the Ville column. Pass 2 retries what is still missing
    without the postcode (the PDF has some wrong ones) and only accepts a match in the same town.
    """
    r = _lookup(df, post, use_postcode=True)
    ok = _accepted(r, min_score, loose_score)
    todo = df.index[~ok]
    if len(todo):
        r2 = _lookup(df.loc[todo].reset_index(drop=True), post, use_postcode=False)
        ok2 = _accepted(r2, min_score, loose_score) & r2["same_town"]
        r2.index, ok2.index = todo, todo
        for col in ("score", "lat", "lon"):
            r.loc[todo[ok2], col] = r2.loc[todo[ok2], col]
        ok = ok | ok2.reindex(df.index, fill_value=False)
    res = df.copy()
    res["latitude"] = r["lat"].where(ok).round(6).to_numpy()
    res["longitude"] = r["lon"].where(ok).round(6).to_numpy()
    res["geocode_score"] = r["score"].round(3).to_numpy()
    return res


def build(raw: pd.DataFrame, post=requests.post, min_rows: int = MIN_ROWS):
    df = clean(raw)
    if len(df) < min_rows:
        raise SystemExit(f"Only {len(df)} rows (expected about 4,000). The export looks incomplete; stopping.")
    out = geocode_all(df, post=post)
    stats = {
        "built_at": date.today().isoformat(),
        "rows": int(len(out)),
        "with_coordinates": int(out["latitude"].notna().sum()),
    }
    return out, stats


def read_input(arg: str):
    """PDF, CSV or --fetch -> (raw DataFrame, lines to look at)."""
    if arg == "--fetch":
        arg = str(pdf_source.fetch_pdf(Path(tempfile.mkdtemp()) / "list.pdf"))
    if arg.lower().endswith(".pdf"):
        return pdf_source.parse_text(pdf_source.pdf_to_text(arg))
    return pd.read_csv(arg, dtype=str, keep_default_na=False), []


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        raise SystemExit(__doc__)
    raw, review = read_input(argv[0])
    out, stats = build(raw)
    OUT_DIR.mkdir(exist_ok=True)
    out.to_csv(OUT_DIR / "stores.csv", index=False)
    (OUT_DIR / "stores_meta.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    left_out = stats["rows"] - stats["with_coordinates"]
    print(f"{stats['rows']} stores, {stats['with_coordinates']} with coordinates, {left_out} left out (weak address match).")
    if review:
        print(f"{len(review)} lines need a look (first 10):")
        for r in review[:10]:
            print("  ", r)
    print(f"Written: {OUT_DIR / 'stores.csv'}")


if __name__ == "__main__":
    main()
