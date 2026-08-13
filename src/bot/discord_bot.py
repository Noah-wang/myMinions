import os

import discord
from discord import app_commands

from agent import generate_coros_report, list_available_coros_tools
from knowledge import answer_running_question


# 拿本地变量
def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing. Add it to .env.")
    return value


# 发送消息
async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
    chunk_size = 1800
    for start in range(0, len(text), chunk_size):
        await channel.send(text[start : start + chunk_size])


# 拿设定的频道id
def _configured_channel_id() -> str | None:
    return os.getenv("DISCORD_RUNNING_CHANNEL_ID")


# 校验当前频道id是否为设定的频道id
def _is_allowed_channel(channel_id: int) -> bool:
    return str(channel_id) == _configured_channel_id()


# 生成并发送coros报告
async def _generate_and_send_report(
    channel: discord.abc.Messageable, request: str
) -> None:
    await channel.send("正在读取 COROS 数据并生成报告...")
    try:
        report = await generate_coros_report(request)
        await _send_chunks(channel, report)
    except Exception as exc:
        await channel.send(f"生成 COROS 报告失败：{exc}")


# 回答跑步问题
async def _answer_running_question(
    channel: discord.abc.Messageable, question: str
) -> None:
    await channel.send("正在检索跑步书籍并生成回答...")
    try:
        answer = await answer_running_question(question)
        await _send_chunks(channel, answer)
    except Exception as exc:
        await channel.send(f"回答跑步问题失败：{exc}")


# 创建discord客户端
def create_discord_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    # 机器人上线
    @client.event
    async def on_ready() -> None:
        await tree.sync()
        print(f"Logged in as {client.user}")

    # coros命令
    @tree.command(name="coros", description="生成 COROS 运动报告")
    @app_commands.describe(
        request="你想分析什么，例如：最近一次跑步、明天是否适合高强度"
    )
    async def coros_command(
        interaction: discord.Interaction, request: str = ""
    ) -> None:
        if interaction.channel_id is None or not _is_allowed_channel(
            interaction.channel_id
        ):
            await interaction.response.send_message(
                "这个命令只能在指定频道使用。", ephemeral=True
            )
            return

        if not request:
            request = "分析我最近一次运动，重点看配速、心率、恢复和下一次训练建议。"

        await interaction.response.send_message("收到，开始生成 COROS 运动报告。")
        if interaction.channel is not None:
            await _generate_and_send_report(interaction.channel, request)

    # coros工具命令
    @tree.command(name="coros-tools", description="列出 COROS MCP 当前提供的工具")
    async def coros_tools_command(interaction: discord.Interaction) -> None:
        if interaction.channel_id is None or not _is_allowed_channel(
            interaction.channel_id
        ):
            await interaction.response.send_message(
                "这个命令只能在指定频道使用。", ephemeral=True
            )
            return

        await interaction.response.send_message("正在读取 COROS MCP 工具列表...")
        try:
            tools = await list_available_coros_tools()
            if interaction.channel is not None:
                await _send_chunks(interaction.channel, tools)
        except Exception as exc:
            if interaction.channel is not None:
                await interaction.channel.send(f"读取 COROS 工具失败：{exc}")

    # 跑步书籍回答命令
    @tree.command(name="running-ask", description="基于已导入跑步书籍回答训练问题")
    @app_commands.describe(question="你的跑步训练问题")
    async def running_ask_command(
        interaction: discord.Interaction, question: str
    ) -> None:
        if interaction.channel_id is None or not _is_allowed_channel(
            interaction.channel_id
        ):
            await interaction.response.send_message(
                "这个命令只能在指定频道使用。", ephemeral=True
            )
            return

        await interaction.response.send_message("收到，开始检索跑步书籍。")
        if interaction.channel is not None:
            await _answer_running_question(interaction.channel, question)

    # 监听消息
    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        if not _is_allowed_channel(message.channel.id):
            return

        content = message.content.strip()
        if content == "!coros-tools":
            await message.channel.send("正在读取 COROS MCP 工具列表...")
            try:
                tools = await list_available_coros_tools()
                await _send_chunks(message.channel, tools)
            except Exception as exc:
                await message.channel.send(f"读取 COROS 工具失败：{exc}")
            return

        if content.startswith("!coros"):
            request = content.removeprefix("!coros").strip()
            if not request:
                request = "分析我最近一次运动，重点看配速、心率、恢复和下一次训练建议。"

            await _generate_and_send_report(message.channel, request)

        if content.startswith("!running"):
            question = content.removeprefix("!running").strip()
            if not question:
                await message.channel.send("请在 `!running` 后面写你的跑步训练问题。")
                return

            await _answer_running_question(message.channel, question)

    return client


def run_discord_bot() -> None:
    token = _required_env("DISCORD_BOT_TOKEN")
    client = create_discord_client()
    client.run(token)
