"""Text processing utilities for the yomiage bot."""

import re

from core.text_normalizer import text_normalizer

_RE_CODE_BLOCK = re.compile(r"```.+```", re.DOTALL)
_RE_URL = re.compile(r"https?://[^\s]+")
_RE_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
_RE_EXCESS_SPACES = re.compile(r"\s{3,}")

PLACEHOLDER_CODE = "コード省略。"
PLACEHOLDER_URL = "リンク省略。"
PLACEHOLDER_LONG = "以下省略。"
PLACEHOLDER_ATTACHED = "添付ファイル。"

# Dictionary entries are applied sequentially for backward compatibility.
# Bound intermediate output so chained entries cannot grow exponentially.
MAX_DICTIONARY_TEXT_LENGTH = 10_000


def omit_code_blocks(text: str) -> str:
    return _RE_CODE_BLOCK.sub(PLACEHOLDER_CODE, text)


def omit_urls(text: str) -> str:
    return _RE_URL.sub(PLACEHOLDER_URL, text)


def omit_custom_emojis(text: str) -> str:
    return _RE_CUSTOM_EMOJI.sub("", text)


def collapse_spaces(text: str) -> str:
    return _RE_EXCESS_SPACES.sub("  ", text).strip()


def _replace_bounded(text: str, old: str, new: str, limit: int) -> str:
    """Replace *old* while materializing at most *limit* output characters."""
    if not old or limit <= 0:
        return text[:limit]

    if len(new) <= len(old):
        return text.replace(old, new)[:limit]

    # At most ceil(limit / len(new)) expanding replacements can contribute to
    # the retained prefix.  Capping count avoids both huge intermediates and a
    # Python-level loop over every match.
    count = (limit + len(new) - 1) // len(new)
    return text.replace(old, new, count)[:limit]


def apply_dictionary(
    text: str,
    readings: dict[str, str],
    max_length: int | None = None,
) -> str:
    # Keep enough source text for normalisation while still preventing chained
    # replacements from producing an unbounded intermediate string.
    limit = (
        max(MAX_DICTIONARY_TEXT_LENGTH, max(0, max_length) + 1)
        if max_length is not None
        else MAX_DICTIONARY_TEXT_LENGTH
    )
    text = text[:limit]

    for key, value in readings.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key:
            continue
        text = _replace_bounded(text, key, value, limit)
    return text


def truncate(text: str, max_length: int) -> str:
    if len(text) > max_length:
        return text[:max_length] + PLACEHOLDER_LONG
    return text


def process_text(
    text: str,
    readings: dict[str, str] | None = None,
    max_length: int = 100,
) -> str:
    if re.fullmatch(r"[8８]+", text.strip()):
        return "パチパチ"

    text = omit_code_blocks(text)
    text = omit_urls(text)
    text = apply_dictionary(text, readings or {}, max_length=max_length)
    text = collapse_spaces(text)
    text = omit_custom_emojis(text)
    text = text_normalizer(text)
    text = truncate(text, max_length)
    return text
