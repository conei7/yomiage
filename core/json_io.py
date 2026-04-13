"""JSON I/O wrapper with atomic writes to prevent data corruption."""

import json
import os
import tempfile


class JsonIO:
    def __init__(
        self,
        file_path: str,
        encoding: str = "utf-8",
        indent: int = 4,
        ensure_ascii: bool = False,
    ) -> None:
        self.file_path = file_path
        self.encoding = encoding
        self.indent = indent
        self.ensure_ascii = ensure_ascii

        self.directory = os.path.dirname(os.path.abspath(file_path)) or "."
        self.file_name = os.path.basename(file_path)

    def write(self, data: dict, file_path: str = "") -> None:
        """Atomically write data to JSON file to prevent corruption."""
        path = file_path or self.file_path

        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".", suffix=".tmp", text=True)
        try:
            with os.fdopen(fd, "w", encoding=self.encoding) as file:
                json.dump(data, file, indent=self.indent, ensure_ascii=self.ensure_ascii)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def read(self, file_path: str = "") -> dict:
        """Read data from JSON file, returning an empty dict if it doesn't exist."""
        path = file_path or self.file_path
        if not os.path.exists(path):
            return {}

        with open(path, "r", encoding=self.encoding) as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                return {}

    def update(self, key: str, value: str) -> bool:
        """Update a specific key if the value has changed."""
        data = self.read()
        if data.get(key) == value:
            return False

        data[key] = value
        self.write(data)
        return True

    def create(self) -> None:
        """Create an empty JSON file if it does not exist."""
        if not os.path.exists(self.file_path):
            self.write({})
