import os

import discord

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


def create_discord_client() -> discord.Client:
	intents = discord.Intents.default()
	intents.message_content = True
	client = discord.Client(intents=intents)

	@client.event
	async def on_ready() -> None:
		print(f"Logged in as {client.user}")

	@client.event
	async def on_message(message: discord.Message) -> None:
		if message.author.bot:
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

			await message.channel.send("正在读取 COROS 数据并生成报告...")
			try:
				report = await generate_coros_report(request)
				await _send_chunks(message.channel, report)
			except Exception as exc:
				await message.channel.send(f"生成 COROS 报告失败：{exc}")

	return client


def run_discord_bot() -> None:
	token = _required_env("DISCORD_BOT_TOKEN")
	client = create_discord_client()
	client.run(token)
