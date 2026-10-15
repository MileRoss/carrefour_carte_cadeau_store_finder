"""Where can I use my Carrefour Carte Cadeau? Nearest stores that accept it, from an address."""

import json
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import stores

st.set_page_config(page_title="Carte Cadeau stores near you", page_icon="🎁", layout="wide")

DEFAULT_KINDS = ["Hypermarché", "Market"]


@st.cache_data
def get_stores() -> pd.DataFrame:
    return stores.load_stores()


def directions_url(lat, lon, origin=None) -> str:
    """Google Maps directions to the store. `origin` is the searched address as text: with
    coordinates, Google snaps the start to whatever business stands nearest."""
    url = f"https://www.google.com/maps/dir/?api=1&destination={quote(f'{lat:.6f},{lon:.6f}', safe=',')}"
    return url + (f"&origin={quote(origin)}" if origin else "")


MAP_HTML = """
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#m{height:100%;margin:0} .leaflet-tooltip{font:13px sans-serif}</style>
<div id="m"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const stores = __STORES__, me = __ME__;
const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
const map = L.map("m");
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  {attribution: "&copy; OpenStreetMap contributors", maxZoom: 19}).addTo(map);
if (dark) document.querySelector(".leaflet-tile-pane").style.filter = "invert(1) hue-rotate(180deg) brightness(0.9) contrast(0.9)";
const pts = [[me.lat, me.lon]];
L.circleMarker([me.lat, me.lon], {radius: 8, color: "#fff", weight: 2, fillColor: "#1a73e8", fillOpacity: 1})
  .bindTooltip("Your address").addTo(map);
stores.forEach(s => {
  pts.push([s.lat, s.lon]);
  const m = L.circleMarker([s.lat, s.lon], {radius: 8, color: "#fff", weight: 2, fillColor: "#e5483f", fillOpacity: 1}).addTo(map);
  m.bindTooltip(s.name + "<br>" + s.address + "<br><i>Click for directions</i>");
  m.on("click", () => window.open(s.url, "_blank", "noopener"));
});
map.fitBounds(pts, {padding: [30, 30]});
</script>
"""


def store_map(res: pd.DataFrame, where: dict) -> None:
    """Clickable map: each red dot opens Google Maps directions to that store in a new tab."""
    origin = where["label"]
    items = [
        {
            "name": r["Magasin"],
            "address": f"{r['Adresse']}, {r['Code Postal']} {r['Ville']}",
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
            "url": directions_url(r["latitude"], r["longitude"], origin),
        }
        for _, r in res.iterrows()
    ]
    me = {"lat": float(where["latitude"]), "lon": float(where["longitude"])}
    safe = lambda obj: json.dumps(obj).replace("</", "<\\/")  # keep store text from closing the script tag
    html = MAP_HTML.replace("__STORES__", safe(items)).replace("__ME__", safe(me))
    components.html(html, height=460)


def list_date():
    meta = stores.DATA_FILE.with_name("stores_meta.json")
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("built_at")
    except (OSError, ValueError):
        return None


st.title("Which Carrefour drives accept the Carte Cadeau?")
st.write(
    "Enter an address in France. The app lists the nearest Carrefour drives that accept the "
    "Carte Cadeau, from Carrefour's published acceptance list."
)

try:
    df = get_stores()
except stores.DataMissing as e:
    st.error(str(e))
    st.stop()

saved = st.query_params.get("address", "")

with st.form("search"):
    address = st.text_input("Your address or town", value=saved, placeholder="e.g. 10 rue de Rivoli, Paris")
    c1, c2, c3 = st.columns([2, 2, 2])
    kinds = c1.multiselect("Store format", stores.KINDS, default=DEFAULT_KINDS)
    radius = c2.slider("Radius (km)", 1, 50, 15)
    limit = c3.slider("Drives shown", 3, 30, 10)
    go = st.form_submit_button("Search", type="primary")

# A bookmarked link carries the address, so the search runs on its own when the page opens.
if not (go or saved):
    st.stop()

address = address.strip()
if not address:
    st.warning("Enter an address or a town.")
    st.stop()
if not kinds:
    st.warning("Pick at least one store format.")
    st.stop()

try:
    places = stores.find_places(address)
except stores.AddressError as e:
    st.error(str(e))
    st.stop()

if stores.needs_choice(address, places):
    st.warning("Several places match. Pick yours, or add a postcode or town to the address.")
    labels = [p["label"] for p in places]
    picked = st.radio("Which one?", labels, index=None)
    if picked is None:
        st.stop()
    st.query_params["address"] = picked  # the full label names the postcode, so the link is exact
    st.rerun()
where = places[0]

st.query_params["address"] = address
st.caption(f"Searching around: {where['label']}")

res = stores.nearest(df, where["latitude"], where["longitude"], accepts="drive", kinds=kinds, radius_km=radius, limit=limit)
if res.empty:
    st.info("No drive found. Try a bigger radius or more store formats.")
    st.stop()

best = res.iloc[0]
st.subheader("Nearest")
st.markdown(
    f"**{best['Magasin']}** ({best['kind']}), {best['Adresse']}, {best['Code Postal']} {best['Ville']}: "
    f"**{best['distance_km']:.1f} km** away"
)

table = pd.DataFrame(
    {
        "Distance": res["distance_km"],
        "Store": res["Magasin"],
        "Format": res["kind"],
        "Address": res["Adresse"] + ", " + res["Code Postal"] + " " + res["Ville"],
        "Directions": [directions_url(a, b, where["label"]) for a, b in zip(res["latitude"], res["longitude"])],
    }
)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "Distance": st.column_config.NumberColumn("Distance", format="%.1f km"),
        "Directions": st.column_config.LinkColumn("Directions", display_text="Open in Google Maps"),
    },
)
store_map(res, where)

built = list_date()
st.caption(
    "Distances are in a straight line, not by road. "
    + (f"Store list built on {built}. " if built else "")
    + "To keep your address, bookmark this page: the link remembers it, and the app does not store it."
)
