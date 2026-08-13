import json
import os
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


async def complete_text(system_prompt: str, user_prompt: str) -> str:
    response = await _client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("DeepSeek returned an empty response.")
    return content


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
