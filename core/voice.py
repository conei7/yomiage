"""Voice connection handler with per-guild state and in-memory streaming."""

import asyncio
import collections
import io
import json
import re
import traceback
from typing import Optional

import discord
import requests


class VoiceSynthesisConnectionError(Exception):
    """音声合成APIへの接続に失敗した場合の例外"""
    pass


class _GuildVoiceState:
    """Per-guild voice connection state."""
    __slots__ = ("voice_client", "play_queue", "synthesis_queue", "synthesis_task",
                 "_dropped_count")

    def __init__(self):
        self.voice_client: Optional[discord.VoiceClient] = None
        # deque for O(1) popleft; maxlen prevents unbounded memory growth
        self.play_queue: collections.deque[bytes] = collections.deque(maxlen=200)
        # Bounded queue — blocks producers when full, providing back-pressure
        self.synthesis_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self.synthesis_task: Optional[asyncio.Task] = None
        self._dropped_count: int = 0


class VCHandler:
    HOST = "127.0.0.1"
    PORT = 50021
    DEFAULT_SPEAKER = 2
    VOICEVOX_CREDIT_PREFIX = "VOICEVOX"
    PREFERRED_SPEAKER_ORDER = ["ずんだもん", "四国めたん", "春日部つむぎ"]
    MAX_QUEUE_SIZE = 100
    REQUEST_TIMEOUT = 10
    # Maximum WAV bytes kept in play_queue before dropping new items
    MAX_PLAY_QUEUE_BYTES = 50 * 1024 * 1024  # 50 MB

    def __init__(self, in_executer: bool = True) -> None:
        self.in_executer = in_executer
        self.loop: asyncio.AbstractEventLoop | None = None

        base = f"http://{self.HOST}:{self.PORT}"
        self.speakers_url = f"{base}/speakers"
        self.audio_query_url = f"{base}/audio_query"
        self.synthesis_url = f"{base}/synthesis"

        self._states: dict[int, _GuildVoiceState] = {}

        self.speakers: list = []
        if self.is_available():
            self.speakers = self.get_speakers()

    def _state(self, guild_id: int) -> _GuildVoiceState:
        if guild_id not in self._states:
            self._states[guild_id] = _GuildVoiceState()
        return self._states[guild_id]

    # ====================================================================
    # API queries
    # ====================================================================

    def get_version(self) -> Optional[str]:
        try:
            r = requests.get(
                f"http://{self.HOST}:{self.PORT}/version", timeout=self.REQUEST_TIMEOUT
            )
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def is_available(self) -> bool:
        return self.get_version() is not None

    def get_speakers(self) -> list:
        try:
            r = requests.get(self.speakers_url, timeout=self.REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise VoiceSynthesisConnectionError(f"Failed to get speakers: {e}") from e

        result = []
        for speaker in data:
            name = speaker["name"]
            for style in speaker["styles"]:
                style["name"] = f"{name} {style['name']}"
                result.append(style)
        return result

    def get_styles(self, name: str) -> list[str]:
        return [
            f"{sp['name'].split()[1]} id:{sp['id']}"
            for sp in self.speakers
            if sp["name"].split()[0] == name
        ]

    def get_layered_speakers_list(self, credit: bool = False) -> list[dict]:
        names = list(dict.fromkeys(sp["name"].split()[0] for sp in self.speakers))
        sorted_names = []
        remaining = list(names)
        for pref in self.PREFERRED_SPEAKER_ORDER:
            if pref in remaining:
                sorted_names.append(pref)
                remaining.remove(pref)
        sorted_names.extend(remaining)

        return [
            {"name": self.add_credit(n) if credit else n, "styles": self.get_styles(n)}
            for n in sorted_names
        ]

    def add_credit(self, name: str) -> str:
        if name.startswith(f"{self.VOICEVOX_CREDIT_PREFIX}:"):
            return name
        return f"{self.VOICEVOX_CREDIT_PREFIX}:{name}"

    # ====================================================================
    # Synthesis & Chunking
    # ====================================================================

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self.loop is None or self.loop.is_closed():
            self.loop = asyncio.get_running_loop()
        return self.loop

    def _chunk_text(self, text: str) -> list[str]:
        pattern = r"([。、！？\.\!\?\n]+)"
        parts = re.split(pattern, text)

        chunks = []
        current_chunk = ""
        for part in parts:
            current_chunk += part
            if re.search(pattern, part) or len(current_chunk) > 40:
                stripped = current_chunk.strip()
                if stripped:
                    chunks.append(stripped)
                current_chunk = ""

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks if chunks else ([text] if text.strip() else [])

    def _play_queue_bytes(self, state: _GuildVoiceState) -> int:
        """Estimate total bytes in the play queue."""
        return sum(len(b) for b in state.play_queue)

    def synthesize(self, text: str, speaker: int = -1, speed: float = 1.0) -> bytes:
        sp = speaker if speaker != -1 else self.DEFAULT_SPEAKER
        try:
            r = requests.post(
                self.audio_query_url,
                params={"text": text, "speaker": sp},
                timeout=self.REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            query = r.json()
        except requests.exceptions.Timeout as e:
            raise VoiceSynthesisConnectionError(f"Audio query timed out: {e}") from e
        except Exception as e:
            raise VoiceSynthesisConnectionError(f"Audio query failed: {e}") from e

        query["speedScale"] = speed
        try:
            r = requests.post(
                self.synthesis_url,
                headers={"Content-Type": "application/json"},
                params={"speaker": sp},
                data=json.dumps(query),
                timeout=self.REQUEST_TIMEOUT * 3,  # synthesis can be slow under load
            )
            r.raise_for_status()
        except requests.exceptions.Timeout as e:
            raise VoiceSynthesisConnectionError(f"Synthesis timed out: {e}") from e
        except Exception as e:
            raise VoiceSynthesisConnectionError(f"Synthesis failed: {e}") from e

        return r.content

    async def _synthesis_worker(self, guild_id: int) -> None:
        """Long-running worker that processes synthesis items for a guild.

        This worker is designed to NEVER crash — every exception is caught
        and logged so the bot keeps running.
        """
        state = self._state(guild_id)
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 10

        while True:
            try:
                # Use wait_for so the worker doesn't hang forever if the
                # guild disconnects while idle.
                try:
                    item = await asyncio.wait_for(state.synthesis_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    # No work for 5 min — check if still connected
                    if state.voice_client is None or not state.voice_client.is_connected():
                        print(f"info    : synthesis worker idle & disconnected, exiting (guild {guild_id})")
                        break
                    continue

                if item is None:
                    state.synthesis_queue.task_done()
                    break

                text, speaker, speed = item
                chunks = self._chunk_text(text)

                for chunk in chunks:
                    # Check connection before each chunk
                    if state.voice_client is None or not state.voice_client.is_connected():
                        break

                    # Back-pressure: wait if play_queue is too large (count or bytes)
                    wait_cycles = 0
                    while (len(state.play_queue) >= self.MAX_QUEUE_SIZE or
                           self._play_queue_bytes(state) >= self.MAX_PLAY_QUEUE_BYTES):
                        if state.voice_client is None or not state.voice_client.is_connected():
                            break
                        await asyncio.sleep(0.5)
                        wait_cycles += 1
                        if wait_cycles > 120:  # 60s max wait per chunk
                            print(f"warning : play queue stuck for 60s, dropping chunk (guild {guild_id})")
                            break

                    try:
                        wav_data = await asyncio.to_thread(self.synthesize, chunk, speaker, speed)
                        state.play_queue.append(wav_data)
                        consecutive_errors = 0  # reset on success

                        if state.voice_client and not state.voice_client.is_playing():
                            self._ensure_loop().call_soon_threadsafe(self._play_next, guild_id)

                    except VoiceSynthesisConnectionError as e:
                        consecutive_errors += 1
                        print(f"warning : synthesis connection error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            print(f"error   : too many consecutive synthesis errors, backing off (guild {guild_id})")
                            await asyncio.sleep(5)
                            consecutive_errors = 0
                    except Exception as e:
                        consecutive_errors += 1
                        print(f"error   : unexpected chunk synthesis error ({consecutive_errors}): {e}")
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            await asyncio.sleep(5)
                            consecutive_errors = 0

                state.synthesis_queue.task_done()

            except asyncio.CancelledError:
                print(f"info    : synthesis worker cancelled (guild {guild_id})")
                break
            except Exception as e:
                # Catch-all: NEVER let the worker die from an unexpected exception
                print(f"error   : synthesis worker unexpected error (guild {guild_id}): {e}")
                traceback.print_exc()
                await asyncio.sleep(1)

    # ====================================================================
    # Playback (per-guild queue)
    # ====================================================================

    def _play_next(self, guild_id: int) -> None:
        """Play the next item in the queue. Fully wrapped in try/except."""
        try:
            state = self._state(guild_id)

            if not state.play_queue:
                return
            if state.voice_client is None or not state.voice_client.is_connected():
                return
            if state.voice_client.is_playing():
                return

            wav_data = state.play_queue.popleft()

            def after(error):
                try:
                    if error:
                        print(f"error   : playback error (guild {guild_id}): {error}")
                    if state.play_queue and state.voice_client and state.voice_client.is_connected():
                        self._ensure_loop().call_soon_threadsafe(self._play_next, guild_id)
                except Exception as e:
                    print(f"error   : after-callback error (guild {guild_id}): {e}")

            try:
                source = discord.FFmpegPCMAudio(io.BytesIO(wav_data), pipe=True)
                state.voice_client.play(source, after=after)
            except Exception as e:
                print(f"error   : play failed (guild {guild_id}): {e}")
                # Try the next item instead of giving up
                if state.play_queue:
                    self._ensure_loop().call_soon_threadsafe(self._play_next, guild_id)

        except Exception as e:
            print(f"error   : _play_next unhandled (guild {guild_id}): {e}")
            traceback.print_exc()

    async def speak(
        self, guild_id: int, text: str, speaker: int = -1, speed: float = 1.0
    ) -> bool:
        state = self._state(guild_id)

        if state.voice_client is None or not state.voice_client.is_connected():
            return False

        loop = self._ensure_loop()

        # Ensure synthesis worker is running — restart if it died
        if state.synthesis_task is None or state.synthesis_task.done():
            # Log if the previous task failed
            if state.synthesis_task is not None and state.synthesis_task.done():
                exc = state.synthesis_task.exception() if not state.synthesis_task.cancelled() else None
                if exc:
                    print(f"warning : restarting synthesis worker (guild {guild_id}), previous died: {exc}")
            state.synthesis_task = loop.create_task(self._synthesis_worker(guild_id))

        # Non-blocking put: if the queue is full, drop the message instead of
        # blocking the event loop (which would freeze the entire bot).
        try:
            state.synthesis_queue.put_nowait((text, speaker, speed))
        except asyncio.QueueFull:
            state._dropped_count += 1
            if state._dropped_count % 10 == 1:
                print(f"warning : synthesis queue full, message dropped "
                      f"(guild {guild_id}, total dropped: {state._dropped_count})")
            return False

        return True

    # ====================================================================
    # Connection management (per-guild)
    # ====================================================================

    def clear_queue(self, guild_id: int) -> None:
        """Clear all pending synthesis and playback items for a guild."""
        state = self._state(guild_id)
        state.play_queue.clear()
        # drain the synthesis queue
        while not state.synthesis_queue.empty():
            try:
                state.synthesis_queue.get_nowait()
                state.synthesis_queue.task_done()
            except asyncio.QueueEmpty:
                break

    def get_voice_client(self, guild_id: int) -> Optional[discord.VoiceClient]:
        return self._state(guild_id).voice_client

    async def connect(
        self,
        guild_id: int,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> Optional[discord.VoiceClient]:
        state = self._state(guild_id)

        try:
            if state.voice_client and state.voice_client.is_connected():
                if state.voice_client.channel == channel:
                    return state.voice_client
                await state.voice_client.move_to(channel)
                return state.voice_client

            state.voice_client = await channel.connect()
            return state.voice_client
        except Exception as e:
            print(f"error   : voice connect failed (guild {guild_id}): {e}")
            traceback.print_exc()
            return None

    async def disconnect(
        self,
        guild_id: int,
        voice_clients: Optional[list[discord.VoiceClient]] = None,
    ) -> None:
        state = self._state(guild_id)

        # Stop synthesis worker
        if state.synthesis_task and not state.synthesis_task.done():
            state.synthesis_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(state.synthesis_task), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            state.synthesis_task = None

        # Drain synthesis queue
        while not state.synthesis_queue.empty():
            try:
                state.synthesis_queue.get_nowait()
                state.synthesis_queue.task_done()
            except asyncio.QueueEmpty:
                break

        clients = voice_clients or ([state.voice_client] if state.voice_client else [])

        for vc in clients:
            if vc and vc.is_connected():
                try:
                    await vc.disconnect()
                except Exception as e:
                    print(f"error   : disconnect failed: {e}")
                if vc == state.voice_client:
                    state.voice_client = None

        state.play_queue.clear()
        state._dropped_count = 0


if __name__ == "__main__":
    vc_handler = VCHandler(in_executer=False)
    print("version:", vc_handler.get_version())
    print("available:", vc_handler.is_available())
    print("speakers:", len(vc_handler.speakers))
