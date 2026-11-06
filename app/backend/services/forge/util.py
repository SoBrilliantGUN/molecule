"""Small shared helpers."""

import hashlib
import json
import re
from typing import Any


class DomainError(Exception):
    """Business error (not found / conflict / validation). Permanent: never retried."""


def sha(*parts: Any) -> str:
    raw = "|".join(p if isinstance(p, str) else json.dumps(p, ensure_ascii=False, sort_keys=True, default=str) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array found in an LLM reply (handles code fences)."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON found in model output")
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("unterminated JSON in model output")
    return json.loads(text[start : end + 1])


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:20] or "app"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default
