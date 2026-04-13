"""Entry point for the yomiage Discord bot."""

import platform

import discord
from discord.ext import commands

from core.config import load_json_with_private
from core.ffmpeg import FFmpegManager

INITIAL_EXTENSIONS = ["cogs.maincog"]

CONFIG_PATH = "./config/settings.json"
CONFIG_PRIVATE_PATH = "./config/settings.private.json"


class Bot(commands.Bot):
    def __init__(self, command_prefix: str, intents: discord.Intents):
        super().__init__(command_prefix, intents=intents)

    async def setup_hook(self) -> None:
        for cog in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(cog)
            except Exception as e:
                print(f"error   : failed to load {cog}: {e}")


def _ensure_ffmpeg() -> None:
    ffmanager = FFmpegManager(download_files_list=["ffmpeg.exe"])

    if ffmanager.is_available():
        return

    answer = input(
        'ffmpegが見つかりません。ダウンロードしますか？(y/n)\n>>> '
    )
    if answer.lower() not in ("", "y"):
        return

    system = platform.system()
    if system == "Windows":
        if not ffmanager.is_exisits("ffmpeg.exe"):
            print("downloading ffmpeg.exe...")
            ffmanager.run(quiet=True)
    else:
        print("警告: Windows以外の環境ではffmpegの自動ダウンロードに対応していません。")


def _ensure_voicevox() -> None:
    import requests
    from core.voice import VCHandler
    try:
        url = f"http://{VCHandler.HOST}:{VCHandler.PORT}/version"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return
    except Exception:
        pass
    print("error   : VOICEVOX APIに接続できません。")
    print(f"          VOICEVOXエンジンを起動({VCHandler.PORT}ポート)してから再実行してください。")
    exit(1)


if __name__ == "__main__":
    print(f"system  : {platform.system()}")

    _ensure_ffmpeg()
    _ensure_voicevox()

    config = load_json_with_private(CONFIG_PATH, CONFIG_PRIVATE_PATH)

    intents = discord.Intents.default()
    intents.message_content = True

    bot = Bot(command_prefix="/", intents=intents)
    bot.run(config["bot_token"])
