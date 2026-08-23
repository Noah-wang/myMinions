import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


from src.runtime.paths import ROOT_DIR  # noqa: E402
DATA_DIR = ROOT_DIR / "data" / "kitchen-assistant"
RECIPES_PATH = DATA_DIR / "recipes.json"
SHOPPING_LIST_PATH = DATA_DIR / "shopping_list.json"
PANTRY_PATH = DATA_DIR / "pantry.json"
DEFAULT_SHELF_LIFE_DAYS = 7
SHELF_LIFE_RULES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("水产", 1, ("鱼", "虾", "蟹", "贝", "海鲜", "三文鱼", "鳕鱼")),
    ("鸡蛋", 21, ("鸡蛋", "蛋")),
    ("肉类", 2, ("鸡", "鸭", "牛", "猪", "羊", "肉", "排骨", "鸡腿", "鸡胸")),
    ("豆制品", 2, ("豆腐", "豆皮", "豆干", "千张")),
    ("叶菜", 3, ("生菜", "菠菜", "油麦菜", "青菜", "小白菜", "空心菜", "香菜")),
    ("蔬菜", 4, ("西红柿", "番茄", "黄瓜", "土豆", "洋葱", "胡萝卜", "蘑菇", "茄子", "蔬菜")),
    ("水果", 5, ("苹果", "香蕉", "橙", "莓", "葡萄", "梨", "桃", "水果")),
    ("奶制品", 7, ("牛奶", "酸奶", "奶酪", "黄油", "淡奶油")),
    ("主食", 30, ("米", "面", "粉", "面包", "吐司", "馒头")),
    ("调料", 180, ("盐", "糖", "酱油", "生抽", "老抽", "醋", "料酒", "蚝油", "油", "香料")),
)


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


def _infer_shelf_life(name: str) -> tuple[str, int]:
    for category, days, keywords in SHELF_LIFE_RULES:
        if any(keyword in name for keyword in keywords):
            return category, days
    return "默认", DEFAULT_SHELF_LIFE_DAYS


def _days_until(expiry: str) -> int | None:
    if not expiry:
        return None
    try:
        expires_on = date.fromisoformat(expiry)
    except ValueError:
        return None
    return (expires_on - datetime.now(UTC).date()).days


def _is_active_pantry_item(item: dict[str, object]) -> bool:
    return item.get("status", "active") == "active"


def _matches_name(item_name: object, target_name: str) -> bool:
    item_key = _lower(item_name)
    target_key = target_name.strip().lower()
    return item_key == target_key or target_key in item_key or item_key in target_key


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


def _recipe_items(recipe: dict[str, object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    ingredients = recipe.get("ingredients")
    seasonings = recipe.get("seasonings")

    if isinstance(ingredients, list):
        items.extend([item for item in ingredients if isinstance(item, dict)])
    if isinstance(seasonings, list):
        items.extend([item for item in seasonings if isinstance(item, dict)])

    return items


def _find_recipe(
    recipes: list[dict[str, object]],
    selector: str,
) -> dict[str, object] | None:
    target = selector.strip().lower()
    if not target:
        return None

    for recipe in recipes:
        if _lower(recipe.get("id")) == target:
            return recipe

    for recipe in recipes:
        dish_name = _lower(recipe.get("dish_name"))
        if dish_name == target or target in dish_name:
            return recipe

    return None


def save_recipe(
    recipe: dict[str, object],
    source: str,
) -> str:
    recipes = _load_list(RECIPES_PATH)

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

    _write_list(RECIPES_PATH, recipes)

    item_count = len(_recipe_items(saved_recipe))
    return (
        f"已保存《{dish_name}》（{recipe_id}），识别到 {item_count} 个采购项。\n"
        f"需要加入采购清单时发送：!kitchen plan {recipe_id}"
    )


def add_recipe_to_shopping_list(selector: str) -> str:
    recipes = _load_list(RECIPES_PATH)
    recipe = _find_recipe(recipes, selector)
    if recipe is None:
        return f"没有找到菜谱：{selector}。可以先用 !kitchen recipes 查看。"

    shopping_items = _load_list(SHOPPING_LIST_PATH)
    dish_name = _text(recipe.get("dish_name")) or "未命名菜谱"
    recipe_id = _text(recipe.get("id"))
    source = _text(recipe.get("source"))
    added_count = 0

    for item in _recipe_items(recipe):
        if not isinstance(item, dict):
            continue
        shopping_items.append(
            {
                "name": _text(item.get("name")),
                "amount": _text(item.get("amount")),
                "note": _text(item.get("note")),
                "source_recipe_id": recipe_id,
                "source_recipe": dish_name,
                "source": source,
                "status": "pending",
                "added_at": _now(),
            }
        )
        added_count += 1

    _write_list(SHOPPING_LIST_PATH, shopping_items)

    pending_count = len(
        [item for item in shopping_items if item.get("status") == "pending"]
    )
    return f"已把《{dish_name}》加入采购清单，新增 {added_count} 项。当前待采购 {pending_count} 项。"


def format_saved_recipes() -> str:
    recipes = _load_list(RECIPES_PATH)
    if not recipes:
        return "还没有保存菜谱。先用 !kitchen add <B站BV号或链接> 添加。"

    lines = ["已保存菜谱："]
    for recipe in recipes[-20:]:
        recipe_id = _text(recipe.get("id"))
        dish_name = _text(recipe.get("dish_name")) or "未命名菜谱"
        item_count = len(_recipe_items(recipe))
        source = _text(recipe.get("source"))
        lines.append(f"- {recipe_id}：{dish_name}（{item_count} 项） {source}")
    lines.append("加入采购清单：!kitchen plan <菜谱ID或菜名>")
    return "\n".join(lines)


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
) -> str:
    item_name = name.strip()
    if not item_name:
        return "请提供食材名。"

    pantry_items = _load_list(PANTRY_PATH)
    shopping_items = _load_list(SHOPPING_LIST_PATH)
    category, shelf_life_days = _infer_shelf_life(item_name)
    shelf_life = f"{shelf_life_days}天"
    expires_at = _parse_expiry(shelf_life)

    pantry_items.append(
        {
            "name": item_name,
            "amount": amount.strip(),
            "category": category,
            "shelf_life": shelf_life.strip(),
            "shelf_life_days": shelf_life_days,
            "expires_at": expires_at,
            "status": "active",
            "created_at": _now(),
        }
    )

    marked_count = 0
    for item in shopping_items:
        if item.get("status") != "pending":
            continue
        if _matches_name(item.get("name"), item_name):
            item["status"] = "bought"
            item["bought_at"] = _now()
            marked_count += 1
            break

    _write_list(PANTRY_PATH, pantry_items)
    _write_list(SHOPPING_LIST_PATH, shopping_items)

    expiry_text = f"，预计 {expires_at} 过期" if expires_at else ""
    marked_text = "，并已从采购清单标记 1 项" if marked_count else ""
    return f"已记录：{item_name} {amount.strip()}，按{category}默认保质期 {shelf_life}{expiry_text}{marked_text}。"


def format_pantry() -> str:
    pantry_items = [
        item for item in _load_list(PANTRY_PATH) if _is_active_pantry_item(item)
    ]
    if not pantry_items:
        return "当前库存是空的。"

    lines = ["当前库存："]
    for index, item in enumerate(pantry_items, start=1):
        name = _text(item.get("name"))
        amount = _text(item.get("amount"))
        category = _text(item.get("category")) or "未分类"
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

        lines.append(f"{index}. {name} {amount}，{category}{expiry_text}")
    return "\n".join(lines)


def format_expiring(days: int = 3) -> str:
    expiring_items: list[tuple[int, dict[str, object]]] = []
    for item in _load_list(PANTRY_PATH):
        if not _is_active_pantry_item(item):
            continue
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
    pantry_items = [
        item for item in _load_list(PANTRY_PATH) if _is_active_pantry_item(item)
    ]
    if not recipes:
        return "还没有保存菜谱。先用 !kitchen add <B站BV号或链接> 添加一个。"
    if not pantry_items:
        return "当前库存是空的。先用 !kitchen bought <食材> <数量> 记录采购。"

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


def remove_shopping_item(name: str) -> str:
    item_name = name.strip()
    if not item_name:
        return "请提供要移除的采购项名称。"

    shopping_items = _load_list(SHOPPING_LIST_PATH)
    for item in shopping_items:
        if item.get("status") != "pending":
            continue
        if not _matches_name(item.get("name"), item_name):
            continue

        removed_name = _text(item.get("name"))
        item["status"] = "removed"
        item["removed_at"] = _now()
        _write_list(SHOPPING_LIST_PATH, shopping_items)
        return f"已从采购清单移除：{removed_name}。"

    return f"没有找到待采购项：{item_name}。"


def use_pantry_item(name: str, amount: str) -> str:
    item_name = name.strip()
    if not item_name:
        return "请提供要消耗的食材名称。"

    pantry_items = _load_list(PANTRY_PATH)
    for item in pantry_items:
        if not _is_active_pantry_item(item):
            continue
        if not _matches_name(item.get("name"), item_name):
            continue

        used_name = _text(item.get("name"))
        recorded_amount = _text(item.get("amount"))
        item["status"] = "used"
        item["used_amount"] = amount.strip()
        item["used_at"] = _now()
        _write_list(PANTRY_PATH, pantry_items)

        amount_text = f" {amount.strip()}" if amount.strip() else ""
        recorded_text = f"（原记录：{recorded_amount}）" if recorded_amount else ""
        return f"已记录消耗：{used_name}{amount_text}{recorded_text}。"

    return f"库存里没有找到可消耗的食材：{item_name}。"
