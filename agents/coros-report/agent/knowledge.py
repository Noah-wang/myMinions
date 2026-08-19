from running_tools import build_running_registry
from src.runtime.conversation import (
    RUNNING_COACH_TOPIC,
    append_turn,
    get_history,
    get_pending_questions,
    last_user_message,
    set_pending_questions,
)
from src.runtime.memory import format_memory_for_prompt
from src.runtime.rag import format_context, search_knowledge
from src.runtime.tools import run_tool_loop


CONVERSATION_TOPIC = RUNNING_COACH_TOPIC
FOLLOW_UP_HEADINGS = {"还需要确认", "仍需确认"}
NO_QUESTION_MARKERS = {"暂无", "暂无。", "无", "无。", "没有", "没有。"}

RUNNING_KNOWLEDGE_PROMPT = """
You are a running training advisor using the user's long-term profile and the provided knowledge excerpts.

Rules:
- Write in Chinese.
- Base training-method claims on the provided excerpts when possible.
- If the excerpts do not contain enough evidence, say so clearly.
- Do not quote long passages from the book.
- Do not include a separate source quotation section yourself. The system will append quoted excerpts after your answer.
- Summarize and explain in practical language.
- When useful, connect the advice to training decisions.
- Distinguish user-provided facts, knowledge-base evidence, hypotheses, and unknowns.
- For marathon improvement questions, compare half-marathon and marathon performances if both are provided. If the marathon is much slower than expected from the half-marathon, explicitly flag likely limiters such as marathon-specific endurance, pacing, fueling, heat, cramps, injury, long-run history, or low weekly volume as hypotheses, then ask what happened in that marathon.
- Do not give medical diagnosis, injury treatment, drug, supplement, or nutrition prescription.
- Do not promise race results or give a single-point prediction.

Tools:
- You have tools. Call them instead of guessing.
- Never compute training paces, VDOT, or equivalent race times in your head. Call training_paces whenever you are about to state a pace number.
- You do not know today's date. Call race_countdown before saying anything about dates, remaining weeks, or training phases.
- When the user states durable personal facts (age, height, weight, race results, goals and dates, weekly mileage, longest run, what went wrong in a race, injuries, preferences), call save_running_profile with exactly what they said. Do not call it when the user is only asking a question.
- You may call several tools in one turn.

Conversation state:
- Earlier turns of this same conversation may be present. They are your own memory: you asked those questions, the user answered them.
- Never repeat a question you already asked, and never re-ask anything the user already answered, even partially.
- The user often replies with a bare numbered list such as "1 ... 2 ... 3 ...". Map each item back, in order, to the questions you asked in your previous turn, and treat them as answers to those questions.
- Choose output mode A or B before writing. Never mix the two.
- If the prompt lists "Follow-up questions you asked in your previous turn", the user is answering them. Use mode B.
- After two rounds of follow-up questions in this conversation, stop asking and commit to a concrete plan, stating the assumptions you made.

Output mode A - first turn of the conversation, or the user has not answered any follow-up question yet:
## 临时判断
> One clear provisional conclusion.

## 为什么这么判断
- Key evidence from user profile/message and retrieved knowledge.

## 还需要确认
- At most 6 targeted questions, ordered by importance.

## 现在可以先做什么
- Low-risk direction, not an exact personalized prescription unless enough context is available.

Output mode B - the user's latest message answers follow-up questions you asked earlier:
- Do not reuse mode A's headings and do not restate the same provisional judgment as if nothing was learned.
- Open by naming what the new answers changed.
## 结论更新
> How the answers confirm, sharpen, or overturn your earlier judgment. Say explicitly which hypothesis is now supported and which is ruled out.

## 依据
- Tie each point to a specific answer the user just gave, plus knowledge-base evidence.

## 接下来怎么练
- Concrete and actionable, using everything gathered so far in this conversation, not just the latest message.

## 仍需确认
- At most 2 items, and only things never asked before. Write 暂无 if nothing important is missing.
""".strip()


def _quote_excerpt(text: str, max_chars: int = 220) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars].rstrip()}..."


def _format_rag_quotes(chunks: list[dict[str, object]], limit: int = 3) -> str:
    lines = ["## 引用原文"]
    for index, chunk in enumerate(chunks[:limit], start=1):
        source = str(chunk.get("source", "unknown"))
        page = chunk.get("page", "?")
        text = _quote_excerpt(str(chunk.get("text", "")))
        lines.append(f"**[{index}] {source} p.{page}**")
        lines.append(f"> {text}")
    return "\n\n".join(lines)


def _extract_follow_up_questions(answer: str) -> list[str]:
    """从回答里抽出「还需要确认 / 仍需确认」小节下的问题。

    抽出来的问题会存成 pending，下一条用户消息就按这批问题来理解。
    """
    questions: list[str] = []
    capturing = False
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            capturing = stripped.lstrip("#").strip() in FOLLOW_UP_HEADINGS
            continue
        if not capturing or not stripped:
            continue

        item = stripped.lstrip("-*>").strip()
        while item and item[0].isdigit():
            item = item[1:]
        item = item.lstrip(".、)）: ").strip().strip("*").strip()
        if not item or item in NO_QUESTION_MARKERS:
            continue
        questions.append(item)
    return questions


def _format_pending_questions(questions: tuple[str, ...]) -> str:
    if not questions:
        return ""

    listed = "\n".join(f"{index}. {item}" for index, item in enumerate(questions, start=1))
    return f"""
Follow-up questions you asked in your previous turn:
{listed}

The user's latest message is most likely answering these questions, in this order.
Map each answer back to the matching question before you write anything.
""".strip()


def _search_query(question: str, conversation_id: str | None) -> str:
    """构造知识库检索用的 query。

    多轮追问时，用户这一轮往往只是「1 每周40公里 2 主要是间歇」这样的答案，
    单独拿它去检索会跑偏，所以拼上上一轮用户消息来保留话题。
    """
    previous = last_user_message(conversation_id, CONVERSATION_TOPIC)
    if not previous:
        return question
    return f"{previous}\n{question}"


async def answer_running_question(question: str, conversation_id: str | None = None) -> str:
    history = get_history(conversation_id, CONVERSATION_TOPIC)
    pending_questions = get_pending_questions(conversation_id, CONVERSATION_TOPIC)

    chunks = await search_knowledge(_search_query(question, conversation_id), limit=5)
    if not chunks:
        base_message = (
            "我没有在已导入的跑步书籍或视频知识里检索到相关内容。"
            "可以换个问法，或先导入更多资料。"
        )
        append_turn(conversation_id, CONVERSATION_TOPIC, question, base_message)
        set_pending_questions(conversation_id, CONVERSATION_TOPIC, [])
        return base_message

    context = format_context(chunks)
    memory = format_memory_for_prompt("coros-report")
    pending_block = _format_pending_questions(pending_questions)
    answer = await run_tool_loop(
        RUNNING_KNOWLEDGE_PROMPT,
        f"""
Question:
{question}

{pending_block}

Long-term running memory:
{memory}

Knowledge excerpts:
{context}

Answer the question using the conversation so far, the memory, and the excerpts.
""".strip(),
        build_running_registry(),
        history=history,
        log=lambda text: print(f"running {text}", flush=True),
    )
    append_turn(conversation_id, CONVERSATION_TOPIC, question, answer)
    set_pending_questions(
        conversation_id,
        CONVERSATION_TOPIC,
        _extract_follow_up_questions(answer),
    )
    return f"{answer}\n\n{_format_rag_quotes(chunks)}"
