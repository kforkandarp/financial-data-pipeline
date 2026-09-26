"""
cleaning/clean_data.py

Transforms raw scraped JSON files into standardized, tidy/long-format CSVs.
Extracts numeric values, standardizes period identifiers into datetime/month-end
objects, infers units explicitly per row, and cleans out unpopulated link rows.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Setup module-level logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

RAW_DATA_DIR = os.path.join("data", "raw")
PROCESSED_DATA_DIR = os.path.join("data", "processed")

TARGET_SECTIONS = [
    "profit_loss",
    "balance_sheet",
    "cash_flow",
    "ratios",
    "shareholding",
    "quarterly",
]


def find_latest_raw_file(
    section_slug: str,
    raw_dir: str = RAW_DATA_DIR,
    explicit_path: Optional[str] = None,
) -> str:
    """
    Resolves the raw JSON file path for a given section.
    Uses explicit_path if provided; otherwise discovers the most recent file
    matching '{section_slug}_raw_*.json' sorted by filename timestamp.
    """
    if explicit_path:
        if not os.path.isfile(explicit_path):
            raise FileNotFoundError(f"Explicitly provided raw file not found: {explicit_path}")
        return explicit_path

    if not os.path.isdir(raw_dir):
        raise FileNotFoundError(f"Raw data directory does not exist: {raw_dir}")

    prefix = f"{section_slug}_raw_"
    suffix = ".json"
    candidates = [
        f for f in os.listdir(raw_dir)
        if f.startswith(prefix) and f.endswith(suffix)
    ]

    if not candidates:
        raise FileNotFoundError(
            f"No raw files found for section '{section_slug}' in '{raw_dir}' "
            f"matching pattern '{prefix}*{suffix}'"
        )

    # Filenames format 'YYYYMMDD_HHMMSS' sorts chronologically via standard string sort
    candidates.sort()
    latest_filename = candidates[-1]
    return os.path.join(raw_dir, latest_filename)


def infer_unit_from_line_item(line_item: str) -> str:
    """
    Infers measurement unit purely based on the line item label name.
    Used for missing values or when raw text lacks formatting indicators.
    """
    line_item_lower = line_item.lower()

    if "%" in line_item:
        return "percent"
    if "eps" in line_item_lower:
        return "INR_per_share"
    if "days" in line_item_lower or line_item_lower == "cash conversion cycle":
        return "days"
    if line_item == "No. of Shareholders":
        return "count"
    
    return "INR_crore"


def clean_value_and_unit(raw_str: str, line_item: str) -> Tuple[float, str]:
    """
    Parses a scraped string into a clean float and associated unit string.
    Strips currency signs, commas, and percentage signs without regular expressions.
    """
    cleaned_str = raw_str.strip()

    # Handle blank, missing, or dash placeholders
    if not cleaned_str or cleaned_str == "--":
        unit = infer_unit_from_line_item(line_item)
        return float("nan"), unit

    # Handle percentage values
    if cleaned_str.endswith("%"):
        val_str = cleaned_str[:-1].strip().replace(",", "")
        try:
            return float(val_str), "percent"
        except ValueError:
            return float("nan"), "percent"

    # Infer unit from line item if no percentage suffix is present
    unit = infer_unit_from_line_item(line_item)

    # Clean standard numeric strings (remove commas, currency symbols, whitespace)
    val_str = (
        cleaned_str.replace(",", "")
        .replace("₹", "")
        .replace(" ", "")
    )

    try:
        numeric_val = float(val_str)
    except ValueError:
        numeric_val = float("nan")

    return numeric_val, unit


def parse_period_key(key: str, default_period_type: str) -> Tuple[Any, str]:
    """
    Parses period key strings for standard statement tables.
    Converts 'TTM' to NaT/ttm and ISO dates (e.g., '2024-03-31') to Timestamps.
    """
    stripped_key = key.strip()
    if stripped_key == "TTM":
        return pd.NaT, "ttm"

    period_dt = pd.to_datetime(stripped_key)
    return period_dt, default_period_type


def parse_shareholding_period_key(key: str) -> Tuple[Any, str]:
    """
    Parses period keys specific to Shareholding Pattern (e.g., 'Sep 2023').
    Rolls the parsed month to the exact calendar quarter-end date.
    All shareholding data is typed as 'quarterly'.
    """
    stripped_key = key.strip()
    # Parsing 'Sep 2023' produces 2023-09-01; MonthEnd(0) rolls to 2023-09-30
    period_dt = pd.to_datetime(stripped_key) + pd.offsets.MonthEnd(0)
    return period_dt, "quarterly"


def build_long_dataframe(raw_rows: List[Dict[str, Any]], section_name: str) -> pd.DataFrame:
    """
    Flattens raw section row dictionaries into a tidy long-format DataFrame:
    columns = [line_item, period, period_type, value, unit].
    Filters out junk rows where all period values are NaN (e.g., 'Raw PDF' links).
    """
    default_period_type = (
        "quarterly" if section_name == "quarterly" else "annual"
    )

    long_records = []

    for item in raw_rows:
        line_item = item.get("line_item", "").strip()
        values_dict = item.get("values", {})

        for period_key, raw_val in values_dict.items():
            val, unit = clean_value_and_unit(raw_val, line_item)

            if section_name == "shareholding":
                period, period_type = parse_shareholding_period_key(period_key)
            else:
                period, period_type = parse_period_key(period_key, default_period_type)

            long_records.append({
                "line_item": line_item,
                "period": period,
                "period_type": period_type,
                "value": val,
                "unit": unit,
            })

    columns_order = ["line_item", "period", "period_type", "value", "unit"]
    if not long_records:
        return pd.DataFrame(columns=columns_order)

    df = pd.DataFrame(long_records)[columns_order]

    # Filter out junk/link rows where all values across all periods are NaN
    initial_line_items = df["line_item"].nunique()
    valid_line_items = (
        df.groupby("line_item")["value"]
        .apply(lambda s: s.notna().any())
    )
    keep_line_items = valid_line_items[valid_line_items].index
    df_cleaned = df[df["line_item"].isin(keep_line_items)].copy()
    dropped_count = initial_line_items - len(keep_line_items)

    if dropped_count > 0:
        logger.info(
            "[%s] Dropped %d line item(s) where all period values were missing/empty",
            section_name,
            dropped_count,
        )

    return df_cleaned


def save_processed_csv(
    df: pd.DataFrame,
    section_name: str,
    output_dir: str = PROCESSED_DATA_DIR,
) -> str:
    """
    Writes processed tidy DataFrame to CSV.
    Overwrites previous runs to provide a clean state for validation and consumption.
    """
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{section_name}.csv")
    df.to_csv(out_path, index=False, date_format="%Y-%m-%d")
    logger.info("Saved processed CSV for [%s]: %s (%d rows)", section_name, out_path, len(df))
    return out_path


def main(raw_file_paths: Optional[Dict[str, str]] = None) -> List[str]:
    """
    Main cleaning routine:
    1. Loads raw JSON per section using explicit paths or timestamp discovery.
    2. Cleans values, standardizes dates, and sets unit metadata.
    3. Prunes empty/unpopulated artifact rows.
    4. Writes long-format CSVs to data/processed/.
    """
    processed_paths = []

    for section in TARGET_SECTIONS:
        try:
            explicit_path = raw_file_paths.get(section) if raw_file_paths else None
            raw_path = find_latest_raw_file(section, explicit_path=explicit_path)
            logger.info("[%s] Reading raw data from: %s", section, raw_path)

            with open(raw_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            processed_df = build_long_dataframe(raw_data, section)
            out_file = save_processed_csv(processed_df, section)
            processed_paths.append(out_file)

        except Exception as exc:
            logger.exception("Failed to process section [%s]: %s", section, exc)

    logger.info(
        "Data cleaning stage finished. Successfully created %d/%d processed datasets.",
        len(processed_paths),
        len(TARGET_SECTIONS),
    )
    return processed_paths


if __name__ == "__main__":
    main()