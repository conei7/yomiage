import glob
import os
import shutil
import subprocess
from urllib import request


class FFmpegManager:
    def __init__(self, ffmpeg_folder_path: str = ".", download_files_list: list = ["ffmpeg.exe", "ffplay.exe", "ffprobe.exe"], over_write: bool = False) -> None:
        self.CURRET_FOLDER_SIGN = "."

        self.TEMPORARY_FOLDER_NAME = "ffmpeg_temp"
        self.TARGET_FOLDER_NAME = "ffmpeg-master-latest-win64-gpl"
        self.FFMPEG_URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{self.TARGET_FOLDER_NAME}.zip"
        self.DOWNLOADED_FOLDER_PATH = f"{self.TEMPORARY_FOLDER_NAME}\\{self.TARGET_FOLDER_NAME}.zip"
        self.EXPANDED_FOLDER_PATH = f"{self.TEMPORARY_FOLDER_NAME}\\{self.TARGET_FOLDER_NAME}"
        self.EXECUTABLE_FILES_PATH_FORMAT = f"{self.TEMPORARY_FOLDER_NAME}\\{self.TARGET_FOLDER_NAME}\\bin\\" + "{}"
        self.EXPAND_COMMAND = ["powershell", "Expand-Archive", "-Path", self.DOWNLOADED_FOLDER_PATH, "-DestinationPath", self.TEMPORARY_FOLDER_NAME, "-Force"]
        self.ffmpeg_folder_path = ffmpeg_folder_path
        self.download_files_list = download_files_list
        self.over_write = over_write
        self.moved_executable_files_path_format = f"{self.ffmpeg_folder_path}\\" + "{}"

    def is_available(self) -> bool:
        try:
            subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return True
        except FileNotFoundError:
            return False

    def is_exisits(self, name: str) -> bool:
        return len(glob.glob(self.moved_executable_files_path_format.format(name))) >= 1

    def file_download(self) -> None:
        os.makedirs(self.TEMPORARY_FOLDER_NAME, exist_ok=True)
        request.urlretrieve(self.FFMPEG_URL, self.DOWNLOADED_FOLDER_PATH)

    def file_expand(self) -> None:
        subprocess.run(self.EXPAND_COMMAND)

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
