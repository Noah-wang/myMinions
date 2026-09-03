import asyncio
import os
import tempfile
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.ask import answer_open_question
from src.registry import CapabilityRegistry, get_registry
from src.runtime.capability import CommandContext, RuntimeAttachment
from src.runtime.conversation import (
    PHOTO_MEMORY_TOPIC,
    RUNNING_COACH_TOPIC,
    get_context_value,
    get_pending_questions,
)
from src.runtime.flow_map import command_modules, module_payload, step_payload
from src.runtime.llm import complete_json
from src.runtime.output_guard import sanitize as sanitize_output
from src.runtime.tools import Tool
from src.runtime.trace import Span, log_event, new_trace


ROUTER_SYSTEM_PROMPT = """
你是 myMinions 主 Agent 的自然语言路由器。

你的任务是把用户的一句话转换成当前频道允许的内部命令。只返回 JSON，不要解释。

返回格式：
{
  "command": "ask | coros | coros-tools | coros-list | coros-activity | coros-pb | coros-fit-sync | running | running-video | feel | feelings | kitchen | photo | discord-admin | none",
  "argument": "传给命令的参数",
  "confidence": 0.0,
  "reason": "一句很短的原因"
}

规则：
- 只能选择用户提示中列出的 allowed_commands。
- command = "ask" 是开放式提问的出口，argument 保留用户原话。
  当用户问的是**关于他自己数据的问题**，而答案需要跨来源查、或者不属于
  任何一个具体命令时，选 ask。例如「我一共跑过几场比赛」「我今年跑了多少公里」
  「我最好的成绩是哪场比赛跑的」「我离目标还差多少」。
  这些问题的共同点是：用户要的是一个答案，不是一份报告、一个列表或一个菜单。
- 有具体命令能精确覆盖时优先用具体命令；只有在没有命令能直接回答时才用 ask。
  用户说「列出运动记录」要的就是那个列表，选 coros-list 而不是 ask。
- 如果用户意图不明确，返回 command = "none"。
- 如果用户只是闲聊、感谢、测试、打招呼，返回 command = "none"。
- 不要编造用户没有提供的食材、数量、视频链接、菜谱 ID 或训练问题。
- command = "coros" 时，argument 保留用户原话，用于生成运动报告。
- 当用户问“今天这次训练怎么样”“最近一次运动/跑步怎么样”“帮我复盘今天/最近训练”“生成运动报告”“下一次应该怎么练”这类需要读取个人 COROS 运动记录、恢复、训练负荷或最近活动数据的问题时，只要 coros 在 allowed_commands 中，就优先选择 command = "coros"，不要选择 running。
- command = "coros-tools" 时，argument 为空字符串，用于列出 COROS MCP 工具。
- command = "coros-list" 时，用于列出 COROS 运动记录摘要。用户说“列出运动记录”“查看历史运动”“看最近运动列表”“查所有运动记录”时选择它，argument 保留用户的时间范围或条数要求。
- command = "coros-activity" 时，用于分析用户刚才通过 coros-list 列表选择的某一条运动。用户说“分析第 1 条”“看第 3 条运动记录”“第 2 条重点看心率”时选择它，argument 保留原话。
- 如果用户刚看过照片，然后说“这次/这场/这个比赛/根据照片/照片里的运动记录生成报告”，这也是 command = "coros-activity"，argument 保留原话。不要选 coros，因为 coros 会默认取最新运动。
- command = "coros-pb" 时，用于查看 COROS 自动记录的个人 PB。用户说“查 PB”“个人最好成绩”“我的 5K/10K/半马/全马最好成绩”时选择它，argument 为空字符串。
- command = "coros-fit-sync" 时，用于把 COROS 原始 FIT 文件下载归档到服务器。用户说“同步 FIT”“下载 FIT”“归档最近 90 天运动文件”时选择它，argument 保留时间范围和条数。
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
  Web 入口只允许照片检索，不允许保存或修改。
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

    def is_discord_channel_allowed(self, channel_id: int) -> bool:
        """Discord 总入口。

        配置 DISCORD_AGENT_CHANNEL_ID 后，所有自然语言和斜杠命令只允许在
        这一条频道执行。未配置时保留原来的 capability 专属频道模式。
        """
        configured = os.getenv("DISCORD_AGENT_CHANNEL_ID")
        if configured:
            return str(channel_id) == configured.strip()
        return self.is_capabilities_channel(channel_id)

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

        if command_name == "ask":
            await self._handle_ask(
                self._command_context(
                    client,
                    channel,
                    attachments=attachments,
                    message=message,
                ),
                argument,
            )
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
        new_trace("dc")
        with Span("request", surface="discord", channel=channel.id, chars=len(content.strip())):
            return await self._dispatch_text_inner(
                client, channel, content, attachments, message
            )

    async def _dispatch_text_inner(
        self,
        client: object,
        channel: MessageChannel,
        content: str,
        attachments: tuple[RuntimeAttachment, ...] = (),
        message: object | None = None,
    ) -> bool:
        if not self.is_discord_channel_allowed(channel.id):
            return False

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

        direct_route = self._route_from_direct_intent(
            stripped,
            self._allowed_natural_language_commands(channel.id),
            self._conversation_id(channel),
        )
        if direct_route is not None:
            self._log(
                "direct_natural_language_dispatch "
                f"channel_id={channel.id} command={direct_route.command_name} "
                f"argument={direct_route.argument!r}"
            )
            return await self.dispatch_command(
                client,
                channel,
                direct_route.command_name,
                direct_route.argument,
                attachments,
                message,
            )

        if self.is_allowed_for_command(
            channel.id, "running"
        ) and self._has_pending_running_questions(channel, stripped):
            self._log(f"pending_answer_dispatch channel_id={channel.id} command=running")
            return await self.dispatch_command(client, channel, "running", stripped, message=message)

        # 自然语言直接进循环。分类器不再是大门——它在看到任何数据之前就要
        # 决定走哪条路，而这个决定本身没有推理能力可用。
        if self._main_agent_loop_enabled():
            self._log(f"main_agent_loop channel_id={channel.id}")
            await self._handle_ask(
                self._command_context(
                    client, channel, attachments=attachments, message=message
                ),
                stripped,
                client=client,
            )
            return True

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

        # 路由失败不代表答不了。原来这里直接打印命令菜单，把「分类器挑不出」
        # 当成了「我不知道」——但用户问的往往只是一个跨来源的普通问题。
        # 有只读工具可用时先让主 Agent 自己试着回答，菜单退到最后一步。
        if self._read_tools_for_channel(channel.id):
            self._log(f"natural_language_no_route_ask channel_id={channel.id}")
            return await self.dispatch_command(
                client, channel, "ask", stripped, attachments, message
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
            "coros-list",
            "coros-activity",
            "coros-pb",
            "running",
            "feelings",
            "kitchen",
            "photo",
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
                self._emit_command_trace(channel, command_name)
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

        # 自然语言走和 Discord 同一条主 Agent 循环。
        # 在此之前网页还留在分类器那条老路上，同一句话两边行为不一样。
        # read_only=True 由 _command_context 传下去，工具表会自动裁掉写工具。
        direct_route = self._route_from_direct_intent(
            stripped,
            allowed_commands,
            self._conversation_id(channel),
        )
        if direct_route is not None:
            self._log(
                "web_direct_natural_language_dispatch "
                f"command={direct_route.command_name} argument={direct_route.argument!r}"
            )
            self._emit_command_trace(channel, direct_route.command_name)
            await self._registry.dispatch_command(
                self._command_context(client, channel, read_only=True),
                direct_route.command_name,
                direct_route.argument,
            )
            return direct_route

        if self._main_agent_loop_enabled():
            self._log("web_main_agent_loop")
            await self._handle_ask(
                self._command_context(client, channel, read_only=True),
                stripped,
                allowed_commands=allowed_commands,
            )
            return NaturalLanguageRoute("ask", stripped, 1.0, "main agent loop")

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
                self._conversation_id(channel),
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
            self._emit_command_trace(channel, route.command_name)
            await self._registry.dispatch_command(
                self._command_context(client, channel, read_only=True),
                route.command_name,
                route.argument,
            )
        except Exception as exc:
            await self._send_error(channel, f"执行 `{route.command_name}` 失败", exc)
            self._log(f"web_command_failed command={route.command_name} error={exc}")
        return route

    def _emit_trace_step(self, channel: MessageChannel, module: str, why: str = "") -> None:
        emit_step = getattr(channel, "trace_step", None)
        if not callable(emit_step):
            return
        try:
            emit_step(module_payload(module, why))
        except Exception:
            pass

    def _emit_command_trace(self, channel: MessageChannel, command_name: str) -> None:
        for module in command_modules(command_name):
            self._emit_trace_step(channel, module, f"{command_name} · {module}")

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

    def _route_from_direct_intent(
        self,
        content: str,
        allowed_commands: tuple[str, ...],
        conversation_id: str,
    ) -> NaturalLanguageRoute | None:
        """对高置信自然语言意图做代码级直通。

        主 Agent loop 很适合开放问题，但「今天训练怎么样」这类话在产品上
        是固定动作：读取最新 COROS 运动并生成 ShadowRunner 报告。这里先
        拦住，避免模型把它当成 RAG 问答或普通 ask。
        """
        context_route = self._route_from_conversation_context(
            content,
            allowed_commands,
            conversation_id,
        )
        if context_route is not None:
            return context_route

        if "coros" in allowed_commands and self._looks_like_daily_coros_report(content):
            return NaturalLanguageRoute(
                "coros",
                content.strip(),
                1.0,
                "direct daily coros report",
            )
        return None

    def _looks_like_daily_coros_report(self, content: str) -> bool:
        text = content.strip().lower()
        if not text:
            return False

        if any(
            term in text
            for term in (
                "半马",
                "全马",
                "pb",
                "个人最好",
                "最好成绩",
                "训练计划",
                "计划",
                "知识库",
                "书",
                "视频",
            )
        ):
            return False

        time_terms = (
            "今天",
            "今日",
            "这次",
            "刚才",
            "刚刚",
            "刚跑完",
            "跑完",
            "最近一次",
            "最新",
            "最近的",
        )
        activity_terms = ("训练", "运动", "跑步", "跑", "run", "workout")
        report_terms = (
            "怎么样",
            "如何",
            "分析",
            "复盘",
            "报告",
            "总结",
            "评价",
            "下一次",
            "下次",
            "怎么练",
        )

        has_time_context = any(term in text for term in time_terms)
        has_activity = any(term in text for term in activity_terms)
        has_report_request = any(term in text for term in report_terms)
        return has_time_context and has_activity and has_report_request

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
            f"channel:{channel_id}",
        )

    async def _route_natural_language_from_allowed(
        self,
        channel_id: int,
        content: str,
        allowed_commands: tuple[str, ...],
        conversation_id: str,
    ) -> NaturalLanguageRoute | None:
        """在给定的允许指令白名单中，调用 LLM 对消息进行意图路由。

        Args:
            channel_id: 频道 ID。
            content: 原始文本消息。
            allowed_commands: 允许匹配的指令列表。
            conversation_id: 当前对话 ID，用于读取多轮上下文。
        """
        context_route = self._route_from_conversation_context(
            content,
            allowed_commands,
            conversation_id,
        )
        if context_route is not None:
            self._log(
                "natural_language_context_route "
                f"channel_id={channel_id} command={context_route.command_name} "
                f"argument={context_route.argument!r}"
            )
            return context_route

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

    def _route_from_conversation_context(
        self,
        content: str,
        allowed_commands: tuple[str, ...],
        conversation_id: str,
    ) -> NaturalLanguageRoute | None:
        """处理必须依赖上一轮上下文的短语引用。

        LLM 路由器只看当前一句话，容易把「根据这次的运动记录生成报告」
        理解成最近一次 COROS 训练。这里在进 LLM 前先看会话里是否刚
        检索过带比赛日期的照片，如果有，就把「这次」绑定到照片那场比赛。
        """
        if "coros-activity" not in allowed_commands:
            return None
        if not self._looks_like_photo_context_activity_request(content):
            return None
        context = get_context_value(
            conversation_id,
            RUNNING_COACH_TOPIC,
            "recent_photo_context",
        )
        if not isinstance(context, dict):
            return None
        if not str(context.get("race_date") or "").strip():
            return None
        return NaturalLanguageRoute(
            "coros-activity",
            content.strip(),
            1.0,
            "photo context activity",
        )

    def _looks_like_photo_context_activity_request(self, content: str) -> bool:
        text = content.strip()
        if not text:
            return False

        context_terms = (
            "这次",
            "这场",
            "这个比赛",
            "这次比赛",
            "这场比赛",
            "刚才",
            "照片里的",
            "根据照片",
            "根据这次",
        )
        activity_terms = (
            "运动记录",
            "运动",
            "训练",
            "跑步",
            "比赛",
            "报告",
            "分析",
            "复盘",
        )
        return any(term in text for term in context_terms) and any(
            term in text for term in activity_terms
        )

    def _allowed_natural_language_commands(self, channel_id: int) -> tuple[str, ...]:
        """获取当前频道所允许调用的所有自然语言路由指令的白名单。"""
        commands: list[str] = []
        for command_name in (
            "coros",
            "running",
            "coros-list",
            "coros-activity",
            "coros-pb",
            "coros-fit-sync",
            "running-video",
            "feel",
            "feelings",
            "kitchen",
            "photo",
            "discord-admin",
        ):
            if self.is_allowed_for_command(channel_id, command_name):
                commands.append(command_name)
        if self._read_tools_for_channel(channel_id):
            commands.append("ask")
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
            "coros-list": "列出 COROS 运动记录摘要，供用户选择某一条。",
            "coros-activity": "根据 coros-list 的序号或 ID，分析用户选择的单条 COROS 运动。",
            "coros-pb": "查看 COROS 自动记录的个人 PB。",
            "coros-fit-sync": "把 COROS 原始 FIT 文件下载归档到服务器。",
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
            "ask": (
                "开放式提问：主 Agent 自己查用户的比赛记录、训练记录、PB、"
                "长期档案和知识库，然后直接回答，而不是产出固定格式的报告或列表。"
            ),
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

        if command_name in {"ask", "coros", "coros-activity", "coros-fit-sync", "running", "feel"} and not argument:
            argument = original_content

        if command_name in {"coros-tools", "coros-pb"}:
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

    def _command_tool(
        self,
        command: Any,
        client: object,
        channel: MessageChannel,
        read_only: bool,
        attachments: tuple[RuntimeAttachment, ...],
        message: object | None,
    ) -> Tool:
        """把一条文本命令包装成循环可以调用的工具。

        命令处理器的签名是「往频道发消息，无返回值」，工具要的是「返回值给模型」。
        这两个签名不兼容，硬改的话 14 个处理器全要重写。

        所以这里给它一个 send 会写进缓冲区的 CommandContext：能力层一行不用改，
        执行完把缓冲区当返回值交给模型。发文件那种直接走 channel.send 的副作用
        照旧发生——照片检索仍然会把图片发到频道里，模型拿到的是文字摘要。
        """

        async def handler(argument: str = "") -> str:
            argument = argument.strip()
            # 只读入口的兜底校验。工具表已经按 writes 裁过一遍，
            # 但 kitchen 这种子命令有读有写的必须按参数再判一次。
            if read_only and not self._is_read_only_command(command.name, argument):
                return f"{command.name} 在只读入口不可用，这个操作要在 Discord 里做。"

            buffer: list[str] = []

            async def capture(text: str) -> None:
                if text and text.strip():
                    buffer.append(text.strip())

            async def notify(text: str) -> None:
                """进度提示直达用户，不进缓冲区。

                这是 3.43 把命令包装成工具时漏掉的一条：那次只考虑了
                「发文件的副作用照旧」，没考虑文字进度提示也是给人看的。
                结果用户对着几十秒沉默干等。
                """
                if text and text.strip():
                    await channel.send(sanitize_output(text.strip()))

            context = CommandContext(
                client=client,
                channel=channel,
                send=capture,
                send_chunks=capture,
                notify=notify,
                # 图片绕过工具缓冲区直发给用户。入口不支持就是 None，
                # 能力层会退回把链接写进文本。
                show_images=getattr(channel, "show_images", None),
                message=message,
                conversation_id=self._conversation_id(channel),
                read_only=read_only,
                attachments=attachments,
            )
            await command.handler(context, argument)
            return "\n\n".join(buffer) or f"{command.name} 执行完成，没有输出。"

        description = command.description
        if read_only and command.read_only_description:
            description = command.read_only_description
        if command.argument_hint:
            description = f"{description}。参数：{command.argument_hint}"

        return Tool(
            name=command.name,
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    "argument": {
                        "type": "string",
                        "description": command.argument_hint or "传给这个动作的参数，可留空",
                    }
                },
                "required": [],
            },
            handler=handler,
            writes=command.writes,
            returns_untrusted=command.returns_untrusted,
        )

    def _loop_tools(
        self,
        client: object,
        channel: MessageChannel,
        read_only: bool = False,
        attachments: tuple[RuntimeAttachment, ...] = (),
        message: object | None = None,
        allowed_commands: tuple[str, ...] | None = None,
    ) -> tuple[Tool, ...]:
        """当前入口下，主 Agent 循环能用的全部工具。

        两类：能力交上来的只读工具（结构化取数），以及包装成工具的文本命令（执行动作）。
        隔离和只读裁剪都在这里做——**权限决定谁进工具表，而不是事后拦截**。
        模型看不见的工具就不可能调用它。

        隔离机制按入口不同有两种：Discord 按频道号比对，网页按命令白名单。
        网页的 channel.id 是 -1，永远匹配不上任何 Discord 频道号，
        所以那边必须走白名单——否则工具表会是空的。
        """
        by_allowlist = allowed_commands is not None
        tools: list[Tool] = (
            list(self._all_read_tools())
            if by_allowlist
            else list(self._read_tools_for_channel(channel.id))
        )

        for channel_env_name, command in self._registry.tool_commands():
            if by_allowlist:
                if command.name not in allowed_commands:
                    continue
            elif channel_env_name is not None and not self._is_allowed_channel(
                channel.id, channel_env_name
            ):
                continue
            if read_only and command.writes and not command.read_only_safe:
                continue
            tools.append(
                self._command_tool(
                    command, client, channel, read_only, attachments, message
                )
            )
        return tuple(tools)

    def _all_read_tools(self) -> tuple[Any, ...]:
        """全部只读工具，不做频道过滤。给网页入口用。

        只读工具按定义不改状态，所以在只读入口上不需要再按频道收窄——
        网页本来就允许读取个人数据，这是明确选择过的。
        """
        return tuple(tool for _, tool in self._registry.read_tools())

    def _read_tools_for_channel(self, channel_id: int) -> tuple[Any, ...]:
        """当前频道能用的只读工具。

        频道隔离照旧：厨房的工具不会出现在跑步频道里。跑步和照片两个能力
        绑的是同一个频道，所以在那里问「跑过几场比赛」时，
        比赛记录和训练记录会同时在手边——这正是这个问题需要的。
        """
        tools = []
        for channel_env_name, tool in self._registry.read_tools():
            if channel_env_name is None or self._is_allowed_channel(
                channel_id, channel_env_name
            ):
                tools.append(tool)
        return tuple(tools)

    async def _handle_ask(
        self,
        context: CommandContext,
        question: str,
        client: object | None = None,
        allowed_commands: tuple[str, ...] | None = None,
    ) -> None:
        """主 Agent 循环：模型自己查数据、自己决定动作、自己组织答案。"""
        question = question.strip()
        if not question:
            await context.send("你想问什么？")
            return

        # 架构图高亮。只有网页入口实现了 trace_step，Discord 那边没有，
        # 拿不到就退化成空操作——不给每个入口都塞一个假方法。
        emit_step = getattr(context.channel, "trace_step", None)

        def emit(payload: dict[str, Any]) -> None:
            if not callable(emit_step):
                return
            try:
                emit_step(payload)
            except Exception:
                # 画图失败不能拖垮回答本身
                pass

        emit({"type": "trace_step", "module": "entry", "label": "入口"})
        emit({"type": "trace_step", "module": "loop", "label": "主 Agent 循环"})

        tools = self._loop_tools(
            client if client is not None else context.client,
            context.channel,
            read_only=context.read_only,
            attachments=context.attachments,
            message=context.message,
            allowed_commands=allowed_commands,
        )
        if not tools:
            await context.send("这个频道没有可用的能力。")
            return

        async def on_tool(name: str, why: str) -> None:
            # why 是模型自己写的完整句子（「需要查看用户记录的所有比赛…」），
            # 前面再加「正在」会拼出不通顺的中文，直接用原句。
            if context.verbose_progress:
                await context.progress(why.strip() or f"正在调用 {name}")

            emit(step_payload(name, why))

        try:
            answer = await answer_open_question(
                question,
                tools,
                conversation_id=context.conversation_id,
                log=self._log,
                on_tool=on_tool,
            )
        except Exception as exc:
            await self._send_error(context.channel, "回答失败", exc)
            self._log(f"ask_failed error={exc}")
            return

        emit({"type": "trace_step", "module": "answer", "label": "生成回答"})
        await context.send_chunks(answer)

    def _main_agent_loop_enabled(self) -> bool:
        """自然语言是否直接进主 Agent 循环。

        关掉就退回原来的「分类器挑一个命令」路径。这是个回退开关：
        循环模式下延迟和成本都更高，万一线上表现不好要能一键切回去。
        """
        value = os.getenv("MAIN_AGENT_LOOP_ENABLED", "true")
        return value.lower() not in {"0", "false", "no", "off"}

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
            return bool(argument.strip())
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
        """输出编排器运行的调试和状态日志。

        保留这条老通道是为了不动散落在各处的调用点，但它现在会带上 trace，
        这样和 log_event 打出来的结构化事件能串到同一次请求上。
        """
        log_event("orchestrator", detail=message)

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
        # 出站的最后一道检查放在这里，因为 Discord 和网页都从这个方法拿上下文，
        # 是唯一的收口点。放到各个能力里就得每处都记得加。
        async def send_text(text: str) -> None:
            await channel.send(sanitize_output(text))

        async def send_chunks(text: str) -> None:
            await self._send_chunks(channel, sanitize_output(text))

        return CommandContext(
            client=client,
            channel=channel,
            send=send_text,
            send_chunks=send_chunks,
            notify=getattr(channel, "notify", None) or send_text,
            show_images=getattr(channel, "show_images", None),
            verbose_progress=bool(getattr(channel, "verbose_progress", False)),
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
