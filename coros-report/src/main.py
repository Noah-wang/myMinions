from dotenv import load_dotenv

from discord_bot import run_discord_bot
from scheduler import start_scheduler


def main() -> None:
	load_dotenv()
	start_scheduler()
	run_discord_bot()


if __name__ == "__main__":
	main()
