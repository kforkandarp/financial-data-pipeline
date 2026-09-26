"""
crosscheck/crosscheck_ril.py

Extracts key quarterly figures from Reliance Industries' official Q1 FY2026-27
Media Release PDF via pdfplumber and reconciles them against the scraped Screener dataset.
Computes absolute/percentage variance and records presentation/label differences.
"""

import logging
import os
from typing import Dict, List, Optional

import pandas as pd
import pdfplumber

# Setup module-level logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_PDF_PATH = os.path.join("data", "raw", "ril_official_q1fy27.pdf")
DEFAULT_QUARTERLY_CSV_PATH = os.path.join("data", "processed", "quarterly.csv")
DEFAULT_OUTPUT_CSV_PATH = os.path.join("data", "processed", "crosscheck_q1fy27.csv")
TARGET_PERIOD = "2026-06-30"

# Maps each row's Sr. No. (a clean, single-token digit that never wraps across lines)
# to its metric name. We match RIL's rows by Sr. No. rather than by label text because
# some labels (rows 9 and 10) wrap across two physical lines in the PDF, which breaks
# pdfplumber's table-column detection — but the Sr. No. itself never wraps, so matching
# on it is fully reliable.
SR_NO_TO_METRIC = {
    1: "Gross Revenue",
    2: "EBITDA",
    4: "Depreciation",
    5: "Finance Costs",
    6: "Profit Before Tax",
    7: "Tax Expenses",
    8: "Profit After Tax",
    9: "Share of Profit/(Loss) of Associates & JVs",
    10: "Profit After Tax and Share of Profit/(Loss) of Associates & JVs",
}



def clean_ril_value(raw_str: str) -> float:
    """
    Parses a string extracted from the RIL PDF into a float.
    Handles commas, accounting parentheses for negative values (e.g. '(27)' -> -27.0),
    and lone dashes without regular expressions.
    """
    cleaned = raw_str.strip()
    if not cleaned or cleaned == "-":
        return float("nan")

    # Handle accounting parentheses for negative numbers
    if cleaned.startswith("(") and cleaned.endswith(")"):
        inner = cleaned[1:-1].strip().replace(",", "")
        try:
            return -float(inner)
        except ValueError:
            return float("nan")

    # Standard positive number or regular minus sign
    val_str = cleaned.replace(",", "")
    try:
        return float(val_str)
    except ValueError:
        return float("nan")


def _is_value_token(token: str) -> bool:
    """
    Checks whether a whitespace-split token looks like a numeric figure
    (e.g. "340,257", "24.5", "(27)", "-") rather than a label word.
    No regular expressions — plain string stripping and .isdigit() only.
    """
    stripped_token = token.strip()
    if stripped_token == "-":
        return True
    digits_only = stripped_token.strip("()").replace(",", "").replace(".", "").lstrip("-")
    return digits_only.isdigit() and len(digits_only) > 0


def extract_ril_summary_table(pdf_path: str = DEFAULT_PDF_PATH) -> Dict[str, float]:
    """
    Opens the official RIL Media Release PDF and extracts plain text from Page 1,
    then parses the 'Consolidated Financial Highlights' table by matching each
    row's Sr. No. (a clean, never-wrapped leading digit) rather than its label
    text, which can wrap across two lines for longer row names. This avoids the
    column-alignment failures of pdfplumber's extract_table() on wrapped cells.
    Returns a dict mapping metric name -> its "1Q FY27" value (float).
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"Official RIL filing PDF not found at: {pdf_path}")

    logger.info("Opening RIL official filing PDF: %s", pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) == 0:
            raise RuntimeError(f"The PDF file at '{pdf_path}' has no pages.")
        page_text = pdf.pages[0].extract_text()

    if not page_text:
        raise RuntimeError(f"No text could be extracted from page 1 of '{pdf_path}'")

    extracted: Dict[int, float] = {}

    for line in page_text.split("\n"):
        tokens = line.split()
        if not tokens:
            continue

        sr_token = tokens[0]
        if not sr_token.isdigit():
            continue

        sr_no = int(sr_token)
        if sr_no not in SR_NO_TO_METRIC:
            continue

        # The first numeric-looking token after the Sr. No. is the "1Q FY27" value.
        # Everything before it is label text, which we ignore here since we already
        # know the metric name for this Sr. No. from SR_NO_TO_METRIC.
        value_token = None
        for token in tokens[1:]:
            if _is_value_token(token):
                value_token = token
                break

        if value_token is None:
            raise RuntimeError(
                f"Could not find a numeric '1Q FY27' value on the row for Sr. No. {sr_no} "
                f"({SR_NO_TO_METRIC[sr_no]}). Raw line: '{line}'"
            )

        extracted[sr_no] = clean_ril_value(value_token)

    missing = [sr for sr in SR_NO_TO_METRIC if sr not in extracted]
    if missing:
        missing_names = [SR_NO_TO_METRIC[sr] for sr in missing]
        raise RuntimeError(
            f"Failed to extract {len(missing)} required row(s) from RIL PDF page 1: {missing_names}"
        )

    logger.info(
        "Successfully extracted %d/%d required financial figures from RIL PDF Page 1.",
        len(extracted), len(SR_NO_TO_METRIC)
    )

    return {SR_NO_TO_METRIC[sr]: val for sr, val in extracted.items()}


def load_screener_q1fy27_values(
    quarterly_csv_path: str = DEFAULT_QUARTERLY_CSV_PATH,
    target_period: str = TARGET_PERIOD,
) -> Dict[str, float]:
    """
    Loads processed quarterly CSV and extracts metrics for the June 2026 quarter (2026-06-30).
    Returns a dictionary mapping line_item to float value.
    """
    if not os.path.isfile(quarterly_csv_path):
        raise FileNotFoundError(f"Processed quarterly CSV not found at: {quarterly_csv_path}")

    df = pd.read_csv(quarterly_csv_path)

    # Filter for targeted quarter-end
    q_df = df[(df["period"] == target_period) & (df["period_type"] == "quarterly")]
    if q_df.empty:
        raise ValueError(
            f"No quarterly data found for period '{target_period}' in '{quarterly_csv_path}'"
        )

    screener_dict: Dict[str, float] = {}
    for _, row in q_df.iterrows():
        item_name = str(row["line_item"]).strip()
        screener_dict[item_name] = float(row["value"])

    logger.info(
        "Loaded %d Screener metrics for quarter ended %s.",
        len(screener_dict),
        target_period,
    )
    return screener_dict


def build_crosscheck_table(
    screener_values: Dict[str, float],
    ril_values: Dict[str, float],
) -> pd.DataFrame:
    """
    Builds the side-by-side reconciliation table comparing Screener metrics
    against official RIL filing numbers, calculating variance and difference %.
    """
    # 1. Gross Revenue
    screener_sales = screener_values.get("Sales", float("nan"))
    ril_revenue = ril_values["Gross Revenue"]

    # 2. EBITDA / Operating Profit
    screener_ebitda = screener_values.get("Operating Profit", float("nan"))
    ril_ebitda = ril_values["EBITDA"]

    # 3. Depreciation
    screener_dep = screener_values.get("Depreciation", float("nan"))
    ril_dep = ril_values["Depreciation"]

    # 4. Finance Costs / Interest
    screener_interest = screener_values.get("Interest", float("nan"))
    ril_finance = ril_values["Finance Costs"]

    # 5. Profit Before Tax
    screener_pbt = screener_values.get("Profit before tax", float("nan"))
    ril_pbt = ril_values["Profit Before Tax"]

    # 6. Tax Expenses (Derived from Screener's Tax % rate)
    screener_tax_pct = screener_values.get("Tax %", float("nan"))
    screener_tax_amount = (
        (screener_pbt * screener_tax_pct / 100.0)
        if pd.notna(screener_pbt) and pd.notna(screener_tax_pct)
        else float("nan")
    )
    ril_tax = ril_values["Tax Expenses"]

    # 7. Net Profit vs Profit After Tax (standalone PAT)
    screener_pat = screener_values.get("Net Profit", float("nan"))
    ril_pat_standalone = ril_values["Profit After Tax"]

    # 8. Net Profit vs Profit After Tax + Associates/JVs — now extracted directly,
    # no longer derived, since Sr.No.-based matching handles wrapped labels correctly.
    ril_pat_total = ril_values["Profit After Tax and Share of Profit/(Loss) of Associates & JVs"]

    comparisons = [
        {
            "metric": "Revenue",
            "screener_value": screener_sales,
            "official_value": ril_revenue,
            "explanation": "label difference: Screener 'Sales' vs RIL 'Gross Revenue'",
        },
        {
            "metric": "EBITDA",
            "screener_value": screener_ebitda,
            "official_value": ril_ebitda,
            "explanation": "label difference: Screener 'Operating Profit' vs RIL 'EBITDA'",
        },
        {
            "metric": "Depreciation",
            "screener_value": screener_dep,
            "official_value": ril_dep,
            "explanation": "direct match: Depreciation",
        },
        {
            "metric": "Finance Costs",
            "screener_value": screener_interest,
            "official_value": ril_finance,
            "explanation": "label difference: Screener 'Interest' vs RIL 'Finance Costs'",
        },
        {
            "metric": "Profit Before Tax",
            "screener_value": screener_pbt,
            "official_value": ril_pbt,
            "explanation": "direct match: Profit Before Tax",
        },
        {
            "metric": "Tax Expenses (derived)",
            "screener_value": screener_tax_amount,
            "official_value": ril_tax,
            "explanation": (
                "derived figure: Screener provides Tax % rate; computed as "
                "(Profit before tax * Tax % / 100)"
            ),
        },
        {
            "metric": "Net Profit vs Profit After Tax",
            "screener_value": screener_pat,
            "official_value": ril_pat_standalone,
            "explanation": "comparison against RIL standalone Profit After Tax (row 8)",
        },
        {
            "metric": "Net Profit vs Profit After Tax + Associates",
            "screener_value": screener_pat,
            "official_value": ril_pat_total,
            "explanation": "comparison against RIL PAT including Share of Profit of Associates & JVs (row 10)",
        },
    ]

    for comp in comparisons:
        s_val = comp["screener_value"]
        o_val = comp["official_value"]

        if pd.notna(s_val) and pd.notna(o_val):
            diff = round(s_val - o_val, 2)
            comp["diff"] = diff
            comp["diff_pct"] = (
                round((diff / o_val) * 100.0, 2) if o_val != 0 else float("nan")
            )
        else:
            comp["diff"] = float("nan")
            comp["diff_pct"] = float("nan")

    cols = ["metric", "screener_value", "official_value", "diff", "diff_pct", "explanation"]
    return pd.DataFrame(comparisons)[cols]


def save_crosscheck_csv(
    df: pd.DataFrame,
    output_path: str = DEFAULT_OUTPUT_CSV_PATH,
) -> str:
    """Saves the crosscheck reconciliation results to CSV."""
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    df.to_csv(output_path, index=False)
    logger.info("Saved crosscheck results to: %s", output_path)
    return output_path


def main(
    pdf_path: str = DEFAULT_PDF_PATH,
    quarterly_csv_path: str = DEFAULT_QUARTERLY_CSV_PATH,
    output_path: str = DEFAULT_OUTPUT_CSV_PATH,
) -> str:
    """
    Main orchestration routine:
    1. Extracts Page 1 table dynamically from RIL official PDF.
    2. Loads June 2026 quarter figures from processed CSV.
    3. Reconciles metrics, evaluates diffs and percentages.
    4. Saves output CSV and displays comparison table on console.
    """
    
    logger.info("Starting RIL quarterly cross-check reconciliation...")

    ril_values = extract_ril_summary_table(pdf_path)
    screener_values = load_screener_q1fy27_values(quarterly_csv_path, TARGET_PERIOD)

    comparison_df = build_crosscheck_table(screener_values, ril_values)
    saved_path = save_crosscheck_csv(comparison_df, output_path)

    # Print clean tabular preview for immediate console inspection
    print("\n" + "=" * 90)
    print(f"RIL Q1 FY2026-27 CROSS-CHECK TABLE ({TARGET_PERIOD})")
    print("=" * 90)
    pd.set_option("display.max_columns", 10)
    pd.set_option("display.width", 120)
    print(comparison_df.to_string(index=False))
    print("=" * 90 + "\n")

    return saved_path

if __name__ == "__main__":
    main()