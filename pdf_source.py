"""Carrefour's published Carte Cadeau acceptance list (PDF): find it, download it, parse it.

The PDF is linked from the "Trouver mon magasin" button on LANDING_URL. Its file name changes
with every update, so the link is always read from that page, never guessed.
"""

import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

from stores import COLUMNS

LANDING_URL = "https://www.carrefour.fr/services/cartes-cadeaux"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
# Values of the Format column, longest first so "CARREFOUR MARKET" wins over "MARKET".
FORMATS = sorted(
    [
        "CARREFOUR CITY", "CARREFOUR EXPRESS", "CARREFOUR CONTACT", "CARREFOUR MARKET",
        "CARREFOUR MONTAGNE", "Market (PROVENCIA)", "Hypermarché", "MARKET", "AUTRES ENSEIGNES",
    ],
    key=len,
    reverse=True,
)
FORMAT_BY_UPPER = {f.upper(): f for f in FORMATS}
NAME_FIXES = {"ST QUENTIN EN YVELIN": "ST QUENTIN EN YVELINE"}  # truncated in the PDF


# ---------- find and download ----------

def find_pdf_url(html: str, base: str = LANDING_URL) -> str:
    """The list link: a .pdf whose address mentions 'liste' and 'magasin'."""
    for href in re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, flags=re.I):
        h = href.lower()
        if "liste" in h and "magasin" in h:
            return urljoin(base, href)
    raise SystemExit(
        "Could not find the PDF link on the cartes-cadeaux page (the page may have changed). "
        "Download the PDF in your browser and pass its path instead."
    )


def _check(resp, what: str) -> None:
    if resp.status_code in (403, 429, 503) or resp.headers.get("cf-mitigated"):
        raise SystemExit(
            f"Carrefour refused the request for the {what} (HTTP {resp.status_code}). "
            f"Open {LANDING_URL} in your browser, click 'Trouver mon magasin' to download the PDF, "
            "then run: python3 prepare_data.py path/to/the.pdf"
        )
    if resp.status_code >= 400:
        raise SystemExit(f"Could not get the {what}: HTTP {resp.status_code}")


def fetch_pdf(dest: Path, session=None) -> Path:
    s = session or requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
    page = s.get(LANDING_URL, timeout=30)
    _check(page, "cartes-cadeaux page")
    url = find_pdf_url(page.text)
    pdf = s.get(url, timeout=120)
    _check(pdf, "PDF")
    Path(dest).write_bytes(pdf.content)
    return Path(dest)


def pdf_to_text(path) -> str:
    try:
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise SystemExit("pdftotext not found. On a Mac: brew install poppler") from None
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"pdftotext could not read the PDF: {e.stderr.strip()}") from None
    return r.stdout


# ---------- parse ----------

def _tokens(line: str) -> list:
    """Columns are separated by runs of 2+ spaces; single spaces occur inside values."""
    return [t.strip() for t in re.split(r"\s{2,}", line.strip()) if t.strip()]


def _is_header(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("Où dépenser") or ("Magasin" in s and "Code Postal" in s)


def _split_swallowed(cell: str):
    """'La Madelaine-sous-Montreuil 44 Place Du General' -> (commune, address). If it cannot be
    told apart, the cell stays as the commune and the address stays empty (and is reported)."""
    words = cell.split()
    for i, w in enumerate(words):  # the address starts at its street number
        if i > 0 and w[0].isdigit():
            return " ".join(words[:i]), " ".join(words[i:])
    hyphenated = [i for i, w in enumerate(words) if "-" in w]  # no number: the commune ends at its last hyphenated word
    if hyphenated and hyphenated[-1] < len(words) - 1:
        k = hyphenated[-1] + 1
        return " ".join(words[:k]), " ".join(words[k:])
    return cell, ""


def parse_tokens(tokens: list):
    """One store line -> dict of the 7 columns (+ 'review' when the address is missing), or None."""
    t = list(tokens)
    if len(t) >= 2:  # a very long commune glued to the postal code by one space
        m = re.match(r"^(\d{5})\s+(.*)$", t[1])
        if m:
            t[1:2] = [m.group(1), m.group(2)]
    if len(t) < 5 or not re.fullmatch(r"\d{5}", t[1]):
        return None
    rest = t[2:]
    fmt_i = next((i for i, x in enumerate(rest) if x.upper() in FORMAT_BY_UPPER), None)
    if fmt_i is None:  # address and format glued by one space: "1 Route D'amiens MARKET"
        for i, x in enumerate(rest):
            f = next((f for f in FORMATS if x.upper().endswith(" " + f.upper())), None)
            if f:
                rest[i:i + 1] = [x[: -len(f) - 1].strip(), f]
                fmt_i = i + 1
                break
    if fmt_i is None or fmt_i == 0:
        return None
    before, flags = rest[:fmt_i], rest[fmt_i + 1:]
    ville = before[0]
    adresse = " ".join(before[1:])
    if not adresse:  # a long commune can swallow the address: "<commune> <address>" in one cell
        ville, adresse = _split_swallowed(ville)
    magasin = NAME_FIXES.get(t[0], t[0])
    has = [f.upper() == "OUI" for f in flags]
    return {
        "Magasin": magasin,
        "Code Postal": t[1],
        "Ville": ville,
        "Adresse": adresse,
        "Format": FORMAT_BY_UPPER[rest[fmt_i].upper()],
        # With one OUI it is En Magasin: En Drive is simply absent when not accepted.
        "En Magasin": "OUI" if len(has) >= 1 and has[0] else "",
        "En Drive": "OUI" if len(has) >= 2 and has[1] else "",
    }


def parse_text(text: str):
    """Text of the PDF -> (DataFrame with the 7 columns, lines that need a human look)."""
    rows, review = [], []
    for line in text.replace("\f", "\n").splitlines():
        if _is_header(line):
            continue
        rec = parse_tokens(_tokens(line))
        if rec is None:
            review.append(f"unreadable line: {line.strip()}")
            continue
        if not rec["Adresse"]:
            review.append(f"no address: {rec['Magasin']} ({rec['Code Postal']} {rec['Ville']})")
        rows.append(rec)
    return pd.DataFrame(rows, columns=COLUMNS), review
