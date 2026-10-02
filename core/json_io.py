"""JSON I/O wrapper with atomic writes to prevent data corruption."""

import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: str) -> threading.RLock:
    normalized = os.path.normcase(os.path.abspath(path))
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(normalized, threading.RLock())


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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Lock a complete read-modify-write sequence for this path."""
        with _path_lock(self.file_path):
            yield

    def write(self, data: dict, file_path: str = "") -> None:
        """Atomically write data to JSON file to prevent corruption."""
        path = file_path or self.file_path

        with _path_lock(path):
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(os.path.abspath(path)),
                prefix=".",
                suffix=".tmp",
                text=True,
            )
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
        with _path_lock(path):
            if not os.path.exists(path):
                return {}

            with open(path, "r", encoding=self.encoding) as file:
                # A malformed protection/config file must not silently become an
                # empty, permissive configuration. Let the caller fail safely.
                return json.load(file)

    def update(self, key: str, value: str) -> bool:
        """Update a specific key if the value has changed."""
        with self.transaction():
            data = self.read()
            if data.get(key) == value:
                return False

            data[key] = value
            self.write(data)
            return True

    def create(self) -> None:
        """Create an empty JSON file if it does not exist."""
        with self.transaction():
            if not os.path.exists(self.file_path):
                self.write({})
