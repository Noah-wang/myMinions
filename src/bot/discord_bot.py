import os

import discord
from discord import app_commands

from src.orchestrator import get_orchestrator


# 拿本地变量
def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing. Add it to .env.")
    return value


async def _dispatch_interaction_command(
    interaction: discord.Interaction,
    client: discord.Client,
    command_name: str,
    argument: str,
    start_message: str,
) -> None:
    orchestrator = get_orchestrator()
    if interaction.channel_id is None or not orchestrator.is_allowed_for_command(
        interaction.channel_id,
        command_name,
    ):
        await interaction.response.send_message(
            "这个命令只能在指定频道使用。", ephemeral=True
        )
        return

    await interaction.response.send_message(start_message)
    if interaction.channel is not None:
        await orchestrator.dispatch_command(
            client,
            interaction.channel,
            command_name,
            argument,
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
        get_orchestrator().run_startup_handlers(client)
        print(f"Logged in as {client.user}")

    # coros命令
    @tree.command(name="coros", description="生成 COROS 运动报告")
    @app_commands.describe(
        request="你想分析什么，例如：最近一次跑步、明天是否适合高强度"
    )
    async def coros_command(
        interaction: discord.Interaction, request: str = ""
    ) -> None:
        if not request:
            request = "分析我最近一次运动，重点看配速、心率、恢复和下一次训练建议。"

        await _dispatch_interaction_command(
            interaction,
            client,
            "coros",
            request,
            "收到，开始生成 COROS 运动报告。",
        )

    # coros工具命令
    @tree.command(name="coros-tools", description="列出 COROS MCP 当前提供的工具")
    async def coros_tools_command(interaction: discord.Interaction) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "coros-tools",
            "",
            "正在读取 COROS MCP 工具列表...",
        )

    # 跑步书籍回答命令
    @tree.command(name="running-ask", description="基于已导入跑步书籍回答训练问题")
    @app_commands.describe(question="你的跑步训练问题")
    async def running_ask_command(
        interaction: discord.Interaction, question: str
    ) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "running",
            question,
            "收到，开始检索跑步书籍。",
        )

    @tree.command(name="feel", description="记录一次运动后的主观感受")
    @app_commands.describe(note="例如：今天腿很沉，RPE 7，左膝有点紧")
    async def feel_command(interaction: discord.Interaction, note: str) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "feel",
            note,
            "正在记录你的运动感受。",
        )

    @tree.command(name="feelings", description="查看最近记录的运动感受")
    async def feelings_command(interaction: discord.Interaction) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "feelings",
            "",
            "正在读取最近记录的运动感受。",
        )

    @tree.command(name="capabilities", description="查看当前已加载的能力")
    async def capabilities_command(interaction: discord.Interaction) -> None:
        orchestrator = get_orchestrator()
        if interaction.channel_id is None or not orchestrator.is_capabilities_channel(
            interaction.channel_id
        ):
            await interaction.response.send_message(
                "这个命令只能在指定频道使用。", ephemeral=True
            )
            return

        await interaction.response.send_message(orchestrator.describe_capabilities())

    @tree.command(name="kitchen-add", description="从 B站视频提取并保存菜谱")
    @app_commands.describe(video="B站 BV号或视频链接")
    async def kitchen_add_command(
        interaction: discord.Interaction, video: str
    ) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            f"add {video}",
            "收到，开始抓取 B站字幕并保存菜谱。",
        )

    @tree.command(name="kitchen-recipes", description="查看已保存菜谱")
    async def kitchen_recipes_command(interaction: discord.Interaction) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            "recipes",
            "正在读取已保存菜谱。",
        )

    @tree.command(name="kitchen-plan", description="选择菜谱并加入采购清单")
    @app_commands.describe(recipe="菜谱 ID 或菜名，例如：recipe-1")
    async def kitchen_plan_command(
        interaction: discord.Interaction,
        recipe: str,
    ) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            f"plan {recipe}",
            "正在把菜谱加入采购清单。",
        )

    @tree.command(name="kitchen-shopping", description="查看厨房采购清单")
    async def kitchen_shopping_command(interaction: discord.Interaction) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            "shopping",
            "正在读取采购清单。",
        )

    @tree.command(name="kitchen-remove-shopping", description="从采购清单移除一项")
    @app_commands.describe(item="要移除的食材名，例如：鸡腿")
    async def kitchen_remove_shopping_command(
        interaction: discord.Interaction,
        item: str,
    ) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            f"remove-shopping {item}",
            "正在更新采购清单。",
        )

    @tree.command(name="kitchen-bought", description="记录已采购食材")
    @app_commands.describe(
        name="食材名，例如：鸡腿",
        amount="数量或重量，例如：1000g",
    )
    async def kitchen_bought_command(
        interaction: discord.Interaction,
        name: str,
        amount: str,
    ) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            f"bought {name} {amount}",
            "正在记录采购入库。",
        )

    @tree.command(name="kitchen-use", description="记录已消耗食材")
    @app_commands.describe(
        name="食材名，例如：鸡腿",
        amount="消耗数量，例如：500g",
    )
    async def kitchen_use_command(
        interaction: discord.Interaction,
        name: str,
        amount: str = "",
    ) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            f"use {name} {amount}".strip(),
            "正在记录食材消耗。",
        )

    @tree.command(name="kitchen-pantry", description="查看当前厨房库存")
    async def kitchen_pantry_command(interaction: discord.Interaction) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            "pantry",
            "正在读取厨房库存。",
        )

    @tree.command(name="kitchen-today", description="根据库存推荐今天可以做什么")
    async def kitchen_today_command(interaction: discord.Interaction) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            "today",
            "正在根据库存匹配菜谱。",
        )

    @tree.command(name="kitchen-expiring", description="查看快过期食材")
    async def kitchen_expiring_command(interaction: discord.Interaction) -> None:
        await _dispatch_interaction_command(
            interaction,
            client,
            "kitchen",
            "expiring",
            "正在检查快过期食材。",
        )

    # 监听消息
    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        await get_orchestrator().dispatch_text(
            client,
            message.channel,
            message.content,
        )

    return client


def run_discord_bot() -> None:
    token = _required_env("DISCORD_BOT_TOKEN")
    client = create_discord_client()
    client.run(token)
