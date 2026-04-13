import re
from typing import Callable


_ALLOWED_CHARACTER_PATTERN = re.compile(
    r"[A-Za-z0-9\uFF10-\uFF19\uFF21-\uFF3A\uFF41-\uFF5A"
    r"\u3040-\u309F\u30A0-\u30FF\u31F0-\u31FF"
    r"\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
    r"\uFF66-\uFF9F"
    r"]"
)

_ATTACHED_PUNCTUATION = {
    "!", "！", "?", "？", ".", "。", ",", "、", "；", ";", "：", ":", "…", "‥", "・"
}

_SPACE_COLLAPSE_PATTERN = re.compile(r"[ \u3000]+")


def _is_readable(ch: str) -> bool:
    return bool(_ALLOWED_CHARACTER_PATTERN.fullmatch(ch))


def _has_adjacent_readable(source: str, index: int, predicate: Callable[[str], bool]) -> bool:
    def scan(step: int) -> bool:
        i = index + step
        while 0 <= i < len(source):
            candidate = source[i]
            if candidate.isspace():
                return False
            if predicate(candidate):
                return True
            if candidate in _ATTACHED_PUNCTUATION:
                i += step
                continue
            return False
        return False

    return scan(-1) or scan(1)


def text_normalizer(text: str) -> str:
    filtered_chars: list[str] = []

    for idx, ch in enumerate(text):
        if ch in (" ", "\u3000"):
            filtered_chars.append(" ")
        elif _is_readable(ch):
            filtered_chars.append(ch)
        elif ch in _ATTACHED_PUNCTUATION and _has_adjacent_readable(text, idx, _is_readable):
            filtered_chars.append(ch)
        elif ch.isspace():
            filtered_chars.append(" ")

    if not filtered_chars:
        return ""

    collapsed = _SPACE_COLLAPSE_PATTERN.sub(" ", "".join(filtered_chars))
    return collapsed.strip()


if __name__ == "__main__":
    text_list = [
        "Hello world!",
        "HELLO WORLD......",
        "こんにちは世界！",
        "コンニチハセカイ! !!!",
        "ｺﾝﾆﾁﾊｾｶｲ!!!!",
        "????",
        "안녕하세요 세상"
    ]
    for value in text_list:
        print(f"{value!r} -> {text_normalizer(value)!r}")
