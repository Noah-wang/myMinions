import asyncio
import os
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol

from src.registry import CapabilityRegistry, get_registry
from src.runtime.capability import CommandContext
from src.runtime.conversation import RUNNING_COACH_TOPIC, get_pending_questions
from src.runtime.llm import complete_json


ROUTER_SYSTEM_PROMPT = """
你是 myMinions 主 Agent 的自然语言路由器。

你的任务是把用户的一句话转换成当前频道允许的内部命令。只返回 JSON，不要解释。

返回格式：
{
  "command": "coros | coros-tools | running | running-video | feel | feelings | kitchen | none",
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
""".strip()


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
        return self._registry.describe()

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
        channel_env_name = self._registry.channel_env_for_command(command_name)
        if channel_env_name is None:
            return True
        return self._is_allowed_channel(channel_id, channel_env_name)

    def is_capabilities_channel(self, channel_id: int) -> bool:
        """检查该频道是否属于任意已注册功能的专属频道。

        Args:
            channel_id: Discord 频道 ID。
        """
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
        """将已解析的指令直接分发给注册表中的具体 Agent 处理。

        Args:
            client: Discord 客户端实例。
            channel: 交互的消息频道。
            command_name: 要执行的指令。
            argument: 指令参数（默认为空）。
        """
        if not self.is_allowed_for_command(channel.id, command_name):
            return True

        try:
            return await self._registry.dispatch_command(
                self._command_context(client, channel),
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
            command_name = stripped[1:].partition(" ")[0]
            if not self.is_allowed_for_command(channel.id, command_name):
                return True

            try:
                return await self._registry.dispatch_text(
                    self._command_context(client, channel),
                    stripped,
                )
            except Exception as exc:
                await self._send_error(channel, f"执行 `{command_name}` 失败", exc)
                self._log(f"text_command_failed command={command_name} error={exc}")
                return True

        if self.is_allowed_for_command(
            channel.id, "running"
        ) and self._has_pending_running_questions(channel, stripped):
            self._log(f"pending_answer_dispatch channel_id={channel.id} command=running")
            return await self.dispatch_command(client, channel, "running", stripped)

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
            )

        if self.is_capabilities_channel(channel.id):
            await channel.send(
                "我没判断出要调用哪个能力。可以试试：\n"
                "!coros <问题>：生成运动报告\n"
                "!running <问题>：基于跑步知识库回答\n"
                "!running-video <B站BV号或链接>：导入跑步视频知识\n"
                "!feel <感受>：记录运动感受\n"
                "!feelings：查看最近感受记录"
            )
            self._log(f"natural_language_no_route channel_id={channel.id}")
            return True

        return False

    async def dispatch_web_text(
        self,
        client: object,
        channel: MessageChannel,
        content: str,
        allowed_commands: tuple[str, ...] = (
            "coros",
            "coros-tools",
            "running",
            "running-video",
            "feel",
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

            try:
                handled = await self._registry.dispatch_command(
                    self._command_context(client, channel),
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
                self._command_context(client, channel),
                "running",
                stripped,
            )
            return NaturalLanguageRoute("running", stripped, 1.0, "pending answer")

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

        self._log(
            "web_natural_language_dispatch "
            f"command={route.command_name} argument={route.argument!r}"
        )
        try:
            await self._registry.dispatch_command(
                self._command_context(client, channel),
                route.command_name,
                route.argument,
            )
        except Exception as exc:
            await self._send_error(channel, f"执行 `{route.command_name}` 失败", exc)
            self._log(f"web_command_failed command={route.command_name} error={exc}")
        return route

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

    def _valid_running_video_argument(self, argument: str) -> bool:
        """验证导入跑步视频的参数，必须包含 BV 号或 bilibili 链接。"""
        if not argument:
            return False
        return "BV" in argument or "bilibili.com" in argument

    def _is_allowed_channel(self, channel_id: int, env_name: str) -> bool:
        """检查给定的频道 ID 是否与环境变量中配置的允许频道 ID 匹配。"""
        configured_id = os.getenv(env_name)
        return configured_id is not None and str(channel_id) == configured_id

    def _log(self, message: str) -> None:
        """输出编排器运行的调试和状态日志。"""
        print(f"orchestrator {message}", flush=True)

    def _command_context(
        self,
        client: object,
        channel: MessageChannel,
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
            conversation_id=self._conversation_id(channel),
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
