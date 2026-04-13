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


def omit_code_blocks(text: str) -> str:
    return _RE_CODE_BLOCK.sub(PLACEHOLDER_CODE, text)


def omit_urls(text: str) -> str:
    return _RE_URL.sub(PLACEHOLDER_URL, text)


def omit_custom_emojis(text: str) -> str:
    return _RE_CUSTOM_EMOJI.sub("", text)


def collapse_spaces(text: str) -> str:
    return _RE_EXCESS_SPACES.sub("  ", text).strip()


def apply_dictionary(text: str, readings: dict[str, str]) -> str:
    for key, value in readings.items():
        text = text.replace(key, value)
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
    text = omit_code_blocks(text)
    text = omit_urls(text)
    text = apply_dictionary(text, readings or {})
    text = collapse_spaces(text)
    text = omit_custom_emojis(text)
    text = text_normalizer(text)
    text = truncate(text, max_length)
    return text
