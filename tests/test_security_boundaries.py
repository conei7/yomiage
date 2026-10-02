import os
import tempfile
import unittest

from core.guild_data import (
    LegacyReadingsCoordinator,
    ensure_guild_readings,
    ensure_guild_settings,
    get_guild_readings,
    get_guild_settings,
    migrate_legacy_readings,
)
from core.json_io import JsonIO
from core.text_processor import (
    MAX_DICTIONARY_TEXT_LENGTH,
    PLACEHOLDER_LONG,
    _replace_bounded,
    apply_dictionary,
    process_text,
)
from core.text_normalizer import text_normalizer


class DictionaryExpansionTests(unittest.TestCase):
    def test_cascading_replacements_stay_bounded(self) -> None:
        readings = {
            "a": "b" * 100,
            "b": "c" * 100,
            "c": "d" * 100,
            "d": "e" * 100,
        }

        intermediate = apply_dictionary("a" * 2_000, readings, max_length=600)
        result = process_text("a" * 2_000, readings=readings, max_length=600)

        self.assertLessEqual(len(intermediate), MAX_DICTIONARY_TEXT_LENGTH)
        self.assertEqual(len(result), 600 + len(PLACEHOLDER_LONG))
        self.assertTrue(result.endswith(PLACEHOLDER_LONG))

    def test_normal_dictionary_replacement_is_unchanged(self) -> None:
        self.assertEqual(process_text("猫です", {"猫": "ねこ"}, 100), "ねこです")

    def test_bounded_replace_matches_full_replace_prefix(self) -> None:
        cases = [
            ("aaa", "a", "ba", 3),
            ("a" * 100, "a", "bb", 100),
            ("abcabc", "abc", "x", 4),
            ("ab--ab--ab", "ab", "long-value", 9),
        ]
        for text, old, new, limit in cases:
            with self.subTest(text=text, old=old, new=new, limit=limit):
                self.assertEqual(
                    _replace_bounded(text, old, new, limit),
                    text.replace(old, new)[:limit],
                )


class TextNormalizerComplexityTests(unittest.TestCase):
    def test_long_punctuation_run_is_discarded_in_one_pass(self) -> None:
        self.assertEqual(text_normalizer("." * 20_000), "")

    def test_punctuation_runs_keep_existing_semantics(self) -> None:
        self.assertEqual(text_normalizer("...A"), "...A")
        self.assertEqual(text_normalizer("A...😀"), "A...")
        self.assertEqual(text_normalizer("A...😀...B"), "A......B")


class GuildIsolationTests(unittest.TestCase):
    def test_legacy_settings_are_copied_before_guild_mutation(self) -> None:
        data = {
            "auto_join_vc": True,
            "announce_join_leave": True,
            "channel_bindings": {"10": 20},
        }

        guild_one = ensure_guild_settings(data, 1)
        guild_one["auto_join_vc"] = False
        guild_one["channel_bindings"]["11"] = 21

        self.assertTrue(get_guild_settings(data, 2)["auto_join_vc"])
        self.assertNotIn("11", get_guild_settings(data, 2)["channel_bindings"])

    def test_legacy_dictionary_is_materialized_once_then_removed(self) -> None:
        data = {"readings": {"共通": "きょうつう"}}

        self.assertTrue(migrate_legacy_readings(data, [1, 2]))
        self.assertNotIn("readings", data)

        guild_one = ensure_guild_readings(data, 1)
        guild_one["専用"] = "せんよう"

        self.assertNotIn("専用", get_guild_readings(data, 2))
        self.assertEqual(get_guild_readings(data, 2)["共通"], "きょうつう")
        self.assertEqual(get_guild_readings(data, 1)["専用"], "せんよう")

    def test_new_guild_does_not_inherit_legacy_or_other_guild_data(self) -> None:
        data = {"readings": {"旧共有": "きゅうきょうゆう"}}
        migrate_legacy_readings(data, [1])

        self.assertEqual(get_guild_readings(data, 2), {})
        self.assertEqual(ensure_guild_readings(data, 2), {})

    def test_multi_account_can_materialize_before_final_removal(self) -> None:
        data = {"readings": {"旧共有": "きゅうきょうゆう"}}

        self.assertTrue(migrate_legacy_readings(data, [1], remove_legacy=False))
        self.assertIn("readings", data)
        self.assertEqual(get_guild_readings(data, 1)["旧共有"], "きゅうきょうゆう")
        self.assertEqual(get_guild_readings(data, 2), {})

        self.assertTrue(migrate_legacy_readings(data, [1, 2], remove_legacy=True))
        self.assertNotIn("readings", data)
        self.assertEqual(get_guild_readings(data, 2)["旧共有"], "きゅうきょうゆう")

    def test_zero_guild_startup_discards_legacy_data(self) -> None:
        data = {"readings": {"旧共有": "きゅうきょうゆう"}}

        self.assertTrue(migrate_legacy_readings(data, [], remove_legacy=True))
        self.assertNotIn("readings", data)

    def test_shared_coordinator_migrates_separate_bot_accounts_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "dictionary.json")
            first_io = JsonIO(path)
            second_io = JsonIO(path)
            first_io.write({"readings": {"旧共有": "きゅうきょうゆう"}})
            coordinator = LegacyReadingsCoordinator()

            coordinator.migrate_account(first_io, "bot-a", 2, [1])
            after_first = first_io.read()
            self.assertNotIn("readings", after_first)
            self.assertIn(coordinator.PENDING_KEY, after_first)
            self.assertEqual(get_guild_readings(after_first, 1)["旧共有"], "きゅうきょうゆう")

            # A fresh coordinator simulates a process restart between accounts.
            coordinator = LegacyReadingsCoordinator()
            coordinator.migrate_account(first_io, "bot-a", 2, [1, 3])
            self.assertEqual(get_guild_readings(first_io.read(), 3), {})
            self.assertIn(coordinator.PENDING_KEY, first_io.read())

            coordinator.migrate_account(second_io, "bot-b", 2, [2])
            after_second = second_io.read()
            self.assertNotIn(coordinator.PENDING_KEY, after_second)
            self.assertEqual(get_guild_readings(after_second, 2)["旧共有"], "きゅうきょうゆう")

    def test_changed_account_cohort_does_not_delete_pending_early(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "dictionary.json")
            io = JsonIO(path)
            io.write({"readings": {"旧共有": "きゅうきょうゆう"}})

            LegacyReadingsCoordinator().migrate_account(io, "old-bot", 2, [1])

            coordinator = LegacyReadingsCoordinator()
            coordinator.migrate_account(io, "new-bot-a", 2, [2])
            self.assertIn(coordinator.PENDING_KEY, io.read())

            coordinator.migrate_account(io, "new-bot-b", 2, [3])
            migrated = io.read()
            self.assertNotIn(coordinator.PENDING_KEY, migrated)
            self.assertEqual(get_guild_readings(migrated, 2)["旧共有"], "きゅうきょうゆう")
            self.assertEqual(get_guild_readings(migrated, 3)["旧共有"], "きゅうきょうゆう")

    def test_coordinator_retries_after_initial_write_failure(self) -> None:
        class FailOnceJsonIO(JsonIO):
            def __init__(self, path: str) -> None:
                super().__init__(path)
                self.fail_next_write = True

            def write(self, data: dict, file_path: str = "") -> None:
                if self.fail_next_write:
                    self.fail_next_write = False
                    raise OSError("simulated write failure")
                super().write(data, file_path)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "dictionary.json")
            JsonIO(path).write({"readings": {"旧共有": "きゅうきょうゆう"}})
            io = FailOnceJsonIO(path)
            coordinator = LegacyReadingsCoordinator()

            with self.assertRaises(OSError):
                coordinator.migrate_account(io, "bot-a", 1, [1])
            coordinator.migrate_account(io, "bot-a", 1, [1])

            migrated = JsonIO(path).read()
            self.assertNotIn("readings", migrated)
            self.assertEqual(get_guild_readings(migrated, 1)["旧共有"], "きゅうきょうゆう")


if __name__ == "__main__":
    unittest.main()
