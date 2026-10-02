import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from cogs.maincog import MainCog


class _FakeVoiceClient:
    def __init__(self, channel) -> None:
        self.channel = channel
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected


class VoiceEventRaceTests(unittest.IsolatedAsyncioTestCase):
    def _cog(self):
        cog = object.__new__(MainCog)
        cog._target_channels = {}
        cog._connecting = set()
        cog.default_bot_speaker = 2
        cog.default_bot_speed = 1.0
        cog.config = {"vc_embed_description": "voice"}
        return cog

    async def test_auto_join_rechecks_when_last_human_leaves_during_connect(self) -> None:
        cog = self._cog()
        guild = SimpleNamespace(id=10)
        human = SimpleNamespace(
            id=20,
            bot=False,
            guild=guild,
            display_name="human",
            display_avatar=SimpleNamespace(url="avatar"),
        )
        bot_member = SimpleNamespace(id=99, bot=True)
        channel = SimpleNamespace(id=30, members=[human])
        voice_client = _FakeVoiceClient(channel)

        async def connect(guild_id, target_channel):
            # Reproduce the leave event being ignored while _connecting is set.
            target_channel.members = [bot_member]
            return voice_client

        handler = SimpleNamespace(
            connect=mock.AsyncMock(side_effect=connect),
            disconnect=mock.AsyncMock(return_value=True),
            get_voice_client=mock.Mock(return_value=voice_client),
            speak=mock.AsyncMock(),
        )
        cog.vc_handler = handler
        cog.bot = SimpleNamespace(
            user=SimpleNamespace(id=99),
            voice_clients=[],
            get_channel=mock.Mock(return_value=None),
        )
        cog._server_settings = mock.Mock(
            return_value=(
                {},
                {
                    "auto_join_vc": True,
                    "announce_join_leave": False,
                    "channel_bindings": {"30": 40},
                },
            )
        )

        with mock.patch("cogs.maincog.asyncio.sleep", new=mock.AsyncMock()):
            await cog.on_voice_state_update(
                human,
                SimpleNamespace(channel=None),
                SimpleNamespace(channel=channel),
            )

        handler.disconnect.assert_awaited_once_with(guild.id)
        self.assertNotIn(guild.id, cog._target_channels)
        self.assertNotIn(guild.id, cog._connecting)

    async def test_manual_connect_rechecks_when_user_leaves(self) -> None:
        cog = self._cog()

        class FakeGuild:
            pass

        guild = FakeGuild()
        guild.id = 10
        guild.voice_client = None
        human = SimpleNamespace(id=20, bot=False)
        channel = SimpleNamespace(id=30, members=[human])
        user = SimpleNamespace(voice=SimpleNamespace(channel=channel))
        voice_client = _FakeVoiceClient(channel)

        async def connect(guild_id, target_channel):
            target_channel.members = []
            return voice_client

        handler = SimpleNamespace(
            connect=mock.AsyncMock(side_effect=connect),
            disconnect=mock.AsyncMock(return_value=True),
            get_voice_client=mock.Mock(return_value=voice_client),
            speak=mock.AsyncMock(),
        )
        cog.vc_handler = handler
        cog._server_settings = mock.Mock(
            return_value=(
                {},
                {
                    "channel_bindings": {},
                },
            )
        )
        cog._embed = mock.Mock(side_effect=lambda **kwargs: kwargs)
        interaction = SimpleNamespace(
            guild=guild,
            user=user,
            channel_id=40,
            response=SimpleNamespace(defer=mock.AsyncMock()),
            followup=SimpleNamespace(send=mock.AsyncMock()),
        )

        with (
            mock.patch("cogs.maincog.discord.Guild", FakeGuild),
            mock.patch("cogs.maincog.asyncio.sleep", new=mock.AsyncMock()),
        ):
            await MainCog.vc.callback(cog, interaction)

        handler.disconnect.assert_awaited_once_with(guild.id)
        self.assertNotIn(guild.id, cog._target_channels)
        self.assertNotIn(guild.id, cog._connecting)
        handler.speak.assert_not_awaited()

    async def test_manual_connect_recovers_stale_discord_voice_cache(self) -> None:
        cog = self._cog()

        class FakeGuild:
            pass

        guild = FakeGuild()
        guild.id = 10
        human = SimpleNamespace(id=20, bot=False)
        channel = SimpleNamespace(id=30, members=[human])
        stale_client = _FakeVoiceClient(channel)
        stale_client.connected = False
        guild.voice_client = stale_client
        new_client = _FakeVoiceClient(channel)
        user = SimpleNamespace(voice=SimpleNamespace(channel=channel))

        handler = SimpleNamespace(
            connect=mock.AsyncMock(return_value=new_client),
            disconnect=mock.AsyncMock(return_value=True),
            get_voice_client=mock.Mock(return_value=new_client),
            speak=mock.AsyncMock(),
        )
        cog.vc_handler = handler
        cog._server_settings = mock.Mock(
            return_value=({}, {"channel_bindings": {}})
        )
        cog._resolve_connection_collision = mock.AsyncMock(return_value="none")
        cog._embed = mock.Mock(side_effect=lambda **kwargs: kwargs)
        interaction = SimpleNamespace(
            guild=guild,
            user=user,
            channel_id=40,
            response=SimpleNamespace(defer=mock.AsyncMock()),
            followup=SimpleNamespace(send=mock.AsyncMock()),
        )

        with (
            mock.patch("cogs.maincog.discord.Guild", FakeGuild),
            mock.patch("cogs.maincog.discord.VoiceClient", _FakeVoiceClient),
            mock.patch("cogs.maincog.asyncio.sleep", new=mock.AsyncMock()),
        ):
            await MainCog.vc.callback(cog, interaction)

        handler.disconnect.assert_awaited_once_with(guild.id, [stale_client])
        handler.connect.assert_awaited_once_with(guild.id, channel)
        handler.speak.assert_awaited_once()
        self.assertEqual(cog._target_channels[guild.id], 40)

    async def test_gateway_ready_cleanup_runs_only_once(self) -> None:
        cog = self._cog()
        cog._initial_ready_complete = False
        voice_client = SimpleNamespace(
            guild=SimpleNamespace(id=10),
            channel=SimpleNamespace(name="voice"),
        )
        cog.bot = SimpleNamespace(
            user=SimpleNamespace(id=99, name="bot"),
            guilds=[],
            voice_clients=[voice_client],
            tree=SimpleNamespace(),
            account_label="bot1",
        )
        cog.vc_handler = SimpleNamespace(
            disconnect=mock.AsyncMock(return_value=True)
        )
        cog._migrate_legacy_dictionary = mock.Mock()

        await cog.on_ready()
        await cog.on_ready()

        cog.vc_handler.disconnect.assert_awaited_once_with(10, [voice_client])
        self.assertTrue(cog._initial_ready_complete)

    async def test_first_ready_does_not_cleanup_client_created_during_sync(self) -> None:
        cog = self._cog()
        cog._initial_ready_complete = False
        sync_started = asyncio.Event()
        allow_sync = asyncio.Event()

        async def sync(*, guild):
            sync_started.set()
            await allow_sync.wait()

        guild = SimpleNamespace(id=10, name="guild")
        voice_clients = []
        cog.bot = SimpleNamespace(
            user=SimpleNamespace(id=99, name="bot"),
            guilds=[guild],
            voice_clients=voice_clients,
            tree=SimpleNamespace(
                copy_global_to=mock.Mock(),
                sync=mock.AsyncMock(side_effect=sync),
            ),
            account_label="bot1",
        )
        cog.vc_handler = SimpleNamespace(
            disconnect=mock.AsyncMock(return_value=True)
        )
        cog._migrate_legacy_dictionary = mock.Mock()
        cog._server_settings = mock.Mock(
            return_value=(
                {},
                {"auto_join_vc": True, "channel_bindings": {}},
            )
        )

        ready_task = asyncio.create_task(cog.on_ready())
        await sync_started.wait()
        fresh_client = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(name="fresh"),
        )
        voice_clients.append(fresh_client)
        allow_sync.set()
        await ready_task

        cog.vc_handler.disconnect.assert_not_awaited()

    async def test_failed_collision_quarantines_text_binding(self) -> None:
        cog = self._cog()
        guild_id = 10
        channel = SimpleNamespace(
            id=30,
            members=[
                SimpleNamespace(id=1, bot=True),
                SimpleNamespace(id=99, bot=True),
            ],
        )
        cog.bot = SimpleNamespace(user=SimpleNamespace(id=99))
        cog.vc_handler = SimpleNamespace(
            disconnect=mock.AsyncMock(return_value=False)
        )
        cog._target_channels[guild_id] = 40

        result = await cog._resolve_connection_collision(guild_id, channel)

        self.assertEqual(result, "failed")
        self.assertNotIn(guild_id, cog._target_channels)


if __name__ == "__main__":
    unittest.main()
