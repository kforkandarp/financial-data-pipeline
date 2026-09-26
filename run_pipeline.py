"""
run_pipeline.py

Root-level pipeline orchestrator. Executes the financial data pipeline sequentially:
Scrape -> Clean -> Validate -> Cross-Check -> Deploy CSVs to Dashboard.
Handles explicit path handoffs and defensive fallbacks.
"""

import logging
import os
import shutil
import sys
from typing import Dict

# Stage imports (aliased to prevent naming collisions)
from scraper.scrape_screener import main as scrape_main
from cleaning.clean_data import main as clean_main
from validation.validate_data import main as validate_main
from crosscheck.crosscheck_ril import main as crosscheck_main

# Setup module-level logging consistent with stage scripts
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PROCESSED_DATA_DIR = os.path.join("data", "processed")
DASHBOARD_DATA_DIR = os.path.join("dashboard", "data")
RIL_PDF_PATH = os.path.join("data", "raw", "ril_official_q1fy27.pdf")


def copy_csvs_to_dashboard(
    source_dir: str = PROCESSED_DATA_DIR,
    target_dir: str = DASHBOARD_DATA_DIR,
) -> int:
    """
    Copies all processed CSV files into the dashboard directory.
    Ensures dashboard/ can be deployed independently as a static artifact.
    """
    os.makedirs(target_dir, exist_ok=True)
    csv_files = [f for f in os.listdir(source_dir) if f.endswith(".csv")]

    for filename in csv_files:
        src = os.path.join(source_dir, filename)
        dst = os.path.join(target_dir, filename)
        shutil.copy2(src, dst)

    return len(csv_files)


def run_pipeline() -> bool:
    """
    Orchestrates the entire financial data extraction and verification flow:
    1. Scrapes raw HTML and structured JSON.
    2. Builds explicit file paths and cleans data into long-format CSVs.
    3. Runs quality and consistency validations.
    4. Performs official RIL filing PDF cross-check (optional/defensive).
    5. Syncs output CSVs to the dashboard folder.
    """
    logger.info("==================================================")
    logger.info("STARTING FINANCIAL DATA PIPELINE")
    logger.info("==================================================")

    # ------------------------------------------------------------------
    # Step 1: Scrape
    # ------------------------------------------------------------------
    logger.info(">>> STAGE 1: SCRAPING SCREENER DATA")
    try:
        run_timestamp, written_files = scrape_main()
        logger.info("Scraping completed. Run timestamp: %s", run_timestamp)
    except Exception as exc:
        logger.exception("Scraping stage failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Step 2: Build Explicit Raw File Mapping
    # ------------------------------------------------------------------
    # written_files contains: [html_path, profit_loss, balance_sheet, cash_flow, ratios, shareholding, quarterly]
    if len(written_files) < 7:
        logger.error(
            "Scraper returned %d files, expected 7 (1 HTML + 6 JSON). Aborting clean handoff.",
            len(written_files),
        )
        return False

    raw_file_paths: Dict[str, str] = {
        "profit_loss": written_files[1],
        "balance_sheet": written_files[2],
        "cash_flow": written_files[3],
        "ratios": written_files[4],
        "shareholding": written_files[5],
        "quarterly": written_files[6],
    }

    # ------------------------------------------------------------------
    # Step 3: Clean
    # ------------------------------------------------------------------
    logger.info(">>> STAGE 2: CLEANING & STRUCTURING DATA")
    try:
        processed_files = clean_main(raw_file_paths=raw_file_paths)
        logger.info("Cleaning completed. Processed files generated: %d", len(processed_files))
    except Exception as exc:
        logger.exception("Cleaning stage failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Step 4: Validate
    # ------------------------------------------------------------------
    logger.info(">>> STAGE 3: VALIDATING PROCESSED DATA")
    validation_passed = False
    try:
        validation_passed = validate_main()
        if not validation_passed:
            logger.warning("Data validation reported one or more check failures.")
        else:
            logger.info("Data validation passed successfully.")
    except Exception as exc:
        logger.exception("Unexpected error encountered during validation: %s", exc)

    # ------------------------------------------------------------------
    # Step 5: Cross-check against Official RIL PDF
    # ------------------------------------------------------------------
    logger.info(">>> STAGE 4: CROSS-CHECKING AGAINST OFFICIAL RIL FILING")
    crosscheck_status = "SKIPPED"
    try:
        if not os.path.isfile(RIL_PDF_PATH):
            raise FileNotFoundError(
                f"Official filing PDF not found at '{RIL_PDF_PATH}'. "
                "Download the PDF manually and place it in data/raw/ to enable cross-check."
            )
        crosscheck_main(pdf_path=RIL_PDF_PATH)
        crosscheck_status = "PASSED"
        logger.info("Cross-check reconciliation completed successfully.")
    except FileNotFoundError as fnf_err:
        crosscheck_status = "SKIPPED (PDF Missing)"
        logger.warning("%s", fnf_err)
    except Exception as exc:
        crosscheck_status = "FAILED"
        logger.exception("Cross-check reconciliation encountered an error: %s", exc)

    # ------------------------------------------------------------------
    # Step 6: Deploy CSVs to Dashboard Data Directory
    # ------------------------------------------------------------------
    logger.info(">>> STAGE 5: SYNCING CSVs TO DASHBOARD")
    try:
        copied_count = copy_csvs_to_dashboard(PROCESSED_DATA_DIR, DASHBOARD_DATA_DIR)
        logger.info("Successfully copied %d CSVs to '%s'", copied_count, DASHBOARD_DATA_DIR)
    except Exception as exc:
        logger.exception("Failed to copy CSV files to dashboard directory: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Final Pipeline Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("PIPELINE EXECUTION SUMMARY")
    print("=" * 60)
    print(f"1. Scraping:         SUCCESS (Snapshot: {run_timestamp})")
    print(f"2. Cleaning:         SUCCESS ({len(processed_files)} tables written)")
    print(f"3. Validation:       {'PASSED' if validation_passed else 'WARNING / FAILED'}")
    print(f"4. Official Check:   {crosscheck_status}")
    print(f"5. Dashboard Sync:   SUCCESS ({copied_count} files in {DASHBOARD_DATA_DIR}/)")
    print("=" * 60 + "\n")

    return True


if __name__ == "__main__":
    success = run_pipeline()
    sys.exit(0 if success else 1)