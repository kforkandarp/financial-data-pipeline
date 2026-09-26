"""
validation/validate_data.py

Runs post-cleaning data quality and integrity checks on all processed CSVs.
Enforces schema completeness, correct data types, absence of duplicate records,
unit consistency, and verifies that cleaning introduced no unexplained NaNs vs raw data.
"""

import json
import logging
import os
import sys
from typing import Dict, List

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

EXPECTED_SECTIONS = [
    "profit_loss",
    "balance_sheet",
    "cash_flow",
    "ratios",
    "shareholding",
    "quarterly",
]


class ValidationError(Exception):
    """Raised when a processed dataset fails a data quality check."""
    pass


def find_latest_raw_file(section_slug: str, raw_dir: str = RAW_DATA_DIR) -> str:
    """
    Finds the most recent raw JSON file for a given section slug.
    Sorts filenames alphabetically, relying on the YYYYMMDD_HHMMSS timestamp suffix.
    """
    if not os.path.isdir(raw_dir):
        raise ValidationError(f"Raw data directory does not exist: {raw_dir}")

    prefix = f"{section_slug}_raw_"
    suffix = ".json"
    candidates = [
        f for f in os.listdir(raw_dir)
        if f.startswith(prefix) and f.endswith(suffix)
    ]

    if not candidates:
        raise ValidationError(
            f"No raw files found for section '{section_slug}' in '{raw_dir}' "
            f"matching pattern '{prefix}*{suffix}'"
        )

    candidates.sort()
    latest_filename = candidates[-1]
    return os.path.join(raw_dir, latest_filename)


def check_all_sections_present(processed_dir: str = PROCESSED_DATA_DIR) -> None:
    """
    Check 1: Asserts that all 6 expected processed CSV files exist .
    """
    missing_files = []
    for section in EXPECTED_SECTIONS:
        expected_path = os.path.join(processed_dir, f"{section}.csv")
        if not os.path.isfile(expected_path):
            missing_files.append(f"{section}.csv")

    if missing_files:
        raise ValidationError(
            f"Missing required processed CSV file(s) in '{processed_dir}': {', '.join(missing_files)}"
        )


def check_table_exists_and_nonempty(csv_path: str) -> pd.DataFrame:
    """
    Check 2: Asserts the CSV file exists and has at least 1 record.
    Parses 'period' column to datetime (coercing blank TTM values to NaT).
    Returns the loaded DataFrame.
    """
    if not os.path.isfile(csv_path):
        raise ValidationError(f"Processed file does not exist: {csv_path}")

    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        raise ValidationError(f"Could not read CSV '{csv_path}': {exc}")

    if len(df) == 0:
        raise ValidationError(f"File '{csv_path}' is empty (0 rows)")

    # Coerce period strings to datetime; blank strings (e.g. from TTM rows) become pd.NaT cleanly
    df["period"] = pd.to_datetime(df["period"], errors="coerce")
    return df


def check_periods_valid_no_duplicates(df: pd.DataFrame, section_name: str) -> None:
    """
    Check 3: Asserts the period column is datetime64 dtype and checks for duplicate
    (line_item, period) pairs among non-TTM rows.
    """
    if not pd.api.types.is_datetime64_any_dtype(df["period"]):
        raise ValidationError(
            f"[{section_name}] 'period' column is not datetime64 dtype (got {df['period'].dtype})"
        )

    # Filter out TTM rows because they legitimately share null/NaT period across line items
    non_ttm_df = df[df["period_type"] != "ttm"]
    duplicates = non_ttm_df[non_ttm_df.duplicated(subset=["line_item", "period"], keep=False)]

    if not duplicates.empty:
        example_dup = duplicates.iloc[0]
        raise ValidationError(
            f"[{section_name}] Found duplicate (line_item, period) pairs among non-TTM rows. "
            f"Example: line_item='{example_dup['line_item']}', period='{example_dup['period']}'"
        )


def check_numeric_dtype(df: pd.DataFrame, section_name: str) -> None:
    """
    Check 4: Asserts that the 'value' column is of a numeric data type (float or int).
    """
    if not pd.api.types.is_numeric_dtype(df["value"]):
        raise ValidationError(
            f"[{section_name}] 'value' column is not numeric dtype (got {df['value'].dtype})"
        )


def check_units_consistent(df: pd.DataFrame, section_name: str) -> None:
    """
    Check 5: Asserts that each line_item maps to exactly one unit across the entire table.
    """
    inconsistent = (
        df.groupby("line_item")["unit"]
        .nunique()
    )
    violations = inconsistent[inconsistent > 1]

    if not violations.empty:
        bad_items = list(violations.index)
        raise ValidationError(
            f"[{section_name}] Inconsistent units detected for line item(s): {bad_items}"
        )


def check_no_new_nans_vs_raw(df: pd.DataFrame, section_name: str, raw_json_path: str) -> None:
    """
    Check 6: Asserts that data cleaning did not introduce unexplained NaNs.
    Verifies that total processed NaNs do not exceed total raw missing values.
    """
    if not os.path.isfile(raw_json_path):
        raise ValidationError(
            f"[{section_name}] Raw JSON file not found for NaN verification: {raw_json_path}"
        )

    with open(raw_json_path, "r", encoding="utf-8") as f:
        raw_rows = json.load(f)

    # Count empty or dash placeholders across the original raw JSON
    raw_missing_count = 0
    for item in raw_rows:
        values_dict = item.get("values", {})
        for raw_val in values_dict.values():
            val_clean = str(raw_val).strip()
            if not val_clean or val_clean == "--":
                raw_missing_count += 1

    processed_nan_count = int(df["value"].isna().sum())

    if processed_nan_count > raw_missing_count:
        raise ValidationError(
            f"[{section_name}] Cleaning introduced unexplained NaNs! "
            f"Raw missing values: {raw_missing_count}, Processed NaN count: {processed_nan_count}"
        )


def main() -> bool:
    """
    Orchestrates the entire validation suite across all 6 processed datasets:
    1. Verifies existence of all output tables.
    2. Runs row count, period, type, unit, and NaN checks per file.
    3. Prints validation summary and returns True if all pass, False otherwise.
    """
    logger.info("Starting data validation pipeline...")

    # Preliminary structural check
    try:
        check_all_sections_present(PROCESSED_DATA_DIR)
        logger.info("Check passed: All %d expected CSV tables are present.", len(EXPECTED_SECTIONS))
    except ValidationError as exc:
        logger.error("Initial check failed: %s", exc)
        print(f"\nVALIDATION SUMMARY: 0/{len(EXPECTED_SECTIONS)} tables passed — {exc}")
        return False

    failed_sections: Dict[str, List[str]] = {}
    passed_count = 0

    for section in EXPECTED_SECTIONS:
        csv_path = os.path.join(PROCESSED_DATA_DIR, f"{section}.csv")
        section_errors = []

        # Run Check 2 (load and verify non-empty)
        try:
            df = check_table_exists_and_nonempty(csv_path)
        except ValidationError as exc:
            section_errors.append(str(exc))
            failed_sections[section] = section_errors
            logger.error(str(exc))
            continue

        # Run Check 3 (valid periods and no duplicate line_item + period)
        try:
            check_periods_valid_no_duplicates(df, section)
        except ValidationError as exc:
            section_errors.append(str(exc))

        # Run Check 4 (numeric value dtype)
        try:
            check_numeric_dtype(df, section)
        except ValidationError as exc:
            section_errors.append(str(exc))

        # Run Check 5 (consistent units per line_item)
        try:
            check_units_consistent(df, section)
        except ValidationError as exc:
            section_errors.append(str(exc))

        # Run Check 6 (no new NaNs introduced vs raw JSON)
        try:
            raw_json_path = find_latest_raw_file(section, RAW_DATA_DIR)
            check_no_new_nans_vs_raw(df, section, raw_json_path)
        except ValidationError as exc:
            section_errors.append(str(exc))

        # Tally results for this section
        if section_errors:
            failed_sections[section] = section_errors
            for err in section_errors:
                logger.error(err)
        else:
            passed_count += 1
            logger.info("Table [%s.csv] passed all validation checks.", section)

    total_sections = len(EXPECTED_SECTIONS)
    print("\n" + "=" * 50)
    if passed_count == total_sections:
        print(f"VALIDATION SUMMARY: {passed_count}/{total_sections} tables passed all checks")
        print("=" * 50 + "\n")
        return True
    else:
        error_breakdown = ", ".join(
            f"{sec} ({len(errs)} checks failed)" for sec, errs in failed_sections.items()
        )
        print(
            f"VALIDATION SUMMARY: {passed_count}/{total_sections} tables passed — "
            f"see errors above for: {error_breakdown}"
        )
        print("=" * 50 + "\n")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)