from src.runtime.capability import Capability, CommandContext, TextCommand

from coros_capability import build_coros_capability
from kitchen_capability import build_kitchen_capability
from photo_capability import build_photo_capability


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability]) -> None:
        """初始化功能注册表。

        解析传入的 capabilities 列表，并为底下的文本指令建立映射，
        以便快速查找指令对应的 Command 实例与所属 Capability 实例。

        Args:
            capabilities: 已加载的功能（Capability）列表。
        """
        self._capabilities = capabilities
        self._commands: dict[str, TextCommand] = {}
        self._command_capabilities: dict[str, Capability] = {}
        for capability in capabilities:
            for command in capability.text_commands:
                for name in command.names:
                    self._commands[name] = command
                    self._command_capabilities[name] = capability

    def describe(self) -> str:
        """生成并返回所有已加载功能及其命令别名的说明文档文本。"""
        lines = ["当前已加载的 capabilities："]
        for capability in self._capabilities:
            lines.append(f"- {capability.name}: {capability.description}")
            for command in capability.text_commands:
                aliases = f" aliases: {', '.join(command.aliases)}" if command.aliases else ""
                lines.append(f"  !{command.name} - {command.description}{aliases}")
        return "\n".join(lines)

    def run_startup_handlers(self, client: object) -> None:
        """在系统启动时，依次执行每个功能模块注册的启动处理器。

        Args:
            client: Discord 客户端实例。
        """
        for capability in self._capabilities:
            for handler in capability.startup_handlers:
                handler(client)

    def channel_env_for_command(self, command_name: str) -> str | None:
        """获取特定指令所对应的专用 Discord 频道的环境变量配置名称。

        Args:
            command_name: 指令名称。
        """
        capability = self._command_capabilities.get(command_name)
        if capability is None:
            return None
        return capability.channel_env_name

    def channel_env_names(self) -> tuple[str, ...]:
        """获取所有已加载能力中定义过的专属频道环境变量名列表（已去重）。"""
        names: list[str] = []
        for capability in self._capabilities:
            if capability.channel_env_name is not None:
                names.append(capability.channel_env_name)
        return tuple(dict.fromkeys(names))

    async def dispatch_command(
        self, context: CommandContext, command_name: str, argument: str = ""
    ) -> bool:
        """根据指令名称，将指令与参数分发给具体对应的命令处理器异步执行。

        Args:
            context: 执行指令所需的上下文（包含 client、channel 以及发送消息的方法）。
            command_name: 待执行的指令名称。
            argument: 指令的附加参数（默认为空）。
        """
        command = self._commands.get(command_name)
        if command is None:
            return False
        await command.handler(context, argument)
        return True

    async def dispatch_text(self, context: CommandContext, content: str) -> bool:
        """解析单条以 `!` 开头的指令文本，并自动分发执行。

        Args:
            context: 指令上下文。
            content: 原始指令文本（如 `!kitchen today`）。
        """
        stripped = content.strip()
        if not stripped.startswith("!"):
            return False

        raw_command = stripped[1:]
        command_name, _, argument = raw_command.partition(" ")
        return await self.dispatch_command(context, command_name, argument.strip())


_registry: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    """获取单例模式的功能注册表实例，默认加载 Coros 报告和厨房助手两大能力。"""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry(
            [
                build_coros_capability(),
                build_kitchen_capability(),
                build_photo_capability(),
            ]
        )
    return _registry
