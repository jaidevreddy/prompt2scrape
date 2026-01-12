import re
from dataclasses import dataclass
from bs4 import BeautifulSoup


@dataclass
class CleanResult:
    cleaned_html: str
    cleaned_text: str
    original_len: int
    cleaned_len: int
    text_len: int


JUNK_SELECTORS = [
    "script", "style", "noscript",
    "svg", "canvas",
    "iframe",
    "nav", "footer", "header", "aside",
    "form", "button", "input", "select", "textarea",
]


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def clean_html(
    raw_html: str,
    max_cleaned_chars: int = 40_000,
    max_text_chars: int = 12_000,
) -> CleanResult:
    if not raw_html or len(raw_html) < 50:
        return CleanResult(
            cleaned_html="",
            cleaned_text="",
            original_len=len(raw_html or ""),
            cleaned_len=0,
            text_len=0,
        )

    soup = BeautifulSoup(raw_html, "lxml")
    original_len = len(raw_html)

    # Remove junk tags
    for sel in JUNK_SELECTORS:
        for tag in soup.select(sel):
            tag.decompose()

    junk_patterns = [
        "nav", "navbar", "footer", "header", "breadcrumb",
        "cookie", "consent", "gdpr",
        "modal", "popup", "newsletter",
        "sidebar", "aside",
        "ads", "advert", "promo",
        "search", "filter", "sort",
    ]

    def looks_junky(val: str) -> bool:
        if not val:
            return False
        v = str(val).lower()
        return any(p in v for p in junk_patterns)

    # Remove nodes with junky id/class
    for tag in soup.find_all(True):
        # ✅ Robust guard: tag.attrs can be None in rare cases
        if not hasattr(tag, "attrs") or tag.attrs is None:
            continue

        # ✅ Safely fetch id/class (avoid .get() crash)
        _id = tag.attrs.get("id", "") if isinstance(tag.attrs, dict) else ""
        _class_list = tag.attrs.get("class", []) if isinstance(tag.attrs, dict) else []
        _class = " ".join(_class_list) if isinstance(_class_list, list) else str(_class_list)

        if looks_junky(_id) or looks_junky(_class):
            # do not delete root containers
            if tag.name not in ["body", "main"]:
                tag.decompose()

    # Prefer main content if present
    main = soup.find("main")
    content_root = main if main else (soup.body or soup)

    cleaned_html = str(content_root)

    # Truncate cleaned HTML
    if len(cleaned_html) > max_cleaned_chars:
        cleaned_html = cleaned_html[:max_cleaned_chars]

    # Extract text
    text = content_root.get_text(separator="\n")
    text = _normalize_whitespace(text)

    if len(text) > max_text_chars:
        text = text[:max_text_chars]

    return CleanResult(
        cleaned_html=cleaned_html,
        cleaned_text=text,
        original_len=original_len,
        cleaned_len=len(cleaned_html),
        text_len=len(text),
    )
