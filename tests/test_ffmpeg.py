import hashlib
import io
import os
import tempfile
import unittest
from unittest import mock

from core.ffmpeg import FFmpegManager


class _FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class FFmpegDownloadTests(unittest.TestCase):
    def _manager(self, directory: str, payload: bytes) -> FFmpegManager:
        manager = FFmpegManager(download_files_list=["ffmpeg.exe"])
        manager.TEMPORARY_FOLDER_NAME = directory
        manager.DOWNLOADED_FOLDER_PATH = os.path.join(directory, "ffmpeg.zip")
        manager.ARCHIVE_SHA256 = hashlib.sha256(payload).hexdigest()
        manager.MAX_ARCHIVE_SIZE = len(payload) + 1
        return manager

    def test_verified_archive_is_kept(self) -> None:
        payload = b"verified archive"
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory, payload)
            with mock.patch("core.ffmpeg.request.urlopen", return_value=_FakeResponse(payload)):
                manager.file_download()

            with open(manager.DOWNLOADED_FOLDER_PATH, "rb") as archive:
                self.assertEqual(archive.read(), payload)

    def test_hash_mismatch_removes_archive(self) -> None:
        payload = b"tampered archive"
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory, payload)
            manager.ARCHIVE_SHA256 = "0" * 64
            with mock.patch("core.ffmpeg.request.urlopen", return_value=_FakeResponse(payload)):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    manager.file_download()

            self.assertFalse(os.path.exists(manager.DOWNLOADED_FOLDER_PATH))


if __name__ == "__main__":
    unittest.main()
