"""Entry point for the yomiage Discord bot."""

import logging
import os
import platform
import subprocess
import sys
import threading
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
    def __init__(
        self,
        command_prefix: str,
        intents: discord.Intents,
        label: str,
        account_index: int = 0,
        account_count: int = 1,
    ):
        super().__init__(command_prefix, intents=intents)
        self.account_label = label
        self.account_index = account_index
        self.account_count = account_count

    async def setup_hook(self) -> None:
        for cog in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(cog)
            except Exception as e:
                print(f"error   : failed to load {cog}: {e}")
                traceback.print_exc()
                raise

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

    if os.environ.get("SBC_MANAGED") == "1":
        print("warning : VOICEVOX APIは停止中です。接続回復後の読み上げを待ちます。")
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


def _run_bot_with_retry(
    token: str,
    label: str,
    account_index: int = 0,
    account_count: int = 1,
) -> bool:
    """Run a single bot instance in a retry loop."""
    attempt = 0

    while attempt < MAX_RESTART_ATTEMPTS:
        try:
            intents = discord.Intents.default()
            intents.message_content = True

            bot = Bot(
                command_prefix="/",
                intents=intents,
                label=label,
                account_index=account_index,
                account_count=account_count,
            )

            if attempt > 0:
                print(f"info    [{label}]: restart attempt {attempt}/{MAX_RESTART_ATTEMPTS}")

            bot.run(token, log_level=logging.WARNING)

            # bot.run() returned cleanly (e.g. user-initiated shutdown)
            print(f"info    [{label}]: bot shut down cleanly")
            print(f"info    [{label}]: bot process finished")
            return True

        except KeyboardInterrupt:
            print(f"info    [{label}]: keyboard interrupt, exiting")
            print(f"info    [{label}]: bot process finished")
            return True

        except SystemExit as error:
            print(f"info    [{label}]: system exit, exiting")
            print(f"info    [{label}]: bot process finished")
            return error.code in (None, 0)

        except Exception as e:
            attempt += 1
            cooldown = min(RESTART_COOLDOWN_BASE * (2 ** (attempt - 1)), 120)
            print(f"error   [{label}]: bot crashed (attempt {attempt}/{MAX_RESTART_ATTEMPTS}): {e}")
            traceback.print_exc()

            if attempt >= MAX_RESTART_ATTEMPTS:
                print(f"error   [{label}]: max restart attempts reached, giving up")
                return False

            print(f"info    [{label}]: restarting in {cooldown}s...")
            time.sleep(cooldown)

    return False


def _collect_accounts(config: dict) -> list[dict]:
    """Return a list of account dicts, each with at minimum a 'bot_token' key.

    Supports two formats in settings.private.json:

    # Single account (legacy):
    { "bot_token": "...", "admin_users": [...] }

    # Multiple accounts:
    {
        "accounts": [
            { "bot_token": "...", "label": "bot1" },
            { "bot_token": "...", "label": "bot2" }
        ],
        "admin_users": [...]
    }

    When using multiple accounts, each entry inherits the top-level config
    and can override any key individually.
    """
    accounts_raw = config.get("accounts")
    if accounts_raw and isinstance(accounts_raw, list):
        accounts = []
        for i, entry in enumerate(accounts_raw):
            merged = config.copy()
            merged.pop("accounts", None)
            merged.update(entry)
            merged.setdefault("label", f"bot{i + 1}")
            accounts.append(merged)
        return accounts

    # Legacy single-token format
    single = config.copy()
    single.setdefault("label", "bot1")
    return [single]


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
    accounts = _collect_accounts(config)
    for account in accounts:
        token = account.get("bot_token")
        if not isinstance(token, str) or not token.strip():
            label = account.get("label", "unknown")
            print(f"error   [{label}]: bot token is missing")
            raise SystemExit(1)

    if len(accounts) == 1:
        # シングルアカウント：そのままメインスレッドで実行
        acc = accounts[0]
        if not _run_bot_with_retry(acc["bot_token"], acc["label"], 0, 1):
            raise SystemExit(1)
    else:
        # マルチアカウント：各アカウントをスレッドで並列実行
        print(f"info    : starting {len(accounts)} bot accounts in parallel")
        threads = []
        results: list[bool | None] = [None] * len(accounts)
        account_failed = threading.Event()

        def run_account(index: int, account: dict) -> None:
            results[index] = _run_bot_with_retry(
                account["bot_token"],
                account["label"],
                index,
                len(accounts),
            )
            if results[index] is not True:
                account_failed.set()

        for index, acc in enumerate(accounts):
            label = acc["label"]
            print(f"info    : launching [{label}]")
            t = threading.Thread(
                target=run_account,
                args=(index, acc),
                name=label,
                daemon=True,
            )
            t.start()
            threads.append(t)

        try:
            while any(t.is_alive() for t in threads):
                if account_failed.wait(timeout=1):
                    raise SystemExit(1)
        except KeyboardInterrupt:
            print("info    : keyboard interrupt, all bots will stop")
        else:
            if any(result is not True for result in results):
                raise SystemExit(1)
