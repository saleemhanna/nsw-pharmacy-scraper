# NSW Pharmacy Register Scraper

Weekly automated scrape of the [NSW Pharmacy Register](https://verify.licence.nsw.gov.au/home/Pharmacies) into a Google Sheet. Captures every current pharmacy + a week-over-week change log.

For first-time setup (GCP project, service account, GitHub secrets), see [SETUP.md](./SETUP.md).

## Architecture

The register is served by a clean public JSON API at `verify.licence.nsw.gov.au/publicregisterapi/api/v1/licence/search/`. This scraper:

1. Enumerates NSW postcodes (2000–2999) — there's no unfiltered "give me everything" endpoint, but the API hard-caps any single query at 200 records, so we partition by postcode (densest is Sydney 2000 with ~23 pharmacies — well under the cap).
2. For each postcode, calls `areasnoCount` to resolve the `locationId`, then `advQuery` with `location:[locationId]` to fetch all pharmacies registered at that postcode.
3. Deduplicates by `licenceNumber` (the registration number, e.g. `PC0024595`).
4. Diffs against the previous `Current` tab contents, then overwrites `Current` and appends rows to `Changes` and `Runs`.

No browser, no HTML parsing, no detail-page fetching — the list response is the full record.

## Files

| File | Responsibility |
|---|---|
| `src/api.py` | httpx async client wrapping `areasnoCount` and `advQuery` with tenacity retries |
| `src/postcodes.py` | NSW postcode enumeration (2000–2999) |
| `src/models.py` | Pydantic `Pharmacy` record + `from_api` parser |
| `src/scraper.py` | Orchestrator: iterate postcodes, fetch, dedupe, write `snapshot.json` |
| `src/diff.py` | Week-over-week diff (excludes `scraped_at` + `raw_json` from change detection) |
| `src/sheets.py` | gspread I/O: read previous `Current`, write all three tabs |
| `src/main.py` | CLI entrypoint, reads env vars, runs everything |

## Local run

```powershell
# From the project directory
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Set credentials
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content sa.json -Raw
$env:SHEET_ID = "1502YpdciO9NSeyzBEr6wf75Y5Gpan-qPXw0elKHve0c"   # optional, has a default

# Limit to a few postcodes for testing (otherwise it scrapes all ~1000)
$env:SCRAPE_POSTCODES = "2000,2010,2030,2088,2300"

python -m src.main
```

Drop `$env:SCRAPE_POSTCODES` for a full scrape (takes ~30–50 minutes).

## Interpreting the `Changes` tab

| `change_type` | Meaning |
|---|---|
| `added` | New `registration_number` since last run. On the very first run, every record appears here. |
| `removed` | Previously seen `registration_number` is no longer in the register. Usually a surrender or cancellation. |
| `modified` | A field on an existing record changed. `field_changed`, `old_value`, `new_value` tell you what. |

`scraped_at` and `raw_json` are excluded from modified detection (otherwise every run would mark every record as modified).

## Fields not captured (and why)

The NSW public-register API does not expose phone numbers, individual owner / partner names, or declared financial interests for pharmacies. The `interestHolderName` parameter is searchable (per the `advLayout` endpoint) but the field itself is never returned in any response — the underlying data exists in the index but isn't published. Placeholder columns (`phone`, `owners`, `financial_interests`, `premises_details`) are kept in the sheet so the schema is forward-compatible if NSW ever publishes them.

Also: the API only returns pharmacies with `status: Current`. Cancelled / expired / surrendered records appear once in `Changes` as `removed` rows, then never again.

## Dev

```powershell
pip install -e ".[dev]"
ruff check src tests
mypy src
pytest tests/ -v
```
