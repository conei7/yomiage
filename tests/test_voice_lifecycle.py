import asyncio
import collections
import contextlib
import threading
import unittest
from unittest import mock

from core.voice import VCHandler


class _FakeVoiceClient:
    def __init__(self, channel=None, *, connected: bool = True) -> None:
        self.channel = channel
        self.connected = connected
        self.disconnect_calls = 0
        self.stop_calls = 0

    def is_connected(self) -> bool:
        return self.connected

    def is_playing(self) -> bool:
        return False

    def stop(self) -> None:
        self.stop_calls += 1

    async def move_to(self, channel) -> None:
        self.channel = channel

    async def disconnect(self, *, force: bool = False) -> None:
        self.disconnect_calls += 1
        self.connected = False


class _ConnectChannel:
    def __init__(self) -> None:
        self.connect_calls = 0
        self.clients: list[_FakeVoiceClient] = []

    async def connect(self) -> _FakeVoiceClient:
        self.connect_calls += 1
        await asyncio.sleep(0)
        client = _FakeVoiceClient(self)
        self.clients.append(client)
        return client


class _BlockingDisconnectVoiceClient(_FakeVoiceClient):
    def __init__(self, channel=None) -> None:
        super().__init__(channel)
        self.disconnect_started = asyncio.Event()
        self.allow_disconnect = asyncio.Event()

    async def disconnect(self, *, force: bool = False) -> None:
        self.disconnect_calls += 1
        self.disconnect_started.set()
        await self.allow_disconnect.wait()
        self.connected = False


class _FailingDisconnectVoiceClient(_FakeVoiceClient):
    async def disconnect(self, *, force: bool = False) -> None:
        self.disconnect_calls += 1
        raise RuntimeError("disconnect failed")


class VoiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _handler(self) -> VCHandler:
        with mock.patch.object(VCHandler, "is_available", return_value=False):
            return VCHandler()

    async def test_concurrent_connects_share_one_voice_client(self) -> None:
        handler = self._handler()
        channel = _ConnectChannel()

        first, second = await asyncio.gather(
            handler.connect(1, channel),
            handler.connect(1, channel),
        )

        self.assertEqual(channel.connect_calls, 1)
        self.assertIs(first, second)
        self.assertIs(handler.get_voice_client(1), first)

    async def test_connect_waits_for_in_progress_disconnect(self) -> None:
        handler = self._handler()
        state = handler._state(1)
        old_client = _BlockingDisconnectVoiceClient()
        state.voice_client = old_client
        new_channel = _ConnectChannel()

        disconnect_task = asyncio.create_task(handler.disconnect(1))
        await asyncio.wait_for(old_client.disconnect_started.wait(), timeout=1)

        connect_task = asyncio.create_task(handler.connect(1, new_channel))
        await asyncio.sleep(0)
        self.assertEqual(new_channel.connect_calls, 0)

        old_client.allow_disconnect.set()
        await disconnect_task
        new_client = await connect_task

        self.assertEqual(new_channel.connect_calls, 1)
        self.assertIs(handler.get_voice_client(1), new_client)

    async def test_disconnect_invalidates_state_before_network_wait(self) -> None:
        handler = self._handler()
        state = handler._state(1)
        client = _BlockingDisconnectVoiceClient()
        state.voice_client = client
        state.play_queue.append(b"queued")

        disconnect_task = asyncio.create_task(handler.disconnect(1))
        await asyncio.wait_for(client.disconnect_started.wait(), timeout=1)

        self.assertIsNone(state.voice_client)
        self.assertEqual(list(state.play_queue), [])
        self.assertEqual(state.generation, 1)
        self.assertFalse(await handler.speak(1, "during disconnect"))

        client.allow_disconnect.set()
        await disconnect_task

    async def test_fresh_connection_discards_stale_session_audio(self) -> None:
        handler = self._handler()
        state = handler._state(1)
        state.voice_client = _FakeVoiceClient(connected=False)
        state.play_queue.append(b"stale audio")
        channel = _ConnectChannel()

        new_client = await handler.connect(1, channel)

        self.assertIs(handler.get_voice_client(1), new_client)
        self.assertEqual(list(state.play_queue), [])
        self.assertEqual(state.generation, 1)

    async def test_channel_move_stops_and_discards_old_session_audio(self) -> None:
        handler = self._handler()
        state = handler._state(1)
        old_channel = object()
        new_channel = _ConnectChannel()
        client = _FakeVoiceClient(old_channel)
        state.voice_client = client
        state.play_queue.append(b"old channel audio")

        moved_client = await handler.connect(1, new_channel)

        self.assertIs(moved_client, client)
        self.assertIs(client.channel, new_channel)
        self.assertEqual(client.stop_calls, 1)
        self.assertEqual(list(state.play_queue), [])
        self.assertEqual(state.generation, 1)
        self.assertEqual(new_channel.connect_calls, 0)

    async def test_failed_explicit_disconnect_restores_live_client(self) -> None:
        handler = self._handler()
        client = _FailingDisconnectVoiceClient()

        disconnected = await handler.disconnect(1, [client])

        self.assertFalse(disconnected)
        self.assertIs(handler.get_voice_client(1), client)
        self.assertTrue(client.is_connected())

    async def test_cancelled_worker_balances_queue_task_done(self) -> None:
        handler = self._handler()
        state = handler._state(1)
        state.voice_client = _FakeVoiceClient()
        synthesis_started = threading.Event()
        release_synthesis = threading.Event()

        def synthesize(text: str, speaker: int, speed: float) -> bytes:
            synthesis_started.set()
            release_synthesis.wait(timeout=2)
            return b"audio"

        handler.synthesize = synthesize
        await handler.speak(1, "message")
        started = await asyncio.to_thread(synthesis_started.wait, 1)
        self.assertTrue(started)

        await handler.disconnect(1)
        release_synthesis.set()

        await asyncio.wait_for(state.synthesis_queue.join(), timeout=1)

    async def test_stuck_backpressure_drops_rest_of_message(self) -> None:
        handler = self._handler()
        state = handler._state(1)
        state.voice_client = _FakeVoiceClient()
        state.play_queue = collections.deque(
            [b"queued"] * handler.MAX_QUEUE_SIZE,
            maxlen=200,
        )
        handler._chunk_text = mock.Mock(return_value=["first", "second", "third"])
        handler.synthesize = mock.Mock(return_value=b"unused")

        with mock.patch("core.voice.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            await handler.speak(1, "first message")
            await handler.speak(1, "second message")
            await handler.speak(1, "third message")
            await asyncio.wait_for(state.synthesis_queue.join(), timeout=1)

        self.assertEqual(sleep.await_count, 121)
        handler.synthesize.assert_not_called()

        if state.synthesis_task is not None:
            state.synthesis_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.synthesis_task


class VoicePlaybackCallbackTests(unittest.TestCase):
    def test_old_voice_client_callback_cannot_drive_reconnected_client(self) -> None:
        with mock.patch.object(VCHandler, "is_available", return_value=False):
            handler = VCHandler()
        state = handler._state(1)
        old_client = _FakeVoiceClient()
        old_client.play = mock.Mock()
        state.voice_client = old_client
        state.play_queue.append(b"old")

        fake_loop = mock.Mock()
        fake_loop.is_closed.return_value = False
        handler.loop = fake_loop

        with mock.patch("core.voice.discord.FFmpegPCMAudio", return_value=mock.Mock()):
            handler._play_next(1)

        after = old_client.play.call_args.kwargs["after"]
        state.voice_client = _FakeVoiceClient()
        state.play_queue.append(b"new")
        after(None)

        fake_loop.call_soon_threadsafe.assert_called_once()
        callback, *args = fake_loop.call_soon_threadsafe.call_args.args
        with mock.patch.object(handler, "_play_next") as play_next:
            callback(*args)
        play_next.assert_not_called()

    def test_play_failure_cleans_audio_source(self) -> None:
        with mock.patch.object(VCHandler, "is_available", return_value=False):
            handler = VCHandler()
        state = handler._state(1)
        client = _FakeVoiceClient()
        client.play = mock.Mock(side_effect=RuntimeError("play failed"))
        state.voice_client = client
        state.play_queue.append(b"audio")
        source = mock.Mock()

        fake_loop = mock.Mock()
        fake_loop.is_closed.return_value = False
        handler.loop = fake_loop

        with mock.patch("core.voice.discord.FFmpegPCMAudio", return_value=source):
            handler._play_next(1)

        source.cleanup.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
