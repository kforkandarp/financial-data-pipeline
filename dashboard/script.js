// ---------------------------------------------------------------------------
// Configuration: one entry per tab/section.
// ---------------------------------------------------------------------------
const SECTIONS = [
  { key: "pl", label: "Profit & Loss", file: "profit_loss.csv" },
  { key: "bs", label: "Balance Sheet", file: "balance_sheet.csv" },
  { key: "cf", label: "Cash Flow", file: "cash_flow.csv" },
  { key: "ratios", label: "Ratios", file: "ratios.csv" },
  { key: "shareholding", label: "Shareholding Pattern", file: "shareholding.csv" },
  { key: "quarterly", label: "Quarterly Results", file: "quarterly.csv" },
];

// Cache of already-loaded sections, keyed by section.key.
// Each entry is either { pivoted: {...} } or { error: true }.
const sectionCache = {};

const MONTH_NAMES = [
  "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// ---------------------------------------------------------------------------
// Step 1: CSV parsing
// ---------------------------------------------------------------------------
// A small hand-rolled parser. The data has no embedded commas or quotes
// (it has already been through our own cleaning pipeline), so a simple
// split-based approach is sufficient.
function parseCSV(text) {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  if (lines.length === 0) {
    return [];
  }

  const header = lines[0].split(",").map((cell) => cell.trim());
  const rows = [];

  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(",").map((cell) => cell.trim());
    const row = {};
    for (let j = 0; j < header.length; j++) {
      row[header[j]] = cells[j] !== undefined ? cells[j] : "";
    }
    rows.push(row);
  }

  return rows;
}

// ---------------------------------------------------------------------------
// Step 2: Pivot long-format rows into a wide structure
// ---------------------------------------------------------------------------
// Input: array of { line_item, period, period_type, value, unit }
// Output: {
//   periods:      [ "2023-03-31", "2024-03-31", ..., "TTM" ]  (chronological, TTM last if present)
//   lineItems:    [ "Revenue", "Net Profit", ... ]              (first-seen order in the CSV)
//   dataMap:      { lineItem: { periodKey: numericValue } }
//   lineItemUnit: { lineItem: "INR_crore" }                     (unit each line item is stated in)
//   dominantUnit: "INR_crore"                                    (most common unit in this section)
// }
function pivotData(rows) {
  const lineItemOrder = [];
  const seenLineItems = {};
  const periodSet = {};
  let hasTTM = false;

  const dataMap = {};
  const lineItemUnit = {};
  const unitCounts = {};

  rows.forEach((row) => {
    const lineItem = row.line_item;
    const periodType = row.period_type;
    const periodRaw = row.period;
    const unit = row.unit;
    const rawValue = row.value;
    const value = rawValue === "" || rawValue === undefined ? NaN : Number(rawValue);

    // Track line item order (first appearance wins the position).
    if (!seenLineItems[lineItem]) {
      seenLineItems[lineItem] = true;
      lineItemOrder.push(lineItem);
    }

    // Each line item keeps the unit it first appeared with.
    if (!(lineItem in lineItemUnit)) {
      lineItemUnit[lineItem] = unit;
    }
    unitCounts[unit] = (unitCounts[unit] || 0) + 1;

    // Decide which column this row belongs to.
    let periodKey;
    if (periodType === "ttm" || periodRaw === "") {
      periodKey = "TTM";
      hasTTM = true;
    } else {
      periodKey = periodRaw;
      periodSet[periodRaw] = true;
    }

    if (!dataMap[lineItem]) {
      dataMap[lineItem] = {};
    }
    dataMap[lineItem][periodKey] = value;
  });

  // ISO dates ("YYYY-MM-DD") sort correctly as plain strings.
  const periods = Object.keys(periodSet).sort();
  if (hasTTM) {
    periods.push("TTM");
  }

  // Dominant unit = the unit used by the most rows in this section.
  let dominantUnit = null;
  let dominantCount = -1;
  Object.keys(unitCounts).forEach((unit) => {
    if (unitCounts[unit] > dominantCount) {
      dominantCount = unitCounts[unit];
      dominantUnit = unit;
    }
  });

  return {
    periods: periods,
    lineItems: lineItemOrder,
    dataMap: dataMap,
    lineItemUnit: lineItemUnit,
    dominantUnit: dominantUnit,
  };
}

// ---------------------------------------------------------------------------
// Step 3: Formatting helpers
// ---------------------------------------------------------------------------
function formatPeriodLabel(periodKey) {
  if (periodKey === "TTM") {
    return "TTM";
  }
  const parts = periodKey.split("-");
  if (parts.length !== 3) {
    return periodKey;
  }
  const year = parts[0];
  const monthIndex = Number(parts[1]);
  const monthName = MONTH_NAMES[monthIndex] || parts[1];
  return monthName + " " + year;
}

function formatValue(value, unit) {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return "—";
  }

  if (unit === "INR_crore" || unit === "count") {
    // Thousand-separated, e.g. 374,372
    return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }

  if (unit === "percent") {
    const text = Number.isInteger(value) ? String(value) : value.toFixed(2);
    return text + "%";
  }

  if (unit === "INR_per_share") {
    return value.toFixed(2);
  }

  if (unit === "days") {
    return Number.isInteger(value) ? String(value) : value.toFixed(1);
  }

  return String(value);
}

// ---------------------------------------------------------------------------
// Step 4: Render a pivoted section as an HTML table
// ---------------------------------------------------------------------------
function renderTable(pivoted) {
  const { periods, lineItems, dataMap, lineItemUnit, dominantUnit } = pivoted;

  const table = document.createElement("table");

  // Header row: blank corner cell + one column per period.
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");

  const cornerCell = document.createElement("th");
  cornerCell.textContent = "Line Item";
  headRow.appendChild(cornerCell);

  periods.forEach((periodKey) => {
    const th = document.createElement("th");
    th.textContent = formatPeriodLabel(periodKey);
    headRow.appendChild(th);
  });

  thead.appendChild(headRow);
  table.appendChild(thead);

  // One body row per line item.
  const tbody = document.createElement("tbody");

  lineItems.forEach((lineItem) => {
    const tr = document.createElement("tr");
    const unit = lineItemUnit[lineItem];

    const labelCell = document.createElement("th");
    labelCell.textContent =
      unit && unit !== dominantUnit ? lineItem + " (" + unit + ")" : lineItem;
    tr.appendChild(labelCell);

    periods.forEach((periodKey) => {
      const td = document.createElement("td");
      const rowValues = dataMap[lineItem];
      const value = rowValues ? rowValues[periodKey] : undefined;
      td.textContent = formatValue(value, unit);
      tr.appendChild(td);
    });

    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  return table;
}

function renderError(section) {
  const div = document.createElement("div");
  div.className = "error-message";
  div.textContent = "Could not load " + section.file;
  return div;
}

// ---------------------------------------------------------------------------
// Step 5: Load (with caching), fetch + parse + pivot, and display a section
// ---------------------------------------------------------------------------
const contentEl = document.getElementById("content");

async function displaySection(section) {
  contentEl.innerHTML = "";

  // Already loaded (successfully or not) — render from cache, no re-fetch.
  if (sectionCache[section.key]) {
    const cached = sectionCache[section.key];
    contentEl.appendChild(cached.error ? renderError(section) : renderTable(cached.pivoted));
    return;
  }

  try {
    const response = await fetch("./data/" + section.file);
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    const text = await response.text();
    const rows = parseCSV(text);
    const pivoted = pivotData(rows);

    sectionCache[section.key] = { pivoted: pivoted };
    contentEl.appendChild(renderTable(pivoted));
  } catch (err) {
    sectionCache[section.key] = { error: true };
    contentEl.appendChild(renderError(section));
  }
}

// ---------------------------------------------------------------------------
// Step 6: Tab switching
// ---------------------------------------------------------------------------
function setupTabs() {
  const buttons = document.querySelectorAll(".tab-button");

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.remove("active"));
      button.classList.add("active");

      const key = button.getAttribute("data-section");
      const section = SECTIONS.find((s) => s.key === key);
      displaySection(section);
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupTabs();
  displaySection(SECTIONS[0]);
});