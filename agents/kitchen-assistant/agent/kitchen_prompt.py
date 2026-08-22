from src.runtime.prompt import compose
from src.runtime.untrusted import UNTRUSTED_CONTENT_RULE
from src.runtime.untrusted import wrap as wrap_untrusted


RECIPE_EXTRACTION_ROLE = """
你是一个后厨采购助手，负责从做菜视频字幕中提取可执行的菜谱和采购信息。

只输出 JSON，不要输出 Markdown。
如果字幕不是做菜内容，也尽量返回空数组和简短原因。

JSON 格式：
{
  "dish_name": "菜名",
  "summary": "一句话说明这道菜",
  "ingredients": [
    {"name": "食材名", "amount": "数量或空字符串", "note": "处理方式或空字符串"}
  ],
  "seasonings": [
    {"name": "调料名", "amount": "数量或空字符串", "note": "用途或空字符串"}
  ],
  "steps": [
    "关键步骤"
  ],
  "shopping_notes": [
    "采购注意事项"
  ],
  "confidence": "high|medium|low"
}
""".strip()

RECIPE_EXTRACTION_SYSTEM_PROMPT = compose(RECIPE_EXTRACTION_ROLE, UNTRUSTED_CONTENT_RULE)


def build_recipe_extraction_prompt(video_input: str, subtitle: str) -> str:
    """字幕是第三方能控制的文本，必须带边界标签进提示词。

    这里是整个系统里外部内容最"生"的一处——一整条视频的字幕原样进模型，
    中间没有任何检索或摘要环节做缓冲。
    """
    return f"""
视频来源：
{video_input}

字幕内容：
{wrap_untrusted(subtitle, source="bilibili-subtitle")}

请提取这条视频里的菜谱信息，并整理成采购清单需要的结构。
""".strip()
