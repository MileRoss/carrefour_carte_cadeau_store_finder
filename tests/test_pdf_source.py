"""Tests for pdf_source.py. The lines mimic the quirks described for Carrefour's PDF; they are
made up, so a run on the real PDF is still the real test (see the README)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdf_source  # noqa: E402

LINES = [
    "Où dépenser votre Carte Cadeau Carrefour ?",
    "Magasin                    Code Postal   Ville          Adresse                    Format              En Magasin   En Drive",
    # normal: both accepted
    "ABBEVILLE -CORDELIER       80100         Abbeville      Rue Des Cordeliers         MARKET              OUI          OUI",
    # En Drive absent when not accepted
    "ABBEVILLE -AMIENS          80100         Abbeville      1 Route D'amiens           MARKET              OUI",
    # address and format glued by one space
    "ABBEVILLE -BOIS            80100         Abbeville      2-4 Chaussee Du Bois CARREFOUR EXPRESS         OUI",
    # nothing accepted: only five columns
    "ACME CLOSED                75011         Paris          3 Rue Close                CARREFOUR CITY",
    # long commune glued to the postal code
    "JAVERLHAC                  24300 Javerlhac-et-la-Chapelle-Saint-Robert   Rue Du Bourg   CARREFOUR CONTACT   OUI",
    # long commune that swallowed the address
    "ROUFFIGNAC -GRAULET        24580         Rouffignac-Saint-Cernin-de-Reilhac Les Plateaux De Graulet   CARREFOUR CONTACT   OUI",
    # truncated name in the PDF
    "ST QUENTIN EN YVELIN       78180         Montigny-le-Bretonneux   Cc Les Ulis   Hypermarché   OUI   OUI",
    "Page 1 / 90",
]
TEXT = "\n".join(LINES[:5]) + "\f" + "\n".join(LINES[5:])


def by_name(df):
    return {r["Magasin"]: r for _, r in df.iterrows()}


def test_parse_text_handles_the_known_quirks():
    df, review = pdf_source.parse_text(TEXT)
    rows = by_name(df)
    assert len(df) == 7
    both = rows["ABBEVILLE -CORDELIER"]
    assert (both["En Magasin"], both["En Drive"], both["Format"]) == ("OUI", "OUI", "MARKET")
    store_only = rows["ABBEVILLE -AMIENS"]
    assert (store_only["En Magasin"], store_only["En Drive"], store_only["Adresse"]) == ("OUI", "", "1 Route D'amiens")
    glued = rows["ABBEVILLE -BOIS"]
    assert (glued["Adresse"], glued["Format"]) == ("2-4 Chaussee Du Bois", "CARREFOUR EXPRESS")
    closed = rows["ACME CLOSED"]
    assert (closed["En Magasin"], closed["En Drive"], closed["Format"]) == ("", "", "CARREFOUR CITY")
    long_town = rows["JAVERLHAC"]
    assert (long_town["Code Postal"], long_town["Ville"], long_town["Adresse"]) == (
        "24300", "Javerlhac-et-la-Chapelle-Saint-Robert", "Rue Du Bourg")
    assert rows["ST QUENTIN EN YVELINE"]["Format"] == "Hypermarché"  # name fixed


def test_swallowed_address_is_split_from_the_commune():
    df, _ = pdf_source.parse_text(TEXT)
    r = by_name(df)["ROUFFIGNAC -GRAULET"]
    assert (r["Ville"], r["Adresse"]) == ("Rouffignac-Saint-Cernin-de-Reilhac", "Les Plateaux De Graulet")
    assert pdf_source._split_swallowed("La Madelaine-sous-Montreuil 44 Place Du General De Gaulle") == (
        "La Madelaine-sous-Montreuil", "44 Place Du General De Gaulle")
    assert pdf_source._split_swallowed("Villedieu-les-Poêles-Rouffigny 19 Place Des Chevaliers") == (
        "Villedieu-les-Poêles-Rouffigny", "19 Place Des Chevaliers")


def test_parse_text_flags_what_cannot_be_read_instead_of_hiding_it():
    df, review = pdf_source.parse_text(TEXT)
    assert any("Page 1 / 90" in r for r in review)
    assert not any(r.startswith("no address") for r in review)
    _, review = pdf_source.parse_text("X STORE   75011   Le Mans   CARREFOUR CITY   OUI")
    assert review == ["no address: X STORE (75011 Le Mans)"]


def test_pdf_end_to_end_with_a_real_pdf(tmp_path):
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    if not pdf_source.subprocess.run(["which", "pdftotext"], capture_output=True).stdout:
        pytest.skip("pdftotext not installed")
    pdf = tmp_path / "list.pdf"
    c = reportlab.Canvas(str(pdf), pagesize=(1200, 800))
    c.setFont("Courier", 8)
    y = 780
    for line in LINES:
        c.drawString(20, y, line)
        y -= 12
    c.save()
    df, review = pdf_source.parse_text(pdf_source.pdf_to_text(pdf))
    assert set(df["Magasin"]) >= {"ABBEVILLE -CORDELIER", "ABBEVILLE -AMIENS", "ABBEVILLE -BOIS", "JAVERLHAC"}
    assert len(df) == 7  # same result as parsing the text directly


LANDING_HTML = """
<a href="/static/cfr/conditions-generales.pdf">CGV</a>
<a class="btn" href="https://static.carrefour.fr/static/cfr/C4FR/2026/06-juin/modalites/Liste-acceptation-des-CC-Magasins-26-mai-2026.pdf">Trouver mon magasin</a>
"""


class FakeResp:
    def __init__(self, status=200, text="", content=b"", headers=None):
        self.status_code, self.text, self.content, self.headers = status, text, content, headers or {}


class FakeSession:
    def __init__(self, landing=None, pdf=None):
        self.headers, self.calls = {}, []
        self.landing = landing or FakeResp(text=LANDING_HTML)
        self.pdf = pdf or FakeResp(content=b"%PDF-fake")

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.landing if url == pdf_source.LANDING_URL else self.pdf


def test_find_pdf_url_picks_the_list_not_other_pdfs():
    assert pdf_source.find_pdf_url(LANDING_HTML).endswith("Liste-acceptation-des-CC-Magasins-26-mai-2026.pdf")
    assert pdf_source.find_pdf_url('<a href="/docs/Liste-des-Magasins.pdf">x</a>') == "https://www.carrefour.fr/docs/Liste-des-Magasins.pdf"
    with pytest.raises(SystemExit):
        pdf_source.find_pdf_url('<a href="/a/other.pdf">x</a>')


def test_fetch_pdf_downloads_the_linked_file(tmp_path):
    s = FakeSession()
    out = pdf_source.fetch_pdf(tmp_path / "list.pdf", session=s)
    assert out.read_bytes() == b"%PDF-fake"
    assert s.calls[1].endswith("26-mai-2026.pdf")


def test_fetch_pdf_explains_what_to_do_when_refused(tmp_path):
    s = FakeSession(landing=FakeResp(status=403, headers={"cf-mitigated": "challenge"}))
    with pytest.raises(SystemExit) as e:
        pdf_source.fetch_pdf(tmp_path / "list.pdf", session=s)
    assert "browser" in str(e.value) and "prepare_data.py" in str(e.value)
