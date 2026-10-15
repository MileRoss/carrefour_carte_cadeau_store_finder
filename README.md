# Which Carrefour drives accept the Carte Cadeau?

Enter an address in France and see the nearest Carrefour drives that accept the Carte Cadeau, with a clickable map and a Google Maps directions link for each drive.

**Live app:** https://carrefour-carte-cadeau-store-finder.streamlit.app/

## What it does
- Takes an address or a town and finds the nearest drives, sorted by distance.
- Filters: store format (Hypermarché, Market, City...), radius, number of drives shown.
- The table and the map both link to Google Maps directions, starting from the address you typed.
- If the address could be in several places (no postcode typed), the page asks you to pick one instead of guessing.
- The address is kept in the page link, so a bookmarked link opens with the search already done. The app does not store it.

## How it works
1. **Source:** Carrefour's published Carte Cadeau acceptance list (PDF, about 4,000 stores), linked from carrefour.fr/services/cartes-cadeaux.
2. **Parsing:** `pdf_source.py` turns the PDF text into a table (store, postcode, town, address, format, accepted in store / at the drive).
3. **Coordinates:** `prepare_data.py` adds latitude and longitude from the French government address service (BAN, free, no key) and writes `data/stores.csv`.
4. **App:** `app.py` (Streamlit) geocodes the visitor's address, ranks the drives by straight-line distance and draws the map (Leaflet, OpenStreetMap tiles).

The app makes no request to Carrefour at runtime. It only reads the prepared list, which is a monthly snapshot; the page shows the date it was built.

## Data problems found and handled
The PDF is a printed table, so reading it back needed care:
- Columns are separated by runs of spaces, but single spaces also occur inside values.
- The "En Drive" column is absent, not "NON", when a store does not accept it.
- Address and format sometimes run together in one cell.
- Very long town names swallow the postcode or the address.
- A store name is truncated in the PDF; page footers are mixed in with the rows.
- Some postcodes in the PDF are wrong or generic, and some territories (Monaco) are outside the address service.

On the real PDF the parser reads 4,048 stores. Coordinates were first found for 3,773. Spelling out street abbreviations did not help (2 more). Listing the match scores showed two causes: stores with no answer because of wrong postcodes, and matches scored just under the cutoff. Retrying without the postcode, and accepting a lower score only when the matched town equals the listed town, brought it to 3,940 (97%). The other 108 are left out of the results rather than shown in the wrong place.

## Edge cases tested on the address search
| Input | Problem | Fix |
|---|---|---|
| `900 Bd Richard Lenoir` | The number does not exist, so the service fell back to a street in Rennes | Ask the visitor to pick |
| `89 Bd Richard Lenoir` (no postcode or town) | Worked only because the address is rare | Ask the visitor to pick |
| `rue richard lenoir` | Exists in Paris, Montreuil, Villemomble, Rennes...; one was chosen silently | Ask the visitor to pick |
| `rue richard lenoir 75011` | none | Goes straight through |
| Directions start | Google started from the visitor's current location, which is not the searched address | Start is the searched address |
| Directions start as coordinates | Google moved the start to a nearby business | Start is sent as address text |
| Map background | The tile provider (CARTO) now needs an API key | Switched to OpenStreetMap tiles |

## Run it
```
pip install -r requirements.txt
streamlit run app.py
```
Tests (no network): `pip install pytest && pytest` (36 tests: parser, geocoding, ranking, page behaviour with sample data).

## Refresh the data
1. One time: `brew install poppler` (gives `pdftotext`).
2. Open https://www.carrefour.fr/services/cartes-cadeaux, click "Trouver mon magasin", save the PDF. (`python3 prepare_data.py --fetch` tries to download it, but Carrefour may refuse the request.)
3. `python3 prepare_data.py path/to/the.pdf`. It prints the row count and any lines it could not read.
4. Commit the new `data/stores.csv` and `data/stores_meta.json`.

## Limits
- Distances are in a straight line, not by road.
- The list is only as recent as the last PDF; stores can change what they accept.
- 108 stores have no coordinates and never appear in results.
- OpenStreetMap's public tile server is meant for light use.
- The monthly refresh is manual.

Built with Claude (Anthropic's AI assistant).
