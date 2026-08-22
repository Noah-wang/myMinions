"""主 Agent 的循环。

原来主 Agent 是个分类器：把每句话塞进十几个固定命令里的一个，代码执行完就结束，
**模型再也看不到结果**。所以「我一共跑过几场比赛」被判成查训练流水之后，
它没有机会发现「这 20 条全是 Indoor Run，一场比赛都没有，我查错源头了」——
那份结果它压根没看见。

现在反过来：自然语言消息直接进循环，工具结果回灌给模型，模型看着结果再决定下一步。
决定从「看到数据之前」挪到了「看到数据之后」，这是这次改动的全部内容。

工具有两类，都由能力自己交上来：
- 只读工具（Capability.read_tools）：结构化取数，给模型看的
- 命令工具（Capability.text_commands）：执行动作，原来的十几个命令

权限挂在工具表上——只读入口根本看不到写工具，看不见就不可能调用。
"""

from typing import Any

from src.runtime.conversation import append_turn, get_history
from src.runtime.tools import Tool, ToolRegistry, run_tool_loop

ASK_TOPIC = "main-agent"


def _no_lookup_needed(reason: str = "") -> str:
    return "好，直接回答，不查数据。"


# 第一轮被强制必须调工具，所以「你好」这种也得有个正规出口，
# 否则模型会被逼着随便调一个查询工具。
NO_LOOKUP_TOOL = Tool(
    name="no_lookup_needed",
    description=(
        "只有在完全不需要查任何数据就能回答时才调用它："
        "打招呼、道谢、闲聊、解释你刚才那句话是什么意思。"
        "**只要问题涉及他的比赛、训练、成绩、目标、照片、菜谱，就不要用这个，"
        "去调真正的查询工具。**"
    ),
    parameters={
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "一句话说明为什么不需要查"}
        },
        "required": [],
    },
    handler=_no_lookup_needed,
)

MAIN_AGENT_PROMPT = """
你是 noahwang 的个人助理，管着他的跑步训练、比赛照片和厨房。

你有一组工具，分两类：查数据的，和执行动作的。想清楚该用哪个再调。

关于跑步数据，最容易搞混的一点：
- **比赛**是他参加过的正式赛事，记在比赛照片的标注里 → list_races
- **训练**是他每天的日常运动，来自 COROS → list_recent_activities 或 coros-list
这两个是两回事。问「跑过几场比赛」要查 list_races，不是把训练记录倒一遍。

怎么选工具：
- 用户要一个答案（几场、多少公里、哪一场最快）→ 用查数据的工具，然后自己算、自己说。
- 用户要一份报告或一个列表（生成运动报告、列出运动记录）→ 调对应的命令工具，
  它的输出已经是给用户看的格式，你直接把它交出去，不要再复述或改写一遍。
- 拿回结果发现查错了地方，就换个工具再查一次，不要拿着不对的数据硬答。

**关于具体数据，有一条硬规则：只要问题涉及他的赛事名、日期、成绩、距离、
数量，就必须在这一轮真的调用工具去查，哪怕对话历史里看起来已经有答案。**
历史里的数字是你上一轮说的，不是数据源。照着它往下答，你会把没查过的细节
一起编出来——赛事名、名次、配速，看着都很合理，但全是假的。
不确定就再查一次，查一次的代价远小于说错一个成绩。

回答要求：
- 用自己的话直接回答，不要套模板，不要列命令菜单。
- 只说这一轮工具返回里有的。查不到就直说查不到，并说明可以怎么补上这份数据。
- 数字要准确，不要估算，不要凭印象补充工具没给的字段。
- 简短。用户问一个数，就先给那个数，再补一两句相关的。
- 需要用户提供信息才能继续时，就直接问他，一次别问超过两个问题。
""".strip()


async def answer_open_question(
    question: str,
    tools: tuple[Any, ...],
    conversation_id: str = "default",
    log: Any = None,
) -> str:
    """跑一轮主 Agent 循环：模型自己查数据、自己决定动作、自己组织答案。"""
    if not tools:
        return "我现在没有可以用的工具。"

    history = get_history(conversation_id, ASK_TOPIC)
    answer = await run_tool_loop(
        MAIN_AGENT_PROMPT,
        question,
        ToolRegistry((*tools, NO_LOOKUP_TOOL)),
        history=history,
        log=log,
        force_first_tool=True,
    )
    # 只回写最终问答。工具往返留在循环内部，否则几轮之后历史里全是
    # 查询结果的 JSON，真正的对话反而被挤出窗口。
    await append_turn(conversation_id, ASK_TOPIC, question, answer)
    return answer
