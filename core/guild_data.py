"""Helpers for isolating mutable data by Discord guild."""

import threading
from typing import Any

from core.json_io import JsonIO


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def get_guild_settings(data: dict, guild_id: int) -> dict:
    """Return effective settings for a guild without mutating *data*.

    Top-level values are retained as legacy defaults so existing
    ``server_config.json`` files continue to work after the migration.
    """
    guilds = _as_dict(data.get("guilds"))
    stored = _as_dict(guilds.get(str(guild_id)))
    legacy_bindings = _as_dict(data.get("channel_bindings"))

    return {
        "auto_join_vc": stored.get("auto_join_vc", data.get("auto_join_vc", True)),
        "announce_join_leave": stored.get(
            "announce_join_leave", data.get("announce_join_leave", True)
        ),
        "channel_bindings": dict(
            _as_dict(stored.get("channel_bindings", legacy_bindings))
        ),
    }


def ensure_guild_settings(data: dict, guild_id: int) -> dict:
    """Return the mutable settings entry for *guild_id*, creating it if needed."""
    guilds = data.get("guilds")
    if not isinstance(guilds, dict):
        guilds = {}
        data["guilds"] = guilds

    key = str(guild_id)
    stored = guilds.get(key)
    if not isinstance(stored, dict):
        stored = get_guild_settings(data, guild_id)
        guilds[key] = stored
        return stored

    effective = get_guild_settings(data, guild_id)
    stored.setdefault("auto_join_vc", effective["auto_join_vc"])
    stored.setdefault("announce_join_leave", effective["announce_join_leave"])
    if not isinstance(stored.get("channel_bindings"), dict):
        stored["channel_bindings"] = effective["channel_bindings"]
    return stored


def get_guild_readings(data: dict, guild_id: int) -> dict[str, str]:
    """Return only the readings stored for *guild_id*."""
    guilds = _as_dict(data.get("guilds"))
    stored = _as_dict(guilds.get(str(guild_id)))
    readings = stored.get("readings")
    if isinstance(readings, dict):
        return readings
    return {}


def ensure_guild_readings(data: dict, guild_id: int) -> dict[str, str]:
    """Create and return an isolated mutable readings dictionary for a guild."""
    guilds = data.get("guilds")
    if not isinstance(guilds, dict):
        guilds = {}
        data["guilds"] = guilds

    key = str(guild_id)
    stored = guilds.get(key)
    if not isinstance(stored, dict):
        stored = {}
        guilds[key] = stored

    readings = stored.get("readings")
    if not isinstance(readings, dict):
        readings = {}
        stored["readings"] = readings
    return readings


def materialize_guild_readings(
    data: dict,
    guild_ids: list[int],
    legacy: dict[str, str],
) -> bool:
    """Copy *legacy* into guilds that do not yet have an isolated dictionary."""
    unique_guild_ids = sorted(set(guild_ids))
    if not unique_guild_ids:
        return False

    guilds = data.get("guilds")
    if not isinstance(guilds, dict):
        guilds = {}
        data["guilds"] = guilds

    changed = False
    for guild_id in unique_guild_ids:
        key = str(guild_id)
        stored = guilds.get(key)
        if not isinstance(stored, dict):
            stored = {}
            guilds[key] = stored
        if not isinstance(stored.get("readings"), dict):
            stored["readings"] = dict(legacy)
            changed = True
    return changed


def migrate_legacy_readings(
    data: dict,
    guild_ids: list[int],
    *,
    remove_legacy: bool = True,
) -> bool:
    """Copy a legacy shared dictionary to current guilds.

    The migration is deliberately explicit: leaving ``readings`` as a read
    fallback would expose one guild's historical entries to every later guild.
    Multi-account startup can defer removal until every account reports in.
    """
    legacy = data.get("readings")
    if not isinstance(legacy, dict):
        return False

    unique_guild_ids = sorted(set(guild_ids))
    if not unique_guild_ids:
        if remove_legacy:
            del data["readings"]
            return True
        return False

    changed = materialize_guild_readings(data, unique_guild_ids, legacy)

    if remove_legacy:
        del data["readings"]
        changed = True
    return changed


class LegacyReadingsCoordinator:
    """Coordinate durable one-time migration across bot accounts.

    The legacy source is moved into a private, non-readable migration record.
    Completion is persisted by stable bot user ID, so a process restart before
    every configured account becomes ready cannot lose or re-expose the data.
    """

    PENDING_KEY = "_legacy_readings_migration"

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._seen_this_process: set[str] = set()

    def migrate_account(
        self,
        dictionary_io: JsonIO,
        account_key: str | int,
        account_count: int,
        guild_ids: list[int],
    ) -> dict:
        with self._guard:
            with dictionary_io.transaction():
                data = dictionary_io.read()
                changed = False

                pending = data.get(self.PENDING_KEY)
                if not (
                    isinstance(pending, dict)
                    and isinstance(pending.get("readings"), dict)
                ):
                    if self.PENDING_KEY in data:
                        del data[self.PENDING_KEY]
                        changed = True
                    legacy = data.pop("readings", None)
                    if isinstance(legacy, dict):
                        pending = {
                            "readings": dict(legacy),
                            "completed_accounts": [],
                        }
                        changed = True
                    else:
                        pending = None
                elif "readings" in data:
                    # A prior interrupted/older migration may have left the
                    # public legacy key beside the private pending record.
                    del data["readings"]
                    changed = True

                if pending is not None:
                    raw_completed = pending.get("completed_accounts", [])
                    completed = {
                        str(value)
                        for value in raw_completed
                        if isinstance(value, (str, int))
                    } if isinstance(raw_completed, list) else set()
                    normalized_key = str(account_key)
                    candidate_seen = self._seen_this_process | {normalized_key}

                    if normalized_key not in completed:
                        changed = materialize_guild_readings(
                            data,
                            guild_ids,
                            pending["readings"],
                        ) or changed
                        completed.add(normalized_key)
                        changed = True

                    # Persisted IDs prevent duplicate materialization.  Only
                    # bots actually seen in this process count toward deleting
                    # pending data, so a changed account cohort cannot cause an
                    # early delete based on stale IDs.
                    if len(candidate_seen) >= max(1, account_count):
                        if self.PENDING_KEY in data:
                            del data[self.PENDING_KEY]
                        changed = True
                    else:
                        normalized_pending = {
                            "readings": pending["readings"],
                            "completed_accounts": sorted(completed),
                        }
                        if data.get(self.PENDING_KEY) != normalized_pending:
                            data[self.PENDING_KEY] = normalized_pending
                            changed = True

                if changed:
                    dictionary_io.write(data)

            # As with completion, only advance volatile coordination state
            # after the file transaction succeeds.
            self._seen_this_process.add(str(account_key))
            return data


legacy_readings_coordinator = LegacyReadingsCoordinator()
