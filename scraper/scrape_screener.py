"""
scraper/scrape_screener.py

Extracts raw financial statements and metadata from Screener.in for Reliance Industries.
Preserves raw strings without type casting and enforces DOM validation checks.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

# Setup module-level logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

TARGET_URL = "https://www.screener.in/company/RELIANCE/consolidated/"
RAW_DATA_DIR = os.path.join("data", "raw")

# Standard headers to prevent Screener from blocking default python-requests user agents
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class DOMStructureChangedError(Exception):
    """Raised when an expected DOM element, attribute, or table layout is missing."""
    pass


def clean_row_label(label_td: Tag) -> str:
    """
    Extracts text from the first cell of a row.
    Handles nested buttons and strips trailing '+' or '-' expand/collapse
    toggle icons that Screener renders next to expandable row labels.
    """
    text = label_td.get_text(separator=" ", strip=True)
    cleaned = re.sub(r"\s*[\+\-]\s*$", "", text)
    return cleaned.strip()


def get_validated_table_structure(
    section_soup: Optional[Tag],
    section_name: str
) -> Tuple[List[str], Tag]:
    """
    Validates that a section's table/thead/periods/tbody all exist as expected,
    and returns (periods, tbody) if so. Raises DOMStructureChangedError otherwise.
    Shared by every period-based table parser (profit-loss, balance-sheet,
    cash-flow, ratios, quarterly) since they all have identical structure.
    """
    if section_soup is None:
        raise DOMStructureChangedError(f"Section #{section_name} not found on page")

    table = section_soup.find("table")
    if not table:
        raise DOMStructureChangedError(f"No table element found inside section #{section_name}")

    thead = table.find("thead")
    if not thead:
        raise DOMStructureChangedError(f"Missing <thead> inside table for section #{section_name}")

    period_ths = thead.find_all("th", attrs={"data-date-key": True})
    if not period_ths:
        raise DOMStructureChangedError(
            f"No data-date-key attributes found in #{section_name} thead — "
            "Screener may have changed header markup"
        )

    periods = [th["data-date-key"].strip() for th in period_ths]

    tbody = table.find("tbody")
    if not tbody:
        raise DOMStructureChangedError(f"Missing <tbody> inside table for section #{section_name}")

    return periods, tbody


def fetch_page(url: str = TARGET_URL) -> str:
    """Fetches raw HTML from the target URL."""
    logger.info("Fetching target page: %s", url)
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
    response.raise_for_status()
    return response.text


def parse_period_table(
    section_soup: Optional[Tag],
    section_name: str
) -> List[Dict[str, Any]]:
    """
    Generic parser for all period-aligned time-series tables:
    Profit & Loss, Balance Sheet, Cash Flow, Ratios, and Quarterly Results.
    Extracts top-level rows only. (Expandable sub-items, e.g. "Sales Growth %",
    were confirmed to load via a separate JS-driven mechanism not present in
    the static HTML this scraper fetches, so they are out of scope.)
    """
    periods, tbody = get_validated_table_structure(section_soup, section_name)
    parsed_rows: List[Dict[str, Any]] = []

    for tr in tbody.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        label = clean_row_label(tds[0])
        value_cells = tds[1:]

        row_values: Dict[str, str] = {}
        for period, td in zip(periods, value_cells):
            row_values[period] = td.get_text(strip=True)

        parsed_rows.append({
            "line_item": label,
            "values": row_values
        })

    return parsed_rows


def parse_shareholding_table(section_soup: Optional[Tag]) -> List[Dict[str, Any]]:
    """
    Parser for #shareholding. Unlike the other sections, this section's <th>
    headers are plain text (e.g. "Sep 2023") with NO data-date-key attribute,
    so periods must be read from header text directly rather than via
    get_validated_table_structure.
    """
    section_name = "shareholding"
    if section_soup is None:
        raise DOMStructureChangedError(f"Section #{section_name} not found on page")

    table = section_soup.find("table")
    if not table:
        raise DOMStructureChangedError(f"No table element found inside section #{section_name}")

    thead = table.find("thead")
    if not thead:
        raise DOMStructureChangedError(f"Missing <thead> inside table for section #{section_name}")

    header_row = thead.find("tr")
    if not header_row:
        raise DOMStructureChangedError(f"Missing header row inside thead for section #{section_name}")

    header_ths = header_row.find_all("th")
    # First <th> is typically an empty label column, so periods start from the second one
    period_ths = header_ths[1:]
    if not period_ths:
        raise DOMStructureChangedError(
            f"No period headers found in #{section_name} thead — "
            "Screener may have changed header markup"
        )

    periods = [th.get_text(strip=True) for th in period_ths]

    tbody = table.find("tbody")
    if not tbody:
        raise DOMStructureChangedError(f"Missing <tbody> inside table for section #{section_name}")

    parsed_rows: List[Dict[str, Any]] = []

    for tr in tbody.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        label = clean_row_label(tds[0])
        value_cells = tds[1:]

        row_values: Dict[str, str] = {}
        for period, td in zip(periods, value_cells):
            row_values[period] = td.get_text(strip=True)

        parsed_rows.append({
            "line_item": label,
            "values": row_values
        })

    return parsed_rows


def save_raw_html(html_string: str, timestamp: str) -> str:
    """Saves the unparsed raw HTML string to the raw data audit directory."""
    os.makedirs(RAW_DATA_DIR, exist_ok=True)
    filename = f"consolidated_reliance_{timestamp}.html"
    filepath = os.path.join(RAW_DATA_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_string)

    logger.info("Saved raw HTML audit copy: %s", filepath)
    return filepath


def save_raw_json(data: Any, section_name: str, timestamp: str) -> str:
    """Saves the scraped structured raw dictionary/list to a JSON file."""
    os.makedirs(RAW_DATA_DIR, exist_ok=True)
    filename = f"{section_name}_raw_{timestamp}.json"
    filepath = os.path.join(RAW_DATA_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Saved raw JSON for [%s]: %s", section_name, filepath)
    return filepath


def main() -> Tuple[str, List[str]]:
    """
    Main execution pipeline:
    1. Fetches raw HTML.
    2. Generates a unified timestamp for the run.
    3. Saves raw HTML.
    4. Parses and stores raw JSON for each section.
    5. Returns the run timestamp and written file paths for pipeline handoff.
    """
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written_files: List[str] = []

    html_content = fetch_page(TARGET_URL)
    html_path = save_raw_html(html_content, run_timestamp)
    written_files.append(html_path)

    soup = BeautifulSoup(html_content, "html.parser")

    # Map section IDs to their corresponding parsing routines
    sections_to_parse = [
        ("profit-loss", "profit_loss", lambda s: parse_period_table(s, "profit-loss")),
        ("balance-sheet", "balance_sheet", lambda s: parse_period_table(s, "balance-sheet")),
        ("cash-flow", "cash_flow", lambda s: parse_period_table(s, "cash-flow")),
        ("ratios", "ratios", lambda s: parse_period_table(s, "ratios")),
        ("shareholding", "shareholding", parse_shareholding_table),
        ("quarters", "quarterly", lambda s: parse_period_table(s, "quarters")),
    ]

    for section_id, file_slug, parse_func in sections_to_parse:
        try:
            section_tag = soup.find("section", id=section_id)
            parsed_data = parse_func(section_tag)
            json_path = save_raw_json(parsed_data, file_slug, run_timestamp)
            written_files.append(json_path)
        except DOMStructureChangedError as exc:
            logger.error("Structural validation failed for section [%s]: %s", section_id, exc)
        except Exception as exc:
            logger.exception("Unexpected error parsing section [%s]: %s", section_id, exc)

    logger.info(
        "Scrape completed for run [%s]. Total raw files written: %d",
        run_timestamp,
        len(written_files),
    )
    return run_timestamp, written_files


if __name__ == "__main__":
    main()