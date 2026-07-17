import unittest
from types import SimpleNamespace

from core.voice_members import bot_member_ids, human_member_count


class HumanMemberCountTests(unittest.TestCase):
    def test_ignores_other_bots(self) -> None:
        channel = SimpleNamespace(
            members=[
                SimpleNamespace(bot=False),
                SimpleNamespace(bot=True),
                SimpleNamespace(bot=True),
            ]
        )

        self.assertEqual(human_member_count(channel), 1)

    def test_zero_when_only_bots_remain(self) -> None:
        channel = SimpleNamespace(
            members=[SimpleNamespace(bot=True), SimpleNamespace(bot=True)]
        )

        self.assertEqual(human_member_count(channel), 0)

    def test_bot_ids_are_sorted_for_deterministic_winner(self) -> None:
        channel = SimpleNamespace(
            members=[
                SimpleNamespace(id=30, bot=True),
                SimpleNamespace(id=10, bot=True),
                SimpleNamespace(id=20, bot=False),
            ]
        )

        self.assertEqual(bot_member_ids(channel), [10, 30])


if __name__ == "__main__":
    unittest.main()
