import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class PostprocessResult:
    df: pd.DataFrame
    csv_bytes: bytes
    removed_duplicates: int


def _clean_text(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    # collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # normalize common junk
    if s.lower() in ["none", "null", "na", "n/a", "-"]:
        return None
    return s


def _clean_number(x: Any) -> Optional[float]:
    if x is None:
        return None

    # already numeric
    if isinstance(x, (int, float)):
        return float(x)

    s = str(x).strip()
    if not s:
        return None

    # remove currency symbols and non-numeric except dot/comma/minus
    s = s.replace("₹", "").replace("$", "").replace("€", "")
    s = re.sub(r"[^0-9\.,\-]", "", s)
    s = s.replace(",", "")

    if s in ["", "-", ".", "-."]:
        return None

    try:
        return float(s)
    except Exception:
        return None


def postprocess_rows(rows: List[Dict[str, Any]]) -> PostprocessResult:
    """
    - Builds DataFrame
    - Cleans strings and numbers
    - Drops fully empty rows
    - Removes duplicates
    - Returns df + csv bytes
    """
    if not rows:
        return PostprocessResult(df=pd.DataFrame(), csv_bytes=b"", removed_duplicates=0)

    df = pd.DataFrame(rows)

    # Clean each column heuristically
    for col in df.columns:
        # if column name hints numeric, treat as number
        col_l = str(col).lower()
        if any(k in col_l for k in ["price", "amount", "mrp", "rating", "score", "count"]):
            df[col] = df[col].apply(_clean_number)
        else:
            df[col] = df[col].apply(_clean_text)

    # Drop rows that are fully empty
    df = df.dropna(how="all")

    # Remove duplicates
    before = len(df)
    df = df.drop_duplicates()
    removed = before - len(df)

    # Create CSV bytes
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    return PostprocessResult(df=df, csv_bytes=csv_bytes, removed_duplicates=removed)
