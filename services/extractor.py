import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup


@dataclass
class ExtractResult:
    rows: List[Dict[str, Any]]
    item_count: int


def _first_match(container, selectors: List[str]):
    for sel in selectors:
        if not sel or not sel.strip():
            continue
        el = container.select_one(sel)
        if el is not None:
            return el, sel
    return None, None


def _extract_text(el) -> str:
    # Prefer visible text
    txt = el.get_text(" ", strip=True)
    return txt.strip()


def _extract_url(el) -> str:
    # Most common: <a href="...">
    href = el.get("href")
    if href:
        return str(href).strip()

    # Sometimes stored in data-* attributes
    for k, v in (el.attrs or {}).items():
        if isinstance(k, str) and "href" in k.lower() and isinstance(v, str) and v.strip():
            return v.strip()

    # fallback to text
    return _extract_text(el)


def _to_number(s: str) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None

    # Keep digits, dots, commas
    s2 = re.sub(r"[^0-9\.,\-]", "", s)
    s2 = s2.replace(",", "")
    if s2 in ["", "-", ".", "-."]:
        return None

    try:
        return float(s2)
    except Exception:
        return None


def _extract_by_type(el, field_type: str) -> Any:
    if el is None:
        return None

    if field_type == "text":
        return _extract_text(el)

    if field_type == "url":
        return _extract_url(el)

    if field_type == "number":
        txt = _extract_text(el)
        return _to_number(txt)

    if field_type == "bool":
        txt = _extract_text(el).lower()
        if txt in ["true", "yes", "available", "in stock"]:
            return True
        if txt in ["false", "no", "unavailable", "out of stock"]:
            return False
        return None

    if field_type == "date":
        # keep as raw string for now (postprocess can parse later)
        return _extract_text(el)

    # default fallback
    return _extract_text(el)


def extract_rows_from_plan(cleaned_html: str, plan: Dict[str, Any]) -> ExtractResult:
    """
    Executes the extraction plan on cleaned HTML.
    Returns rows + count of matched items.
    """
    if not isinstance(plan, dict):
        raise ValueError("plan must be a dict")

    item_sel = plan.get("item_container")
    fields = plan.get("fields")

    if not item_sel or not isinstance(item_sel, str):
        raise ValueError("plan.item_container is missing or invalid")
    if not fields or not isinstance(fields, list):
        raise ValueError("plan.fields is missing or invalid")

    soup = BeautifulSoup(cleaned_html, "lxml")

    items = soup.select(item_sel)
    rows: List[Dict[str, Any]] = []

    for item in items:
        row: Dict[str, Any] = {}
        for f in fields:
            name = f.get("name")
            selector = f.get("selector")
            ftype = f.get("type", "text")
            fallbacks = f.get("fallback_selectors", [])

            if not isinstance(name, str) or not name.strip():
                continue

            selectors = []
            if isinstance(selector, str) and selector.strip():
                selectors.append(selector.strip())
            if isinstance(fallbacks, list):
                selectors.extend([x.strip() for x in fallbacks if isinstance(x, str) and x.strip()])

            el, used_selector = _first_match(item, selectors)

            row[name.strip()] = _extract_by_type(el, ftype)

        # Only keep non-empty rows (at least 1 value not None/blank)
        if any(v is not None and str(v).strip() != "" for v in row.values()):
            rows.append(row)

    return ExtractResult(rows=rows, item_count=len(items))
