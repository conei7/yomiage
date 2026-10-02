import json
import os
import tempfile
import unittest
from unittest import mock

from core.config import choose_runtime_json_path, load_json_with_private
from core.json_io import JsonIO


class ConfigSecretTests(unittest.TestCase):
    def test_environment_token_overrides_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_path = os.path.join(directory, "settings.json")
            private_path = os.path.join(directory, "settings.private.json")
            with open(base_path, "w", encoding="utf-8") as file:
                json.dump({"version": "1"}, file)
            with open(private_path, "w", encoding="utf-8") as file:
                json.dump({"bot_token": "file-token", "admin_users": [1]}, file)

            with mock.patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "env-token"}):
                config = load_json_with_private(base_path, private_path)

            self.assertEqual(config["bot_token"], "env-token")
            self.assertEqual(config["admin_users"], [1])

    def test_environment_token_works_without_file_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_path = os.path.join(directory, "settings.json")
            private_path = os.path.join(directory, "settings.private.json")
            with open(base_path, "w", encoding="utf-8") as file:
                json.dump({"version": "1"}, file)
            with open(private_path, "w", encoding="utf-8") as file:
                json.dump({"admin_users": [1]}, file)

            with mock.patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "env-token"}):
                config = load_json_with_private(base_path, private_path)

            self.assertEqual(config["bot_token"], "env-token")
            self.assertEqual(config["admin_users"], [1])

    def test_runtime_data_is_bootstrapped_to_private_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_path = os.path.join(directory, "data.json")
            private_path = os.path.join(directory, "data.private.json")
            with open(base_path, "w", encoding="utf-8") as file:
                json.dump({"template": True}, file)

            selected = choose_runtime_json_path(base_path, private_path)
            JsonIO(selected).write({"runtime": True})

            self.assertEqual(selected, private_path)
            self.assertEqual(JsonIO(base_path).read(), {"template": True})
            self.assertEqual(JsonIO(private_path).read(), {"runtime": True})


if __name__ == "__main__":
    unittest.main()
