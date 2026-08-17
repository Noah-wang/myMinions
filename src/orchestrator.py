import asyncio
import os
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol

from src.registry import CapabilityRegistry, get_registry
from src.runtime.capability import CommandContext
from src.runtime.llm import complete_json


ROUTER_SYSTEM_PROMPT = """
你是 myMinions 主 Agent 的自然语言路由器。

你的任务是把用户的一句话转换成当前频道允许的内部命令。只返回 JSON，不要解释。

返回格式：
{
  "command": "coros | running | running-video | feel | feelings | kitchen | none",
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
- command = "running" 时，argument 保留用户原话，用于跑步知识问答，或补充跑步长期档案。
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

    async def _route_natural_language(
        self,
        channel_id: int,
        content: str,
    ) -> NaturalLanguageRoute | None:
        if not self._natural_language_routing_enabled() or not content:
            return None

        allowed_commands = self._allowed_natural_language_commands(channel_id)
        if not allowed_commands:
            return None

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
        command_descriptions = {
            "coros": "生成 COROS 单次运动报告或训练复盘。",
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

        if command_name == "running-video" and not self._valid_running_video_argument(
            argument
        ):
            return None

        if command_name == "feelings":
            argument = ""

        if command_name == "kitchen" and not self._valid_kitchen_argument(argument):
            return None

        return NaturalLanguageRoute(command_name, argument)

    def _natural_language_routing_enabled(self) -> bool:
        value = os.getenv("NATURAL_LANGUAGE_ROUTING_ENABLED", "true")
        return value.lower() not in {"0", "false", "no", "off"}

    def _natural_language_confidence_threshold(self) -> float:
        value = os.getenv("NATURAL_LANGUAGE_ROUTING_CONFIDENCE", "0.7")
        try:
            return float(value)
        except ValueError:
            return 0.7

    def _natural_language_timeout_seconds(self) -> int:
        value = os.getenv("NATURAL_LANGUAGE_ROUTING_TIMEOUT_SECONDS", "20")
        try:
            return max(int(value), 1)
        except ValueError:
            return 20

    def _parse_confidence(self, value: object) -> float:
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    def _valid_kitchen_argument(self, argument: str) -> bool:
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
        if not argument:
            return False
        return "BV" in argument or "bilibili.com" in argument

    def _is_allowed_channel(self, channel_id: int, env_name: str) -> bool:
        configured_id = os.getenv(env_name)
        return configured_id is not None and str(channel_id) == configured_id

    def _log(self, message: str) -> None:
        print(f"orchestrator {message}", flush=True)

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

    async def _send_error(
        self,
        channel: MessageChannel,
        title: str,
        exc: Exception,
    ) -> None:
        error_text = str(exc).strip() or exc.__class__.__name__
        if len(error_text) > 500:
            error_text = f"{error_text[:500].rstrip()}..."
        await channel.send(f"{title}。\n```text\n{error_text}\n```")


_orchestrator: MainAgentOrchestrator | None = None


def get_orchestrator() -> MainAgentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MainAgentOrchestrator()
    return _orchestrator
