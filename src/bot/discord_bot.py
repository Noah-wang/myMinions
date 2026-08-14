import os

import discord
from discord import app_commands

from src.registry import get_registry
from src.runtime.capability import CommandContext


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


def _command_context(
    client: discord.Client, channel: discord.abc.Messageable
) -> CommandContext:
    return CommandContext(
        client=client,
        channel=channel,
        send=channel.send,
        send_chunks=lambda text: _send_chunks(channel, text),
    )


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
        get_registry().run_startup_handlers(client)
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
            await get_registry().dispatch_command(
                _command_context(client, interaction.channel),
                "coros",
                request,
            )

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
        if interaction.channel is not None:
            await get_registry().dispatch_command(
                _command_context(client, interaction.channel),
                "coros-tools",
            )

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
            await get_registry().dispatch_command(
                _command_context(client, interaction.channel),
                "running",
                question,
            )

    @tree.command(name="feel", description="记录一次运动后的主观感受")
    @app_commands.describe(note="例如：今天腿很沉，RPE 7，左膝有点紧")
    async def feel_command(interaction: discord.Interaction, note: str) -> None:
        if interaction.channel_id is None or not _is_allowed_channel(
            interaction.channel_id
        ):
            await interaction.response.send_message(
                "这个命令只能在指定频道使用。", ephemeral=True
            )
            return

        await interaction.response.send_message("正在记录你的运动感受。")
        if interaction.channel is not None:
            await get_registry().dispatch_command(
                _command_context(client, interaction.channel),
                "feel",
                note,
            )

    @tree.command(name="feelings", description="查看最近记录的运动感受")
    async def feelings_command(interaction: discord.Interaction) -> None:
        if interaction.channel_id is None or not _is_allowed_channel(
            interaction.channel_id
        ):
            await interaction.response.send_message(
                "这个命令只能在指定频道使用。", ephemeral=True
            )
            return

        await interaction.response.send_message("正在读取最近记录的运动感受。")
        if interaction.channel is not None:
            await get_registry().dispatch_command(
                _command_context(client, interaction.channel),
                "feelings",
            )

    @tree.command(name="capabilities", description="查看当前已加载的能力")
    async def capabilities_command(interaction: discord.Interaction) -> None:
        if interaction.channel_id is None or not _is_allowed_channel(
            interaction.channel_id
        ):
            await interaction.response.send_message(
                "这个命令只能在指定频道使用。", ephemeral=True
            )
            return

        await interaction.response.send_message(get_registry().describe())

    # 监听消息
    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        if not _is_allowed_channel(message.channel.id):
            return

        content = message.content.strip()
        if content == "!capabilities":
            await message.channel.send(get_registry().describe())
            return

        await get_registry().dispatch_text(
            _command_context(client, message.channel),
            content,
        )

    return client


def run_discord_bot() -> None:
    token = _required_env("DISCORD_BOT_TOKEN")
    client = create_discord_client()
    client.run(token)
