from running_profile import update_running_profile_from_message
from src.runtime.llm import complete_text
from src.runtime.memory import format_memory_for_prompt
from src.runtime.rag import format_context, search_knowledge


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
- If the user's current information is not enough for a precise judgment, do not pretend it is enough. Give a provisional judgment, then ask targeted follow-up questions.
- Ask at most 6 follow-up questions, ordered by importance.
- For marathon improvement questions, compare half-marathon and marathon performances if both are provided. If the marathon is much slower than expected from the half-marathon, explicitly flag likely limiters such as marathon-specific endurance, pacing, fueling, heat, cramps, injury, long-run history, or low weekly volume as hypotheses, then ask what happened in that marathon.
- Do not give medical diagnosis, injury treatment, drug, supplement, or nutrition prescription.
- Do not promise race results or give a single-point prediction.

Preferred output:
## 临时判断
> One clear provisional conclusion.

## 为什么这么判断
- Key evidence from user profile/message and retrieved knowledge.

## 还需要确认
- Targeted questions needed for precision.

## 现在可以先做什么
- Low-risk direction, not an exact personalized prescription unless enough context is available.
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


async def answer_running_question(question: str) -> str:
    memory_update = ""
    try:
        memory_update = await update_running_profile_from_message(question)
    except Exception as exc:
        print(f"running profile update failed: {exc}", flush=True)

    chunks = await search_knowledge(question, limit=5)
    if not chunks:
        base_message = (
            "我没有在已导入的跑步书籍或视频知识里检索到相关内容。"
            "可以换个问法，或先导入更多资料。"
        )
        if memory_update:
            return f"{base_message}\n\n已写入长期记忆：{memory_update}"
        return base_message

    context = format_context(chunks)
    memory = format_memory_for_prompt("coros-report")
    answer = await complete_text(
        RUNNING_KNOWLEDGE_PROMPT,
        f"""
Question:
{question}

Long-term running memory:
{memory}

Knowledge excerpts:
{context}

Answer the question using the memory and excerpts.
""".strip(),
    )
    answer = f"{answer}\n\n{_format_rag_quotes(chunks)}"
    if memory_update:
        return f"{answer}\n\n> 已写入长期记忆：{memory_update}"
    return answer
