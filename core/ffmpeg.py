import glob
import hashlib
import os
import shutil
import subprocess
from urllib import request


class FFmpegManager:
    # BtbN retains the final build of each month long-term.
    RELEASE_TAG = "autobuild-2026-08-31-13-27"
    ARCHIVE_NAME = "ffmpeg-N-126342-gf88b741dbf-win64-gpl.zip"
    ARCHIVE_SHA256 = "b4da332540eaebc6939181b59e267f163dd57407ef6596f7f3452845921d1d91"
    MAX_ARCHIVE_SIZE = 180 * 1024 * 1024
    DOWNLOAD_TIMEOUT = 30

    def __init__(
        self,
        ffmpeg_folder_path: str = ".",
        download_files_list: list | None = None,
        over_write: bool = False,
    ) -> None:
        self.CURRET_FOLDER_SIGN = "."

        self.TEMPORARY_FOLDER_NAME = "ffmpeg_temp"
        self.TARGET_FOLDER_NAME = self.ARCHIVE_NAME.removesuffix(".zip")
        self.FFMPEG_URL = (
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
            f"{self.RELEASE_TAG}/{self.ARCHIVE_NAME}"
        )
        self.DOWNLOADED_FOLDER_PATH = f"{self.TEMPORARY_FOLDER_NAME}\\{self.ARCHIVE_NAME}"
        self.EXPANDED_FOLDER_PATH = f"{self.TEMPORARY_FOLDER_NAME}\\{self.TARGET_FOLDER_NAME}"
        self.EXECUTABLE_FILES_PATH_FORMAT = f"{self.TEMPORARY_FOLDER_NAME}\\{self.TARGET_FOLDER_NAME}\\bin\\" + "{}"
        self.EXPAND_COMMAND = ["powershell", "Expand-Archive", "-Path", self.DOWNLOADED_FOLDER_PATH, "-DestinationPath", self.TEMPORARY_FOLDER_NAME, "-Force"]
        self.ffmpeg_folder_path = ffmpeg_folder_path
        self.download_files_list = (
            download_files_list
            if download_files_list is not None
            else ["ffmpeg.exe", "ffplay.exe", "ffprobe.exe"]
        )
        self.over_write = over_write
        self.moved_executable_files_path_format = f"{self.ffmpeg_folder_path}\\" + "{}"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def is_exisits(self, name: str) -> bool:
        return len(glob.glob(self.moved_executable_files_path_format.format(name))) >= 1

    def file_download(self) -> None:
        os.makedirs(self.TEMPORARY_FOLDER_NAME, exist_ok=True)
        digest = hashlib.sha256()
        downloaded = 0
        download_request = request.Request(
            self.FFMPEG_URL, headers={"User-Agent": "yomiage-v2"}
        )

        try:
            with request.urlopen(download_request, timeout=self.DOWNLOAD_TIMEOUT) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self.MAX_ARCHIVE_SIZE:
                    raise RuntimeError("FFmpeg archive exceeds the size limit")

                with open(self.DOWNLOADED_FOLDER_PATH, "wb") as archive:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > self.MAX_ARCHIVE_SIZE:
                            raise RuntimeError("FFmpeg archive exceeds the size limit")
                        archive.write(chunk)
                        digest.update(chunk)

            if digest.hexdigest() != self.ARCHIVE_SHA256:
                raise RuntimeError("FFmpeg archive SHA-256 verification failed")
        except Exception:
            if os.path.exists(self.DOWNLOADED_FOLDER_PATH):
                os.remove(self.DOWNLOADED_FOLDER_PATH)
            raise

    def file_expand(self) -> None:
        subprocess.run(self.EXPAND_COMMAND, check=True, timeout=180)

    def temporary_folder_delete(self) -> bool:
        result = False
        try:
            shutil.rmtree(self.TEMPORARY_FOLDER_NAME)
            result = True
        except FileNotFoundError:
            result = False
        return result

    def ffmpeg_folder_reset(self) -> bool:
        result = False
        try:
            shutil.rmtree(self.ffmpeg_folder_path)
            result = True
        except FileNotFoundError:
            result = False
        os.makedirs(self.ffmpeg_folder_path, exist_ok=True)
        return result

    def move(self) -> None:
        for name in self.download_files_list:
            if self.is_exisits(name):
                os.remove(self.moved_executable_files_path_format.format(name))
            shutil.move(self.EXECUTABLE_FILES_PATH_FORMAT.format(name), self.ffmpeg_folder_path)

    def run(self, quiet: bool = False, use_msbox: bool = True) -> bool:
        self.temporary_folder_delete()

        if sum([int(self.is_exisits(name)) for name in self.download_files_list]) == len(self.download_files_list):
            if not self.over_write:
                return False

        if not quiet:
            print("downloading zip...")
        self.file_download()

        if not quiet:
            print("done")
            print("expanding zip...")
        self.file_expand()

        if not quiet:
            print("done")
            print("moving files...")
        self.move()

        if not quiet:
            print("done")

        self.temporary_folder_delete()
        return True


if __name__ == "__main__":
    manager = FFmpegManager(".")
    manager.run()
