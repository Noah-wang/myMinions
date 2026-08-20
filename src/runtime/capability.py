from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SendText = Callable[[str], Awaitable[None]]
CommandHandler = Callable[["CommandContext", str], Awaitable[None]]
StartupHandler = Callable[[Any], None]
AttachmentSaver = Callable[[Path], Awaitable[None]]


IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
)


@dataclass(frozen=True)
class RuntimeAttachment:
    filename: str
    content_type: str | None
    url: str
    size: int
    save: AttachmentSaver

    @property
    def is_image(self) -> bool:
        """是不是图片。

        放在运行时类型上而不是某个能力里，是因为主 Agent 要用它决定
        「这条带附件的消息该不该交给照片能力」——否则在跑步频道随手贴一张
        截图或传一份 PDF，都会被当成存比赛照片。
        """
        if self.content_type and self.content_type.startswith("image/"):
            return True
        return Path(self.filename).suffix.lower() in IMAGE_SUFFIXES


@dataclass(frozen=True)
class CommandContext:
    client: Any
    channel: Any
    send: SendText
    send_chunks: SendText
    message: Any | None = None
    conversation_id: str = "default"
    # 公开的 Web 入口没有认证，只允许读。写操作（改长期记忆、记录感受、
    # 导入知识库、改厨房库存）必须在能力层再拒一次，不能只依赖命令白名单。
    read_only: bool = False
    attachments: tuple[RuntimeAttachment, ...] = ()


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
