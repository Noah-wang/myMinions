import json
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
MEMORY_PATH = ROOT_DIR / "data" / "memory.json"

DEFAULT_MEMORY: dict[str, Any] = {
    "global": {
        "name": "Noah",
        "language": "zh",
        "preferences": [],
    },
    "agents": {},
}


def load_memory() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        save_memory(deepcopy(DEFAULT_MEMORY))

    with MEMORY_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_memory(memory: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_PATH.open("w", encoding="utf-8") as file:
        json.dump(memory, file, ensure_ascii=False, indent=2)
        file.write("\n")


def get_global_memory() -> dict[str, Any]:
    memory = load_memory()
    global_memory = memory.setdefault("global", {})
    if not isinstance(global_memory, dict):
        return {}
    return global_memory


def get_agent_memory(agent_name: str) -> dict[str, Any]:
    memory = load_memory()
    agents = memory.setdefault("agents", {})
    if not isinstance(agents, dict):
        return {}
    agent_memory = agents.setdefault(agent_name, {})
    if not isinstance(agent_memory, dict):
        return {}
    return agent_memory


def update_agent_memory(agent_name: str, patch: dict[str, Any]) -> dict[str, Any]:
    memory = load_memory()
    agents = memory.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        memory["agents"] = agents

    agent_memory = agents.setdefault(agent_name, {})
    if not isinstance(agent_memory, dict):
        agent_memory = {}
        agents[agent_name] = agent_memory

    agent_memory.update(patch)
    save_memory(memory)
    return agent_memory


def format_memory_for_prompt(agent_name: str) -> str:
    memory = {
        "global": get_global_memory(),
        agent_name: get_agent_memory(agent_name),
    }
    return json.dumps(memory, ensure_ascii=False, indent=2)
