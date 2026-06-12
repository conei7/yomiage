"""Entry point for the yomiage Discord bot."""

import logging
import platform
import subprocess
import sys
import time
import traceback

import discord
from discord.ext import commands

from core.config import load_json_with_private
from core.ffmpeg import FFmpegManager

INITIAL_EXTENSIONS = ["cogs.maincog"]

CONFIG_PATH = "./config/settings.json"
CONFIG_PRIVATE_PATH = "./config/settings.private.json"

# Maximum number of consecutive restart attempts before giving up
MAX_RESTART_ATTEMPTS = 10
RESTART_COOLDOWN_BASE = 5  # seconds, doubles each attempt


class Bot(commands.Bot):
    def __init__(self, command_prefix: str, intents: discord.Intents):
        super().__init__(command_prefix, intents=intents)

    async def setup_hook(self) -> None:
        for cog in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(cog)
            except Exception as e:
                print(f"error   : failed to load {cog}: {e}")
                traceback.print_exc()

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        """Global error handler — prevents any unhandled event error from crashing the bot."""
        print(f"error   : unhandled exception in {event_method}")
        traceback.print_exc()


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

    url = f"http://{VCHandler.HOST}:{VCHandler.PORT}/version"

    def _check() -> bool:
        try:
            r = requests.get(url, timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    if _check():
        return

    # Linux: 既存のDockerコンテナ「voicevox」の起動を試みる
    if platform.system() == "Linux":
        print("info    : VOICEVOXに接続できません。Dockerコンテナの起動を試みます...")
        try:
            result = subprocess.run(
                ["docker", "start", "voicevox"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                print("info    : Dockerコンテナ「voicevox」を起動しました。API待機中...")
                for _ in range(30):
                    time.sleep(1)
                    if _check():
                        print("info    : VOICEVOX API接続OK")
                        return
                print("error   : VOICEVOX APIが30秒以内に応答しませんでした。")
            else:
                print(f"error   : docker start失敗: {result.stderr.strip()}")
                print("          「docker run -d --name voicevox ...」で事前にコンテナを作成してください。")
        except FileNotFoundError:
            print("error   : dockerコマンドが見つかりません。")
        except Exception as e:
            print(f"error   : Docker起動中にエラー: {e}")

    print("error   : VOICEVOX APIに接続できません。")
    print(f"          VOICEVOXエンジンを起動({VCHandler.PORT}ポート)してから再実行してください。")
    exit(1)


def _run_bot_with_retry(config: dict) -> None:
    """Run the bot in a retry loop — if the bot crashes, restart it automatically."""
    attempt = 0

    while attempt < MAX_RESTART_ATTEMPTS:
        try:
            intents = discord.Intents.default()
            intents.message_content = True

            bot = Bot(command_prefix="/", intents=intents)

            if attempt > 0:
                print(f"info    : restart attempt {attempt}/{MAX_RESTART_ATTEMPTS}")

            bot.run(config["bot_token"], log_level=logging.WARNING)

            # bot.run() returned cleanly (e.g. user-initiated shutdown)
            print("info    : bot shut down cleanly")
            break

        except KeyboardInterrupt:
            print("info    : keyboard interrupt, exiting")
            break

        except SystemExit:
            print("info    : system exit, exiting")
            break

        except Exception as e:
            attempt += 1
            cooldown = min(RESTART_COOLDOWN_BASE * (2 ** (attempt - 1)), 120)
            print(f"error   : bot crashed (attempt {attempt}/{MAX_RESTART_ATTEMPTS}): {e}")
            traceback.print_exc()

            if attempt >= MAX_RESTART_ATTEMPTS:
                print("error   : max restart attempts reached, giving up")
                sys.exit(1)

            print(f"info    : restarting in {cooldown}s...")
            time.sleep(cooldown)

    print("info    : bot process finished")


if __name__ == "__main__":
    print(f"system  : {platform.system()}")

    # Linux: libopusのロード
    if platform.system() == "Linux":
        try:
            discord.opus.load_opus("libopus.so.0")
            print("opus    : loaded")
        except Exception as e:
            print(f"warning : opus load failed: {e}")

    _ensure_ffmpeg()
    _ensure_voicevox()

    config = load_json_with_private(CONFIG_PATH, CONFIG_PRIVATE_PATH)

    _run_bot_with_retry(config)
