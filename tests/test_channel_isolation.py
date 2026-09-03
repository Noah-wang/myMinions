import os
import unittest
from unittest.mock import patch

from src.orchestrator import MainAgentOrchestrator
from src.registry import CapabilityRegistry


class _Channel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)


class ChannelIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.orchestrator = MainAgentOrchestrator(CapabilityRegistry([]))

    def test_global_channel_matches_only_one_channel(self) -> None:
        with patch.dict(os.environ, {"DISCORD_AGENT_CHANNEL_ID": "123"}, clear=False):
            self.assertTrue(self.orchestrator.is_discord_channel_allowed(123))
            self.assertFalse(self.orchestrator.is_discord_channel_allowed(456))

    async def test_disallowed_message_stops_before_routing(self) -> None:
        channel = _Channel(456)
        with patch.dict(os.environ, {"DISCORD_AGENT_CHANNEL_ID": "123"}, clear=False):
            handled = await self.orchestrator.dispatch_text(object(), channel, "你好")
        self.assertFalse(handled)
        self.assertEqual(channel.messages, [])


if __name__ == "__main__":
    unittest.main()
