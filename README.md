# Reliance Industries Financial Data Pipeline

A reproducible pipeline that scrapes, cleans, validates, and cross-checks consolidated
financial data for Reliance Industries Ltd. from Screener.in, and presents it in a
static dashboard.

## Setup

```bash
git clone <this-repo>
cd <this-repo>
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**One manual step required before the cross-check stage will work:** download RIL's
official Q1 FY2026-27 results PDF and save it exactly here:
```
data/raw/ril_official_q1fy27.pdf
```
Source: https://www.ril.com/sites/default/files/2026-07/Media_Release_RIL_Q1_FY2026-27_Financial_and_Operational_Performance.pdf

This isn't automated because it's a one-time reference document for this specific
quarter's cross-check, not something the pipeline needs to re-fetch on every run.

## Running the pipeline

Single command, runs every stage in order:
```bash
python run_pipeline.py
```

This will:
1. Scrape Screener.in and save raw HTML + JSON to `data/raw/`
2. Clean the raw data into long-format CSVs in `data/processed/`
3. Run validation checks and print a pass/fail summary
4. Cross-check Q1 FY27 figures against the official RIL filing (skipped with a warning
   if the PDF above hasn't been placed yet — every other stage still completes)
5. Copy the processed CSVs into `dashboard/data/` so the dashboard is self-contained

Each stage can also be run independently, e.g. `python scraper/scrape_screener.py`,
if you only want to re-run one part.

## Viewing the dashboard

Open `dashboard/index.html` directly in a browser, or serve the `dashboard/` folder
with any static file server. The dashboard folder is fully self-contained — it does
not depend on anything outside itself once `dashboard/data/*.csv` is populated by the
pipeline — so it can be deployed to Netlify by dragging and dropping the `dashboard/`
folder, or by connecting the repo and setting the publish directory to `dashboard/`.

## Repository structure

```
project/
├── scraper/scrape_screener.py      # Stage 1: scrape Screener.in
├── cleaning/clean_data.py          # Stage 3: raw JSON -> clean long-format CSVs
├── validation/validate_data.py     # Stage 4: data quality checks
├── crosscheck/crosscheck_ril.py    # Stage 5: reconcile vs. official RIL filing
├── data/
│   ├── raw/                        # Timestamped raw HTML + JSON (audit trail)
│   └── processed/                  # Long-format CSVs + crosscheck output
├── dashboard/                      # Self-contained static dashboard
├── run_pipeline.py                 # Single entrypoint, runs all stages in order
├── requirements.txt
└── README.md
```

## Scraping approach

Target: `https://www.screener.in/company/RELIANCE/consolidated/` — one page contains
all needed tables (Profit & Loss, Balance Sheet, Cash Flow, Ratios, Shareholding
Pattern, Quarterly Results), fetched once with `requests` and parsed with
`BeautifulSoup`. No JavaScript rendering is needed — the target data is present in
the static server-rendered HTML (confirmed via `view-source`).

Tables are located by their stable `<section id="...">` wrapper (e.g.
`#profit-loss`), not by page position, so the scraper survives the page being
reordered or restyled. Within each section, periods are read from the `data-date-key`
attribute on each header `<th>` (an ISO-format date) rather than the visible text
("Mar 2024"), and row labels are read from each row's first cell using
`.get_text()`, which correctly handles both plain-text labels and labels wrapped in
an expandable `<button>`.

**Structural guardrails:** before parsing each section, the scraper verifies the
section exists and that period headers were found. If either check fails, it raises
a clear `DOMStructureChangedError` naming exactly what was missing, rather than
silently writing an empty or corrupted file. One section failing does not stop the
others from being scraped.

**Two items were investigated and deliberately excluded from scope:**
- **Expandable sub-items** (e.g. "Sales Growth %" nested under "Sales"): initially
  planned to include these for Quarterly Results, but direct inspection of the raw
  fetched HTML confirmed they are not present in the static page — they're injected
  by JavaScript after page load rather than being pre-rendered. Capturing them would 
  require reverse-engineering a dynamic request; this was intentionally left out of scope for this pipeline.
- **Peer comparison table** (`#peers`): also found to load via a separate dynamic
  request (indicated by a `data-page-results` marker on its wrapping element) rather
  than being present in the static HTML. Descoped for the same reason as above; it
  was already a partial fit for this pipeline's reproducibility goals anyway, since
  it's a point-in-time snapshot of other companies rather than Reliance's own
  period-aligned financial history.

## Raw data approach

Two raw artifacts are saved per run, both timestamped (never overwritten) to preserve
an audit trail:
- `data/raw/consolidated_reliance_<timestamp>.html` — the full unmodified page HTML
- `data/raw/{section}_raw_<timestamp>.json` — one file per section, structured but
  unconverted (values kept as scraped strings, e.g. `"23,640"`, `"17%"`)

Keeping both means a bug can always be isolated to either the *fetch* step (compare
against the raw HTML) or the *parsing* step (compare against the raw JSON), without
needing to re-hit the live website.

## Cleaning process

`cleaning/clean_data.py` transforms each raw JSON into a tidy, long-format CSV with
an identical schema across all six sections: `line_item, period, period_type, value,
unit`. Long format (one row per line_item + period) was chosen over the visually
familiar wide format (periods as columns) because it gives every processed file the
same shape regardless of how many periods a section covers, makes validation and
filtering trivial with plain pandas operations, and avoids schema drift as new
periods are added over time. The dashboard reconstructs a wide view for display.

Cleaning rules:
- Strip `₹`, `%`, commas, whitespace; convert to numeric
- `--`/blank values become real pandas `NaN`, not placeholder strings
- Percentages are stored as unit `"percent"` with the numeric value (e.g. `17.0`),
  not as strings with a `%` suffix
- The literal value `"TTM"` (Trailing Twelve Months — Screener's rolling-window
  column in Profit & Loss) is not a real calendar date, so it's parsed to `period =
  NaT` with `period_type = "ttm"`, keeping the data rather than dropping it while
  still allowing the `period` column to stay a uniform datetime type
- Shareholding Pattern's periods are given as text ("Sep 2023") rather than an ISO
  date attribute; these are parsed to the corresponding quarter-end date
- A row is dropped entirely only if every one of its values is missing — this
  generic rule (rather than matching a specific label) correctly filters out one
  genuine non-data row on the Quarterly Results page (a row of links to each
  quarter's official filing PDF, which has no financial values at all) without
  risking the removal of a real row that merely has one missing quarter

## Processed schema

Every file in `data/processed/` (except `crosscheck_q1fy27.csv`) shares this schema:

| Column | Type | Notes |
|---|---|---|
| `line_item` | string | Kept exactly as Screener labels it |
| `period` | date | ISO date, or blank for `ttm` rows |
| `period_type` | string | `annual`, `quarterly`, or `ttm` |
| `value` | float | May be `NaN` for genuinely missing data |
| `unit` | string | `INR_crore`, `percent`, `INR_per_share`, `days`, or `count` |

## Validation checks

`validation/validate_data.py` runs six checks against the processed data, each
mapped to a specific requirement in the assessment brief:
1. All six expected tables exist and are non-empty
2. Periods parse as valid dates with no duplicate (line_item, period) pairs
3. The `value` column is a genuine numeric dtype, not text
4. Each `line_item` maps to exactly one `unit` throughout its file
5. Cleaning did not introduce more missing values than were already present in the
   raw data
6. Every check fails loudly with a specific message rather than silently producing
   an incomplete dataset; one section failing a check does not stop the others from
   being checked

## Assumptions and known limitations

- The peer comparison table and expandable sub-line-items are out of scope (see
  Scraping approach above)
- The "latest" raw file for a given section is resolved by sorting filenames
  (timestamps sort correctly as strings); when run via `run_pipeline.py`, this
  ambiguity is avoided entirely because the scraper hands its exact file paths
  directly to the cleaning stage
- The RIL official filing PDF must be downloaded manually (see Setup) — this is a
  one-time reference document, not something re-fetched on every run
- One figure in the cross-check ("Share of Profit/(Loss) of Associates & JVs") is
  extracted by matching each row's leading Sr. No. rather than its label text,
  because that row's label wraps across two lines in the source PDF, which breaks
  naive column-based table extraction; the Sr. No. token never wraps, so matching on
  it is fully reliable and still runs automatically at runtime — see the technical
  note for details