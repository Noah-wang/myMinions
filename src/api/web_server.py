import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
WEB_COMMANDS = (
    "coros",
    "coros-tools",
    "running",
    "running-video",
    "feel",
    "feelings",
    "kitchen",
)


@dataclass(frozen=True)
class DemoResponse:
    message: str
    capability: str
    confidence: float
    tools: tuple[str, ...]
    graph_steps: tuple[str, ...]
    citations: tuple[str, ...]
    memory: tuple[str, ...]


SAMPLE_PROMPTS = {
    "跑步问答": "我现在半马 1:40，全马 4:30，想提高全马成绩，应该加强哪部分训练？",
    "运动报告": "我今天这次训练怎么样？下一次应该怎么练？",
    "后厨助手": "我想做一个番茄牛腩，把它加入下次采购清单。",
}


def _route_prompt(prompt: str) -> str:
    text = prompt.lower()
    if any(term in prompt for term in ("菜", "采购", "食材", "库存", "过期", "牛腩")):
        return "kitchen"
    if any(term in text for term in ("eval", "评测", "测试", "路由")):
        return "evals"
    return "running"


def _demo_response(prompt: str) -> DemoResponse:
    route = _route_prompt(prompt)
    if route == "kitchen":
        return DemoResponse(
            message=(
                "好，我把番茄牛腩需要的食材加进采购清单了：\n\n"
                "- 牛腩 1000g\n- 番茄 4 个\n- 洋葱 1 个\n- 胡萝卜 2 根\n\n"
                "买回来之后告诉我一声，比如「牛腩 1000g」，我会记进库存并按保质期提醒你，"
                "之后也能根据现有食材推荐今天做什么。"
            ),
            capability="kitchen-assistant",
            confidence=0.91,
            tools=("B 站字幕抓取", "菜谱提取器", "库存与采购清单"),
            graph_steps=("识别意图", "提取菜谱", "更新采购清单", "生成回复"),
            citations=("B 站字幕文本 -> 菜谱草稿",),
            memory=("采购清单：待购买食材", "库存：Demo 示例库存"),
        )
    if route == "evals":
        return DemoResponse(
            message=(
                "已路由到 Evaluation Harness。当前评测覆盖自然语言路由、频道权限隔离、低置信度拒绝、"
                "非法参数拒绝等场景。它的作用是保证新增 capability 后，主 Agent 不会把厨房请求发给跑步 Agent，"
                "也不会在错误频道响应。"
            ),
            capability="评测体系",
            confidence=0.88,
            tools=("评测脚本", "自然语言路由用例集"),
            graph_steps=("加载用例", "运行路由器", "判断预期能力", "输出指标"),
            citations=("evals/datasets/natural_language_routing.json",),
            memory=("质量门禁：最近一次本地路由评测 15/15 通过",),
        )

    return DemoResponse(
        message=(
            "## 临时判断\n"
            "> 你的半马能力明显高于当前全马表现，短板更可能在马拉松专项耐力，而不是整体体能。\n\n"
            "## 为什么这么判断\n"
            "- 半马 1:40 推算的全马成绩应该远好于 4:30，这个差距通常指向专项耐力、配速控制、"
            "补给或长距离后段维持能力。\n\n"
            "## 还需要确认\n"
            "1. 你最近 1-2 个月的周跑量大概是多少？\n"
            "2. 赛前最长一次长距离跑了多少公里？\n"
            "3. 上一次全马后半程具体发生了什么？\n\n"
            "## 现在可以先做什么\n"
            "- 先别急着加量，把大部分训练放回能边跑边说话的轻松配速，每周固定安排一次长距离。"
        ),
        capability="跑步教练",
        confidence=0.94,
        tools=("COROS MCP", "LangGraph", "RAG 检索", "教练 Skill", "长期记忆"),
        graph_steps=("请求路由", "工具规划", "获取上下文", "检索知识库", "生成回答", "质量检查"),
        citations=(
            "《丹尼尔斯经典跑步训练法》p.143：马拉松计划应基于现实的当前能力。",
            "已导入跑步长视频：长距离训练与补给内容作为辅助依据。",
        ),
        memory=("当前成绩：半马 1:40:00", "当前成绩：全马 4:30:00"),
    )


REPORT_PATH = ROOT_DIR / "docs" / "project-iteration-report.md"
RAG_DOC_PATH = ROOT_DIR / "docs" / "rag-pipeline.md"


def _split_report(text: str) -> list[dict[str, Any]]:
    """把迭代报告按 ## / ### 两级拆开。"""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    subsection: dict[str, Any] | None = None
    in_code_block = False

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block

        if in_code_block:
            # 代码块里的 ## 是示例内容，不是章节标题。
            # 文档里引用过 prompt 模板中的「## 引用原文」，不排除就会把章节劈开。
            if current is not None:
                target = subsection["body"] if subsection is not None else current["body"]
                target.append(line)
            continue

        if line.startswith("## "):
            current = {"title": line[3:].strip(), "body": [], "subs": []}
            subsection = None
            sections.append(current)
        elif line.startswith("### ") and current is not None:
            subsection = {"title": line[4:].strip(), "body": []}
            current["subs"].append(subsection)
        elif current is not None:
            target = subsection["body"] if subsection is not None else current["body"]
            target.append(line)

    return sections


def _find_section(sections: list[dict[str, Any]], prefix: str) -> dict[str, Any] | None:
    for section in sections:
        if section["title"].startswith(prefix):
            return section
    return None


def _flatten(section: dict[str, Any] | None) -> str:
    if section is None:
        return ""
    parts = ["\n".join(section["body"]).strip()]
    for sub in section["subs"]:
        parts.append(f"### {sub['title']}\n" + "\n".join(sub["body"]).strip())
    return "\n\n".join(part for part in parts if part).strip()


def _items(section: dict[str, Any] | None) -> list[dict[str, str]]:
    if section is None:
        return []
    return [
        {"title": sub["title"], "body": "\n".join(sub["body"]).strip()}
        for sub in section["subs"]
    ]


def _tech_payload() -> dict[str, Any]:
    """把迭代报告整理成三个 tab 供网页展示。

    文档本身就是按「结构演进 / 核心功能 / 问题与解决」组织的，
    所以这里只做映射，不额外维护一份内容，避免两处不同步。
    """
    if not REPORT_PATH.exists():
        return {"tabs": []}

    sections = _split_report(REPORT_PATH.read_text(encoding="utf-8"))
    structure = _find_section(sections, "2.")
    features = _find_section(sections, "3.")
    problems = _find_section(sections, "4.")
    summary = _find_section(sections, "5.")

    overview: list[dict[str, str]] = []
    if summary is not None:
        overview.append({"title": "能力总结", "body": _flatten(summary)})
    if structure is not None:
        overview.append({"title": "项目结构", "body": _flatten(structure)})

    return {
        "tabs": [
            {"key": "stack", "title": "包含的技术", "items": overview},
            {"key": "path", "title": "升级路径", "items": _items(features)},
            {"key": "problems", "title": "遇到的困难和解决方法", "items": _items(problems)},
            {"key": "rag", "title": "RAG 全流程", "items": _rag_items()},
        ]
    }


def _rag_items() -> list[dict[str, str]]:
    """RAG 全流程文档按 ## 一级标题拆成条目。

    这份文档只有一层标题，每个标题就是流水线里的一个环节，
    所以直接用顶级 section 当条目，不像迭代报告那样取子节。
    """
    if not RAG_DOC_PATH.exists():
        return []

    sections = _split_report(RAG_DOC_PATH.read_text(encoding="utf-8"))
    return [
        {"title": section["title"], "body": "\n".join(section["body"]).strip()}
        for section in sections
        if "\n".join(section["body"]).strip()
    ]


def _json_response(payload: Any, status: HTTPStatus = HTTPStatus.OK) -> tuple[int, bytes, str]:
    return status.value, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


class WebHandler(BaseHTTPRequestHandler):
    server_version = "AgentDeckWeb/0.1"

    def do_GET(self) -> None:
        self._handle_get(include_body=True)

    def do_HEAD(self) -> None:
        self._handle_get(include_body=False)

    def _handle_get(self, include_body: bool) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send(
                *_json_response({"ok": True, "domain": "agent.noahwang.run"}),
                include_body=include_body,
            )
            return
        if parsed.path == "/api/capabilities":
            # 只暴露给前端做空状态提示的示例问题，不再对外列出内部能力名和命令。
            payload = {
                "project": "AgentDeck",
                "sample_prompts": SAMPLE_PROMPTS,
            }
            self._send(*_json_response(payload), include_body=include_body)
            return
        if parsed.path == "/api/tech":
            self._send(*_json_response(_tech_payload()), include_body=include_body)
            return
        if parsed.path == "/tech":
            self._serve_static("/tech.html", include_body=include_body)
            return
        self._serve_static(parsed.path, include_body=include_body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/chat", "/api/chat/stream"}:
            self._send(*_json_response({"error": "未找到接口"}, HTTPStatus.NOT_FOUND))
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8")) if raw else {}
            prompt = str(data.get("message", "")).strip()
            if not prompt:
                self._send(*_json_response({"error": "请输入消息"}, HTTPStatus.BAD_REQUEST))
                return

            conversation_id = self._conversation_id(data)

            if parsed.path == "/api/chat/stream":
                self._stream_chat(prompt, conversation_id)
                return

            if _web_agent_mode() == "real":
                result = asyncio.run(_collect_real_chat(prompt, conversation_id))
                self._send(*_json_response(result))
                return

            response = _demo_response(prompt)
            self._send(*_json_response(asdict(response)))
        except Exception as exc:
            self._send(
                *_json_response(
                    {"error": str(exc) or exc.__class__.__name__},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            )

    def _conversation_id(self, data: dict[str, Any]) -> str:
        raw = str(data.get("session_id", "")).strip()[:64]
        safe = "".join(char for char in raw if char.isalnum() or char in "-_")
        if safe:
            return f"web:{safe}"
        return f"web:{self.client_address[0]}"

    def _stream_chat(self, prompt: str, conversation_id: str) -> None:
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # 必须让连接在流结束后关闭。keep-alive 会让 close_connection=False，
        # 前端的 reader 永远等不到 EOF，一次对话之后就再也发不出消息。
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(payload: dict[str, Any]) -> None:
            body = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            self.wfile.write(body)
            self.wfile.flush()

        try:
            if _web_agent_mode() == "real":
                asyncio.run(_stream_real_chat(prompt, emit, conversation_id))
            else:
                response = _demo_response(prompt)
                emit({"type": "trace", "result": asdict(response)})
                emit({"type": "message", "message": response.message})
            emit({"type": "done"})
        except BrokenPipeError:
            return
        except Exception as exc:
            try:
                emit({"type": "error", "error": str(exc) or exc.__class__.__name__})
            except BrokenPipeError:
                return

    def _serve_static(self, path: str, include_body: bool = True) -> None:
        if path in {"", "/"}:
            file_path = WEB_DIR / "index.html"
        elif path == "/architecture.svg":
            file_path = ROOT_DIR / "docs" / "architecture.svg"
        else:
            safe_path = path.lstrip("/")
            file_path = (WEB_DIR / safe_path).resolve()
            if not str(file_path).startswith(str(WEB_DIR.resolve())):
                self._send(
                    *_json_response({"error": "禁止访问"}, HTTPStatus.FORBIDDEN),
                    include_body=include_body,
                )
                return

        if not file_path.exists() or not file_path.is_file():
            self._send(
                *_json_response({"error": "文件不存在"}, HTTPStatus.NOT_FOUND),
                include_body=include_body,
            )
            return

        content_type = _content_type(file_path)
        self._send(
            HTTPStatus.OK.value,
            file_path.read_bytes(),
            content_type,
            include_body=include_body,
        )

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        include_body: bool = True,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 静态资源文件名没有版本号，长缓存会让部署后的一段时间内用户拿到旧前端，
        # 所以统一要求每次回源校验。
        self.send_header(
            "Cache-Control",
            "no-store" if content_type.startswith("application/json") else "no-cache",
        )
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"web {self.address_string()} {format % args}", flush=True)


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }.get(suffix, "application/octet-stream")


class WebChannel:
    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        conversation_id: str = "web:default",
    ) -> None:
        self.id = -1
        self.conversation_id = conversation_id
        self._emit = emit
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)
        self._emit({"type": "message", "message": content})


def _ensure_agent_paths() -> None:
    paths = (
        ROOT_DIR,
        ROOT_DIR / "agents" / "kitchen-assistant" / "agent",
        ROOT_DIR / "agents" / "coros-report" / "agent",
    )
    for path in paths:
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _web_agent_mode() -> str:
    return os.getenv("WEB_AGENT_MODE", "demo").strip().lower()


def _route_result_payload(route: object | None) -> dict[str, Any]:
    if route is None:
        return {
            "message": "",
            "capability": "未路由",
            "confidence": 0.0,
            "tools": (),
            "graph_steps": (),
            "citations": (),
            "memory": (),
        }

    command_name = getattr(route, "command_name", "unknown")
    return {
        "message": "",
        "capability": _capability_label(str(command_name)),
        "confidence": float(getattr(route, "confidence", 1.0)),
        "tools": _tools_for_command(str(command_name)),
        "graph_steps": _steps_for_command(str(command_name)),
        "citations": (),
        "memory": (),
    }


def _capability_label(command_name: str) -> str:
    if command_name == "kitchen":
        return "后厨助手"
    if command_name in {"coros", "coros-tools", "running", "running-video", "feel", "feelings"}:
        return "跑步教练"
    return command_name


def _tools_for_command(command_name: str) -> tuple[str, ...]:
    if command_name == "kitchen":
        return ("B 站字幕抓取", "菜谱提取器", "库存与采购清单")
    if command_name == "running":
        return ("RAG 检索", "Embedding", "长期记忆", "DeepSeek")
    if command_name == "running-video":
        return ("B 站字幕抓取", "知识库切分", "Embedding")
    if command_name == "coros-tools":
        return ("COROS MCP",)
    return ("COROS MCP", "LangGraph", "RAG 检索", "长期记忆")


def _steps_for_command(command_name: str) -> tuple[str, ...]:
    if command_name == "kitchen":
        return ("自然语言路由", "执行厨房能力", "更新库存/采购数据", "返回结果")
    if command_name == "running":
        return ("自然语言路由", "更新训练画像", "检索知识库", "生成训练建议")
    if command_name == "running-video":
        return ("自然语言路由", "抓取字幕", "切分知识", "写入知识库")
    if command_name == "coros-tools":
        return ("自然语言路由", "连接 COROS MCP", "读取工具列表")
    return ("自然语言路由", "LangGraph 编排", "读取 COROS 数据", "生成报告")


async def _stream_real_chat(
    prompt: str,
    emit: Callable[[dict[str, Any]], None],
    conversation_id: str = "web:default",
) -> None:
    _ensure_agent_paths()
    from src.orchestrator import get_orchestrator

    channel = WebChannel(emit, conversation_id)
    emit({"type": "status", "message": "正在调用真实 Agent..."})
    route = await get_orchestrator().dispatch_web_text(
        None,
        channel,
        prompt,
        WEB_COMMANDS,
    )
    emit({"type": "trace", "result": _route_result_payload(route)})


async def _collect_real_chat(
    prompt: str,
    conversation_id: str = "web:default",
) -> dict[str, Any]:
    messages: list[str] = []

    def emit(payload: dict[str, Any]) -> None:
        if payload.get("type") == "message":
            message = payload.get("message")
            if isinstance(message, str):
                messages.append(message)

    _ensure_agent_paths()
    from src.orchestrator import get_orchestrator

    channel = WebChannel(emit, conversation_id)
    route = await get_orchestrator().dispatch_web_text(
        None,
        channel,
        prompt,
        WEB_COMMANDS,
    )
    result = _route_result_payload(route)
    result["message"] = "\n\n".join(messages)
    return result


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    load_dotenv(ROOT_DIR / ".env")
    _ensure_agent_paths()
    httpd = ThreadingHTTPServer((host, port), WebHandler)
    print(f"AgentDeck 智能体控制台运行中：http://{host}:{port}", flush=True)
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 AgentDeck 智能体控制台。")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
