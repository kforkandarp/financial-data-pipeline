# Technical Note — Reliance Industries Financial Data Pipeline

## Scraping approach

The scraper fetches `https://www.screener.in/company/RELIANCE/consolidated/` once
with `requests` and parses it with `BeautifulSoup` — no JavaScript rendering is
needed, since the target data is confirmed present in the static server-rendered
HTML. Rather than relying on fixed table/row/column positions, each financial
section is located by its stable `<section id="...">` wrapper (e.g.
`#profit-loss`), and within it, periods are read from the `data-date-key` attribute
on each header cell (an ISO date) instead of the visible text, and row labels are
read via `.get_text()` on each row's first cell, which correctly handles both
plain-text labels and labels wrapped in an expandable toggle button. This means the
scraper finds the right values by name/attribute rather than by position, so it
degrades gracefully — raising a clear error — rather than silently misaligning data
if Screener reorders rows or columns.

Two features originally planned for inclusion were investigated and explicitly
descoped after direct inspection of the actual fetched HTML showed they are not
present in the static page: expandable sub-line-items (e.g. "Sales Growth %") and
the peer comparison table are both injected via a separate dynamic mechanism after
initial page load, not part of the HTML `requests.get()` receives. Capturing them 
would require reverse-engineering an internal API endpoint; this was intentionally 
left out of scope and is documented here rather than silently worked around.

## Raw data approach

Two raw artifacts are written per run, both timestamped and never overwritten: the
full page HTML, and one structured-but-uncleaned JSON file per section (values kept
exactly as scraped, e.g. `"23,640"`). This lets any downstream issue be isolated to
either the fetch step or the parsing step without re-hitting the live site, and
preserves history across runs rather than only ever keeping the most recent snapshot.

## Cleaning process

All six sections are transformed into an identical long-format schema —
`line_item, period, period_type, value, unit` — chosen over the more visually
familiar wide (periods-as-columns) layout because it keeps every processed file the
same shape regardless of how many periods a section spans, and makes the validation
stage's checks (duplicate detection, dtype checks, groupby-based unit consistency)
straightforward native pandas operations rather than requiring a reshape first. The
dashboard reconstructs a wide view in JavaScript for display, since that's the more
natural way to read a financial statement.

Cleaning strips currency symbols, percent signs, and thousand-separator commas, and
converts to proper numeric types; missing or placeholder values become real pandas
`NaN` rather than a string placeholder. Two data-shape quirks required explicit
handling: Screener's Profit & Loss table includes a "TTM" (Trailing Twelve Months)
column, which is not a real calendar date — this is preserved (not dropped) via
`period = NaT`, `period_type = "ttm"`, keeping the `period` column a uniform
datetime type throughout. Shareholding Pattern's periods are given as text ("Sep
2023") rather than a date attribute, so these are parsed separately to the
corresponding quarter-end date.

One data quality issue was caught and handled generically rather than with a
name-specific fix: the Quarterly Results table contains one row that is not
financial data at all (a row of icon-only links to each quarter's official filing
PDF, which extracts as a row of empty strings). Rather than filtering by matching
its specific label, the cleaning stage drops any `line_item` whose values are
missing across every single period — a rule that correctly removes this row while
correctly keeping legitimate rows that merely have one or two missing values (e.g.
"Tax %" and "Dividend Payout %", which have no meaningful TTM figure but real values
everywhere else).

## Data schema

| Column | Type | Notes |
|---|---|---|
| `line_item` | string | Screener's own label, unmodified |
| `period` | date | ISO date, blank for `ttm` rows |
| `period_type` | string | `annual` / `quarterly` / `ttm` |
| `value` | float | `NaN` where genuinely missing |
| `unit` | string | `INR_crore`, `percent`, `INR_per_share`, `days`, `count` |

Schema is intentionally identical across all six output files, which is what allows
one validation function and one dashboard-rendering function to work across every
section without per-file special-casing.

## Validation checks

Six checks run after cleaning, implemented as plain assertions with clear failure
messages (a lighter-weight approach than a full test framework, appropriate for this
project's scope — nothing in the assessment brief specifies a particular testing
tool): all expected tables present and non-empty; periods valid and free of
duplicates; `value` column genuinely numeric; each `line_item` mapped to exactly one
`unit`; cleaning did not introduce more missing values than already existed in the
raw data; and every check fails loudly, with one section's failure logged clearly
without preventing the remaining sections from being checked.

## Cross-check against official filing

`crosscheck/crosscheck_ril.py` opens RIL's official Q1 FY2026-27 filing PDF at
runtime with `pdfplumber` and extracts the "Consolidated Financial Highlights"
table programmatically — no figures are pre-typed into the code. This step went
through several iterations worth documenting, since it surfaced a genuine, instructive
limitation:

pdfplumber's structured `extract_table()` reliably captured the numeric columns of
the source table but consistently failed to align the label column correctly for
two specific rows whose labels wrap across two physical lines in the PDF layout
("Share of Profit/(Loss) of Associates & JVs" and "Profit After Tax and Share of
Profit/(Loss) of Associates & JVs"). Two different `table_settings` configurations
were tried; each fixed one column at the cost of breaking the other for those two
rows specifically. Rather than falling back to a hardcoded value for those two
figures, the extraction was rebuilt around `page.extract_text()` (plain text, not
structured table detection) combined with matching on each row's **Sr. No.** token —
a single leading digit that, unlike the label text, never wraps across lines and
is therefore always cleanly extractable. This fully restored automated extraction
for all required figures with no hardcoded values, while keeping the underlying
row-label-to-metric mapping in one clearly commented lookup table
(`SR_NO_TO_METRIC`) rather than embedding it as magic strings.

### Reconciliation results (Q1 FY27 / June 2026 quarter, ₹ crore)

| Metric | Screener | Official | Diff | Notes |
|---|---|---|---|---|
| Revenue | 309,468 | 340,257 | −30,789 (−9.1%) | See below |
| EBITDA | 47,517 | 54,067 | −6,550 (−12.1%) | See below |
| Depreciation | 15,100 | 15,100 | 0 | Exact match |
| Finance Costs | 8,337 | 8,337 | 0 | Exact match (label: Screener "Interest") |
| Profit Before Tax | 30,630 | 30,630 | 0 | Exact match |
| Tax Expenses (derived) | 7,657.5 | 7,629 | +28.5 (0.4%) | Derived from Screener's Tax % rate; small variance expected from rounding |
| Net Profit vs. Profit After Tax | 23,196 | 23,001 | +195 | See below |
| Net Profit vs. PAT + Associates & JVs | 23,196 | 23,196 | 0 | Exact match |

**Explaining the differences:**

- **Net Profit**: Screener's consolidated "Net Profit" (23,196) matches RIL's
  **"Profit After Tax and Share of Profit/(Loss) of Associates & JVs"** figure
  exactly, confirming Screener's reported Net Profit already includes RIL's share of
  associate/JV profits (195), not the narrower standalone PAT figure (23,001).
- **EBITDA**: the gap of 6,550 matches RIL's reported "Other Income" for the quarter
  (6,550) exactly. This shows Screener's "Operating Profit" is computed strictly as
  Sales − Expenses (excluding Other Income, the conventional definition of an
  operating metric), while RIL's self-reported EBITDA figure includes Other Income —
  a genuine, identifiable definitional difference between the two sources.
- **Revenue**: RIL explicitly labels its figure "**Gross** Revenue" while Screener
  reports standard net Sales/Revenue from Operations. This is very likely a
  presentation-basis difference (e.g. treatment of excise duty pass-through or other
  gross-vs-net adjustments), consistent with the ~9% gap, but could not be fully
  reconciled to the rupee from the summary-level data available on page 1 of the
  filing; a full reconciliation would require RIL's detailed segment notes, which
  were out of scope for this task.
- **Tax Expenses**: Screener stores this as a percentage rate rather than an
  absolute figure, so the comparison value is derived (Profit Before Tax × Tax %);
  the small residual difference (0.4%) is consistent with rounding in the
  underlying rate.

## Challenges

The two most significant challenges were both cases where an initial assumption,
formed from browser inspection, didn't hold once tested against the actual output
of a plain HTTP fetch — expandable sub-items and the peers table looked present in
rendered DevTools views but were confirmed absent from the raw static HTML once
directly tested, and the PDF's wrapped-label rows required moving from structured
table extraction to text-based extraction once the actual failure mode was
diagnosed by inspecting pdfplumber's raw output rather than guessing at
configuration fixes. In both cases, the fix came from testing directly against real
extracted data rather than assumption, which is the general practice this project
was scoped to demonstrate.