import os
from collections.abc import Awaitable
from typing import Protocol

from src.registry import CapabilityRegistry, get_registry
from src.runtime.capability import CommandContext


class MessageChannel(Protocol):
    id: int

    def send(self, content: str, /) -> Awaitable[object]:
        ...


class MainAgentOrchestrator:
    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    def describe_capabilities(self) -> str:
        return self._registry.describe()

    def run_startup_handlers(self, client: object) -> None:
        self._registry.run_startup_handlers(client)

    def is_allowed_for_command(self, channel_id: int, command_name: str) -> bool:
        channel_env_name = self._registry.channel_env_for_command(command_name)
        if channel_env_name is None:
            return True
        return self._is_allowed_channel(channel_id, channel_env_name)

    def is_capabilities_channel(self, channel_id: int) -> bool:
        for env_name in self._registry.channel_env_names():
            if self._is_allowed_channel(channel_id, env_name):
                return True
        return False

    async def dispatch_command(
        self,
        client: object,
        channel: MessageChannel,
        command_name: str,
        argument: str = "",
    ) -> bool:
        if not self.is_allowed_for_command(channel.id, command_name):
            return True

        return await self._registry.dispatch_command(
            self._command_context(client, channel),
            command_name,
            argument,
        )

    async def dispatch_text(
        self,
        client: object,
        channel: MessageChannel,
        content: str,
    ) -> bool:
        stripped = content.strip()
        if stripped == "!capabilities":
            if self.is_capabilities_channel(channel.id):
                await channel.send(self.describe_capabilities())
            return True

        if stripped.startswith("!"):
            command_name = stripped[1:].partition(" ")[0]
            if not self.is_allowed_for_command(channel.id, command_name):
                return True

        return await self._registry.dispatch_text(
            self._command_context(client, channel),
            stripped,
        )

    def _is_allowed_channel(self, channel_id: int, env_name: str) -> bool:
        configured_id = os.getenv(env_name)
        return configured_id is not None and str(channel_id) == configured_id

    def _command_context(
        self,
        client: object,
        channel: MessageChannel,
    ) -> CommandContext:
        async def send_text(text: str) -> None:
            await channel.send(text)

        async def send_chunks(text: str) -> None:
            await self._send_chunks(channel, text)

        return CommandContext(
            client=client,
            channel=channel,
            send=send_text,
            send_chunks=send_chunks,
        )

    async def _send_chunks(self, channel: MessageChannel, text: str) -> None:
        chunk_size = 1800
        for start in range(0, len(text), chunk_size):
            await channel.send(text[start : start + chunk_size])


_orchestrator: MainAgentOrchestrator | None = None


def get_orchestrator() -> MainAgentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MainAgentOrchestrator()
    return _orchestrator
