# Technical Note — Reliance Industries Financial Data Pipeline

## Scraping approach

- Fetches `https://www.screener.in/company/RELIANCE/consolidated/` once with `requests`, parsed with `BeautifulSoup`.
- No JavaScript rendering needed — target data is confirmed present in the static server-rendered HTML.
- Tables are located by their stable `<section id="...">` wrapper (e.g. `#profit-loss`), not by page position.
- Periods are read from the `data-date-key` attribute on each header cell (an ISO date), not the visible text.
- Row labels are read via `.get_text()` on each row's first cell — correctly handles both plain-text labels and labels wrapped in an expandable toggle button.
- Values are matched to periods by attribute/label, not fixed row/column position, so the scraper degrades to a clear error rather than silently misaligning data if Screener's layout changes.

**Descoped after investigation** (not included in the final scrape):
- **Expandable sub-line-items** (e.g. "Sales Growth %" nested under "Sales") — Confirmed via direct inspection of the raw fetched HTML to be injected by JavaScript after page load, not present in the static page.
- **Peer comparison table** — Same reasoning; also identified by a `data-page-results` marker indicating dynamic loading. It's a point-in-time snapshot of other companies rather than Reliance's own period-aligned data.

## Raw data approach

- Two artifacts saved per run, both timestamped and never overwritten:
  - `data/raw/consolidated_reliance_<timestamp>.html` — full unmodified page
  - `data/raw/{section}_raw_<timestamp>.json` — one file per section, values kept exactly as scraped (e.g. `"23,640"`, `"17%"`)
- Purpose: Any downstream issue can be isolated to either the fetch step (raw HTML) or the parsing step (raw JSON) without re-hitting the live site.
- Timestamping (rather than overwriting) preserves history across runs as an audit trail.

## Cleaning process

- All six sections transformed into one identical long-format schema: `line_item, period, period_type, value, unit`.
- Long format chosen over wide (periods-as-columns) because:
  - Every processed file has the same shape regardless of how many periods a section spans.
  - Validation checks (duplicates, dtypes, unit consistency) become simple native pandas operations.
  - New periods are appended as rows, not new columns — no schema drift over time.
  - Dashboard reconstructs a wide view in JavaScript for display, where it reads more naturally.
- Currency symbols, percent signs, and thousand-separator commas stripped; values converted to proper numeric types.
- Missing/placeholder values become real pandas `NaN`, not a string placeholder.

**Two data-shape quirks handled explicitly:**
- Screener's Profit & Loss table has a "TTM" (Trailing Twelve Months) column — not a real calendar date. Preserved via `period = NaT`, `period_type = "ttm"`, keeping the `period` column a uniform datetime type throughout.
- Shareholding Pattern's periods are given as text ("Sep 2023") rather than a date attribute — parsed separately to the corresponding quarter-end date.

**One data quality issue, handled generically rather than by name-matching:**
- The Quarterly Results table contains one non-data row (icon-only links to each quarter's filing PDF, extracting as empty strings).
- Rather than filtering by matching its specific label, the cleaning stage drops any `line_item` whose values are missing across *every* period — correctly removes this row while correctly keeping legitimate rows that merely have one or two missing values (e.g. "Tax %", which has no TTM figure but real values everywhere else).

## Data schema

| Column | Type | Notes |
|---|---|---|
| `line_item` | string | Screener's own label, unmodified |
| `period` | date | ISO date, blank for `ttm` rows |
| `period_type` | string | `annual` / `quarterly` / `ttm` |
| `value` | float | `NaN` where genuinely missing |
| `unit` | string | `INR_crore`, `percent`, `INR_per_share`, `days`, `count` |

- Identical across all six output files — enables one validation function and one dashboard-rendering function to work across every section with no per-file special-casing.

## Validation checks

Six checks, run after cleaning, implemented as plain assertions with clear failure messages:

1. All expected tables present and non-empty.
2. Periods parse as valid dates, no duplicate (line_item, period) pairs.
3. `value` column is genuinely numeric, not text.
4. Each `line_item` maps to exactly one `unit`.
5. Cleaning did not introduce more missing values than already existed in the raw data.
6. Every check fails loudly with a specific message; one section's failure doesn't stop the others from being checked.

- This is an Assertion-based test framework rather than a full test framework.

## Cross-check against official filing

- `crosscheck/crosscheck_ril.py` opens RIL's official Q1 FY2026-27 filing PDF at runtime with `pdfplumber` and extracts the "Consolidated Financial Highlights" table programmatically — no figures pre-typed into the code.

**Extraction challenge and resolution:**
- pdfplumber's structured `extract_table()` reliably captured the numeric columns but consistently misaligned the label column for two rows whose labels wrap across two physical lines in the PDF ("Share of Profit/(Loss) of Associates & JVs" and the combined PAT+Associates row).
- Two different `table_settings` configurations were tried; each fixed one column at the cost of the other, for those two rows specifically.
- Resolved by switching to `page.extract_text()` (plain text) combined with matching on each row's **Sr. No.** — a single leading digit that never wraps, unlike the label text.
- Result: fully automated extraction for all required figures, no hardcoded values, with the row-to-metric mapping kept in one clearly commented lookup table (`SR_NO_TO_METRIC`).

### Reconciliation results (Q1 FY27 / June 2026 quarter, ₹ crore)

| Metric | Screener | Official | Diff | Notes |
|---|---|---|---|---|
| Revenue | 309,468 | 340,257 | −30,789 (−9.1%) | See below |
| EBITDA | 47,517 | 54,067 | −6,550 (−12.1%) | See below |
| Depreciation | 15,100 | 15,100 | 0 | Exact match |
| Finance Costs | 8,337 | 8,337 | 0 | Exact match (label: Screener "Interest") |
| Profit Before Tax | 30,630 | 30,630 | 0 | Exact match |
| Tax Expenses (derived) | 7,657.5 | 7,629 | +28.5 (0.4%) | Derived from Screener's Tax % rate; small variance from rounding |
| Net Profit vs. Profit After Tax | 23,196 | 23,001 | +195 | See below |
| Net Profit vs. PAT + Associates & JVs | 23,196 | 23,196 | 0 | Exact match |

**Explaining the differences:**

- **Net Profit** — Screener's "Net Profit" (23,196) matches RIL's "Profit After Tax and Share of Profit/(Loss) of Associates & JVs" exactly, confirming Screener's reported figure already includes the associate/JV share (195), not the narrower standalone PAT (23,001).

- **EBITDA** — the gap of 6,550 matches RIL's reported "Other Income" for the quarter exactly. Screener's "Operating Profit" is computed strictly as Sales − Expenses (excluding Other Income, the conventional definition), while RIL's self-reported EBITDA includes it — a clearly 
identifiable definitional difference.

- **Revenue** — RIL labels its figure "**Gross** Revenue"; Screener reports standard net Sales/Revenue from Operations. Consistent with a presentation-basis difference (e.g. excise duty pass-through), but not fully reconciled to the rupee from the summary-level data on page 1 — a full reconciliation would require RIL's detailed segment notes, out of scope here.

- **Tax Expenses** — Screener stores this as a rate, not an absolute figure, so the comparison value is derived (Profit Before Tax × Tax %); the small residual (0.4%) is consistent with rounding in the underlying rate.

## Challenges

- Two assumptions formed from browser/DevTools inspection didn't hold once tested against the actual output of a plain HTTP fetch:
  - Expandable sub-items and the peers table appeared present in rendered DevTools views but were confirmed absent from the raw static HTML once directly tested.
  - The PDF's wrapped-label rows required moving from structured table extraction to text-based extraction, once the actual failure mode was diagnosed from pdfplumber's raw output rather than guessed at via configuration changes.
- In both cases, the resolution came from testing directly against real extracted data rather than assumption — the general practice this project was scoped to demonstrate.