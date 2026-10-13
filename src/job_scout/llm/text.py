from __future__ import annotations

import hashlib
import html
import re

_TAG_RE = re.compile(r"<[^>]+>")

_CHARS_PER_TOKEN_ESTIMATE = 4
_PROMPT_OVERHEAD_FRACTION = 0.25  # reserve for system prompt/schema/model output


def strip_html(description_html: str) -> str:
    # Greenhouse (and possibly others) return description content with its HTML
    # entity-escaped rather than raw tags (e.g. "&lt;h2&gt;...&lt;/h2&gt;"), and
    # some of that is itself double-escaped ("&amp;nbsp;"). Unescape twice before
    # stripping tags — a second pass is a harmless no-op for already-clean input.
    unescaped = html.unescape(html.unescape(description_html or ""))
    stripped = _TAG_RE.sub(" ", unescaped)
    return " ".join(stripped.split())


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def truncate_for_context(text: str, *, num_ctx: int) -> str:
    # Approximate token budget, not a real tokenizer — Ollama's own tokenizer
    # varies per model, so this is a documented heuristic, not exact.
    budget_tokens = int(num_ctx * (1 - _PROMPT_OVERHEAD_FRACTION))
    budget_chars = budget_tokens * _CHARS_PER_TOKEN_ESTIMATE
    return text[:budget_chars]


def extraction_text(title: str, location: str, description_html: str) -> str:
    # Title and location usually live outside the description body, and the
    # model can't extract location/remote/seniority it never sees.
    return f"Title: {title}\nLocation: {location}\n\n{strip_html(description_html)}"
