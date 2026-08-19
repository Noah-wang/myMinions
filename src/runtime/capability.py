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
    conversation_id: str = "default"
    # 公开的 Web 入口没有认证，只允许读。写操作（改长期记忆、记录感受、
    # 导入知识库、改厨房库存）必须在能力层再拒一次，不能只依赖命令白名单。
    read_only: bool = False


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
    channel_env_name: str | None = None
    text_commands: tuple[TextCommand, ...] = ()
    startup_handlers: tuple[StartupHandler, ...] = field(default_factory=tuple)
