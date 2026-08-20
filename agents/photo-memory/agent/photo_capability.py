from pathlib import Path

import discord

from photo_intent import classify_photo_intent
from photo_store import (
    append_photos,
    find_group,
    format_photo_summary,
    has_pending_update,
    list_recent_groups,
    merge_groups,
    pending_photo_id,
    photo_paths,
    restore_pending_questions,
    save_photo_batch,
    search_photos,
    update_photo_meta,
)
from src.runtime.capability import Capability, CommandContext, TextCommand
from src.runtime.conversation import PHOTO_MEMORY_TOPIC, set_pending_questions


PHOTO_HELP = """
直接发图片并说明就行，例如：这是我洛杉矶马拉松的比赛照片。
想往同一场比赛补图，说「再加上这张」我会加进同一组。
想看照片就说：给我看洛杉矶马拉松的照片。
""".strip()

# 显式命令是手动兜底，走确定性分支；自然语言一律交给意图识别。
EXPLICIT_ACTIONS = {
    "store": "create",
    "save": "create",
    "add": "create",
    "append": "append",
    "update": "update",
    "补充": "update",
    "search": "search",
    "show": "search",
    "find": "search",
    "list": "search",
}


async def _send_files(context: CommandContext, paths: list[Path]) -> None:
    if not paths:
        return
    for start in range(0, len(paths), 10):
        batch = paths[start : start + 10]
        files = [discord.File(path) for path in batch]
        await context.channel.send(files=files)


def _strip_explicit_action(text: str) -> tuple[str, str]:
    action, _, rest = text.strip().partition(" ")
    mapped = EXPLICIT_ACTIONS.get(action.lower())
    if mapped:
        return mapped, rest.strip()
    return "", text.strip()


async def _photo(context: CommandContext, argument: str) -> None:
    if context.read_only:
        await context.send("网页入口不开放照片库。请在 Discord 里使用照片记忆。")
        return

    forced_action, text = _strip_explicit_action(argument)
    has_attachments = any(item.is_image for item in context.attachments)
    pending_id = pending_photo_id(context.conversation_id)

    if not forced_action and not has_attachments and not text:
        await context.send(PHOTO_HELP)
        return

    intent = await classify_photo_intent(
        text,
        has_attachments,
        list_recent_groups(),
        pending_id,
    )
    if forced_action:
        intent["action"] = forced_action
        if forced_action in {"append", "update"} and not intent["target_id"]:
            intent["target_id"] = pending_id

    print(
        f"photo intent action={intent['action']} target={intent['target_id'] or '-'} "
        f"source={intent['source_id'] or '-'} "
        f"event={intent['event'] or '-'} reason={intent['reason']}",
        flush=True,
    )

    await _execute(context, intent, text)
    _sync_pending_questions(context)


async def _execute(context: CommandContext, intent: dict, text: str) -> None:
    action = intent["action"]

    if action == "append":
        await context.send(await append_photos(intent["target_id"], context.attachments))
        # 追加时用户可能顺带补了元数据，例如「再加一张，成绩是 4:30:48」
        if any(intent[key] for key in ("event", "race_date", "result")):
            await context.send(
                update_photo_meta(
                    intent["target_id"],
                    event=intent["event"],
                    race_date=intent["race_date"],
                    result=intent["result"],
                    conversation_id=context.conversation_id,
                )
            )
        return

    if action == "create":
        await context.send(
            await save_photo_batch(
                text,
                context.attachments,
                context.conversation_id,
                event=intent["event"],
                race_date=intent["race_date"],
                result=intent["result"],
            )
        )
        return

    if action == "update":
        target = intent["target_id"]
        if not target or find_group(target) is None:
            await context.send("现在没有正在补充信息的照片。你可以先发一批照片。")
            return
        await context.send(
            update_photo_meta(
                target,
                event=intent["event"],
                race_date=intent["race_date"],
                result=intent["result"],
                note=text,
                conversation_id=context.conversation_id,
            )
        )
        return

    if action == "merge":
        await context.send(
            merge_groups(
                intent["source_id"],
                intent["target_id"],
                conversation_id=context.conversation_id,
            )
        )
        return

    if action == "search":
        await _search_and_send(context, intent)
        return

    await context.send(PHOTO_HELP)


def _sync_pending_questions(context: CommandContext) -> None:
    if has_pending_update(context.conversation_id):
        set_pending_questions(
            context.conversation_id,
            PHOTO_MEMORY_TOPIC,
            ["这批照片的比赛年月日是什么？", "这批照片的比赛成绩是多少？"],
        )
    else:
        set_pending_questions(context.conversation_id, PHOTO_MEMORY_TOPIC, [])


async def _search_and_send(context: CommandContext, intent: dict) -> None:
    """照片检索由模型直接挑分组，不做关键词匹配。

    关键词匹配在中文上很脆：查询按空格切词，而中文整句没有空格，
    「给我看看我2026年洛杉矶马拉松的照片」会变成一个词去 AND 匹配，
    必然落空。而且「2026年」要对上 race_date、「LA」要对上洛杉矶，
    每一条都得手写别名表，写多少都补不完。

    模型本来就拿到了全部分组（id、赛事名、日期、成绩），让它挑 id 就行。
    关键词搜索只在模型不可用时兜底——那时结果差，总好过没有。
    """
    query = intent["search_query"]

    if intent["used_fallback"]:
        records = search_photos(query)
    else:
        records = [
            group for group in (find_group(pid) for pid in intent["match_ids"]) if group
        ]

    if not records:
        hint = f"没有找到和「{query}」相关的照片。" if query else "你想看哪组照片？"
        groups = list_recent_groups()
        if groups:
            names = "、".join(str(g["event"]) for g in groups[:5])
            hint += f"\n现在存了这些：{names}"
        await context.send(hint)
        return

    await context.send(format_photo_summary(records))
    for record in records:
        await _send_files(context, photo_paths(record))


def _restore_pending(_client: object) -> None:
    """启动时把磁盘上的待补充照片回填进内存 pending。

    内存 pending 随进程消失，磁盘上那条不会。不回填的话，重启之后
    用户回一句「成绩是 4:30:48」不会被识别成补充信息。
    """

    def _set(conversation_id: str, questions: list[str]) -> None:
        set_pending_questions(conversation_id, PHOTO_MEMORY_TOPIC, questions)

    try:
        restored = restore_pending_questions(_set)
    except Exception as exc:
        print(f"photo pending restore failed: {exc}", flush=True)
        return
    if restored:
        print(f"photo-memory restored {restored} pending conversations", flush=True)


def build_photo_capability() -> Capability:
    return Capability(
        name="photo-memory",
        description="保存和检索 Discord 上传的比赛照片，并追问比赛日期、成绩等元数据。",
        channel_env_name="DISCORD_RUNNING_CHANNEL_ID",
        text_commands=(
            TextCommand("photo", "保存或检索照片", _photo, aliases=("photos",)),
        ),
        startup_handlers=(_restore_pending,),
    )
