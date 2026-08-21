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
from urllib.parse import quote, unquote, urlparse

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
# 公开入口只读。running-video 和 feel 是纯写操作，直接不在白名单里；
# kitchen 读写混在一个命令里，由 orchestrator 按动作拦截。
WEB_COMMANDS = (
    "coros",
    "coros-tools",
    "coros-list",
    "coros-activity",
    "coros-pb",
    "running",
    "feelings",
    "kitchen",
    "photo",
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
    "后厨助手": "我今天根据库存能做什么？",
}

SAMPLE_ACTIONS = (
    {
        "title": "查最近 90 天运动记录",
        "description": "",
        "prompt": "列出我最近 90 天的运动记录",
        "mode": "web",
    },
    {
        "title": "查最近一次训练报告",
        "description": "",
        "prompt": "我今天这次训练怎么样？下一次应该怎么练？",
        "mode": "web",
    },
    {
        "title": "查个人 PB",
        "description": "",
        "prompt": "查我的个人 PB",
        "mode": "web",
    },
    {
        "title": "查洛杉矶马拉松照片",
        "description": "",
        "prompt": "查洛杉矶马拉松照片",
        "mode": "web",
    },
    {
        "title": "查成绩瓶颈",
        "description": "",
        "prompt": "我现在半马 1:40，全马 4:30，想提高全马成绩，应该加强哪部分训练？",
        "mode": "web",
    },
    {
        "title": "查今天能做什么菜",
        "description": "",
        "prompt": "我今天根据库存能做什么？",
        "mode": "web",
    },
    {
        "title": "查快过期食材",
        "description": "",
        "prompt": "查一下快过期食材",
        "mode": "web",
    },
)


def _route_prompt(prompt: str) -> str:
    text = prompt.lower()
    if any(term in prompt for term in ("照片", "相册", "图片")) or "photo" in text:
        return "photo"
    if any(term in text for term in ("pb", "personal best")) or any(
        term in prompt for term in ("个人最好", "最好成绩", "最好记录")
    ):
        return "pb"
    if any(term in prompt for term in ("运动记录", "历史运动", "记录列表")) or "activity" in text:
        return "activity-list"
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
                "## 今天可以优先做\n"
                "> 如果冰箱里有番茄、鸡蛋、牛肉或叶菜，优先处理保质期短的蔬菜和熟食。\n\n"
                "## 推荐菜\n"
                "- 番茄鸡蛋面：消耗番茄、鸡蛋，适合快速晚饭。\n"
                "- 青菜牛肉盖饭：优先消耗叶菜，牛肉按剩余重量调整。\n"
                "- 洋葱番茄牛腩：如果牛腩库存足够，可以直接生成采购缺口。\n\n"
                "Discord 里可以继续发「牛腩 1000g」「番茄 4 个」，我会记录库存、估算保质期，"
                "再根据库存提醒你该先吃什么。"
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
    if route == "activity-list":
        return DemoResponse(
            message=(
                "查到最近 90 天的 COROS 运动记录，共 3 条。\n\n"
                "```text\n"
                "1. 2026-08-20 | Indoor Run | 10.00 km | 39:22\n"
                "2. 2026-08-12 | Indoor Run | 8.01 km | 39:22\n"
                "3. 2026-07-28 | Outdoor Run | 6.20 km | 34:10\n"
                "```\n\n"
                "你可以继续说：`分析第 1 条运动记录`。真实模式下会读取所选记录详情，"
                "再生成对应报告。"
            ),
            capability="运动记录浏览器",
            confidence=0.92,
            tools=("COROS MCP", "运动摘要缓存"),
            graph_steps=("查询运动摘要", "展示列表", "等待选择", "按需读取详情"),
            citations=(),
            memory=("last_activity_list：覆盖式短期选择缓存",),
        )
    if route == "pb":
        return DemoResponse(
            message=(
                "你的 COROS 自动 PB：\n\n"
                "| 项目 | 成绩 | 日期 | 来源 |\n"
                "|---|---:|---|---|\n"
                "| 1 公里 | - | - | - |\n"
                "| 3 公里 | - | - | - |\n"
                "| 5 公里 | - | - | - |\n"
                "| 10 公里 | 39:22 | 2026-08-12 | COROS 自动检测 |\n"
                "| 半马 | - | - | - |\n"
                "| 全马 | - | - | - |\n\n"
                "PB 只能由 COROS 运动详情自动更新，不能通过聊天手动修改。"
            ),
            capability="COROS 自动 PB",
            confidence=0.92,
            tools=("COROS MCP", "长期记忆"),
            graph_steps=("读取 PB 记忆", "返回只读表格"),
            citations=(),
            memory=("personal_bests：只由 COROS 详情自动写入",),
        )
    if route == "photo":
        return DemoResponse(
            message=(
                "找到 1 组照片：\n"
                "- 洛杉矶马拉松 · 2026-03 · 成绩 4:30:48：3 张\n\n"
                "真实模式下这里会直接显示照片缩略图；网页入口只允许查看，不允许保存或修改照片库。"
            ),
            capability="照片记忆",
            confidence=0.91,
            tools=("照片记忆", "只读媒体展示"),
            graph_steps=("检索照片分组", "生成图片链接", "展示只读图片"),
            citations=(),
            memory=("photo-memory：Discord 写入，Web 只读",),
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
                "sample_actions": SAMPLE_ACTIONS,
            }
            self._send(*_json_response(payload), include_body=include_body)
            return
        if parsed.path == "/api/tech":
            self._send(*_json_response(_tech_payload()), include_body=include_body)
            return
        if parsed.path == "/api/showcase":
            self._send(*_json_response(_showcase_payload()), include_body=include_body)
            return
        if parsed.path == "/api/data":
            self._send(*_json_response(_data_payload()), include_body=include_body)
            return
        if parsed.path == "/data":
            self._serve_static("/data.html", include_body=include_body)
            return
        if parsed.path == "/tech":
            self._serve_static("/tech.html", include_body=include_body)
            return
        if parsed.path.startswith("/media/photo-memory/"):
            self._serve_photo_media(parsed.path, include_body=include_body)
            return
        if parsed.path.startswith("/media/coros-route-maps/"):
            self._serve_route_map_media(parsed.path, include_body=include_body)
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

    def _serve_photo_media(self, path: str, include_body: bool = True) -> None:
        media_root = (ROOT_DIR / "data" / "media" / "photo-memory").resolve()
        safe_path = unquote(path.removeprefix("/media/photo-memory/").lstrip("/"))
        file_path = (media_root / safe_path).resolve()
        if not str(file_path).startswith(str(media_root)):
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
        self._send(
            HTTPStatus.OK.value,
            file_path.read_bytes(),
            _content_type(file_path),
            include_body=include_body,
        )

    def _serve_route_map_media(self, path: str, include_body: bool = True) -> None:
        media_root = (ROOT_DIR / "data" / "coros-report" / "route-maps").resolve()
        safe_path = unquote(path.removeprefix("/media/coros-route-maps/").lstrip("/"))
        file_path = (media_root / safe_path).resolve()
        if not str(file_path).startswith(str(media_root)):
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
        self._send(
            HTTPStatus.OK.value,
            file_path.read_bytes(),
            _content_type(file_path),
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
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".ico": "image/x-icon",
    }.get(suffix, "application/octet-stream")


def _read_json(path: Path, fallback: object) -> object:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _showcase_payload() -> dict[str, Any]:
    photos = _showcase_photos()
    recipes = _showcase_recipes()
    shopping = _showcase_shopping()
    personal_bests = _showcase_personal_bests()
    fit_files = list((ROOT_DIR / "data" / "coros-report" / "fit-files").glob("**/*.fit"))
    route_maps = list((ROOT_DIR / "data" / "coros-report" / "route-maps").glob("*.png"))

    return {
        "summary": {
            "photo_groups": len(photos),
            "recipes": len(recipes),
            "shopping_items": len(shopping),
            "fit_files": len(fit_files),
            "route_maps": len(route_maps),
        },
        "sections": [
            {
                "key": "photos",
                "title": "照片记忆",
                "description": "Discord 写入，网页只读展示。",
                "items": photos,
            },
            {
                "key": "running",
                "title": "运动数据",
                "description": "COROS MCP、PB、FIT 归档与路线图。",
                "items": [
                    {
                        "title": "COROS FIT 原始文件",
                        "meta": f"已归档 {len(fit_files)} 个 FIT 文件",
                        "prompt": "列出我最近 90 天的运动记录",
                    },
                    {
                        "title": "路线图素材",
                        "meta": f"已生成 {len(route_maps)} 张路线图",
                        "prompt": "查我的个人 PB",
                    },
                    *personal_bests,
                ],
            },
            {
                "key": "kitchen",
                "title": "后厨数据",
                "description": "B 站菜谱、购物清单、库存建议。",
                "items": [*recipes, *shopping],
            },
        ],
    }


def _showcase_photos() -> list[dict[str, str]]:
    path = ROOT_DIR / "data" / "photo-memory" / "photos.json"
    records = _read_json(path, [])
    if not isinstance(records, list):
        return []

    items: list[dict[str, str]] = []
    media_root = ROOT_DIR / "data" / "media" / "photo-memory"
    for record in records:
        if not isinstance(record, dict):
            continue
        files = record.get("files")
        image_url = ""
        if isinstance(files, list) and files:
            first = files[0]
            if isinstance(first, dict) and first.get("path"):
                file_path = ROOT_DIR / str(first["path"])
                try:
                    image_url = "/media/photo-memory/" + quote(
                        file_path.relative_to(media_root).as_posix(),
                        safe="/",
                    )
                except ValueError:
                    image_url = ""
        event = str(record.get("event") or "未命名照片")
        race_date = str(record.get("race_date") or "日期未补充")
        result = str(record.get("result") or "成绩未补充")
        photo_count = len(files) if isinstance(files, list) else 0
        items.append(
            {
                "title": event,
                "meta": f"{race_date} · {result} · {photo_count} 张",
                "prompt": f"查{event}照片",
                "image": image_url,
            }
        )
    return items


def _showcase_recipes() -> list[dict[str, str]]:
    records = _read_json(ROOT_DIR / "data" / "kitchen-assistant" / "recipes.json", [])
    if not isinstance(records, list):
        return []
    items: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("dish_name") or "未命名菜谱")
        source = str(record.get("source") or "无来源")
        ingredients = record.get("ingredients")
        count = len(ingredients) if isinstance(ingredients, list) else 0
        items.append(
            {
                "title": f"菜谱：{name}",
                "meta": f"{source} · {count} 个食材",
                "prompt": "我今天根据库存能做什么？",
            }
        )
    return items


def _showcase_shopping() -> list[dict[str, str]]:
    records = _read_json(
        ROOT_DIR / "data" / "kitchen-assistant" / "shopping_list.json",
        [],
    )
    if not isinstance(records, list):
        return []
    items: list[dict[str, str]] = []
    pending = [item for item in records if isinstance(item, dict) and item.get("status") == "pending"]
    for record in pending[:12]:
        name = str(record.get("name") or "未命名食材")
        amount = str(record.get("amount") or "数量未知")
        recipe = str(record.get("source_recipe") or "未关联菜谱")
        items.append(
            {
                "title": f"待采购：{name}",
                "meta": f"{amount} · {recipe}",
                "prompt": "查一下采购清单",
            }
        )
    if len(pending) > 12:
        items.append(
            {
                "title": "更多待采购食材",
                "meta": f"还有 {len(pending) - 12} 项没有展示",
                "prompt": "查一下采购清单",
            }
        )
    return items


def _showcase_personal_bests() -> list[dict[str, str]]:
    memory = _read_json(ROOT_DIR / "data" / "memory.json", {})
    if not isinstance(memory, dict):
        return []
    agents = memory.get("agents")
    if not isinstance(agents, dict):
        return []
    coros = agents.get("coros-report")
    if not isinstance(coros, dict):
        return []
    personal_bests = coros.get("personal_bests")
    if not isinstance(personal_bests, dict):
        return []

    labels = {
        "1k": "1 公里 PB",
        "3k": "3 公里 PB",
        "5k": "5 公里 PB",
        "10k": "10 公里 PB",
        "half_marathon": "半马 PB",
        "marathon": "全马 PB",
    }
    items = []
    for key, label in labels.items():
        record = personal_bests.get(key)
        if not isinstance(record, dict):
            continue
        items.append(
            {
                "title": label,
                "meta": f"{record.get('time', '-')} · {record.get('date') or '日期未知'}",
                "prompt": "查我的个人 PB",
            }
        )
    return items


def _data_payload() -> dict[str, Any]:
    memory = _read_json(ROOT_DIR / "data" / "memory.json", {})
    coros_memory: dict[str, Any] = {}
    if isinstance(memory, dict):
        agents = memory.get("agents")
        if isinstance(agents, dict) and isinstance(agents.get("coros-report"), dict):
            coros_memory = agents["coros-report"]

    sections = [
        _data_profile_section(coros_memory),
        _data_personal_bests_section(coros_memory),
        _data_photos_section(),
        _data_rag_section(),
        _data_coros_archive_section(coros_memory),
        _data_kitchen_section(),
    ]
    counts = {
        "sections": len(sections),
        "photos": sum(len(item.get("images", [])) for item in sections[2]["items"]),
        "knowledge_chunks": _json_count(ROOT_DIR / "data" / "knowledge" / "coros-report" / "chunks.json"),
        "fit_files": len(list((ROOT_DIR / "data" / "coros-report" / "fit-files").glob("**/*.fit"))),
        "recipes": _json_count(ROOT_DIR / "data" / "kitchen-assistant" / "recipes.json"),
    }
    return {
        "summary": [
            {"label": "数据模块", "value": str(counts["sections"]), "detail": "个人只读资料库"},
            {"label": "照片", "value": str(counts["photos"]), "detail": "Discord 写入，网页查看"},
            {"label": "知识块", "value": str(counts["knowledge_chunks"]), "detail": "书籍与视频 RAG"},
            {"label": "FIT 文件", "value": str(counts["fit_files"]), "detail": "COROS 原始运动归档"},
            {"label": "菜谱", "value": str(counts["recipes"]), "detail": "B 站字幕提取"},
        ],
        "sections": sections,
    }


def _data_profile_section(coros_memory: dict[str, Any]) -> dict[str, Any]:
    profile = coros_memory.get("athlete_profile")
    items: list[dict[str, Any]] = []
    if isinstance(profile, dict):
        current_times = profile.get("current_times")
        if isinstance(current_times, dict) and current_times:
            facts = [
                {"label": _race_label(key), "value": str(value)}
                for key, value in current_times.items()
            ]
            items.append(
                {
                    "title": "当前能力画像",
                    "meta": "长期记忆 · 用户确认过的稳定信息",
                    "description": "用于训练计划、成绩瓶颈分析和追问判断。",
                    "facts": facts,
                    "prompt": "根据我的当前能力，制定一份训练计划",
                }
            )
        goals = profile.get("goals")
        if isinstance(goals, list) and goals:
            seen_goals: set[tuple[str, str, str]] = set()
            for goal in goals:
                if not isinstance(goal, dict):
                    continue
                goal_key = (
                    str(goal.get("distance") or "race"),
                    str(goal.get("target_time") or ""),
                    str(goal.get("target_date") or ""),
                )
                if goal_key in seen_goals:
                    continue
                seen_goals.add(goal_key)
                target = goal.get("target_time") or "目标成绩未填写"
                target_date = goal.get("target_date") or "目标日期未填写"
                items.append(
                    {
                        "title": f"{_race_label(str(goal.get('distance') or 'race'))}目标",
                        "meta": f"{target} · {target_date}",
                        "description": "目标信息会影响训练周期、长距离安排和强度比例。",
                        "prompt": "根据我的目标，帮我安排接下来的训练周期",
                    }
                )

    if not items:
        items.append(
            {
                "title": "运动画像待补充",
                "meta": "暂无稳定画像",
                "description": "在 Discord 或网页对话里补充年龄、身高体重、近期周跑量、目标比赛后，会进入长期记忆。",
                "prompt": "帮我建立跑步长期画像",
            }
        )

    return {
        "key": "profile",
        "title": "运动画像",
        "description": "长期稳定信息，给训练计划和报告提供背景。",
        "items": items,
    }


def _data_personal_bests_section(coros_memory: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "1k": "1 公里",
        "3k": "3 公里",
        "5k": "5 公里",
        "10k": "10 公里",
        "half_marathon": "半马",
        "marathon": "全马",
    }
    personal_bests = coros_memory.get("personal_bests")
    if not isinstance(personal_bests, dict):
        personal_bests = {}

    items = []
    for key, label in labels.items():
        record = personal_bests.get(key)
        if isinstance(record, dict):
            value = str(record.get("time") or "-")
            date = str(record.get("date") or "日期未知")
            source = str(record.get("source") or "COROS 自动识别")
            items.append(
                {
                    "title": label,
                    "meta": f"{value} · {date}",
                    "description": source,
                    "state": "ready",
                    "facts": [
                        {"label": "成绩", "value": value},
                        {"label": "日期", "value": date},
                    ],
                    "prompt": "查我的个人 PB",
                }
            )
        else:
            items.append(
                {
                    "title": label,
                    "meta": "尚未自动识别",
                    "description": "PB 只能由 COROS 运动详情自动更新，网页和聊天都不能手动改。",
                    "state": "empty",
                    "prompt": "查我的个人 PB",
                }
            )

    return {
        "key": "personal-bests",
        "title": "个人 PB",
        "description": "只读永久记忆；检测到更好成绩时自动覆盖。",
        "items": items,
    }


def _data_photos_section() -> dict[str, Any]:
    records = _read_json(ROOT_DIR / "data" / "photo-memory" / "photos.json", [])
    media_root = ROOT_DIR / "data" / "media" / "photo-memory"
    items: list[dict[str, Any]] = []
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            files = record.get("files")
            image_urls = []
            if isinstance(files, list):
                for file_record in files:
                    if not isinstance(file_record, dict) or not file_record.get("path"):
                        continue
                    file_path = ROOT_DIR / str(file_record["path"])
                    try:
                        image_urls.append(
                            "/media/photo-memory/"
                            + quote(file_path.relative_to(media_root).as_posix(), safe="/")
                        )
                    except ValueError:
                        continue
            event = str(record.get("event") or "未命名照片")
            race_date = str(record.get("race_date") or "日期未补充")
            result = str(record.get("result") or "成绩未补充")
            items.append(
                {
                    "title": event,
                    "meta": f"{race_date} · {result} · {len(image_urls)} 张",
                    "description": str(record.get("notes") or "从 Discord 上传并归档的照片记忆。"),
                    "images": image_urls,
                    "facts": [
                        {"label": "比赛日期", "value": race_date},
                        {"label": "成绩", "value": result},
                    ],
                    "prompt": f"查{event}照片",
                }
            )

    return {
        "key": "photos",
        "title": "照片记忆",
        "description": "比赛照片、日期、成绩和说明。写入只在 Discord 开放。",
        "items": items,
    }


def _data_rag_section() -> dict[str, Any]:
    base = ROOT_DIR / "data" / "knowledge" / "coros-report"
    build_info = _read_json(base / "build_info.json", {})
    embeddings = _read_json(base / "embeddings.json", {})
    chunks_count = _json_count(base / "chunks.json")
    items: list[dict[str, Any]] = []

    for file_path in sorted((base / "books").glob("*.pdf")):
        items.append(
            {
                "title": file_path.name,
                "meta": f"书籍 · {_file_size(file_path)}",
                "description": "训练问答会从这类资料中检索原文，再把引用放进回答。",
                "prompt": "根据跑步书籍回答我的训练问题",
            }
        )

    for file_path in sorted((base / "videos").glob("*.md")):
        items.append(
            {
                "title": file_path.stem,
                "meta": f"视频字幕知识 · {_file_size(file_path)}",
                "description": "长视频字幕会被切分并进入同一个跑步知识库。",
                "prompt": "根据已导入的视频知识给我训练建议",
            }
        )

    if isinstance(build_info, dict):
        config = build_info.get("config")
        chunk_size = "-"
        overlap = "-"
        if isinstance(config, dict):
            chunk_size = str(config.get("chunk_size") or "-")
            overlap = str(config.get("chunk_overlap") or "-")
        items.append(
            {
                "title": "RAG 切分索引",
                "meta": f"{chunks_count} 个 chunk · {build_info.get('built_at', '构建时间未知')}",
                "description": "保存书籍和视频的切分结果，用于检索候选原文。",
                "facts": [
                    {"label": "chunk_size", "value": chunk_size},
                    {"label": "overlap", "value": overlap},
                ],
                "prompt": "解释一下我的 RAG 知识库里有什么",
            }
        )

    if isinstance(embeddings, dict):
        items.append(
            {
                "title": "Embedding 向量库",
                "meta": f"{embeddings.get('model', '模型未知')} · {embeddings.get('chunk_count', chunks_count)} 个主块",
                "description": "先用向量找相近知识块，再交给 LLM 生成答案和引用。",
                "facts": [
                    {"label": "child vectors", "value": str(embeddings.get("child_count") or "-")},
                    {"label": "model", "value": str(embeddings.get("model") or "-")},
                ],
                "prompt": "我的 RAG 是怎么检索答案的？",
            }
        )

    return {
        "key": "rag",
        "title": "RAG 知识库",
        "description": "跑步书籍、视频字幕、chunk 和 embedding。",
        "items": items,
    }


def _data_coros_archive_section(coros_memory: dict[str, Any]) -> dict[str, Any]:
    fit_files = sorted((ROOT_DIR / "data" / "coros-report" / "fit-files").glob("**/*.fit"))
    route_root = ROOT_DIR / "data" / "coros-report" / "route-maps"
    route_maps = sorted(route_root.glob("*.png"))
    items: list[dict[str, Any]] = []

    latest = coros_memory.get("latest_reported_activity")
    if isinstance(latest, dict):
        title = str(latest.get("name") or latest.get("sportType") or "最近已报告运动")
        distance = latest.get("distance")
        distance_text = f"{float(distance) / 1000:.2f} km" if isinstance(distance, (int, float)) else "-"
        items.append(
            {
                "title": title,
                "meta": f"{latest.get('startTime') or latest.get('date') or '日期未知'} · {distance_text}",
                "description": "自动报告会用它判断最近运动是否已经发送过。",
                "prompt": "根据最近一次运动生成报告",
            }
        )

    items.append(
        {
            "title": "FIT 原始文件归档",
            "meta": f"{len(fit_files)} 个文件 · {_total_size(fit_files)}",
            "description": "每天同步 COROS 原始运动记录，后续可用于轨迹、分段和地图生成。",
            "facts": [
                {"label": "最近文件", "value": fit_files[-1].name if fit_files else "暂无"},
            ],
            "prompt": "列出我最近 90 天的运动记录",
        }
    )

    for file_path in route_maps:
        items.append(
            {
                "title": file_path.stem,
                "meta": f"路线图 · {_file_size(file_path)}",
                "description": "室外跑步有 GPS 时自动生成路线图。",
                "images": [
                    "/media/coros-route-maps/"
                    + quote(file_path.relative_to(route_root).as_posix(), safe="/")
                ],
                "prompt": "查看这次室外跑步路线",
            }
        )

    return {
        "key": "coros",
        "title": "COROS 数据",
        "description": "运动记录、FIT 原始文件和路线图素材。",
        "items": items,
    }


def _data_kitchen_section() -> dict[str, Any]:
    recipes = _read_json(ROOT_DIR / "data" / "kitchen-assistant" / "recipes.json", [])
    shopping = _read_json(ROOT_DIR / "data" / "kitchen-assistant" / "shopping_list.json", [])
    items: list[dict[str, Any]] = []

    if isinstance(recipes, list):
        for recipe in recipes:
            if not isinstance(recipe, dict):
                continue
            ingredients = recipe.get("ingredients")
            seasonings = recipe.get("seasonings")
            ingredient_count = len(ingredients) if isinstance(ingredients, list) else 0
            seasoning_count = len(seasonings) if isinstance(seasonings, list) else 0
            items.append(
                {
                    "title": str(recipe.get("dish_name") or "未命名菜谱"),
                    "meta": f"{recipe.get('source') or '无来源'} · {ingredient_count} 个食材",
                    "description": str(recipe.get("summary") or "由 B 站字幕提取出的菜谱。"),
                    "facts": [
                        {"label": "食材", "value": str(ingredient_count)},
                        {"label": "调味", "value": str(seasoning_count)},
                    ],
                    "prompt": "我今天根据库存能做什么？",
                }
            )

    pending = []
    if isinstance(shopping, list):
        pending = [item for item in shopping if isinstance(item, dict) and item.get("status") == "pending"]
    if pending:
        preview = "、".join(str(item.get("name") or "未命名") for item in pending[:6])
        items.append(
            {
                "title": "待采购清单",
                "meta": f"{len(pending)} 项待买",
                "description": preview,
                "prompt": "查一下采购清单",
            }
        )

    return {
        "key": "kitchen",
        "title": "后厨数据",
        "description": "菜谱、采购清单和库存相关数据。",
        "items": items,
    }


def _json_count(path: Path) -> int:
    data = _read_json(path, [])
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return len(data["items"])
        value = data.get("chunk_count")
        if isinstance(value, int):
            return value
    return 0


def _race_label(key: str) -> str:
    return {
        "1k": "1 公里",
        "3k": "3 公里",
        "5k": "5 公里",
        "10k": "10 公里",
        "half_marathon": "半马",
        "marathon": "全马",
        "race": "比赛",
    }.get(key, key)


def _file_size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "大小未知"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _total_size(paths: list[Path]) -> str:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    if total >= 1024 * 1024:
        return f"{total / 1024 / 1024:.1f} MB"
    if total >= 1024:
        return f"{total / 1024:.1f} KB"
    return f"{total} B"


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
        ROOT_DIR / "agents" / "photo-memory" / "agent",
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
    if command_name == "photo":
        return "照片记忆"
    if command_name in {
        "coros",
        "coros-tools",
        "coros-list",
        "coros-activity",
        "coros-pb",
        "running",
        "running-video",
        "feel",
        "feelings",
    }:
        return "跑步教练"
    return command_name


def _tools_for_command(command_name: str) -> tuple[str, ...]:
    if command_name == "kitchen":
        return ("B 站字幕抓取", "菜谱提取器", "库存与采购清单")
    if command_name == "running":
        return ("RAG 检索", "Embedding", "长期记忆", "DeepSeek")
    if command_name == "running-video":
        return ("B 站字幕抓取", "知识库切分", "Embedding")
    if command_name == "photo":
        return ("照片记忆", "只读媒体展示")
    if command_name in {"coros-tools", "coros-list", "coros-activity", "coros-pb"}:
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
    if command_name == "coros-list":
        return ("自然语言路由", "查询运动摘要", "缓存可选列表")
    if command_name == "coros-activity":
        return ("自然语言路由", "读取所选记录", "拉取详情", "生成报告")
    if command_name == "coros-pb":
        return ("自然语言路由", "读取 PB 记忆", "返回只读表格")
    if command_name == "photo":
        return ("自然语言路由", "检索照片分组", "展示只读图片")
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
