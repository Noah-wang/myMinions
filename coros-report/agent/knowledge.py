from src.runtime.llm import complete_text
from src.runtime.rag import format_context, search_knowledge


RUNNING_KNOWLEDGE_PROMPT = """
You answer running training questions using the provided book excerpts.

Rules:
- Write in Chinese.
- Base the answer on the provided excerpts.
- If the excerpts do not contain enough evidence, say so clearly.
- Do not quote long passages from the book.
- Summarize and explain in practical language.
- When useful, connect the advice to training decisions.
""".strip()


async def answer_running_question(question: str) -> str:
    chunks = search_knowledge(question, limit=5)
    if not chunks:
        return "我没有在已导入的跑步书籍里检索到相关内容。可以换个问法，或先导入更多资料。"

    context = format_context(chunks)
    return await complete_text(
        RUNNING_KNOWLEDGE_PROMPT,
        f"""
Question:
{question}

Book excerpts:
{context}

Answer the question using the excerpts.
""".strip(),
    )
