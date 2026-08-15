from agent import list_available_coros_tools
from graph import generate_coros_graph_report
from auto_report import check_and_send_coros_auto_report, register_coros_auto_report
from feelings import list_recent_feelings, record_feeling
from knowledge import answer_running_question
from src.runtime.capability import Capability, CommandContext, TextCommand


DEFAULT_REPORT_REQUEST = "分析我最近一次运动，重点看配速、心率、恢复和下一次训练建议。"


async def _coros_report(context: CommandContext, argument: str) -> None:
    request = argument.strip() or DEFAULT_REPORT_REQUEST
    await context.send("正在读取 COROS 数据并生成报告...")
    try:
        report = await generate_coros_graph_report(request)
        await context.send_chunks(report)
    except Exception as exc:
        await context.send(f"生成 COROS 报告失败：{exc}")


async def _coros_tools(context: CommandContext, _: str) -> None:
    await context.send("正在读取 COROS MCP 工具列表...")
    try:
        tools = await list_available_coros_tools()
        await context.send_chunks(tools)
    except Exception as exc:
        await context.send(f"读取 COROS 工具失败：{exc}")


async def _running_ask(context: CommandContext, argument: str) -> None:
    question = argument.strip()
    if not question:
        await context.send("请写你的跑步训练问题。")
        return

    await context.send("正在检索跑步书籍并生成回答...")
    try:
        answer = await answer_running_question(question)
        await context.send_chunks(answer)
    except Exception as exc:
        await context.send(f"回答跑步问题失败：{exc}")


async def _record_feeling(context: CommandContext, argument: str) -> None:
    result = await record_feeling(argument)
    await context.send(result)


async def _list_feelings(context: CommandContext, _: str) -> None:
    await context.send(list_recent_feelings())


async def _auto_check(context: CommandContext, _: str) -> None:
    await context.send("正在检查是否有新的 COROS 运动...")
    result = await check_and_send_coros_auto_report(
        context.client,
        notify_no_change=True,
        send_on_first_run=True,
    )
    if (
        result.startswith("COROS auto report")
        and "no new activity" not in result
        and not result.endswith("sent.")
    ):
        await context.send(result)


async def _auto_report(context: CommandContext, _: str) -> None:
    await context.send("正在对最新一条 COROS 运动生成自动报告...")
    result = await check_and_send_coros_auto_report(
        context.client,
        send_on_first_run=True,
        force_send=True,
    )
    if result.startswith("COROS auto report") and not result.endswith("sent."):
        await context.send(result)


def build_coros_capability() -> Capability:
    return Capability(
        name="coros-report",
        description="读取 COROS 运动数据，生成训练报告，回答跑步问题，并记录主观感受。",
        channel_env_name="DISCORD_RUNNING_CHANNEL_ID",
        text_commands=(
            TextCommand("coros", "生成 COROS 运动报告", _coros_report),
            TextCommand("coros-tools", "列出 COROS MCP 工具", _coros_tools),
            TextCommand("running", "基于跑步书籍回答训练问题", _running_ask),
            TextCommand(
                "feel",
                "记录运动后的主观感受",
                _record_feeling,
                aliases=("feeling",),
            ),
            TextCommand(
                "feelings",
                "查看最近记录的运动感受",
                _list_feelings,
                aliases=("feeling-list",),
            ),
            TextCommand(
                "coros-auto-check", "手动检查是否有新的 COROS 运动", _auto_check
            ),
            TextCommand(
                "coros-auto-report", "强制对最新一条 COROS 运动生成报告", _auto_report
            ),
        ),
        startup_handlers=(register_coros_auto_report,),
    )
