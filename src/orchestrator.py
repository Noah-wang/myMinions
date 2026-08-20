import asyncio
import os
import tempfile
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.registry import CapabilityRegistry, get_registry
from src.runtime.capability import CommandContext, RuntimeAttachment
from src.runtime.conversation import (
    PHOTO_MEMORY_TOPIC,
    RUNNING_COACH_TOPIC,
    get_pending_questions,
)
from src.runtime.llm import complete_json


ROUTER_SYSTEM_PROMPT = """
你是 myMinions 主 Agent 的自然语言路由器。

你的任务是把用户的一句话转换成当前频道允许的内部命令。只返回 JSON，不要解释。

返回格式：
{
  "command": "coros | coros-tools | running | running-video | feel | feelings | kitchen | photo | discord-admin | none",
  "argument": "传给命令的参数",
  "confidence": 0.0,
  "reason": "一句很短的原因"
}

规则：
- 只能选择用户提示中列出的 allowed_commands。
- 如果用户意图不明确，返回 command = "none"。
- 如果用户只是闲聊、感谢、测试、打招呼，返回 command = "none"。
- 不要编造用户没有提供的食材、数量、视频链接、菜谱 ID 或训练问题。
- command = "coros" 时，argument 保留用户原话，用于生成运动报告。
- 当用户问“今天这次训练怎么样”“最近一次运动/跑步怎么样”“帮我复盘今天/最近训练”“生成运动报告”“下一次应该怎么练”这类需要读取个人 COROS 运动记录、恢复、训练负荷或最近活动数据的问题时，只要 coros 在 allowed_commands 中，就优先选择 command = "coros"，不要选择 running。
- command = "coros-tools" 时，argument 为空字符串，用于列出 COROS MCP 工具。
- command = "running" 时，argument 保留用户原话，用于跑步知识问答，或补充跑步长期档案。
- command = "running" 只用于不需要读取具体 COROS 活动记录的训练理论、训练计划、跑步书籍/RAG 问答、长期目标分析或用户档案补充。
- 用户补充年龄、身高、体重、半马/全马成绩、目标成绩、目标日期、周跑量、最长跑、比赛崩盘原因时，如果 running 在 allowed_commands 中，优先选择 command = "running"。
- command = "running-video" 时，argument 必须是用户提供的 B站链接或 BV号，用于导入跑步知识库。
- command = "feel" 时，argument 保留用户原话，用于记录主观感受。
- command = "feelings" 时，argument 为空字符串。
- command = "kitchen" 时，argument 必须是下面之一：
  - add <B站链接或BV号>
  - recipes
  - plan <菜谱ID或菜名>
  - shopping
  - remove-shopping <食材>
  - bought <食材> <数量>
  - use <食材> <数量>
  - pantry
  - today
  - expiring
- command = "photo" 时，argument 保留用户原话。照片能力内部会自己做意图识别，
  判断是新建一组、追加到已有分组、补充元数据还是检索，这里不要替它决定。
- command = "discord-admin" 时，argument 保留用户原话，只用于 Discord 服务器管理。
  目前只支持修改服务器头像/图标。普通频道没有独立头像，不要把频道图片请求路由到这里。
""".strip()


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_ICON_PATH = ROOT_DIR / "assets" / "brand" / "discord-bot-avatar.png"


class MessageChannel(Protocol):
    id: int

    def send(self, content: str, /) -> Awaitable[object]:
        ...


@dataclass(frozen=True)
class NaturalLanguageRoute:
    command_name: str
    argument: str
    confidence: float = 1.0
    reason: str = ""


class MainAgentOrchestrator:
    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        """初始化编排器，绑定功能注册中心。

        Args:
            registry: 功能注册表实例，若为 None 则使用默认的全局注册表。
        """
        self._registry = registry or get_registry()

    def describe_capabilities(self) -> str:
        """获取当前所有已加载功能的文本描述。"""
        return (
            f"{self._registry.describe()}\n"
            "- main-agent: 主 Agent 内置工具\n"
            "  !discord-admin - 修改 Discord 服务器设置（目前支持服务器头像）"
        )

    def run_startup_handlers(self, client: object) -> None:
        """在系统启动时，唤醒并执行所有已注册功能的初始化回调。

        Args:
            client: Discord 客户端实例。
        """
        self._registry.run_startup_handlers(client)

    def is_allowed_for_command(self, channel_id: int, command_name: str) -> bool:
        """检查特定指令是否允许在指定的 Discord 频道中执行。

        通过对比该指令绑定的环境变量中的频道 ID 与当前频道 ID 是否一致来判定。

        Args:
            channel_id: Discord 频道 ID。
            command_name: 指令名称。
        """
        if command_name in {"discord-admin", "discord"}:
            return self._is_allowed_discord_admin_channel(channel_id)

        channel_env_name = self._registry.channel_env_for_command(command_name)
        if channel_env_name is None:
            return True
        return self._is_allowed_channel(channel_id, channel_env_name)

    def is_capabilities_channel(self, channel_id: int) -> bool:
        """检查该频道是否属于任意已注册功能的专属频道。

        Args:
            channel_id: Discord 频道 ID。
        """
        admin_channel = os.getenv("DISCORD_ADMIN_CHANNEL_ID")
        if admin_channel is not None and str(channel_id) == admin_channel:
            return True

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
        attachments: tuple[RuntimeAttachment, ...] = (),
        message: object | None = None,
    ) -> bool:
        """将已解析的指令直接分发给注册表中的具体 Agent 处理。

        Args:
            client: Discord 客户端实例。
            channel: 交互的消息频道。
            command_name: 要执行的指令。
            argument: 指令参数（默认为空）。
        """
        if not self.is_allowed_for_command(channel.id, command_name):
            return True

        if command_name in {"discord-admin", "discord"}:
            try:
                await self._handle_discord_admin(
                    self._command_context(
                        client,
                        channel,
                        attachments=attachments,
                        message=message,
                    ),
                    argument,
                )
            except Exception as exc:
                await self._send_error(channel, "执行 `discord-admin` 失败", exc)
                self._log(f"discord_admin_failed error={exc}")
            return True

        try:
            return await self._registry.dispatch_command(
                self._command_context(
                    client,
                    channel,
                    attachments=attachments,
                    message=message,
                ),
                command_name,
                argument,
            )
        except Exception as exc:
            await self._send_error(channel, f"执行 `{command_name}` 失败", exc)
            self._log(f"command_failed command={command_name} error={exc}")
            return True

    async def dispatch_text(
        self,
        client: object,
        channel: MessageChannel,
        content: str,
        attachments: tuple[RuntimeAttachment, ...] = (),
        message: object | None = None,
    ) -> bool:
        """接收 Discord 聊天框中的普通文本并进行分发。

        处理流程：
        1. 检查是否是特殊的全局命令 `!capabilities`，若是则回复所有加载的能力列表。
        2. 检查是否是以 `!` 开头的显式指令，若是则直接解析并分发。
        3. 若是普通自然语言，调用 LLM 进行意图路由，若成功则分发给对应的 Agent；
           若无法识别且在受控频道内，则给出命令格式提示。

        Args:
            client: Discord 客户端实例。
            channel: 聊天频道。
            content: 接收到的文本内容。
        """
        stripped = content.strip()
        if stripped == "!capabilities":
            if self.is_capabilities_channel(channel.id):
                await channel.send(self.describe_capabilities())
            return True

        if stripped.startswith("!"):
            command_name, _, argument = stripped[1:].partition(" ")
            if not self.is_allowed_for_command(channel.id, command_name):
                return True

            if command_name in {"discord-admin", "discord"}:
                return await self.dispatch_command(
                    client,
                    channel,
                    command_name,
                    argument.strip(),
                    attachments,
                    message,
                )

            try:
                return await self._registry.dispatch_text(
                    self._command_context(
                        client,
                        channel,
                        attachments=attachments,
                        message=message,
                    ),
                    stripped,
                )
            except Exception as exc:
                await self._send_error(channel, f"执行 `{command_name}` 失败", exc)
                self._log(f"text_command_failed command={command_name} error={exc}")
                return True

        if (
            self.is_allowed_for_command(channel.id, "discord-admin")
            and any(attachment.is_image for attachment in attachments)
            and self._looks_like_discord_admin_request(stripped)
        ):
            self._log(f"attachment_dispatch channel_id={channel.id} command=discord-admin")
            return await self.dispatch_command(
                client,
                channel,
                "discord-admin",
                stripped,
                attachments,
                message,
            )

        # 只拦图片。原来是「有任何附件就当存照片」，
        # 结果在跑步频道贴张截图或传个 PDF 都会被照片能力接走。
        if self.is_allowed_for_command(channel.id, "photo") and any(
            attachment.is_image for attachment in attachments
        ):
            self._log(f"attachment_dispatch channel_id={channel.id} command=photo")
            # 原文交给照片能力，由它做意图识别。
            # 这里原来写死 store，等于把「再加上这张」和「这是新的一场比赛」
            # 当成同一件事，用户想追加到已有分组的意图从头到尾没人看。
            return await self.dispatch_command(
                client,
                channel,
                "photo",
                stripped,
                attachments,
                message,
            )

        if self.is_allowed_for_command(
            channel.id, "photo"
        ) and self._has_pending_photo_questions(channel, stripped):
            self._log(f"pending_photo_dispatch channel_id={channel.id} command=photo")
            return await self.dispatch_command(
                client,
                channel,
                "photo",
                stripped,
                attachments,
                message,
            )

        if self.is_allowed_for_command(
            channel.id, "running"
        ) and self._has_pending_running_questions(channel, stripped):
            self._log(f"pending_answer_dispatch channel_id={channel.id} command=running")
            return await self.dispatch_command(client, channel, "running", stripped, message=message)

        try:
            route = await self._route_natural_language(channel.id, stripped)
        except Exception as exc:
            await self._send_error(channel, "自然语言路由失败", exc)
            self._log(f"natural_language_routing_failed channel_id={channel.id} error={exc}")
            return True
        if route is not None:
            self._log(
                "natural_language_dispatch "
                f"channel_id={channel.id} command={route.command_name} "
                f"argument={route.argument!r}"
            )
            return await self.dispatch_command(
                client,
                channel,
                route.command_name,
                route.argument,
                attachments,
                message,
            )

        if self.is_capabilities_channel(channel.id):
            await channel.send(
                "我没判断出要调用哪个能力。可以试试：\n"
                "!coros <问题>：生成运动报告\n"
                "!running <问题>：基于跑步知识库回答\n"
                "!running-video <B站BV号或链接>：导入跑步视频知识\n"
                "!feel <感受>：记录运动感受\n"
                "!feelings：查看最近感受记录\n"
                "!photo search <关键词>：检索并发送照片\n"
                "!discord-admin 把服务器头像改成这张：修改服务器头像"
            )
            self._log(f"natural_language_no_route channel_id={channel.id}")
            return True

        return False

    async def dispatch_web_text(
        self,
        client: object,
        channel: MessageChannel,
        content: str,
        # 默认值必须和 web_server.WEB_COMMANDS 一样是只读集合。
        # 留着写命令的话，任何不传这个参数的调用方都会绕过收窄。
        allowed_commands: tuple[str, ...] = (
            "coros",
            "coros-tools",
            "running",
            "feelings",
            "kitchen",
        ),
    ) -> NaturalLanguageRoute | None:
        """接收来自 Web 端（网页入口）的文本输入并进行分发。

        与 Discord 入口类似，但主要面向网页前端用户，限制执行特定的 allowed_commands，
        并返回路由解析结果（`NaturalLanguageRoute`）以便前端渲染。

        Args:
            client: 客户端实例。
            channel: 聊天通道。
            content: 文本输入。
            allowed_commands: 允许执行的指令列表白名单。
        """
        stripped = content.strip()
        if not stripped:
            return None

        if stripped.startswith("!"):
            command_name, _, argument = stripped[1:].partition(" ")
            command_name = command_name.strip()
            if command_name not in allowed_commands:
                await channel.send("这个网页入口不支持这个命令。")
                return None
            if not self._is_read_only_command(command_name, argument):
                await channel.send("网页入口是只读的，写操作请在 Discord 里进行。")
                return None

            try:
                handled = await self._registry.dispatch_command(
                    self._command_context(client, channel, read_only=True),
                    command_name,
                    argument.strip(),
                )
            except Exception as exc:
                await self._send_error(channel, f"执行 `{command_name}` 失败", exc)
                self._log(f"web_command_failed command={command_name} error={exc}")
                return NaturalLanguageRoute(command_name, argument.strip(), 1.0, "explicit")

            if handled:
                return NaturalLanguageRoute(command_name, argument.strip(), 1.0, "explicit")
            await channel.send("我没有找到这个命令。")
            return None

        if "running" in allowed_commands and self._has_pending_running_questions(
            channel, stripped
        ):
            self._log("web_pending_answer_dispatch command=running")
            await self._registry.dispatch_command(
                self._command_context(client, channel, read_only=True),
                "running",
                stripped,
            )
            return NaturalLanguageRoute("running", stripped, 1.0, "pending answer")

        if "photo" in allowed_commands and self._has_pending_photo_questions(
            channel, stripped
        ):
            self._log("web_pending_photo_rejected")
            await channel.send("网页入口不开放照片库。请在 Discord 里补充照片信息。")
            return None

        try:
            route = await self._route_natural_language_from_allowed(
                -1,
                stripped,
                allowed_commands,
            )
        except Exception as exc:
            await self._send_error(channel, "自然语言路由失败", exc)
            self._log(f"web_natural_language_routing_failed error={exc}")
            return None

        if route is None:
            await channel.send(
                "我没判断出要调用哪个能力。可以直接试：\n"
                "!coros 分析我最近一次运动\n"
                "!running 我现在半马 1:40，全马 4:30，应该怎么练\n"
                "!kitchen today"
            )
            return None

        if not self._is_read_only_command(route.command_name, route.argument):
            self._log(
                "web_write_rejected "
                f"command={route.command_name} argument={route.argument!r}"
            )
            await channel.send("网页入口是只读的，写操作请在 Discord 里进行。")
            return None

        self._log(
            "web_natural_language_dispatch "
            f"command={route.command_name} argument={route.argument!r}"
        )
        try:
            await self._registry.dispatch_command(
                self._command_context(client, channel, read_only=True),
                route.command_name,
                route.argument,
            )
        except Exception as exc:
            await self._send_error(channel, f"执行 `{route.command_name}` 失败", exc)
            self._log(f"web_command_failed command={route.command_name} error={exc}")
        return route

    async def _handle_discord_admin(
        self,
        context: CommandContext,
        argument: str,
    ) -> None:
        """主 Agent 内置 Discord 管理工具。

        这类操作不是业务 subagent 能力，而是宿主聊天环境的管理动作。
        所以放在 orchestrator 里，并且每次执行都做频道、用户权限和 bot 权限检查。
        """
        if context.read_only:
            await context.send("网页入口不支持 Discord 管理操作。")
            return

        if self._discord_admin_action(argument) != "server_icon":
            await context.send(
                "我现在只支持修改 Discord 服务器头像。\n"
                "用法：发一张图片并说「把服务器头像改成这张」。\n"
                "也可以说「把服务器头像改成素材库那个奖牌图」。"
            )
            return

        guild = self._message_guild(context)
        if guild is None:
            await context.send("这个操作只能在 Discord 服务器频道里使用。")
            return

        author = getattr(context.message, "author", None)
        if author is None:
            await context.send("我拿不到这条消息的发送者，不能执行服务器管理操作。")
            return

        if not self._is_discord_admin_user(author):
            await context.send("你需要拥有「管理服务器」权限，才能让我修改服务器头像。")
            return

        bot_member = self._bot_guild_member(context, guild)
        if bot_member is None or not self._member_can_manage_guild(bot_member):
            await context.send("我还没有「管理服务器」权限，不能修改服务器头像。")
            return

        icon_bytes = await self._server_icon_bytes(context)
        await guild.edit(
            icon=icon_bytes,
            reason=f"Requested by {getattr(author, 'name', 'user')} via AgentDeck",
        )
        await context.send("已把服务器头像改好了。")

    async def _server_icon_bytes(self, context: CommandContext) -> bytes:
        image_attachment = next(
            (attachment for attachment in context.attachments if attachment.is_image),
            None,
        )
        if image_attachment is None:
            if not DEFAULT_SERVER_ICON_PATH.exists():
                raise RuntimeError("没有收到图片，素材库里也没有默认服务器头像。")
            return DEFAULT_SERVER_ICON_PATH.read_bytes()

        if image_attachment.size > 8 * 1024 * 1024:
            raise RuntimeError("图片太大了，请换一张 8MB 以内的图片。")

        suffix = Path(image_attachment.filename).suffix or ".png"
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / f"server-icon{suffix}"
            await image_attachment.save(target)
            return target.read_bytes()

    def _message_guild(self, context: CommandContext) -> object | None:
        message_guild = getattr(context.message, "guild", None)
        if message_guild is not None:
            return message_guild
        return getattr(context.channel, "guild", None)

    def _bot_guild_member(self, context: CommandContext, guild: object) -> object | None:
        member = getattr(guild, "me", None)
        if member is not None:
            return member
        client_user = getattr(context.client, "user", None)
        user_id = getattr(client_user, "id", None)
        getter = getattr(guild, "get_member", None)
        if callable(getter) and user_id is not None:
            return getter(user_id)
        return None

    def _is_discord_admin_user(self, author: object) -> bool:
        configured_ids = {
            item.strip()
            for item in os.getenv("DISCORD_ADMIN_USER_IDS", "").split(",")
            if item.strip()
        }
        author_id = str(getattr(author, "id", ""))
        if configured_ids and author_id not in configured_ids:
            return False
        return self._member_can_manage_guild(author)

    def _member_can_manage_guild(self, member: object) -> bool:
        permissions = getattr(member, "guild_permissions", None)
        return bool(
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
        )

    def _looks_like_discord_admin_request(self, content: str) -> bool:
        return self._discord_admin_action(content) is not None

    def _discord_admin_action(self, content: str) -> str | None:
        text = content.strip().lower()
        if not text:
            return None
        server_terms = (
            "服务器头像",
            "服务器图标",
            "服务器图片",
            "server icon",
            "guild icon",
        )
        action_terms = ("改", "换", "设置", "设成", "改成", "update", "set", "change")
        if any(term in text for term in server_terms) and any(
            term in text for term in action_terms
        ):
            return "server_icon"
        if text.partition(" ")[0] in {"set-server-icon", "server-icon"}:
            return "server_icon"
        return None

    def _has_pending_running_questions(self, channel: MessageChannel, content: str) -> bool:
        """判断这条消息是不是在回答跑步教练上一轮的追问。

        教练追问后会把问题存成 pending。这时用户的回复往往只是
        「1 每周40公里 2 主要是间歇」这种裸答案，交给自然语言路由很容易
        被判成 none 或置信度不足而被拒绝，所以直接跳过路由投给 running。

        pending 会随会话一起过期，超时之后这条路径自动失效。
        调用方负责各自入口的权限校验。
        """
        if not content:
            return False
        return bool(
            get_pending_questions(self._conversation_id(channel), RUNNING_COACH_TOPIC)
        )

    def _has_pending_photo_questions(self, channel: MessageChannel, content: str) -> bool:
        if not content:
            return False
        return bool(
            get_pending_questions(self._conversation_id(channel), PHOTO_MEMORY_TOPIC)
        )

    async def _route_natural_language(
        self,
        channel_id: int,
        content: str,
    ) -> NaturalLanguageRoute | None:
        """对自然语言消息进行路由，找出匹配的指令及参数。

        Args:
            channel_id: 消息所在的 Discord 频道 ID。
            content: 原始聊天消息文本。
        """
        if not self._natural_language_routing_enabled() or not content:
            return None

        allowed_commands = self._allowed_natural_language_commands(channel_id)
        if not allowed_commands:
            return None

        return await self._route_natural_language_from_allowed(
            channel_id,
            content,
            allowed_commands,
        )

    async def _route_natural_language_from_allowed(
        self,
        channel_id: int,
        content: str,
        allowed_commands: tuple[str, ...],
    ) -> NaturalLanguageRoute | None:
        """在给定的允许指令白名单中，调用 LLM 对消息进行意图路由。

        Args:
            channel_id: 频道 ID。
            content: 原始文本消息。
            allowed_commands: 允许匹配的指令列表。
        """
        try:
            route = await asyncio.wait_for(
                complete_json(
                    ROUTER_SYSTEM_PROMPT,
                    self._build_router_prompt(content, allowed_commands),
                ),
                timeout=self._natural_language_timeout_seconds(),
            )
            self._log(
                "natural_language_route_raw "
                f"channel_id={channel_id} allowed={allowed_commands} route={route}"
            )
        except Exception as exc:
            raise RuntimeError(str(exc) or exc.__class__.__name__) from exc

        parsed_route = self._route_from_llm_response(route, content, allowed_commands)
        if parsed_route is None:
            self._log(
                "natural_language_route_rejected "
                f"channel_id={channel_id} allowed={allowed_commands} route={route}"
            )
        return parsed_route

    def _allowed_natural_language_commands(self, channel_id: int) -> tuple[str, ...]:
        """获取当前频道所允许调用的所有自然语言路由指令的白名单。"""
        commands: list[str] = []
        for command_name in (
            "coros",
            "running",
            "running-video",
            "feel",
            "feelings",
            "kitchen",
            "photo",
            "discord-admin",
        ):
            if self.is_allowed_for_command(channel_id, command_name):
                commands.append(command_name)
        return tuple(commands)

    def _build_router_prompt(
        self,
        content: str,
        allowed_commands: tuple[str, ...],
    ) -> str:
        """构造发送给 LLM 进行意图识别的提示词。"""
        command_descriptions = {
            "coros": "生成 COROS 单次运动报告或训练复盘。",
            "coros-tools": "列出 COROS MCP 当前提供的工具。",
            "running": (
                "基于跑步知识库回答训练方法、计划、成绩瓶颈问题，"
                "也接收年龄、身高、体重、半马/全马成绩、目标、跑量、比赛问题等长期档案补充。"
            ),
            "running-video": "把 B站跑步长视频字幕导入跑步知识库。",
            "feel": "记录运动后的主观感受，例如 RPE、腿沉、酸痛、疲劳。",
            "feelings": "查看最近记录的运动感受。",
            "kitchen": "处理厨房助手：B站菜谱、采购清单、库存、消耗、过期和今日推荐。",
            "photo": "处理照片记忆：保存 Discord 上传的图片附件，或按事件、地点、比赛名检索并发送照片。",
            "discord-admin": "主 Agent 的 Discord 管理工具：修改服务器头像或图标。",
        }
        allowed_text = "\n".join(
            f"- {name}: {command_descriptions[name]}" for name in allowed_commands
        )
        return f"""
Allowed commands in this channel:
{allowed_text}

User message:
{content}
""".strip()

    def _route_from_llm_response(
        self,
        route: dict[str, Any],
        original_content: str,
        allowed_commands: tuple[str, ...],
    ) -> NaturalLanguageRoute | None:
        """从 LLM 解析后的 JSON 响应中提取路由意图。"""
        command_name = route.get("command")
        if not isinstance(command_name, str):
            return None

        command_name = command_name.strip()
        if command_name == "none":
            return None
        if command_name not in allowed_commands:
            return None

        confidence = self._parse_confidence(route.get("confidence"))
        if confidence < self._natural_language_confidence_threshold():
            return None

        argument = route.get("argument")
        if not isinstance(argument, str):
            argument = ""
        argument = argument.strip()

        if command_name in {"coros", "running", "feel"} and not argument:
            argument = original_content

        if command_name == "coros-tools":
            argument = ""

        if command_name == "running-video" and not self._valid_running_video_argument(
            argument
        ):
            return None

        if command_name == "feelings":
            argument = ""

        if command_name == "kitchen" and not self._valid_kitchen_argument(argument):
            return None

        if command_name == "photo" and not self._valid_photo_argument(argument):
            return None

        if command_name == "discord-admin" and not self._valid_discord_admin_argument(
            argument
        ):
            return None

        reason = route.get("reason")
        if not isinstance(reason, str):
            reason = ""

        return NaturalLanguageRoute(command_name, argument, confidence, reason)

    def _natural_language_routing_enabled(self) -> bool:
        """检查环境变量中是否启用了自然语言路由。"""
        value = os.getenv("NATURAL_LANGUAGE_ROUTING_ENABLED", "true")
        return value.lower() not in {"0", "false", "no", "off"}

    def _natural_language_confidence_threshold(self) -> float:
        """从环境变量获取自然语言路由置信度阈值（默认 0.7）。"""
        value = os.getenv("NATURAL_LANGUAGE_ROUTING_CONFIDENCE", "0.7")
        try:
            return float(value)
        except ValueError:
            return 0.7

    def _natural_language_timeout_seconds(self) -> int:
        """获取 LLM 意图路由的超时秒数。"""
        value = os.getenv("NATURAL_LANGUAGE_ROUTING_TIMEOUT_SECONDS", "20")
        try:
            return max(int(value), 1)
        except ValueError:
            return 20

    def _parse_confidence(self, value: object) -> float:
        """解析 LLM 返回的置信度值，确保其转换为 float 类型。"""
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    def _valid_kitchen_argument(self, argument: str) -> bool:
        """验证厨房助手指令参数的格式合法性。"""
        if not argument:
            return False

        action, _, rest = argument.partition(" ")
        actions_without_rest = {"recipes", "shopping", "pantry", "today", "expiring"}
        actions_with_rest = {
            "add",
            "plan",
            "remove-shopping",
            "bought",
            "use",
        }

        if action in actions_without_rest:
            return not rest.strip()
        if action in actions_with_rest:
            return bool(rest.strip())
        return False

    def _valid_photo_argument(self, argument: str) -> bool:
        """照片命令收原话即可，意图由能力层判断。

        原来要求必须是 store/search/update 前缀，等于让路由器替照片能力
        决定动作，而它既看不到现有分组也看不到附件，判断不了追加还是新建。
        """
        return bool(argument.strip())

    # kitchen 把读和写混在同一个命令里，所以只读入口要按动作拦。
    READ_ONLY_KITCHEN_ACTIONS = {"recipes", "shopping", "pantry", "today", "expiring"}

    def _is_read_only_command(self, command_name: str, argument: str) -> bool:
        """判断这条命令在只读入口是否放行。"""
        if command_name in {"feel", "running-video"}:
            return False
        if command_name == "kitchen":
            action = argument.strip().partition(" ")[0]
            return action in self.READ_ONLY_KITCHEN_ACTIONS
        if command_name == "photo":
            return False
        if command_name == "discord-admin":
            return False
        return True

    def _valid_running_video_argument(self, argument: str) -> bool:
        """验证导入跑步视频的参数，必须包含 BV 号或 bilibili 链接。"""
        if not argument:
            return False
        return "BV" in argument or "bilibili.com" in argument

    def _is_allowed_channel(self, channel_id: int, env_name: str) -> bool:
        """检查给定的频道 ID 是否与环境变量中配置的允许频道 ID 匹配。"""
        configured_id = os.getenv(env_name)
        return configured_id is not None and str(channel_id) == configured_id

    def _is_allowed_discord_admin_channel(self, channel_id: int) -> bool:
        """Discord 管理工具默认只在主 Agent 频道可用。

        如果设置了 DISCORD_ADMIN_CHANNEL_ID，则优先使用专门的管理频道。
        没设置时退回 DISCORD_RUNNING_CHANNEL_ID，方便个人服务器先快速使用。
        """
        admin_channel_id = os.getenv("DISCORD_ADMIN_CHANNEL_ID")
        if admin_channel_id:
            return str(channel_id) == admin_channel_id
        return self._is_allowed_channel(channel_id, "DISCORD_RUNNING_CHANNEL_ID")

    def _valid_discord_admin_argument(self, argument: str) -> bool:
        """主 Agent 只接受明确的服务器头像修改请求。"""
        return self._discord_admin_action(argument) is not None

    def _log(self, message: str) -> None:
        """输出编排器运行的调试和状态日志。"""
        print(f"orchestrator {message}", flush=True)

    def _command_context(
        self,
        client: object,
        channel: MessageChannel,
        read_only: bool = False,
        attachments: tuple[RuntimeAttachment, ...] = (),
        message: object | None = None,
    ) -> CommandContext:
        """构造 Agent 执行指令时所需的上下文对象（包括发送文本及分片发送方法）。

        上下文中带上 conversation_id，供 Agent 读取多轮对话历史。
        Discord 侧按频道区分会话；Web 侧由 channel 自带的 conversation_id 按浏览器会话区分。
        """
        async def send_text(text: str) -> None:
            await channel.send(text)

        async def send_chunks(text: str) -> None:
            await self._send_chunks(channel, text)

        return CommandContext(
            client=client,
            channel=channel,
            send=send_text,
            send_chunks=send_chunks,
            message=message,
            conversation_id=self._conversation_id(channel),
            read_only=read_only,
            attachments=attachments,
        )

    def _conversation_id(self, channel: MessageChannel) -> str:
        """获取该频道对应的会话 ID，用于隔离不同来源的多轮对话历史。"""
        conversation_id = getattr(channel, "conversation_id", None)
        if isinstance(conversation_id, str) and conversation_id.strip():
            return conversation_id.strip()
        return f"channel:{channel.id}"

    async def _send_chunks(self, channel: MessageChannel, text: str) -> None:
        """由于 Discord 限制单条消息最大 2000 字符，将超长文本切片分段发送。"""
        chunk_size = 1800
        for start in range(0, len(text), chunk_size):
            await channel.send(text[start : start + chunk_size])

    async def _send_error(
        self,
        channel: MessageChannel,
        title: str,
        exc: Exception,
    ) -> None:
        """统一封装指令执行失败时的报错回复。"""
        error_text = str(exc).strip() or exc.__class__.__name__
        if len(error_text) > 500:
            error_text = f"{error_text[:500].rstrip()}..."
        await channel.send(f"{title}。\n```text\n{error_text}\n```")


_orchestrator: MainAgentOrchestrator | None = None


def get_orchestrator() -> MainAgentOrchestrator:
    """获取单例模式的编排器实例。"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MainAgentOrchestrator()
    return _orchestrator
