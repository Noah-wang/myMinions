import inspect
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from src.runtime.llm import complete_with_tools


DEFAULT_MAX_ROUNDS = 4
MAX_RESULT_CHARS = 4000


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool: {name}"}

        try:
            result = tool.handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
            return result
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:
            return {"error": f"{name} failed: {exc}"}


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_result(result: Any) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return f"{text[:MAX_RESULT_CHARS]}...(truncated)"


async def run_tool_loop(
    system_prompt: str,
    user_prompt: str,
    registry: ToolRegistry,
    history: Sequence[dict[str, str]] = (),
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    log: Callable[[str], None] | None = None,
) -> str:
    """跑一轮完整的工具调用循环，返回模型的最终文本回答。

    工具往返只存在于这一次调用内部。调用方传进来的 history 只包含
    user / assistant 两种消息，返回值也只是最终文本，
    这样多轮会话历史不会被中间过程撑爆。
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(dict(message) for message in history)
    messages.append({"role": "user", "content": user_prompt})

    for round_index in range(max_rounds):
        message = await complete_with_tools(messages, registry.schemas())
        tool_calls = getattr(message, "tool_calls", None)

        if not tool_calls:
            return message.content or ""

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ],
            }
        )

        for call in tool_calls:
            arguments = _parse_arguments(call.function.arguments)
            result = await registry.execute(call.function.name, arguments)
            if log is not None:
                log(
                    f"tool_call round={round_index} name={call.function.name} "
                    f"arguments={arguments}"
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": _serialize_result(result),
                }
            )

    # 轮数用尽，收掉工具再要一次最终回答，避免无限循环。
    final = await complete_with_tools(messages, None)
    return final.content or ""
