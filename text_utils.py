from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def join_texts(texts: Iterable[str]) -> str:
    return " ".join(text for text in texts if text)


def contains_any_marker(text: str, markers: Iterable[str]) -> bool:
    normalized_text = normalize_text(text or "")
    if not normalized_text:
        return False

    return any(contains_marker(normalized_text, marker) for marker in markers)


def contains_marker(normalized_text: str, marker: str) -> bool:
    normalized_marker = normalize_text(marker)
    if not normalized_marker:
        return False
    if is_phrase_marker(normalized_marker):
        return normalized_marker in normalized_text
    return bool(re.search(rf"\b{re.escape(normalized_marker)}\b", normalized_text))


def is_phrase_marker(normalized_marker: str) -> bool:
    return " " in normalized_marker or bool(re.search(r"[^\w\s]", normalized_marker))
