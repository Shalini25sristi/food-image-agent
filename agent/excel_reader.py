"""Read food item names from an Excel file."""

import logging
import re

import pandas as pd

log = logging.getLogger(__name__)

_COLUMN_HINTS = ["food item", "item_name", "item name", "item", "name", "dish",
                 "menu item", "menu_item"]


def _promote_embedded_header(df: pd.DataFrame) -> pd.DataFrame:
    """Sheets sometimes have the real header as the first data row (columns
    read as 'Unnamed: N'). Detect and fix that."""
    if not all(str(c).startswith("Unnamed") for c in df.columns):
        return df
    first = df.iloc[0]
    if first.notna().any():
        df = df.copy()
        df.columns = [str(v).strip() for v in first]
        df = df.iloc[1:]
        log.info("Promoted embedded header row: %s", list(df.columns))
    return df


def _pick_column(df: pd.DataFrame, column: str | None):
    cols = list(df.columns)
    if column and column in cols:
        return column
    lowered = {str(c).strip().lower(): c for c in cols}
    if column and column.strip().lower() in lowered:
        return lowered[column.strip().lower()]
    for hint in _COLUMN_HINTS:
        if hint in lowered:
            log.info("Auto-detected food-item column %r.", lowered[hint])
            return lowered[hint]
    log.warning("Column %r not found; using first column %r.", column, cols[0])
    return cols[0]


def read_food_items(excel_path: str, column: str | None = None) -> list[str]:
    """Return a list of clean, unique food item names from the Excel sheet.

    - Handles embedded header rows ('Unnamed' columns).
    - Auto-detects the column if `column` is not given or not found.
    - Splits cells that pack several dishes on separate lines ("a\\nb").
    - Strips whitespace, drops empty rows and duplicates (order preserved).
    """
    df = pd.read_excel(excel_path)
    df = df.dropna(how="all")
    if df.empty:
        raise ValueError(f"Excel file '{excel_path}' contains no data.")

    df = _promote_embedded_header(df)
    col = _pick_column(df, column)

    items: list[str] = []
    seen = set()
    for raw in df[col].tolist():
        if pd.isna(raw):
            continue
        for part in re.split(r"[\n\r]+", str(raw)):
            name = part.strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(name)

    log.info("Read %d food item(s) from '%s' (column %r).", len(items), excel_path, col)
    return items
