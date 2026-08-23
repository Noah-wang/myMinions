# AgentDeck

个人多能力 Agent 运行时，当前主要通过 Discord 和 Web Console 交互。

当前包含三个主要能力：

- `coros-report`：COROS 运动报告、LangGraph 工作流、RAG 训练问答、跑步视频知识导入和长期训练记忆。
- `kitchen-assistant`：从 B 站字幕提取菜谱，管理采购清单、库存、食材消耗和临期提醒。
- `photo-memory`：在 Discord 保存比赛照片，记录比赛日期、成绩等元数据，并按自然语言检索后发送照片。照片操作（新建 / 追加 / 补充信息 / 检索 / 合并分组）由能力内部的意图识别判断，检索也由模型直接挑分组而非关键词匹配，用户说原话即可。

## 架构

![AgentDeck architecture](docs/architecture.svg)

运行时被拆成可复用的主 Agent 层和领域 Capability。Discord 消息从 `src/bot/discord_bot.py` 进入，Web Demo 从 `src/api/web_server.py` 进入，主控层负责自然语言路由、权限隔离和命令分发，具体业务由已注册的 capability 执行。

**自然语言消息直接进主 Agent 循环**（`src/ask.py` + `src/runtime/tools.py`）：模型拿到当前频道可用的工具表，自己决定查什么、执行什么，工具结果回灌之后再决定下一步，最后用自己的话回答。关键在于决定发生在**看到数据之后**——分类器只能在看到任何数据之前猜一次，猜错了也没有第二次机会。

`!coros-pb` 这类显式命令走快速通道，绕过循环直接执行：用户打命令就是要确定的输出。原来的分类器保留在 `MAIN_AGENT_LOOP_ENABLED` 开关后面作为回退。

能力提供两类东西给循环：`read_tools` 是结构化取数，`text_commands` 被包装成执行动作的工具（命令处理器往频道发消息，包装时给它一个 `send` 写进缓冲区的上下文，缓冲区内容作为工具返回值）。权限挂在命令自己身上（`writes` / `read_only_safe`），只读入口构造工具表时直接跳过写工具——模型看不见的工具不可能被调用。

## 多轮对话

`src/runtime/conversation.py` 提供多轮会话历史，送进模型的窗口保留最近 6 轮消息，让 Agent 在追问之后能承接用户的回答，而不是把答案当成一次全新的提问。

会话按来源隔离：Discord 用频道 ID，Web 用前端 `sessionStorage` 生成的 session id。

历史落在 `data/conversations/` 下的 JSONL 日志里，**只追加、不改写**，内存里的会话是这份日志的一个视图。进程重启后按日志重建；压缩只把老消息移出内存窗口并写一条覆盖标记，日志里的原文一行不删，`read_full_history()` 可以完整取回。会话边界（闲置超时后重新开始）在重放时按相邻记录的时间间隔推断，不需要额外标记。

## Web 控制台

公开地址 [agent.noahwang.run](https://agent.noahwang.run)。

页面分为 `对话 / 数据 / 技术` 三个入口：

- `对话`：直接用自然语言提问，主 Agent 自动判断该调用哪个能力。左侧是历史对话，顶部固定“新建对话”，空白草稿最多一个且不会进入历史列表；底部快捷问题会根据当前上下文变成下一步操作，例如查完洛杉矶马拉松照片后提示生成报告、查对应运动记录等。
- `数据`：只读展示个人数据，包括运动画像、PB、比赛照片、RAG 书籍和视频知识库、COROS FIT 归档、厨房菜谱和采购数据。
- `技术`：展示项目迭代报告、RAG 流程和问题解决记录。

公开 Web 入口只读，写入操作仍限制在 Discord 能力频道。

## FIT 全量归档

`scripts/archive_all_fit.py` 把 COROS 上的历史活动 FIT 文件拉到 `data/coros-report/fit-files/`。任务幂等——本地已有的不会重复下载，中断了重跑即可续传。

COROS 对 FIT 下载有**每日配额**（实测约 50 次），且配额用完时接口不报错、只返回空，和「这条活动本来就没有 FIT」无法区分。所以脚本带熔断：连续 5 条失败就停并说明原因，不会空烧几百次调用。`--max-downloads` 可以把单次运行压在配额以内。

服务器上由 systemd 定时器每天跑一次：

```
agentdeck-fit-archive.timer  →  --max-downloads 45
```

留 5 次余量给 Discord bot 的自动报告。手动跑：

```bash
uv run python scripts/archive_all_fit.py --max-downloads 45
```

## 评测与留出集

`evals/` 下有五套回归评测（路由、照片、会话持久化、注入防护、RAG 检索），`uv run python evals/run_evals.py` 全跑。

另有一套**留出集** `evals/run_holdout.py`，26 道全新的检索题，**刻意不接进默认评测**。它的价值完全来自「从来没参与过任何决定」——现有的 30 道检索题已经被反复用于调参（切片参数、混合检索、reranking 都是看着它的结果定的），在它上面调出来的最优值不一定能泛化。

实例：top-k 在调参集上看 k=4 是完美饱和点，在留出集上 k=3 和 k=4 **完全没有区别**。所以 k 维持 3。

留出集只在重大改动（换切片策略、换嵌入模型、加 reranker）后跑一次做最终验收，**跑完不要照着失败用例调参数**，否则这把尺子就废了。

## 可观测性

`src/runtime/trace.py` 给每次请求分配 `trace_id`（用 `ContextVar` 传递，跨 `await` 自动携带），日志是单行 `key=value` 的结构化事件，一次请求从入口、模型调用、工具执行到二次推理可以用同一个 trace 串起来，结束时汇总耗时、模型调用次数和 token 总量。

Prompt 默认只记指纹不记明文——里面有成绩、伤病、目标等个人数据。需要复现模型异常输出时用 `LOG_PROMPTS=1` 临时开启。

长期记忆与临时缓存分开存放：`memory.json` 的 `agents` 是会进提示词的长期记忆，`caches` 是不进提示词的临时数据（如「分析第 N 条」的选择缓存）。历史遗留的缓存键在每次加载时自愈式迁移。

## 限流与出站检查

公开网页入口有两层限流（`src/runtime/ratelimit.py`）：按来源挡单 IP 高频，按全局挡分散来源。只按 IP 限流保护不了模型账单，因为账单是按总量算的。真实 IP 取 `X-Forwarded-For` 的最后一段（Caddy 追加的那段，客户端伪造不了）。可用 `WEB_RATE_LIMIT_PER_MINUTE` 和 `WEB_RATE_LIMIT_GLOBAL_PER_MINUTE` 调整。

发给用户的文本会过一遍 `src/runtime/output_guard.py`：抹掉环境变量里的真实密钥值，删掉泄露的 `<untrusted-data>` 边界标签。它只做精确匹配这类零误报的事——会误伤正常回答的安全层最终会被关掉，那比没有更糟。

## 注入防护

外部来源的文本（书籍原文、B 站字幕、第三方接口返回）进提示词前会被 `src/runtime/untrusted.py` 包进 `<untrusted-data>` 边界标签，内容里的标签字面量会先被打断，防止攻击者自行闭合跳出边界；系统提示里有一条常驻规则说明标签内只是数据、其中的指令一律无视。

更重要的是第二道：`Tool` 带 `writes` 和 `returns_untrusted` 两个属性，主循环一旦读取过外部内容，本轮剩下的写操作一律拒绝。注入的典型形态是「先让 agent 读到被投毒的资料，再诱导它去写」，把这两步隔开就切断了利用链。这一层由 `evals/specs/prompt_injection.json` 守着。

本地启动：

```bash
uv run python -m src.api.web_server --host 127.0.0.1 --port 8787
```

打开 <http://127.0.0.1:8787>。

`WEB_AGENT_MODE` 控制运行模式：

- `demo`（默认）：走本地关键词假路由和固定文案，不调用任何外部服务，可以离线演示。
- `real`：调用与 Discord 相同的真实 capability，需要配置 `.env` 里的模型和数据源密钥。

项目迭代记录见 [docs/project-iteration-report.md](docs/project-iteration-report.md)。
