from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


SendText = Callable[[str], Awaitable[None]]
CommandHandler = Callable[["CommandContext", str], Awaitable[None]]
StartupHandler = Callable[[Any], None]


@dataclass(frozen=True)
class CommandContext:
    client: Any
    channel: Any
    send: SendText
    send_chunks: SendText


@dataclass(frozen=True)
class TextCommand:
    name: str
    description: str
    handler: CommandHandler
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    text_commands: tuple[TextCommand, ...] = ()
    startup_handlers: tuple[StartupHandler, ...] = field(default_factory=tuple)
