import os

import discord
from discord import app_commands

from agent import generate_coros_report, list_available_coros_tools


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing. Add it to .env.")
    return value


async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
    chunk_size = 1800
    for start in range(0, len(text), chunk_size):
        await channel.send(text[start : start + chunk_size])


def _configured_channel_id() -> str | None:
    return os.getenv("DISCORD_RUNNING_CHANNEL_ID")


def _is_allowed_channel(channel_id: int) -> bool:
    return str(channel_id) == _configured_channel_id()


async def _generate_and_send_report(
    channel: discord.abc.Messageable, request: str
) -> None:
    await channel.send("正在读取 COROS 数据并生成报告...")
    try:
        report = await generate_coros_report(request)
        await _send_chunks(channel, report)
    except Exception as exc:
        await channel.send(f"生成 COROS 报告失败：{exc}")


def create_discord_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @client.event
    async def on_ready() -> None:
        await tree.sync()
        print(f"Logged in as {client.user}")

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

    return client


def run_discord_bot() -> None:
    token = _required_env("DISCORD_BOT_TOKEN")
    client = create_discord_client()
    client.run(token)
