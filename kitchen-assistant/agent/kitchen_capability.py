from pantry import (
    add_recipe_to_shopping_list,
    format_expiring,
    format_pantry,
    format_saved_recipes,
    format_shopping_list,
    recommend_today,
    record_purchase,
    remove_shopping_item,
    save_recipe,
    use_pantry_item,
)
from recipe_extractor import extract_recipe_from_subtitle
from src.runtime.capability import Capability, CommandContext, TextCommand
from subtitle_fetcher import fetch_bilibili_subtitle


async def _kitchen(context: CommandContext, argument: str) -> None:
    action, _, rest = argument.strip().partition(" ")

    if action == "add":
        await _add_recipe(context, rest.strip())
        return

    if action == "shopping":
        await context.send(format_shopping_list())
        return

    if action == "recipes":
        await context.send(format_saved_recipes())
        return

    if action == "plan":
        await context.send(add_recipe_to_shopping_list(rest.strip()))
        return

    if action == "remove-shopping":
        await context.send(remove_shopping_item(rest.strip()))
        return

    if action == "bought":
        await context.send(_record_purchase_from_text(rest.strip()))
        return

    if action == "use":
        await context.send(_use_pantry_item_from_text(rest.strip()))
        return

    if action == "pantry":
        await context.send(format_pantry())
        return

    if action == "today":
        await context.send(recommend_today())
        return

    if action == "expiring":
        await context.send(format_expiring())
        return

    await context.send(
        "可用命令：\n"
        "!kitchen add <B站BV号或链接>\n"
        "!kitchen recipes\n"
        "!kitchen plan <菜谱ID或菜名>\n"
        "!kitchen shopping\n"
        "!kitchen remove-shopping <食材>\n"
        "!kitchen bought <食材> <数量>\n"
        "!kitchen use <食材> <数量>\n"
        "!kitchen pantry\n"
        "!kitchen today\n"
        "!kitchen expiring"
    )


async def _add_recipe(context: CommandContext, video_input: str) -> None:
    if not video_input:
        await context.send("请提供 B站 BV号或视频链接。")
        return

    await context.send("正在抓取 B站字幕...")
    try:
        subtitle = await fetch_bilibili_subtitle(video_input)
    except Exception as exc:
        await context.send(f"抓取字幕失败：{exc}")
        return

    await context.send("字幕已抓到，正在提取菜谱和采购项...")
    try:
        recipe = await extract_recipe_from_subtitle(video_input, subtitle)
        result = save_recipe(recipe, video_input)
    except Exception as exc:
        await context.send(f"提取菜谱失败：{exc}")
        return

    await context.send(result)


def _record_purchase_from_text(argument: str) -> str:
    parts = argument.split(maxsplit=1)
    if len(parts) < 2:
        return "用法：!kitchen bought <食材> <数量>，例如：!kitchen bought 鸡腿 1000g"

    name = parts[0]
    amount = parts[1]
    return record_purchase(name, amount)


def _use_pantry_item_from_text(argument: str) -> str:
    parts = argument.split(maxsplit=1)
    if len(parts) < 1 or not parts[0]:
        return "用法：!kitchen use <食材> <数量>，例如：!kitchen use 鸡腿 500g"

    name = parts[0]
    amount = parts[1] if len(parts) >= 2 else ""
    return use_pantry_item(name, amount)


def build_kitchen_capability() -> Capability:
    return Capability(
        name="kitchen-assistant",
        description="从 B站做菜视频提取菜谱，并维护采购清单。",
        channel_env_name="DISCORD_COOKING_CHANNEL_ID",
        text_commands=(
            TextCommand(
                "kitchen",
                "管理菜谱和采购清单",
                _kitchen,
            ),
        ),
    )
