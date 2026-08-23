from agents.kitchen_assistant.kitchen_prompt import RECIPE_EXTRACTION_SYSTEM_PROMPT, build_recipe_extraction_prompt
from src.runtime.llm import complete_json


async def extract_recipe_from_subtitle(
    video_input: str,
    subtitle: str,
) -> dict[str, object]:
    return await complete_json(
        RECIPE_EXTRACTION_SYSTEM_PROMPT,
        build_recipe_extraction_prompt(video_input, subtitle),
    )
