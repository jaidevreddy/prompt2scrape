import re
from typing import Any, Dict, List, Tuple, Optional


# -----------------------------
# Helpers
# -----------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _row_text(row: Dict[str, Any]) -> str:
    parts = []
    for v in row.values():
        if v is None:
            continue
        parts.append(str(v))
    return _norm(" ".join(parts))


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)

    s = str(x).strip()
    if not s:
        return None

    # Remove currency + junk
    s = s.replace("₹", "").replace("$", "").replace("€", "")
    s = re.sub(r"[^0-9\.,\-]", "", s)
    s = s.replace(",", "")

    try:
        return float(s)
    except Exception:
        return None


# -----------------------------
# Prompt → FilterSpec
# -----------------------------
def _expand_only_phrase(phrase: str) -> List[str]:
    """
    Convert "women products" => ["women"]
    Convert "running shoes" => ["running", "shoes"]
    """
    phrase = _norm(phrase)

    stop_words = {
        "product", "products", "item", "items", "collection", "collections",
        "for", "the", "a", "an", "only", "just", "strictly",
        "category", "categories", "type", "types"
    }

    # split by spaces and strip punctuation
    tokens = [t.strip(" ,.'\"") for t in phrase.split()]
    tokens = [t for t in tokens if t and t not in stop_words]

    # normalize women/men variants
    normalized = []
    for t in tokens:
        t = t.replace("womens", "women").replace("women’s", "women").replace("women's", "women")
        t = t.replace("mens", "men").replace("men’s", "men").replace("men's", "men")
        normalized.append(t)

    return normalized


def parse_filters_from_prompt(prompt: str) -> Dict[str, Any]:
    """
    Universal prompt filter parser.

    Produces FilterSpec:
    {
      include_keywords: [...],
      exclude_keywords: [...],
      numeric_filters: [{field_hint, op, value}],
    }
    """
    p = _norm(prompt)

    spec = {
        "include_keywords": [],
        "exclude_keywords": [],
        "numeric_filters": [],  # {field_hint, op, value}
    }

    # --- "only X" keyword intent ---
    # examples:
    # only hoodies
    # only women products
    # only running shoes
    m = re.search(r"\bonly\s+([a-z0-9\-\s']{3,})", p)
    if m:
        phrase = m.group(1).strip()

        # stop at common separators
        phrase = re.split(r"(with|having|that|where|which|and|,|:)", phrase)[0].strip()

        tokens = _expand_only_phrase(phrase)
        if tokens:
            spec["include_keywords"].extend(tokens)
        elif phrase:
            spec["include_keywords"].append(phrase)

    # --- explicit exclude keywords ---
    excl_patterns = [
        r"\bexclude\s+([a-z0-9\-\s']{3,})",
        r"\bwithout\s+([a-z0-9\-\s']{3,})",
    ]
    for pat in excl_patterns:
        mm = re.search(pat, p)
        if mm:
            phrase = mm.group(1).strip()
            phrase = re.split(r"(with|having|that|where|which|and|,|:)", phrase)[0].strip()

            tokens = _expand_only_phrase(phrase)
            if tokens:
                spec["exclude_keywords"].extend(tokens)
            elif phrase:
                spec["exclude_keywords"].append(phrase)

    # --- numeric comparisons ---
    # Supports:
    # price > 10000
    # rating >= 4.5
    # above 10k / below 5k
    numeric_patterns = [
        # field + comparator
        (r"\b(price|mrp|amount|cost|rating|score)\s*(>=|<=|>|<|=)\s*(\d+(\.\d+)?)", True),
        # above 10k / below 5k
        (r"\b(above|over|greater than)\s+(\d+)\s*k\b", False),
        (r"\b(below|under|less than)\s+(\d+)\s*k\b", False),
        # above 10000 / below 5000
        (r"\b(above|over|greater than)\s+(\d{3,})\b", False),
        (r"\b(below|under|less than)\s+(\d{3,})\b", False),
        # > 10000
        (r"(>=|<=|>|<|=)\s*(\d{3,}(\.\d+)?)", False),
    ]

    for pat, has_field in numeric_patterns:
        mm = re.search(pat, p)
        if not mm:
            continue

        if has_field:
            field_hint = mm.group(1)
            op = mm.group(2)
            val = float(mm.group(3))
            spec["numeric_filters"].append({"field_hint": field_hint, "op": op, "value": val})
            break

        # direction words above/below
        if "above" in pat or "below" in pat:
            direction = mm.group(1)
            raw_val = float(mm.group(2))

            if "k" in pat:
                raw_val *= 1000

            op = ">" if direction in ["above", "over", "greater than"] else "<"
            spec["numeric_filters"].append({"field_hint": "price", "op": op, "value": raw_val})
            break

        # direct comparator
        if mm.group(1) in [">", ">=", "<", "<=", "="]:
            op = mm.group(1)
            val = float(mm.group(2))
            spec["numeric_filters"].append({"field_hint": "price", "op": op, "value": val})
            break

    return spec


# -----------------------------
# Apply FilterSpec to rows
# -----------------------------
def apply_filter_spec(rows: List[Dict[str, Any]], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    filtered = rows

    include_keywords = [_norm(k) for k in spec.get("include_keywords", []) if k]
    exclude_keywords = [_norm(k) for k in spec.get("exclude_keywords", []) if k]
    numeric_filters = spec.get("numeric_filters", [])

    # -----------------------------
    # Include keywords
    # If multiple include keywords: require ALL (more accurate for "only women shoes")
    # -----------------------------
    if include_keywords:
        out = []
        for r in filtered:
            t = _row_text(r)

            if len(include_keywords) > 1:
                if all(k in t for k in include_keywords):
                    out.append(r)
            else:
                if any(k in t for k in include_keywords):
                    out.append(r)

        filtered = out

    # -----------------------------
    # Exclude keywords
    # -----------------------------
    if exclude_keywords:
        out = []
        for r in filtered:
            t = _row_text(r)
            if not any(k in t for k in exclude_keywords):
                out.append(r)
        filtered = out

    # -----------------------------
    # Numeric filters
    # -----------------------------
    def get_numeric_value(row: Dict[str, Any], field_hint: str) -> Optional[float]:
        hint = _norm(field_hint)

        # 1) try exact keys containing hint
        for k, v in row.items():
            kk = _norm(k)
            if hint in kk:
                return _to_float(v)

        # 2) common mapping
        mapping = {
            "price": ["price", "mrp", "amount", "cost"],
            "rating": ["rating", "score"],
        }
        candidates = mapping.get(hint, [hint])

        for k, v in row.items():
            kk = _norm(k)
            if any(c in kk for c in candidates):
                return _to_float(v)

        return None

    def passes_numeric(val: float, op: str, target: float) -> bool:
        if op == ">":
            return val > target
        if op == ">=":
            return val >= target
        if op == "<":
            return val < target
        if op == "<=":
            return val <= target
        if op == "=":
            return val == target
        return True

    for nf in numeric_filters:
        hint = nf.get("field_hint", "price")
        op = nf.get("op", ">")
        target = nf.get("value")

        if target is None:
            continue

        out = []
        for r in filtered:
            v = get_numeric_value(r, hint)
            if v is None:
                continue
            if passes_numeric(v, op, float(target)):
                out.append(r)

        filtered = out

    return filtered


def filter_rows(rows: List[Dict[str, Any]], prompt: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    meta = {"before": len(rows), "after": len(rows), "spec": {}, "applied": []}

    if not rows:
        return rows, meta

    spec = parse_filters_from_prompt(prompt)
    meta["spec"] = spec

    filtered = apply_filter_spec(rows, spec)
    meta["after"] = len(filtered)

    if spec.get("include_keywords"):
        meta["applied"].append("include_keywords")
    if spec.get("exclude_keywords"):
        meta["applied"].append("exclude_keywords")
    if spec.get("numeric_filters"):
        meta["applied"].append("numeric_filters")

    return filtered, meta

