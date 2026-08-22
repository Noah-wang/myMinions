from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.runtime.tools import Tool


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

    # 这条命令会不会改变状态。主 Agent 的循环按它决定在只读入口暴露哪些工具——
    # 权限挂在命令自己身上，而不是散落在调用方的 if 里。
    writes: bool = False
    # 这条命令的输出里含有第三方能控制的文本（书籍原文、视频字幕）。
    # 包装成工具后，它一旦执行过，本轮就不再允许写操作。
    returns_untrusted: bool = False
    # writes=True 但这条命令自己会在只读入口拒掉写操作，所以仍然可以暴露。
    # kitchen 一条命令下面既有 pantry 也有 bought，粗粒度的 writes 表达不了；
    # photo 和 running 则是在能力内部按 read_only 裁剪自己的动作。
    read_only_safe: bool = False
    # 参数格式说明，给模型看。留空就只用 description。
    # kitchen 这类子命令很多的能力必须写，否则模型只能猜参数长什么样。
    argument_hint: str = ""
    # 有些命令是运维用的手动触发，不该让模型自己去调。
    expose_as_tool: bool = True

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
    # 能力主动交给主 Agent 的只读工具，用于开放式提问。
    #
    # 主 Agent 需要跨来源回答「我一共跑过几场比赛」这种问题，但它不应该
    # 直接 import 能力内部的存储——那样 src/ 就反向依赖 agents/ 了。
    # 所以反过来：能力自己决定愿意暴露什么，主 Agent 只负责收集和调用。
    #
    # 只读是硬约束。这些工具在没有认证的公开 Web 入口上也会被调用，
    # 任何写操作都必须留在各自的命令里，不能出现在这里。
    read_tools: tuple[Tool, ...] = ()
