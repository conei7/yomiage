import asyncio
import contextlib
import threading
import unittest
from unittest import mock

from core.voice import VCHandler


class _FakeVoiceClient:
    def is_connected(self) -> bool:
        return True

    def is_playing(self) -> bool:
        return False


class VoiceSkipGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_queue_discards_inflight_audio_but_allows_new_speech(self) -> None:
        with mock.patch.object(VCHandler, "is_available", return_value=False):
            handler = VCHandler()

        guild_id = 123
        state = handler._state(guild_id)
        state.voice_client = _FakeVoiceClient()
        handler.loop = asyncio.get_running_loop()
        handler._play_next = mock.Mock()

        synthesis_started = threading.Event()
        release_synthesis = threading.Event()

        def synthesize(text: str, speaker: int, speed: float) -> bytes:
            if text == "old message":
                synthesis_started.set()
                if not release_synthesis.wait(timeout=2):
                    raise TimeoutError("test synthesis was not released")
            return text.encode("utf-8")

        handler.synthesize = synthesize

        await handler.speak(guild_id, "old message")
        started = await asyncio.to_thread(synthesis_started.wait, 1)
        self.assertTrue(started)

        handler.clear_queue(guild_id)
        release_synthesis.set()
        await asyncio.wait_for(state.synthesis_queue.join(), timeout=2)
        await asyncio.sleep(0)

        self.assertEqual(list(state.play_queue), [])
        handler._play_next.assert_not_called()

        await handler.speak(guild_id, "new message")
        await asyncio.wait_for(state.synthesis_queue.join(), timeout=2)
        await asyncio.sleep(0)

        self.assertEqual(list(state.play_queue), [b"new message"])
        handler._play_next.assert_called_once_with(guild_id)

        if state.synthesis_task is not None:
            state.synthesis_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.synthesis_task


if __name__ == "__main__":
    unittest.main()
