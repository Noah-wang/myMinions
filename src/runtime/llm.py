import json
import os
from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI


def _client() -> AsyncOpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing. Add it to .env.")

    return AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def _model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


async def complete_text(
    system_prompt: str,
    user_prompt: str,
    history: Sequence[dict[str, str]] = (),
) -> str:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(dict(message) for message in history)
    messages.append({"role": "user", "content": user_prompt})

    response = await _client().chat.completions.create(
        model=_model(),
        messages=messages,
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("DeepSeek returned an empty response.")
    return content


async def complete_with_tools(
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
) -> Any:
    """发一轮带工具的对话，返回模型的原始 message。

    返回的 message 可能带 tool_calls，也可能只有 content，由调用方的循环处理。

    tool_choice="required" 强制这一轮必须调工具。用它来保证模型不会
    拿对话历史里自己上一轮说过的话当数据源——那样它会连没查过的细节一起编出来。
    """
    kwargs: dict[str, Any] = {
        "model": _model(),
        "messages": list(messages),
    }
    if tools:
        kwargs["tools"] = list(tools)
        kwargs["tool_choice"] = tool_choice

    try:
        response = await _client().chat.completions.create(**kwargs)
    except Exception:
        # 不是所有服务端都支持 required。退回 auto 总比整轮失败好。
        if not tools or tool_choice == "auto":
            raise
        kwargs["tool_choice"] = "auto"
        response = await _client().chat.completions.create(**kwargs)
    return response.choices[0].message


async def complete_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    response = await _client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("DeepSeek returned an empty JSON response.")
    return json.loads(content)
