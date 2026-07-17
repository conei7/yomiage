"""Helpers for deciding whether a voice channel contains real users."""

from typing import Any


def human_member_count(channel: Any) -> int:
    """Return the number of non-bot members currently in a voice channel."""
    return sum(1 for member in getattr(channel, "members", []) if not member.bot)


def bot_member_ids(channel: Any) -> list[int]:
    """Return sorted bot user IDs currently in a voice channel."""
    return sorted(member.id for member in getattr(channel, "members", []) if member.bot)
