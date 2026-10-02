import json
import os
import tempfile
import threading
import unittest

from core.json_io import JsonIO


class JsonIOFailureTests(unittest.TestCase):
    def test_malformed_json_is_not_treated_as_empty_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as file:
                file.write('{"blacklist": ')

            with self.assertRaises(json.JSONDecodeError):
                JsonIO(path).read()

            with open(path, "r", encoding="utf-8") as file:
                self.assertEqual(file.read(), '{"blacklist": ')

    def test_transaction_prevents_threaded_lost_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "counter.json")
            JsonIO(path).write({"count": 0})

            def increment() -> None:
                io = JsonIO(path)
                for _ in range(50):
                    with io.transaction():
                        data = io.read()
                        data["count"] += 1
                        io.write(data)

            threads = [threading.Thread(target=increment) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(JsonIO(path).read()["count"], 200)


if __name__ == "__main__":
    unittest.main()
