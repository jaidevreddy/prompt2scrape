import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


@dataclass
class PlanResult:
    plan: Dict[str, Any]
    model: str
    attempts: int


PLAN_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "item_container": {"type": "string", "minLength": 1},
        "fields": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "selector": {"type": "string", "minLength": 1},
                    "type": {
                        "type": "string",
                        "enum": ["text", "number", "url", "date", "bool"],
                    },
                    "fallback_selectors": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "selector", "type", "fallback_selectors"],
            },
        },
    },
    "required": ["item_container", "fields"],
}


def _contains_unsupported_selector_features(sel: str) -> Optional[str]:
    """
    SoupSieve supports many selectors but NOT web-standard :contains(), and :has() is risky.
    We'll forbid these so extraction never crashes.
    """
    s = sel.lower()

    # forbidden pseudo-classes / patterns
    forbidden = [
        ":has(",
        ":contains(",
        ":-soup-contains(",  # even though soupsieve supports it, it's fragile in planning
        ":matches(",
        ":nth-match(",
    ]
    for f in forbidden:
        if f in s:
            return f"Selector contains unsupported/forbidden feature: {f}"

    # Also disallow newline selectors and extremely long selectors
    if "\n" in sel or "\r" in sel:
        return "Selector contains newlines"

    if len(sel) > 200:
        return "Selector too long (likely brittle)"

    return None


def _validate_plan(plan: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    if not isinstance(plan, dict):
        return False, ["Plan is not an object."]

    item_container = plan.get("item_container")
    if not isinstance(item_container, str) or not item_container.strip():
        errors.append("item_container must be a non-empty string CSS selector.")
    else:
        bad = _contains_unsupported_selector_features(item_container)
        if bad:
            errors.append(f"item_container invalid: {bad}")

    fields = plan.get("fields")
    if not isinstance(fields, list) or len(fields) == 0:
        errors.append("fields must be a non-empty list.")
        return False, errors

    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            errors.append(f"fields[{i}] must be an object.")
            continue

        name = f.get("name")
        sel = f.get("selector")
        typ = f.get("type")
        fb = f.get("fallback_selectors")

        if not isinstance(name, str) or not name.strip():
            errors.append(f"fields[{i}].name must be a non-empty string.")

        if not isinstance(sel, str) or not sel.strip():
            errors.append(f"fields[{i}].selector must be a non-empty string.")
        else:
            bad = _contains_unsupported_selector_features(sel)
            if bad:
                errors.append(f"fields[{i}].selector invalid: {bad}")

        if typ not in ["text", "number", "url", "date", "bool"]:
            errors.append(f"fields[{i}].type must be one of text/number/url/date/bool.")

        if not isinstance(fb, list) or any(not isinstance(x, str) for x in fb):
            errors.append(f"fields[{i}].fallback_selectors must be a list of strings.")
        else:
            # Validate fallbacks too
            for j, s in enumerate(fb):
                bad = _contains_unsupported_selector_features(s)
                if bad:
                    errors.append(f"fields[{i}].fallback_selectors[{j}] invalid: {bad}")

    return len(errors) == 0, errors


def _build_messages(user_prompt: str, cleaned_html: str, cleaned_text: str) -> List[Dict[str, str]]:
    system = (
        "You are a web scraping planner.\n"
        "Given a user's extraction request and cleaned HTML/text, output a strict JSON extraction plan.\n\n"
        "VERY IMPORTANT RULES:\n"
        "- Output JSON ONLY (no markdown, no explanation).\n"
        "- item_container MUST be a SIMPLE CSS selector supported by BeautifulSoup/SoupSieve.\n"
        "- DO NOT use advanced selectors like :has(), :contains(), :-soup-contains(), :matches().\n"
        "- Filtering like 'only hoodies' or 'above 10k' MUST NOT be encoded into CSS selectors.\n"
        "- Instead: extract enough fields so the app can filter later (example: product_type/subtitle + price).\n"
        "- Field selectors should be simple and stable (classes, data-testid attributes).\n"
        "- Always include fallback_selectors array (can be empty).\n"
    )

    user = (
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"CLEANED HTML (truncated):\n{cleaned_html}\n\n"
        f"CLEANED TEXT (truncated):\n{cleaned_text}\n"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def generate_extraction_plan(
    user_prompt: str,
    cleaned_html: str,
    cleaned_text: str,
    model: str = "gpt-4o-mini",
    timeout_s: int = 45,
) -> PlanResult:
    client = OpenAI()
    messages = _build_messages(user_prompt, cleaned_html, cleaned_text)

    def call_once(extra_feedback: Optional[str] = None) -> Dict[str, Any]:
        input_msgs = list(messages)
        if extra_feedback:
            input_msgs.append(
                {
                    "role": "user",
                    "content": (
                        "The previous plan failed validation.\n"
                        "Fix the plan according to this feedback:\n"
                        f"{extra_feedback}"
                    ),
                }
            )

        resp = client.responses.create(
            model=model,
            input=input_msgs,
            temperature=0,
            max_output_tokens=900,
            timeout=timeout_s,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "extraction_plan",
                    "strict": True,
                    "schema": PLAN_JSON_SCHEMA,
                }
            },
        )

        raw = resp.output_text
        return json.loads(raw)

    # Attempt 1
    plan = call_once()
    ok, errs = _validate_plan(plan)
    if ok:
        return PlanResult(plan=plan, model=model, attempts=1)

    # Retry once with validation feedback
    feedback = "Plan validation errors:\n" + "\n".join(f"- {e}" for e in errs)
    plan2 = call_once(extra_feedback=feedback)
    ok2, errs2 = _validate_plan(plan2)

    if not ok2:
        raise RuntimeError("Plan validation failed after retry:\n" + "\n".join(errs2))

    return PlanResult(plan=plan2, model=model, attempts=2)
