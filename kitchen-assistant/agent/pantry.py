import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data" / "kitchen-assistant"
RECIPES_PATH = DATA_DIR / "recipes.json"
SHOPPING_LIST_PATH = DATA_DIR / "shopping_list.json"
PANTRY_PATH = DATA_DIR / "pantry.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_list(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_list(path: Path, items: list[dict[str, object]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _lower(value: object) -> str:
    return _text(value).lower()


def _parse_expiry(shelf_life: str) -> str:
    value = shelf_life.strip()
    if not value or value in {"无", "未知", "none", "None"}:
        return ""

    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass

    match = re.search(r"(\d+)\s*(天|day|days|d)", value, re.IGNORECASE)
    if match:
        days = int(match.group(1))
        return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()

    return ""


def _days_until(expiry: str) -> int | None:
    if not expiry:
        return None
    try:
        expires_on = date.fromisoformat(expiry)
    except ValueError:
        return None
    return (expires_on - datetime.now(UTC).date()).days


def _normalize_items(items: object) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                normalized.append({"name": name, "amount": "", "note": ""})
            continue

        if not isinstance(item, dict):
            continue

        name = _text(item.get("name"))
        if not name:
            continue

        normalized.append(
            {
                "name": name,
                "amount": _text(item.get("amount")),
                "note": _text(item.get("note")),
            }
        )

    return normalized


def save_recipe_and_update_shopping_list(
    recipe: dict[str, object],
    source: str,
) -> str:
    recipes = _load_list(RECIPES_PATH)
    shopping_items = _load_list(SHOPPING_LIST_PATH)

    dish_name = _text(recipe.get("dish_name")) or "未命名菜谱"
    recipe_id = f"recipe-{len(recipes) + 1}"
    saved_recipe = {
        "id": recipe_id,
        "dish_name": dish_name,
        "source": source,
        "summary": _text(recipe.get("summary")),
        "ingredients": _normalize_items(recipe.get("ingredients")),
        "seasonings": _normalize_items(recipe.get("seasonings")),
        "steps": recipe.get("steps") if isinstance(recipe.get("steps"), list) else [],
        "shopping_notes": recipe.get("shopping_notes")
        if isinstance(recipe.get("shopping_notes"), list)
        else [],
        "confidence": _text(recipe.get("confidence")),
        "created_at": _now(),
    }
    recipes.append(saved_recipe)

    for item in [
        *saved_recipe["ingredients"],
        *saved_recipe["seasonings"],
    ]:
        if not isinstance(item, dict):
            continue
        shopping_items.append(
            {
                "name": item["name"],
                "amount": item["amount"],
                "note": item["note"],
                "source_recipe_id": recipe_id,
                "source_recipe": dish_name,
                "source": source,
                "status": "pending",
                "added_at": _now(),
            }
        )

    _write_list(RECIPES_PATH, recipes)
    _write_list(SHOPPING_LIST_PATH, shopping_items)

    pending_count = len(
        [item for item in shopping_items if item.get("status") == "pending"]
    )
    return f"已保存《{dish_name}》，并把食材加入采购清单。当前待采购 {pending_count} 项。"


def format_shopping_list() -> str:
    shopping_items = [
        item
        for item in _load_list(SHOPPING_LIST_PATH)
        if item.get("status") == "pending"
    ]
    if not shopping_items:
        return "当前采购清单是空的。"

    lines = ["下次采购清单："]
    for index, item in enumerate(shopping_items, start=1):
        amount = _text(item.get("amount"))
        note = _text(item.get("note"))
        source_recipe = _text(item.get("source_recipe"))
        detail = f" {amount}" if amount else ""
        note_text = f"，{note}" if note else ""
        lines.append(
            f"{index}. {item.get('name')}{detail}{note_text}（来自：{source_recipe}）"
        )
    return "\n".join(lines)


def record_purchase(
    name: str,
    amount: str,
    storage: str,
    shelf_life: str,
) -> str:
    item_name = name.strip()
    if not item_name:
        return "请提供食材名。"

    pantry_items = _load_list(PANTRY_PATH)
    shopping_items = _load_list(SHOPPING_LIST_PATH)
    expires_at = _parse_expiry(shelf_life)

    pantry_items.append(
        {
            "name": item_name,
            "amount": amount.strip(),
            "storage": storage.strip(),
            "shelf_life": shelf_life.strip(),
            "expires_at": expires_at,
            "created_at": _now(),
        }
    )

    marked_count = 0
    item_key = item_name.lower()
    for item in shopping_items:
        if item.get("status") != "pending":
            continue
        shopping_name = _lower(item.get("name"))
        if shopping_name == item_key or item_key in shopping_name or shopping_name in item_key:
            item["status"] = "bought"
            item["bought_at"] = _now()
            marked_count += 1
            break

    _write_list(PANTRY_PATH, pantry_items)
    _write_list(SHOPPING_LIST_PATH, shopping_items)

    expiry_text = f"，预计 {expires_at} 过期" if expires_at else ""
    marked_text = "，并已从采购清单标记 1 项" if marked_count else ""
    return f"已记录：{item_name} {amount.strip()}，{storage.strip()}{expiry_text}{marked_text}。"


def format_pantry() -> str:
    pantry_items = _load_list(PANTRY_PATH)
    if not pantry_items:
        return "当前库存是空的。"

    lines = ["当前库存："]
    for index, item in enumerate(pantry_items, start=1):
        name = _text(item.get("name"))
        amount = _text(item.get("amount"))
        storage = _text(item.get("storage"))
        expires_at = _text(item.get("expires_at"))
        days = _days_until(expires_at)

        expiry_text = ""
        if days is not None:
            if days < 0:
                expiry_text = f"，已过期 {abs(days)} 天"
            elif days == 0:
                expiry_text = "，今天到期"
            else:
                expiry_text = f"，还剩 {days} 天"

        lines.append(f"{index}. {name} {amount}，{storage}{expiry_text}")
    return "\n".join(lines)


def format_expiring(days: int = 3) -> str:
    expiring_items: list[tuple[int, dict[str, object]]] = []
    for item in _load_list(PANTRY_PATH):
        expires_at = _text(item.get("expires_at"))
        remaining = _days_until(expires_at)
        if remaining is None:
            continue
        if remaining <= days:
            expiring_items.append((remaining, item))

    if not expiring_items:
        return f"未来 {days} 天内没有记录到快过期食材。"

    lines = [f"未来 {days} 天内需要优先处理："]
    for index, (remaining, item) in enumerate(
        sorted(expiring_items, key=lambda value: value[0]),
        start=1,
    ):
        name = _text(item.get("name"))
        amount = _text(item.get("amount"))
        if remaining < 0:
            status = f"已过期 {abs(remaining)} 天"
        elif remaining == 0:
            status = "今天到期"
        else:
            status = f"还剩 {remaining} 天"
        lines.append(f"{index}. {name} {amount}，{status}")
    return "\n".join(lines)


def recommend_today() -> str:
    recipes = _load_list(RECIPES_PATH)
    pantry_items = _load_list(PANTRY_PATH)
    if not recipes:
        return "还没有保存菜谱。先用 !kitchen add <B站BV号或链接> 添加一个。"
    if not pantry_items:
        return "当前库存是空的。先用 !kitchen bought <食材> <数量> <保存方式> <保质期> 记录采购。"

    pantry_names = [_lower(item.get("name")) for item in pantry_items]
    scored: list[tuple[int, int, dict[str, object], list[str], list[str]]] = []

    for recipe in recipes:
        ingredients = recipe.get("ingredients")
        if not isinstance(ingredients, list):
            ingredients = []

        matched: list[str] = []
        missing: list[str] = []
        urgency_score = 0

        for ingredient in ingredients:
            if not isinstance(ingredient, dict):
                continue
            name = _text(ingredient.get("name"))
            key = name.lower()
            has_item = any(key in pantry_name or pantry_name in key for pantry_name in pantry_names)
            if has_item:
                matched.append(name)
                for item in pantry_items:
                    item_name = _lower(item.get("name"))
                    if key in item_name or item_name in key:
                        remaining = _days_until(_text(item.get("expires_at")))
                        if remaining is not None and remaining <= 3:
                            urgency_score += max(1, 4 - remaining)
                        break
            else:
                missing.append(name)

        if matched:
            scored.append((len(matched), urgency_score, recipe, matched, missing))

    if not scored:
        return "现在库存和已保存菜谱暂时匹配不上。可以先补充库存，或者继续添加菜谱视频。"

    lines = ["今天优先推荐："]
    for index, (_, _, recipe, matched, missing) in enumerate(
        sorted(scored, key=lambda value: (value[1], value[0]), reverse=True)[:3],
        start=1,
    ):
        dish_name = _text(recipe.get("dish_name"))
        source = _text(recipe.get("source"))
        matched_text = "、".join(matched[:5])
        missing_text = "、".join(missing[:5]) if missing else "基本够了"
        lines.append(
            f"{index}. {dish_name}\n"
            f"   可用：{matched_text}\n"
            f"   缺少：{missing_text}\n"
            f"   来源：{source}"
        )
    return "\n".join(lines)
