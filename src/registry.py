from src.runtime.capability import Capability, CommandContext, TextCommand

from coros_capability import build_coros_capability
from kitchen_capability import build_kitchen_capability


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability]) -> None:
        self._capabilities = capabilities
        self._commands: dict[str, TextCommand] = {}
        for capability in capabilities:
            for command in capability.text_commands:
                for name in command.names:
                    self._commands[name] = command

    def describe(self) -> str:
        lines = ["当前已加载的 capabilities："]
        for capability in self._capabilities:
            lines.append(f"- {capability.name}: {capability.description}")
            for command in capability.text_commands:
                aliases = f" aliases: {', '.join(command.aliases)}" if command.aliases else ""
                lines.append(f"  !{command.name} - {command.description}{aliases}")
        return "\n".join(lines)

    def run_startup_handlers(self, client: object) -> None:
        for capability in self._capabilities:
            for handler in capability.startup_handlers:
                handler(client)

    async def dispatch_command(
        self, context: CommandContext, command_name: str, argument: str = ""
    ) -> bool:
        command = self._commands.get(command_name)
        if command is None:
            return False
        await command.handler(context, argument)
        return True

    async def dispatch_text(self, context: CommandContext, content: str) -> bool:
        stripped = content.strip()
        if not stripped.startswith("!"):
            return False

        raw_command = stripped[1:]
        command_name, _, argument = raw_command.partition(" ")
        return await self.dispatch_command(context, command_name, argument.strip())


_registry: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry(
            [
                build_coros_capability(),
                build_kitchen_capability(),
            ]
        )
    return _registry
