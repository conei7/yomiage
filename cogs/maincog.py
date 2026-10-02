"""Main cog for the yomiage Discord bot — multi-guild support."""

import asyncio
import collections
import json
import os
import re
import tempfile
import time
import traceback
from typing import Literal, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from core.config import choose_runtime_json_path, load_json_with_private
from core.guild_data import (
    ensure_guild_readings,
    ensure_guild_settings,
    get_guild_readings,
    get_guild_settings,
    legacy_readings_coordinator,
)
from core.json_io import JsonIO
from core.text_processor import process_text, PLACEHOLDER_ATTACHED
from core.voice import VCHandler
from core.voice_members import bot_member_ids, human_member_count
from core.views import SetvoiceView

_RE_CHANNEL = re.compile(r"<#(\d+)>")
_RE_USER = re.compile(r"<@(\d+)>")
_RE_ROLE = re.compile(r"<@&(\d+)>")

CONFIG_PATH = "./config/settings.json"
CONFIG_PRIVATE_PATH = "./config/settings.private.json"
DATA_DIR = "./data"


_admin_ids: list[int] = load_json_with_private(CONFIG_PATH, CONFIG_PRIVATE_PATH).get("admin_users", [])


def _is_admin():
    """manage_guild権限 または admin_usersに含まれるユーザーのみ許可"""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id in _admin_ids:
            return True
        perms = interaction.user.guild_permissions if hasattr(interaction.user, "guild_permissions") else None
        if perms and perms.manage_guild:
            return True
        raise app_commands.MissingPermissions(["manage_guild"])
    return app_commands.check(predicate)


class MainCog(commands.Cog):
    _config = load_json_with_private(CONFIG_PATH, CONFIG_PRIVATE_PATH)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.loop = asyncio.get_running_loop()

        # --- I/O ---
        self.user_data_io = JsonIO(
            choose_runtime_json_path(f"{DATA_DIR}/user_data.json", f"{DATA_DIR}/user_data.private.json")
        )
        self.dictionary_io = JsonIO(
            choose_runtime_json_path(
                f"{DATA_DIR}/dictionary.json", f"{DATA_DIR}/dictionary.private.json"
            )
        )
        self.server_config_io = JsonIO(
            choose_runtime_json_path(f"{DATA_DIR}/server_config.json", f"{DATA_DIR}/server_config.private.json")
        )

        # --- Data ---
        self.config: dict = load_json_with_private(CONFIG_PATH, CONFIG_PRIVATE_PATH)
        self.user_data: dict = self.user_data_io.read()
        self.dictionary: dict = self.dictionary_io.read()

        # --- Settings ---
        self.command_config: dict = self.config["command_config"]
        self.embed_color: int = int(self.config["default_embed_color"].lstrip("#"), 16)
        self.max_message_length: int = self.config["max_message_length"]
        self.default_bot_speed: float = self.config["default_bot_speed"]
        self.default_bot_speaker: int = self.config["default_bot_speaker"]
        self.exceptional_bots: list = self.config.get("exceptional_bots", [])
        self._default_user = {
            "speaker_id": self.config["default_user_speaker"],
            "speed": self.config["default_user_speed"],
            "muted": False,
        }

        # --- Dictionary limits ---
        self.MAX_DICT_ENTRIES = 10_000
        self.MAX_KEY_LEN = 100
        self.MAX_VAL_LEN = 100
        self.MAX_FILE_SIZE = 10 * 1024 * 1024

        # --- Voice (per-guild state managed inside VCHandler) ---
        self.vc_handler = VCHandler()
        self.all_speakers = self.vc_handler.speakers
        self.layered_speakers = self.vc_handler.get_layered_speakers_list(credit=True)

        # --- Per-guild runtime state ---
        self._target_channels: dict[int, int] = {}
        self._connecting: set[int] = set()
        self._initial_ready_complete = False

        # --- Rate limiting: per-user/per-guild timestamps (sliding window) ---
        self._msg_timestamps: dict[tuple[int, int], collections.deque] = {}
        self._guild_msg_timestamps: dict[int, collections.deque] = {}
        self._RATE_LIMIT_WINDOW = 5.0   # seconds
        self._RATE_LIMIT_MAX = 10       # max messages per user/window
        self._GUILD_RATE_LIMIT_MAX = 30
        self._last_rate_limit_cleanup = 0.0

        # Missing files are created, while malformed files fail startup rather
        # than silently disabling blacklist and other protective settings.
        self.server_config_io.create()
        self.server_config_io.read()

    # ====================================================================
    # Helpers
    # ====================================================================

    def _embed(self, **kwargs) -> discord.Embed:
        return discord.Embed(color=self.embed_color, **kwargs)

    async def _respond(
        self,
        interaction: discord.Interaction,
        content: str = "",
        *,
        embed: Optional[discord.Embed] = None,
        files: Optional[list[discord.File]] = None,
        ephemeral: bool = False,
    ) -> None:
        if embed is None:
            embed = self._embed(description=content)
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )
        kwargs: dict = {"embed": embed, "ephemeral": ephemeral}
        if files:
            kwargs["files"] = files
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    def _server_settings(
        self, guild: discord.Guild, *, writable: bool = False
    ) -> tuple[dict, dict]:
        """Load this guild's settings and discard bindings to other guilds."""
        data = self.server_config_io.read()
        settings = (
            ensure_guild_settings(data, guild.id)
            if writable
            else get_guild_settings(data, guild.id)
        )

        valid_channel_ids = {str(channel.id) for channel in guild.channels}
        bindings: dict[str, int] = {}
        for source, target in settings.get("channel_bindings", {}).items():
            try:
                source_id = str(int(source))
                target_id = int(target)
            except (TypeError, ValueError):
                continue
            if source_id in valid_channel_ids and str(target_id) in valid_channel_ids:
                bindings[source_id] = target_id
        settings["channel_bindings"] = bindings
        return data, settings

    def _readings(self, guild_id: int, *, writable: bool = False) -> dict[str, str]:
        # Multiple configured bot accounts share this file.  Refresh under the
        # path lock so every account observes the latest committed dictionary.
        self.dictionary = self.dictionary_io.read()
        return (
            ensure_guild_readings(self.dictionary, guild_id)
            if writable
            else get_guild_readings(self.dictionary, guild_id)
        )

    def _migrate_legacy_dictionary(self, guild_ids: list[int]) -> None:
        """Migrate the legacy dictionary on this account's first ready event."""
        if self.bot.user is None:
            return
        self.dictionary = legacy_readings_coordinator.migrate_account(
            self.dictionary_io,
            self.bot.user.id,
            max(1, int(getattr(self.bot, "account_count", 1))),
            guild_ids,
        )

    # --- User data ---

    def _ensure_user(self, user_id: int) -> None:
        with self.user_data_io.transaction():
            self.user_data = self.user_data_io.read()
            key = str(user_id)
            changed = False
            if key not in self.user_data:
                self.user_data[key] = self._default_user.copy()
                changed = True
            else:
                # Migrate: add missing fields to existing users
                for field, default in self._default_user.items():
                    if field not in self.user_data[key]:
                        self.user_data[key][field] = default
                        changed = True
            if changed:
                self.user_data_io.write(self.user_data)

    def _set_user_data(
        self, user_id: int, *, speaker_id: Optional[int] = None, speed: Optional[float] = None
    ) -> bool:
        with self.user_data_io.transaction():
            self._ensure_user(user_id)
            key = str(user_id)
            data = self.user_data[key]
            changed = False
            if speaker_id is not None and data["speaker_id"] != speaker_id:
                data["speaker_id"] = speaker_id
                changed = True
            if speed is not None and data["speed"] != speed:
                data["speed"] = speed
                changed = True
            if changed:
                self.user_data[key] = data
                self.user_data_io.write(self.user_data)
            return changed

    def _speaker_info(self, user_id: int) -> tuple[int, str, str]:
        sid = self.user_data[str(user_id)]["speaker_id"]
        name = style = "None"
        for sp in self.all_speakers:
            if sp["id"] == sid:
                parts = sp["name"].split()
                name = parts[0]
                style = parts[1] if len(parts) > 1 else "?"
                break
        return sid, name, style

    async def _send_speaker_info(self, interaction: discord.Interaction) -> None:
        sid, name, style = self._speaker_info(interaction.user.id)
        speed = self.user_data[str(interaction.user.id)]["speed"]
        embed = self._embed(
            title="読み上げ設定を適用しました",
            description=(
                f"**{self.vc_handler.add_credit(name)}({style})** id:{sid}\n"
                f"読み上げ速度: {speed}"
            ),
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(embed=embed, ephemeral=True)

    # --- Mention resolution ---

    async def _resolve_mentions(self, text: str, guild: discord.Guild) -> str:
        for pattern, resolver in [
            (_RE_CHANNEL, lambda g, i: getattr(g.get_channel(i), "name", None)),
            (_RE_USER, lambda g, i: getattr(g.get_member(i), "display_name", None)),
            (_RE_ROLE, lambda g, i: getattr(g.get_role(i), "name", None)),
        ]:
            for match in list(pattern.finditer(text)):
                raw = match.group(1)
                if not raw.isdigit():
                    continue
                resolved = resolver(guild, int(raw))
                if resolved:
                    text = text.replace(match.group(0), resolved, 1)
        return text

    async def _prepare_speech(self, message: discord.Message) -> str:
        text = message.content.replace("\n", " ")
        if message.attachments:
            text = PLACEHOLDER_ATTACHED + text
        if message.guild:
            text = await self._resolve_mentions(text, message.guild)
        return process_text(
            text,
            readings=self._readings(message.guild.id) if message.guild else {},
            max_length=self.max_message_length,
        )

    # --- Dictionary import ---

    def _import_dictionary(
        self, imported: dict, guild_id: int, replace: bool = False
    ) -> dict:
        result = {"success": False, "message": ""}
        readings = imported.get("readings")
        if not isinstance(readings, dict):
            result["message"] = '不正な辞書: "readings"キーが存在しないか辞書型ではありません。'
            return result
        if len(readings) > self.MAX_DICT_ENTRIES:
            result["message"] = f"エントリ数超過（最大{self.MAX_DICT_ENTRIES}件）。"
            return result

        valid = {
            k: v for k, v in readings.items()
            if isinstance(k, str) and isinstance(v, str)
            and 0 < len(k.strip()) <= self.MAX_KEY_LEN
            and 0 < len(v.strip()) <= self.MAX_VAL_LEN
        }
        with self.dictionary_io.transaction():
            current = self._readings(guild_id, writable=True)
            new_total = len(valid) if replace else len(current) + len(valid)
            if new_total > self.MAX_DICT_ENTRIES:
                result["message"] = (
                    f"エントリ数制限超過（{new_total}/{self.MAX_DICT_ENTRIES}件）。"
                )
                return result

            if replace:
                current.clear()
                current.update(valid)
                result["message"] = f"辞書を置き換えました（{len(valid)}件）。"
            else:
                before = len(current)
                current.update(valid)
                result["message"] = (
                    f"辞書を追記しました（{len(current) - before}件追加）。"
                )

            self.dictionary_io.write(self.dictionary)
        result["success"] = True
        return result

    # ====================================================================
    # Events
    # ====================================================================

    async def _resolve_connection_collision(
        self,
        guild_id: int,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> Literal["none", "yielded", "failed"]:
        """Keep one bot when multiple bots connect to the same VC concurrently.

        The smallest Discord bot user ID wins so every reading bot independently
        reaches the same decision, even across processes or connection methods.
        """
        bot_id = getattr(self.bot.user, "id", None)
        ids = bot_member_ids(channel)
        if (
            bot_id is None
            or bot_id not in ids
            or len(ids) < 2
            or bot_id == ids[0]
        ):
            return "none"

        print(
            f"info    : auto-join collision in VC {channel.id}; "
            f"bot {bot_id} yields to bot {ids[0]}"
        )
        if await self.vc_handler.disconnect(guild_id):
            self._target_channels.pop(guild_id, None)
            return "yielded"
        # Quarantine the still-connected loser so two bots do not continue
        # reading the same text while a user retries the disconnect.
        self._target_channels.pop(guild_id, None)
        print(f"warning : could not yield voice connection in guild {guild_id}")
        return "failed"

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.bot.user is None:
            print("error   : bot user is None")
            return

        # Snapshot startup leftovers before the first await.  Voice-state
        # listeners run concurrently with on_ready; taking this snapshot after
        # command sync could mistake a newly auto-joined client for stale.
        startup_voice_clients: list[discord.VoiceClient] = []
        if not self._initial_ready_complete:
            self._initial_ready_complete = True
            startup_voice_clients = list(self.bot.voice_clients)

        for vc in startup_voice_clients:
            try:
                print(f"info    : disconnecting stale VC in {getattr(vc.channel, 'name', '?')}")
                if await self.vc_handler.disconnect(vc.guild.id, [vc]):
                    self._target_channels.pop(vc.guild.id, None)
            except Exception as e:
                print(f"error   : {e}")

        self._migrate_legacy_dictionary([guild.id for guild in self.bot.guilds])

        for guild in self.bot.guilds:
            try:
                self.bot.tree.copy_global_to(guild=guild)
                await self.bot.tree.sync(guild=guild)
            except Exception as e:
                print(f"error   : failed to sync to {guild.name}: {e}")

        print(f"client  : {self.bot.user.name}")
        print(f"cli id  : {self.bot.user.id}")
        print(f"guilds  : {len(self.bot.guilds)}")
        account_label = getattr(self.bot, "account_label", "unknown")
        for guild in self.bot.guilds:
            _, settings = self._server_settings(guild)
            print(
                f"voice   [{account_label}/{guild.id}]: "
                f"auto_join={settings['auto_join_vc']}, "
                f"bindings={settings['channel_bindings']}"
            )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=guild)
            print(f"info    : synced commands to new guild: {guild.name}")
        except Exception as e:
            print(f"error   : failed to sync to new guild {guild.name}: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            if isinstance(message.channel, discord.DMChannel):
                return
            if message.guild is None:
                return
            gid = message.guild.id
            if gid not in self._target_channels or message.channel.id != self._target_channels[gid]:
                return
            if message.author.bot and message.author.id not in self.exceptional_bots:
                return

            # Command-like messages are intentionally ignored before rate limiting.
            if message.content.startswith("."):
                return

            # Cheap ingress limit before any synchronous JSON I/O.  Muted or
            # blacklisted users only consume their own bucket, never the guild's.
            now = time.monotonic()
            rate_key = (gid, message.author.id)
            ts_deque = self._msg_timestamps.setdefault(rate_key, collections.deque())
            while ts_deque and now - ts_deque[0] > self._RATE_LIMIT_WINDOW:
                ts_deque.popleft()
            if len(ts_deque) >= self._RATE_LIMIT_MAX:
                return
            ts_deque.append(now)

            if now - self._last_rate_limit_cleanup >= 60:
                cutoff = now - self._RATE_LIMIT_WINDOW
                for key, timestamps in list(self._msg_timestamps.items()):
                    while timestamps and timestamps[0] < cutoff:
                        timestamps.popleft()
                    if not timestamps:
                        del self._msg_timestamps[key]
                self._last_rate_limit_cleanup = now

            sc = self.server_config_io.read()
            blacklist = sc.get("blacklist", {}).get(str(gid), [])
            if message.author.id in blacklist:
                return

            self._ensure_user(message.author.id)
            uid = str(message.author.id)
            if self.user_data[uid].get("muted", False):
                return

            # Keep an aggregate ceiling as a second line of defence against
            # multi-account bursts without letting ignored users consume it.
            guild_deque = self._guild_msg_timestamps.setdefault(gid, collections.deque())
            while guild_deque and now - guild_deque[0] > self._RATE_LIMIT_WINDOW:
                guild_deque.popleft()
            if len(guild_deque) >= self._GUILD_RATE_LIMIT_MAX:
                return
            guild_deque.append(now)

            text = await self._prepare_speech(message)
            if not text:
                return

            await self.vc_handler.speak(
                gid, text, self.user_data[uid]["speaker_id"], self.user_data[uid]["speed"]
            )
        except Exception as e:
            print(f"error   : on_message handler failed: {e}")
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        try:
            before_ch = before.channel if before else None
            after_ch = after.channel if after else None
            if before_ch == after_ch:
                return

            gid = member.guild.id
            bot_id = getattr(self.bot.user, "id", None)

            # A second reading bot may win the pre-connect race. Reconcile the
            # collision deterministically when its voice-state event arrives.
            if member.bot:
                if (
                    member.id == bot_id
                    and before_ch is not None
                    and after_ch is None
                ):
                    # Discord can drop or externally disconnect this bot
                    # without going through VCHandler.disconnect().
                    if await self.vc_handler.disconnect(gid):
                        self._target_channels.pop(gid, None)
                    self._connecting.discard(gid)
                    return
                if (
                    after_ch is not None
                    and bot_id is not None
                    and bot_id in bot_member_ids(after_ch)
                ):
                    await self._resolve_connection_collision(gid, after_ch)
                return

            if gid in self._connecting:
                return

            # --- Auto-join ---
            # Count only people. Other reading bots may already be in the same VC.
            auto_joined = False
            if after_ch is not None:
                _, settings = self._server_settings(member.guild)
                bindings = settings["channel_bindings"]
                vc_key = str(after_ch.id)

                if (
                    settings["auto_join_vc"]
                    and human_member_count(after_ch) >= 1
                    and not bot_member_ids(after_ch)
                    and vc_key in bindings
                ):
                    already = any(
                        isinstance(vc, discord.VoiceClient) and vc.guild.id == gid
                        for vc in self.bot.voice_clients
                    )
                    if not already:
                        self._connecting.add(gid)
                        voice_client = None
                        try:
                            voice_client = await self.vc_handler.connect(gid, after_ch)
                        except Exception as e:
                            print(f"error   : auto-join failed: {e}")
                        finally:
                            # Once connect() returns, the presence of the
                            # VoiceClient itself prevents duplicate auto-joins.
                            # Do not suppress leave events during collision wait.
                            self._connecting.discard(gid)

                        if voice_client is not None:
                            auto_joined = True
                            target_channel_id = bindings[vc_key]
                            self._target_channels[gid] = target_channel_id
                            # Allow Discord's member cache to receive any
                            # concurrent bot connection before announcing.
                            await asyncio.sleep(0.75)

                            current_client = self.vc_handler.get_voice_client(gid)
                            still_current = (
                                current_client is voice_client
                                and voice_client.is_connected()
                                and voice_client.channel == after_ch
                            )
                            if not still_current:
                                if current_client is voice_client:
                                    await self.vc_handler.disconnect(gid)
                                    if self._target_channels.get(gid) == target_channel_id:
                                        self._target_channels.pop(gid, None)
                                elif (
                                    current_client is None
                                    and self._target_channels.get(gid) == target_channel_id
                                ):
                                    self._target_channels.pop(gid, None)
                                auto_joined = False
                            elif human_member_count(after_ch) == 0:
                                # The join event that started this connection
                                # may have been followed by a leave while
                                # channel.connect() was still pending.
                                await self.vc_handler.disconnect(gid)
                                if self._target_channels.get(gid) == target_channel_id:
                                    self._target_channels.pop(gid, None)
                                auto_joined = False
                            else:
                                collision = await self._resolve_connection_collision(gid, after_ch)
                                if collision != "none":
                                    # Suppress both the success embed and join
                                    # announcement when yielding failed.
                                    auto_joined = True
                                else:
                                    text_ch = self.bot.get_channel(
                                        self._target_channels.get(gid, 0)
                                    )
                                    if text_ch and hasattr(text_ch, "send"):
                                        embed = self._embed(
                                            title=f"<#{after_ch.id}>に自動で接続しました",
                                            description=(
                                                f"{self.config['vc_embed_description']}\n\n"
                                                f"<#{target_channel_id}>とバインドされています。"
                                            ),
                                        )
                                        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
                                        await text_ch.send(embed=embed)

            # --- Join announcement ---
            if not auto_joined and after_ch is not None and bot_id is not None:
                if bot_id in [m.id for m in after_ch.members]:
                    _, settings = self._server_settings(member.guild)
                    if settings["announce_join_leave"]:
                        text = process_text(
                            f"{member.display_name}さんが入室しました",
                            readings=self._readings(gid),
                        )
                        await self.vc_handler.speak(gid, text, self.default_bot_speaker, self.default_bot_speed)

            # --- Auto-disconnect / leave announcement ---
            if before_ch is not None and bot_id is not None:
                if bot_id in [m.id for m in before_ch.members]:
                    # Disconnect when no people remain, even if other bots do.
                    if human_member_count(before_ch) == 0:
                        if await self.vc_handler.disconnect(gid):
                            self._target_channels.pop(gid, None)
                    else:
                        _, settings = self._server_settings(member.guild)
                        if settings["announce_join_leave"]:
                            text = process_text(
                                f"{member.display_name}さんが退出しました",
                                readings=self._readings(gid),
                            )
                            await self.vc_handler.speak(gid, text, self.default_bot_speaker, self.default_bot_speed)
        except Exception as e:
            print(f"error   : on_voice_state_update failed: {e}")
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        embed = self._embed(title="エラーが発生しました")
        embed.color = discord.Color.red()
        if isinstance(error, app_commands.CommandOnCooldown):
            m, s = divmod(int(error.retry_after), 60)
            embed.description = f"クールダウン中です。{f'{m}分{s}秒' if m else f'{s}秒'}後に再実行してください。"
        elif isinstance(error, (app_commands.MissingPermissions, app_commands.BotMissingPermissions)):
            embed.description = "必要な権限がありません。"
        else:
            embed.description = "予期しないエラーが発生しました。"
            print(f"error   : {type(error).__name__}: {error}")
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        try:
            await self._respond(interaction, embed=embed, ephemeral=True)
        except Exception:
            pass

    # ====================================================================
    # Commands
    # ====================================================================

    @app_commands.command(description=_config["command_config"]["zunda"]["explanation"])
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def zunda(self, interaction: discord.Interaction) -> None:
        ver = self.config.get("version", "不明")
        engine = await asyncio.to_thread(self.vc_handler.get_version) or "不明"
        await self._respond(
            interaction,
            f"**mon**\nbot version: {ver}\nvoicevox: {engine}\nlatency: {self.bot.latency:.2f}s",
            ephemeral=True,
        )

    @app_commands.command(description=_config["command_config"]["help"]["explanation"])
    async def help(self, interaction: discord.Interaction) -> None:
        embed = self._embed(title="使えるコマンドの一覧")
        for name, info in self.command_config.items():
            embed.add_field(name=name, value=info["explanation"], inline=False)
        await self._respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(description=_config["command_config"]["skip"]["explanation"])
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def skip(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if isinstance(guild, discord.Guild) and isinstance(guild.voice_client, discord.VoiceClient):
            user_voice = getattr(interaction.user, "voice", None)
            if getattr(user_voice, "channel", None) != guild.voice_client.channel:
                await self._respond(
                    interaction,
                    "ボットと同じボイスチャンネルに参加してください。",
                    ephemeral=True,
                )
                return
            # キューもクリアして完全にスキップ
            self.vc_handler.clear_queue(guild.id)
            guild.voice_client.stop()
            await self._respond(interaction, "スキップしました。")
        else:
            await self._respond(interaction, "ボイスチャンネルに接続されていません。", ephemeral=True)

    @app_commands.command(description=_config["command_config"]["setspeed"]["explanation"])
    @app_commands.describe(speed="読み上げスピード(初期値:1.0, 範囲:0.5〜2.0)")
    async def setspeed(self, interaction: discord.Interaction, speed: float = 1.0) -> None:
        if not (0.5 <= speed <= 2.0):
            await self._respond(interaction, "速度は0.5〜2.0の範囲で指定してください。", ephemeral=True)
            return
        if self._set_user_data(interaction.user.id, speed=speed):
            await self._respond(interaction, f"読み上げスピードを「{speed}」に設定しました。", ephemeral=True)
        else:
            await self._respond(interaction, f"読み上げスピードは「{speed}」にすでに設定されています。", ephemeral=True)

    @app_commands.command(description=_config["command_config"]["setvoice"]["explanation"])
    @app_commands.describe(id="指定しない場合選択画面が表示されます")
    async def setvoice(self, interaction: discord.Interaction, id: Optional[int] = None) -> None:
        if id is None:
            view = SetvoiceView(self.layered_speakers, self._on_setvoice)
            await interaction.response.send_message(
                content="話者を選択した後、スタイルを選択してください。", view=view, ephemeral=True
            )
        else:
            if id not in [s["id"] for s in self.all_speakers]:
                await self._respond(interaction, f"ID「{id}」に対応する話者が存在しません。", ephemeral=True)
            else:
                self._set_user_data(interaction.user.id, speaker_id=id)
                await self._send_speaker_info(interaction)

    async def _on_setvoice(self, interaction: discord.Interaction, speaker_id: int) -> None:
        self._set_user_data(interaction.user.id, speaker_id=speaker_id)
        await self._send_speaker_info(interaction)

    @app_commands.command(description=_config["command_config"]["show_all_speakers"]["explanation"])
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def show_all_speakers(self, interaction: discord.Interaction) -> None:
        all_speakers = await asyncio.to_thread(self.vc_handler.get_speakers)
        self.vc_handler.speakers = all_speakers
        self.all_speakers = all_speakers
        self.layered_speakers = self.vc_handler.get_layered_speakers_list(credit=True)
        lines = []
        for sp in all_speakers:
            parts = sp["name"].split()
            name = self.vc_handler.add_credit(parts[0])
            style = parts[1] if len(parts) > 1 else "?"
            lines.append(f"**{name}({style})** id:{sp['id']}")
        await self._respond(interaction, embed=self._embed(title="話者一覧", description="\n".join(lines)), ephemeral=True)

    @app_commands.command(description=_config["command_config"]["vc"]["explanation"])
    async def vc(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not isinstance(guild, discord.Guild):
            return
        gid = guild.id
        user = interaction.user
        user_voice = getattr(user, "voice", None)
        user_channel = user_voice.channel if user_voice else None

        if gid in self._connecting:
            await self._respond(
                interaction,
                "ボイスチャンネルへの接続・切断処理中です。",
                ephemeral=True,
            )
            return

        guild_voice_client = guild.voice_client
        if (
            isinstance(guild_voice_client, discord.VoiceClient)
            and not guild_voice_client.is_connected()
        ):
            # Force-clean an abnormal/stale Discord voice cache, then continue
            # this same command into the normal connection path.
            self._connecting.add(gid)
            try:
                stale_cleaned = await self.vc_handler.disconnect(
                    gid, [guild_voice_client]
                )
            finally:
                self._connecting.discard(gid)
            if not stale_cleaned:
                await self._respond(
                    interaction,
                    "古いボイス接続を解放できませんでした。",
                    ephemeral=True,
                )
                return
            self._target_channels.pop(gid, None)
            guild_voice_client = None

        if guild_voice_client:
            if user_channel is None:
                await self._respond(interaction, embed=self._embed(title="エラー", description="ボイスチャンネルに参加してからコマンドを実行してください。"), ephemeral=True)
                return
            if isinstance(guild_voice_client, discord.VoiceClient):
                if guild_voice_client.channel != user_channel:
                    await self._respond(interaction, embed=self._embed(title="エラー", description=f"ボットはすでに<#{guild_voice_client.channel.id}>に接続されています。"), ephemeral=True)
                    return
                self._connecting.add(gid)
                try:
                    disconnected = await self.vc_handler.disconnect(
                        gid, [guild_voice_client]
                    )
                finally:
                    self._connecting.discard(gid)
                if not disconnected:
                    await self._respond(
                        interaction,
                        embed=self._embed(
                            title="エラー",
                            description="ボイスチャンネルから切断できませんでした。",
                        ),
                        ephemeral=True,
                    )
                    return
            self._target_channels.pop(gid, None)
            await self._respond(interaction, embed=self._embed(title="切断しました"))
            return

        if user_channel is None:
            await self._respond(interaction, embed=self._embed(title="エラー", description="ボイスチャンネルに参加してからコマンドを実行してください。"), ephemeral=True)
            return

        existing_bot_ids = bot_member_ids(user_channel)
        if existing_bot_ids:
            await self._respond(
                interaction,
                embed=self._embed(
                    title="接続できません",
                    description="このボイスチャンネルには、すでに別のBotが接続されています。",
                ),
                ephemeral=True,
            )
            return

        # バインド設定があればそちらを優先、なければコマンド実行チャンネル
        _, settings = self._server_settings(guild)
        bindings = settings["channel_bindings"]
        vc_key = str(user_channel.id)
        target_channel_id = bindings.get(vc_key, interaction.channel_id or 0)

        voice_client = None
        self._connecting.add(gid)
        try:
            await interaction.response.defer()
            voice_client = await self.vc_handler.connect(gid, user_channel)
        finally:
            self._connecting.discard(gid)
        if voice_client is None:
            await interaction.followup.send(
                embed=self._embed(title="エラー", description="ボイスチャンネルへの接続に失敗しました。"),
                ephemeral=True,
            )
            return

        # Publish the text-channel binding only after the voice connection is
        # committed.  Concurrent commands cannot overwrite an in-flight one.
        self._target_channels[gid] = target_channel_id

        # Resolve simultaneous connections from different bot accounts.
        await asyncio.sleep(0.75)
        current_client = self.vc_handler.get_voice_client(gid)
        connection_valid = (
            current_client is voice_client
            and voice_client.is_connected()
            and voice_client.channel == user_channel
            and human_member_count(user_channel) > 0
        )
        if not connection_valid:
            if current_client is voice_client:
                disconnected = await self.vc_handler.disconnect(gid)
                if self._target_channels.get(gid) == target_channel_id:
                    self._target_channels.pop(gid, None)
                if not disconnected:
                    await interaction.followup.send(
                        embed=self._embed(
                            title="エラー",
                            description="接続状態が変わり、後処理にも失敗しました。もう一度切断してください。",
                        ),
                        ephemeral=True,
                    )
                    return
            elif current_client is None and self._target_channels.get(gid) == target_channel_id:
                self._target_channels.pop(gid, None)
            await interaction.followup.send(
                embed=self._embed(
                    title="接続を中止しました",
                    description="接続中にボイスチャンネルの状態が変わりました。",
                ),
                ephemeral=True,
            )
            return

        collision = await self._resolve_connection_collision(gid, user_channel)
        if collision != "none":
            description = (
                "別のBotが同時に接続したため、このBotは切断しました。"
                if collision == "yielded"
                else "別のBotとの接続競合を解消できませんでした。もう一度切断してください。"
            )
            await interaction.followup.send(
                embed=self._embed(title="接続を中止しました", description=description),
                ephemeral=True,
            )
            return
        desc = self.config["vc_embed_description"]
        if vc_key in bindings:
            desc += f"\n\n<#{target_channel_id}>とバインドされています。"
        embed = self._embed(title=f"<#{user_channel.id}>に接続しました", description=desc)
        await interaction.followup.send(embed=embed)
        await self.vc_handler.speak(gid, "接続しました", self.default_bot_speaker, self.default_bot_speed)

    @app_commands.command(description=_config["command_config"]["add_dict"]["explanation"])
    @app_commands.describe(word="単語", reading="読み")
    @_is_admin()
    @app_commands.checks.cooldown(5, 60, key=lambda i: (i.guild_id, i.user.id))
    async def add_dict(self, interaction: discord.Interaction, word: str, reading: str) -> None:
        if not (0 < len(word.strip()) <= self.MAX_KEY_LEN):
            await self._respond(interaction, f"単語は1〜{self.MAX_KEY_LEN}文字で指定してください。", ephemeral=True)
            return
        if not (0 < len(reading.strip()) <= self.MAX_VAL_LEN):
            await self._respond(interaction, f"読みは1〜{self.MAX_VAL_LEN}文字で指定してください。", ephemeral=True)
            return
        if interaction.guild_id is None:
            await self._respond(interaction, "サーバー内で実行してください。", ephemeral=True)
            return
        at_capacity = False
        with self.dictionary_io.transaction():
            readings = self._readings(interaction.guild_id, writable=True)
            if len(readings) >= self.MAX_DICT_ENTRIES and word not in readings:
                at_capacity = True
            else:
                readings[word] = reading
                self.dictionary_io.write(self.dictionary)
        if at_capacity:
            await self._respond(interaction, f"辞書の登録上限（{self.MAX_DICT_ENTRIES}件）に達しています。", ephemeral=True)
            return
        embed = self._embed(title="単語を登録しました")
        embed.add_field(name="単語", value=word)
        embed.add_field(name="読み", value=reading)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description=_config["command_config"]["del_dict"]["explanation"])
    @app_commands.describe(word="単語")
    @_is_admin()
    @app_commands.checks.cooldown(5, 60, key=lambda i: (i.guild_id, i.user.id))
    async def del_dict(self, interaction: discord.Interaction, word: str) -> None:
        if interaction.guild_id is None:
            await self._respond(interaction, "サーバー内で実行してください。", ephemeral=True)
            return
        reading = None
        with self.dictionary_io.transaction():
            readings = self._readings(interaction.guild_id, writable=True)
            if word in readings:
                reading = readings.pop(word)
                self.dictionary_io.write(self.dictionary)
        if reading is None:
            embed = self._embed(title="単語が存在しません")
            embed.add_field(name="単語", value=word)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = self._embed(title="単語を削除しました")
        embed.add_field(name="単語", value=word)
        embed.add_field(name="読み", value=reading)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="入室退室の読み上げON/OFFを設定します")
    @app_commands.describe(enable="TrueでON、FalseでOFF")
    @_is_admin()
    async def toggle_announce(self, interaction: discord.Interaction, enable: bool) -> None:
        if not isinstance(interaction.guild, discord.Guild):
            return
        with self.server_config_io.transaction():
            data, settings = self._server_settings(interaction.guild, writable=True)
            settings["announce_join_leave"] = enable
            self.server_config_io.write(data)
        await self._respond(interaction, f"入室・退室の読み上げ機能を「{'ON' if enable else 'OFF'}」に設定しました。", ephemeral=True)

    @app_commands.command(description="ボイスチャンネル自動参加のON/OFFを設定します")
    @_is_admin()
    async def toggle_auto_join(self, interaction: discord.Interaction, enable: bool) -> None:
        if not isinstance(interaction.guild, discord.Guild):
            return
        with self.server_config_io.transaction():
            data, settings = self._server_settings(interaction.guild, writable=True)
            settings["auto_join_vc"] = enable
            self.server_config_io.write(data)
        await self._respond(interaction, f"自動参加機能を「{'ON' if enable else 'OFF'}」に設定しました。", ephemeral=True)

    @app_commands.command(description="辞書データをインポートします")
    @app_commands.describe(file="インポートするjsonファイル", replace="Trueで置き換え、Falseで追記")
    @_is_admin()
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def import_dict(self, interaction: discord.Interaction, file: discord.Attachment, replace: bool = False) -> None:
        if file.size > self.MAX_FILE_SIZE:
            await self._respond(interaction, "ファイルサイズが大きすぎます。", ephemeral=True)
            return
        if not file.filename.lower().endswith(".json"):
            await self._respond(interaction, "jsonファイルを添付してください。", ephemeral=True)
            return
        if interaction.guild_id is None:
            await self._respond(interaction, "サーバー内で実行してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        backup_path = None
        try:
            data = await file.read()
            if len(data) > self.MAX_FILE_SIZE:
                await self._respond(interaction, "ファイルサイズが大きすぎます。", ephemeral=True)
                return
            imported = json.loads(data.decode("utf-8"))

            # Capture the exact pre-change state in the same transaction as
            # the import, so another bot cannot make the backup stale.
            with self.dictionary_io.transaction():
                backup_readings = dict(self._readings(interaction.guild_id))
                status = self._import_dictionary(imported, interaction.guild_id, replace)

            with tempfile.NamedTemporaryFile(
                "w", delete=False, encoding="utf-8", suffix=".json"
            ) as tmp:
                json.dump(
                    {"readings": backup_readings},
                    tmp,
                    ensure_ascii=False,
                    indent=2,
                )
                backup_path = tmp.name
            files_list = [discord.File(backup_path, filename="dictionary_backup.json")] if backup_path else None
            await self._respond(interaction, status["message"] + ("\n（変更前の辞書を添付）" if backup_path else ""), files=files_list)
        except Exception as e:
            await self._respond(
                interaction,
                f"ファイル読み込み失敗（{type(e).__name__}）。",
                ephemeral=True,
            )
        finally:
            if backup_path and os.path.exists(backup_path):
                os.remove(backup_path)

    @app_commands.command(description="辞書データをエクスポートします")
    @_is_admin()
    async def export_dict(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await self._respond(interaction, "サーバー内で実行してください。", ephemeral=True)
            return
        self.dictionary = self.dictionary_io.read()
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as tmp:
            json.dump(
                {"readings": dict(self._readings(interaction.guild_id))},
                tmp,
                ensure_ascii=False,
                indent=4,
            )
            path = tmp.name

        try:
            await self._respond(
                interaction,
                "辞書データを送信します。",
                files=[discord.File(path, filename="dictionary.json")],
                ephemeral=True
            )
        finally:
            if os.path.exists(path):
                os.remove(path)

    @app_commands.command(description="ボイスチャンネルとテキストチャンネルをバインドします")
    @app_commands.describe(voice_channel="バインド元のVC", text_channel="バインド先のテキストチャンネル")
    @_is_admin()
    async def bind(self, interaction: discord.Interaction, voice_channel: discord.VoiceChannel, text_channel: Union[discord.TextChannel, discord.VoiceChannel]) -> None:
        if not isinstance(interaction.guild, discord.Guild):
            return
        with self.server_config_io.transaction():
            data, settings = self._server_settings(interaction.guild, writable=True)
            bindings = settings["channel_bindings"]
            bindings[str(voice_channel.id)] = text_channel.id
            self.server_config_io.write(data)
        await self._respond(interaction, f"<#{voice_channel.id}>と<#{text_channel.id}>をバインドしました。", ephemeral=True)

    @app_commands.command(description="ボイスチャンネルのバインドを解除します")
    @app_commands.describe(voice_channel="解除するVC")
    @_is_admin()
    async def unbind(self, interaction: discord.Interaction, voice_channel: discord.VoiceChannel) -> None:
        if not isinstance(interaction.guild, discord.Guild):
            return
        with self.server_config_io.transaction():
            data, settings = self._server_settings(interaction.guild, writable=True)
            bindings = settings["channel_bindings"]
            vc_key = str(voice_channel.id)
            if vc_key in bindings:
                del bindings[vc_key]
                self.server_config_io.write(data)
                msg = f"<#{voice_channel.id}>のバインドを解除しました。"
            else:
                msg = f"<#{voice_channel.id}>はバインドされていません。"
        await self._respond(interaction, msg, ephemeral=True)

    @app_commands.command(description=_config["command_config"]["mute"]["explanation"])
    async def mute(self, interaction: discord.Interaction) -> None:
        with self.user_data_io.transaction():
            self._ensure_user(interaction.user.id)
            key = str(interaction.user.id)
            current = self.user_data[key].get("muted", False)
            self.user_data[key]["muted"] = not current
            self.user_data_io.write(self.user_data)
        status = "OFF（読み上げません）" if not current else "ON（読み上げます）"
        await self._respond(interaction, f"読み上げを{status}に設定しました。", ephemeral=True)

    @app_commands.command(description=_config["command_config"]["mysettings"]["explanation"])
    async def mysettings(self, interaction: discord.Interaction) -> None:
        self.user_data = self.user_data_io.read()
        self._ensure_user(interaction.user.id)
        uid = str(interaction.user.id)
        data = self.user_data[uid]
        sid, name, style = self._speaker_info(interaction.user.id)
        muted = "OFF（読み上げない）" if data.get("muted", False) else "ON（読み上げる）"
        embed = self._embed(
            title="あなたの読み上げ設定",
            description=(
                f"**話者:** {self.vc_handler.add_credit(name)}({style}) id:{sid}\n"
                f"**速度:** {data['speed']}\n"
                f"**読み上げ:** {muted}\n\n"
                f"*メッセージ先頭に「.」で1回だけスキップできます*"
            ),
        )
        await self._respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(description=_config["command_config"]["show_dict"]["explanation"])
    async def show_dict(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await self._respond(interaction, "サーバー内で実行してください。", ephemeral=True)
            return
        self.dictionary = self.dictionary_io.read()
        readings = self._readings(interaction.guild_id)
        if not readings:
            await self._respond(interaction, "辞書にエントリがありません。", ephemeral=True)
            return
        lines: list[str] = []
        description_length = 0
        for key, value in readings.items():
            line = f"**{key}** → {value}"
            if len(lines) >= 50 or description_length + len(line) + 1 > 3_800:
                break
            lines.append(line)
            description_length += len(line) + 1
        desc = "\n".join(lines)
        if len(readings) > len(lines):
            desc += f"\n\n…他{len(readings) - len(lines)}件"
        embed = self._embed(title=f"辞書一覧（{len(readings)}件）", description=desc)
        await self._respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(description=_config["command_config"]["show_bindings"]["explanation"])
    @_is_admin()
    async def show_bindings(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.guild, discord.Guild):
            return
        _, settings = self._server_settings(interaction.guild)
        bindings = settings["channel_bindings"]
        lines = [f"<#{vc_id}> → <#{tc_id}>" for vc_id, tc_id in bindings.items()]
        if not lines:
            lines.append("（バインドなし）")

        auto = "ON" if settings["auto_join_vc"] else "OFF"
        gid = interaction.guild_id
        voice_client = self.vc_handler.get_voice_client(gid) if gid is not None else None
        if voice_client is not None and voice_client.is_connected():
            connection = f"<#{voice_client.channel.id}>"
        else:
            connection = "未接続"
        target_id = self._target_channels.get(gid) if gid is not None else None
        target = f"<#{target_id}>" if target_id else "なし"
        account_label = getattr(self.bot, "account_label", "不明")
        bot_name = self.bot.user.name if self.bot.user else "不明"

        embed = self._embed(
            title="チャンネルバインド一覧",
            description=(
                "\n".join(lines)
                + f"\n\nBot: **{bot_name}** (`{account_label}`)"
                + f"\n自動参加: **{auto}**"
                + f"\n現在のVC: {connection}"
                + f"\n現在の読み上げ先: {target}"
            ),
        )
        await self._respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(description="自動接続するVCとテキストチャンネルを設定します（既存バインドは置き換え）")
    @app_commands.describe(
        voice_channel="自動接続するVC",
        text_channel="読み上げ対象のテキストチャンネル",
    )
    @_is_admin()
    async def set_auto_channel(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel,
        text_channel: Union[discord.TextChannel, discord.VoiceChannel],
    ) -> None:
        if not isinstance(interaction.guild, discord.Guild):
            return
        with self.server_config_io.transaction():
            data, settings = self._server_settings(interaction.guild, writable=True)
            settings["channel_bindings"] = {str(voice_channel.id): text_channel.id}
            settings["auto_join_vc"] = True
            self.server_config_io.write(data)
        await self._respond(
            interaction,
            f"自動接続を設定しました。\n"
            f"VC: <#{voice_channel.id}>\n"
            f"テキスト: <#{text_channel.id}>\n"
            f"自動参加: **ON**",
            ephemeral=True,
        )

    @app_commands.command(description="指定したユーザーのメッセージを読み上げないようにします（ブラックリスト追加）")
    @app_commands.describe(user="読み上げを無視するユーザー")
    @_is_admin()
    async def blacklist_add(self, interaction: discord.Interaction, user: discord.User) -> None:
        already_present = False
        with self.server_config_io.transaction():
            sc = self.server_config_io.read()
            bl = sc.setdefault("blacklist", {})
            guild_bl = bl.setdefault(str(interaction.guild_id), [])
            if user.id in guild_bl:
                already_present = True
            else:
                guild_bl.append(user.id)
                self.server_config_io.write(sc)
        if already_present:
            await self._respond(interaction, f"{user.display_name}は既にブラックリストに登録されています。", ephemeral=True)
            return
        await self._respond(interaction, f"{user.display_name}のメッセージを読み上げないように設定しました。", ephemeral=True)

    @app_commands.command(description="指定したユーザーのブラックリスト登録を解除します")
    @app_commands.describe(user="ブラックリストから削除するユーザー")
    @_is_admin()
    async def blacklist_remove(self, interaction: discord.Interaction, user: discord.User) -> None:
        was_present = False
        with self.server_config_io.transaction():
            sc = self.server_config_io.read()
            bl = sc.get("blacklist", {})
            guild_bl = bl.get(str(interaction.guild_id), [])
            if user.id in guild_bl:
                was_present = True
                guild_bl.remove(user.id)
                sc["blacklist"][str(interaction.guild_id)] = guild_bl
                self.server_config_io.write(sc)
        if not was_present:
            await self._respond(interaction, f"{user.display_name}はブラックリストに登録されていません。", ephemeral=True)
            return
        await self._respond(interaction, f"{user.display_name}のブラックリスト登録を解除しました。", ephemeral=True)

    @app_commands.command(description="このサーバーのブラックリスト一覧を表示します")
    @_is_admin()
    async def blacklist_show(self, interaction: discord.Interaction) -> None:
        sc = self.server_config_io.read()
        guild_bl = sc.get("blacklist", {}).get(str(interaction.guild_id), [])
        if not guild_bl:
            await self._respond(interaction, "このサーバーにはブラックリストに登録されているユーザーはいません。", ephemeral=True)
            return
        
        mentions = [f"<@{uid}>" for uid in guild_bl]
        embed = self._embed(title="ブラックリスト一覧", description="\n".join(mentions))
        await self._respond(interaction, embed=embed, ephemeral=True)



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MainCog(bot))
