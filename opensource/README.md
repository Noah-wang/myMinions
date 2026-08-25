# COROS Running Agent

一个自托管的跑步教练 Agent。它读你自己的 COROS 运动数据、你自己的训练书籍和
跑鞋测评视频，然后用自然语言回答关于**你**的问题——不是泛泛的跑步常识。

```
你：我一共跑过几场比赛
它：你一共跑过 8 场比赛。从比赛记录来看，跨度从半马、10K，到越野跑和全马都有，
    最近的分别是今年 3 月的洛杉矶马拉松（4:30:48）和 1 月的 Rosebowl 半马（1:42:54）。
```

两个入口：Discord 机器人，和一个可以公开挂出去的 Web 控制台。

> 这是从一个更大的个人 Agent 平台里抽出来的跑步部分。
> 它是**给自己用的工具**，不是 SaaS：没有多用户、没有登录，
> 一份部署对应一个人的数据。

---

## 目录

- [它能做什么](#它能做什么)
- [快速开始](#快速开始)
- [架构](#架构)
- [用到的开源项目](#用到的开源项目)
- [配置项](#配置项)
- [知识库](#知识库)
- [定时任务](#定时任务)
- [评测](#评测)
- [安全设计](#安全设计)
- [部署](#部署)
- [常见问题](#常见问题)

---

## 它能做什么

**回答关于你自己数据的开放问题**

不是「跑步该怎么练」，而是「我最近三周的跑量趋势对不对」。模型拿到一张工具表，
自己决定查什么、查几次、怎么组织答案。查回来发现方向错了，它会换个工具重查。

**生成训练报告**

单次运动的复盘：配速、心率、训练负荷、和你历史水平的对比，以及下一次该练什么。
底层是一条 LangGraph 工作流，使用 ShadowRunner 风格的跑者决策框架，
把工具规划、数据读取、报告生成、critic 审阅和修订输出拆开。

**跑步知识库问答（RAG）**

把训练书籍（PDF）和 B 站跑鞋测评的字幕导进本地知识库，检索时按**内容方向**
先缩范围（跑鞋 / 训练），再按语义排序。父子分块：子块用来匹配保精度，
命中后回溯父块投喂给模型保上下文。

**自动同步跑鞋测评**

订阅几个 B 站 UP 主，定时任务自己发现新视频、抓字幕、进知识库、重建索引。
订阅名单可以在对话里加——把 UP 主的空间链接发给它就行。

**长期档案与主观感受**

年龄、体重、PB、目标成绩、目标日期、周跑量。以及「今天腿很沉」这类
数据里看不出来的东西，模型回答时会一起考虑。

**FIT 文件归档**

把 COROS 上的历史活动原始 FIT 拉到本地，任务幂等，可断点续传。

**动态架构图**

Web 控制台会把一次提问走过的模块实时点亮：入口、语义路由、能力层、
LangGraph、COROS MCP、RAG、LLM 生成和最终回答。它是展示层，不参与决策，
但能让别人一眼看懂这个 Agent 不是一个单 prompt 问答框。

**联网搜索**（可选）

知识库里没有的东西——赛事安排、比赛日天气。没配 key 就不启用。

---

## 快速开始

### 1. 装依赖

需要 Python 3.13+ 和 [uv](https://github.com/astral-sh/uv)。

```bash
git clone <your-fork-url> coros-running-agent
cd coros-running-agent
uv sync
```

### 2. 配置

```bash
cp .env.example .env
```

**最小可运行配置**只要两项：

```bash
DEEPSEEK_API_KEY=sk-...        # 或任何 OpenAI 兼容的接口
AGENT_OWNER_NAME=你的名字        # 出现在系统提示词里
```

这样就能启动 Web 控制台并问知识库问题了。要接 COROS 数据再加：

```bash
COROS_MCP_URL=https://...      # 你的 COROS MCP 服务地址
```

### 3. 起 Web 控制台

```bash
WEB_AGENT_MODE=real uv run python -m src.api.web_server --host 127.0.0.1 --port 8787
```

打开 <http://127.0.0.1:8787>。

不配任何 key 时用 `WEB_AGENT_MODE=demo`（默认）——走本地假路由和固定文案，
可以离线看界面长什么样。

### 4.（可选）起 Discord 机器人

```bash
uv run python -m src.main
```

需要 `DISCORD_BOT_TOKEN` 和 `DISCORD_RUNNING_CHANNEL_ID`。
**写操作只在 Discord 开放**，Web 入口是只读的——原因见[安全设计](#安全设计)。

---

## 架构

```
                 Discord 消息            HTTP / SSE
                      │                      │
              src/bot/discord_bot.py   src/api/web_server.py
                      └──────────┬───────────┘
                                 ▼
                       src/orchestrator.py          ← 权限隔离、命令分发
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
        !coros-pb 这类显式命令              自然语言
          （快速通道，直接执行）        src/ask.py + runtime/tools.py
                                              （主 Agent 循环）
                                                 │
                                    ┌────────────┴────────────┐
                                    ▼                         ▼
                            read_tools（取数）        text_commands（执行动作）
                                    └────────────┬────────────┘
                                                 ▼
                                   agents/coros_report/（能力实现）
```

### 主 Agent 是一个循环，不是一个分类器

最早的版本是「分类器挑一个命令」：模型看一眼用户的话，选一个命令执行完就结束。
问题是**分类器只能在看到任何数据之前猜一次，猜错了没有第二次机会**。
问「我一共跑过几场比赛」，它会选「列出运动记录」，然后倒出 20 条日常训练。

现在是循环：模型拿到工具表，调一个，看结果，再决定下一步。
**决定发生在看到数据之后。**

`!coros-pb` 这类显式命令仍然走快速通道绕过循环——用户打命令就是要确定的输出，
不需要模型再想一遍。原来的分类器保留在 `MAIN_AGENT_LOOP_ENABLED` 开关后面作为回退。

### 能力（Capability）是怎么接进来的

一个能力包提供两类东西：

| 类型            | 是什么                 | 例子                                |
| --------------- | ---------------------- | ----------------------------------- |
| `read_tools`    | 结构化取数，给模型吃   | `list_recent_activities`             |
| `text_commands` | 执行动作，输出给人看   | `!coros`（生成报告）、`!feel`（记感受）|

`text_commands` 会被自动包装成工具：命令处理器本来是往频道发消息的，
包装时给它一个 `send` 写进缓冲区的上下文，缓冲区内容就是工具返回值。

**加一个能力 = 加一个目录。** `src/registry.py` 扫描 `agents/*/xxx_capability.py`，
找到 `build_*_capability()` 就装上。删掉目录能力就消失，不用改任何代码。

`src/` 从不 import `agents/` 里的具体模块——这条边界让运行时可以独立于领域逻辑演进。

### 权限挂在工具表上

这是整个设计里最重要的一条：

```python
TextCommand(name="feel", writes=True, ...)     # 写工具
Tool(name="list_races", read_only_safe=True)   # 只读工具
```

构造工具表时，只读入口**直接跳过写工具**——它们根本不出现在发给模型的
tools 参数里。**模型看不见的工具不可能被调用。**

这比「让模型自己别调」或者「调了再拦截」都强：前者靠提示词，可以被绕过；
后者的拦截逻辑本身可能有洞。工具表是结构性的。

### 数据流

```
data/
├── memory.json                      长期记忆（档案、感受）+ 临时缓存，两个命名空间分开
├── conversations/                   只追加的 JSONL 会话日志
├── coros-report/fit-files/          FIT 归档
└── knowledge/coros-report/
    ├── books/                       你自己放的 PDF
    ├── videos/shoes/                跑鞋测评字幕（自动同步）
    ├── videos/training/             训练理论字幕
    ├── chunks.json                  分块结果
    ├── embeddings.json              向量（按内容哈希缓存）
    └── index.json                   BM25 关键词索引
```

**会话历史只追加、不改写。** 内存里的窗口是这份日志的一个视图，
压缩只是往日志里写一条覆盖标记，原文一行不删。进程重启后按日志重建。

更详细的设计取舍见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，
RAG 那部分单独写在 [docs/rag-pipeline.md](docs/rag-pipeline.md)。

---

## 用到的开源项目

| 项目 | 用途 | 为什么是它 |
| --- | --- | --- |
| [uv](https://github.com/astral-sh/uv) | 依赖与虚拟环境管理 | 解析和安装比 pip 快一个数量级，`uv.lock` 保证跨机器一致 |
| [openai-python](https://github.com/openai/openai-python) | 模型调用 | 只用它的 HTTP 客户端和类型，**任何 OpenAI 兼容接口都能接**（默认 DeepSeek） |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 运动报告的内部工作流 | 报告生成是固定的多步流程（取数 → 生成 → critic 审阅 → 修订），用状态图表达比手写 if 清楚 |
| [discord.py](https://github.com/Rapptz/discord.py) | Discord 入口 | 成熟、异步、事件模型干净 |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 连 COROS 数据源 | COROS 的数据通过 MCP 服务暴露，用官方 SDK 而不是自己拼协议 |
| [bilibili-api-python](https://github.com/Nemo2011/bilibili-api) | B 站视频列表与字幕 | WBI 签名依赖未公开接口，B 站会不定期改，让一个持续更新的库去跟比自己写划算 |
| [httpx](https://github.com/encode/httpx) | 异步 HTTP | 字幕抓取要并发和超时控制 |
| [pypdf](https://github.com/py-pdf/pypdf) | 解析训练书籍 PDF | 纯 Python，没有系统级依赖 |
| [NumPy](https://numpy.org/) | 向量检索 | 相似度计算向量化。**缺失时自动退回纯 Python**，不是硬依赖 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 读 `.env` | 标准做法 |
| [Caddy](https://caddyserver.com/) | 反向代理与 HTTPS | 自动证书，配置文件三行 |

**刻意没有用的东西**，以及原因：

- **向量数据库**（Chroma / Qdrant / pgvector）：当前语料几百个块，
  瓶颈在 JSON 解析而不是相似度计算。触发迁移的具体信号是
  `embeddings.json` 超过 100MB 或加载超过 1 秒——**在那之前，
  多一个服务只是多一个会在半夜挂掉的东西**。
- **RSSHub**（监听 B 站更新）：那要多养一个常驻容器，而同样的事
  一个 pip 包就能做。
- **MCP 版的搜索工具**：每次调用要起一个 node 子进程（5~15 秒），
  而搜索是用户等着的同步调用；key 写在 URL 里还会进进程列表和日志。
- **混合检索（RRF）**：实现了，实测在这个语料上比纯向量更差，默认关闭。
  代码留着，开关在 `RAG_HYBRID_ENABLED`。

---

## 配置项

完整列表见 `.env.example`。常用的：

### 模型

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com   # 换成任何 OpenAI 兼容的地址
DEEPSEEK_MODEL=deepseek-chat
EMBEDDING_API_KEY=...                        # 不填则复用上面的
EMBEDDING_MODEL=text-embedding-3-small
```

### 数据源

```bash
COROS_MCP_URL=...                  # COROS MCP 服务地址
COROS_MCP_CLIENT=mcp-remote@0.1.38 # 固定版本，别用不带版本的写法（见常见问题）
```

### 入口

```bash
DISCORD_BOT_TOKEN=...
DISCORD_RUNNING_CHANNEL_ID=...     # 只在这个频道响应
WEB_AGENT_MODE=real                # demo = 离线假数据
WEB_PUBLIC_DOMAIN=agent.example.com
WEB_RATE_LIMIT_PER_MINUTE=10
WEB_RATE_LIMIT_GLOBAL_PER_MINUTE=60
```

### 检索

```bash
CHUNK_SIZE=1200
CHILD_CHUNK_SIZE=700
RAG_HYBRID_ENABLED=false
```

### 可选

```bash
TAVILY_API_KEY=...                 # 或 BRAVE_SEARCH_API_KEY，都不填则不启用搜索
WEB_SEARCH_DAILY_LIMIT=50
LOG_PROMPTS=0                      # 调试时才开，提示词里有个人数据
```

---

## 知识库

### 放书

把 PDF 丢进 `data/knowledge/coros-report/books/`，然后：

```bash
uv run python scripts/ingest_books.py
```

分块策略是**父子分块**：先切 1200 字的父块保上下文，再切 700 字的子块用来匹配。
检索时子块命中，投喂父块。跨页合并、按语义边界切分、页眉页脚去噪都在
`src/runtime/chunking.py` 里。

嵌入按**内容哈希**缓存：加一本新书只算新增的块，已有的直接复用。

### 导入 B 站视频

单条：

```bash
# Discord 里
!running-video https://www.bilibili.com/video/BV1...
```

订阅一个 UP 主，让它自动同步：

```bash
# Discord 里，把空间链接发给它
!knowledge-source https://space.bilibili.com/32360754 shoes
```

订阅名单落在 `data/knowledge/coros-report/sources.json`，
分类只能是 `shoes` 或 `training`——它决定视频进哪个目录，
也决定检索时属于哪个内容方向。

抓字幕需要 B 站登录态，放在 `agents/coros_report/config.toml`（**这个文件不要提交**）：

```toml
[credential]
sessdata = "..."
bili_jct = "..."
buvid = "..."
```

### 检索为什么要分类

书和跑鞋测评的用词高度重叠：配速、脚感、体重、里程。
问「我这个水平该选什么跑鞋」，纯靠语义相似度会把训练理论排进前三——
它们确实在讲「跑者水平」，但对选鞋没用。**区别是意图上的，不是文本上的。**

所以先按分类缩范围，再排序。认不出的分类名（模型可能传 `跑鞋`、`shoe`、
`running_shoes`）**返回全部而不是返回空**——退化成「没分类」只是效果差一点，
返回空则整个知识库查不到东西，而且失败形态是「什么都没查到」，看起来像库是空的。

---

## 定时任务

`deploy/systemd/` 下有现成的 unit 文件。

### B 站字幕同步

每半小时一轮，单轮 8 条，订阅源之间平分配额。

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentdeck-bili-sync.timer
```

手动跑一次看会导入什么：

```bash
uv run python scripts/sync_bilibili.py --dry-run
```

**限流卡在「列视频」而不是「抓字幕」**——这是实测出来的，和直觉相反。
字幕连抓 15 条（间隔 4 秒）零失败，而空间列表接口打几次就 -799/412。
所以视频列表缓存 20 小时，列表请求失败时退回缓存继续回填，连续出错则熔断。

熔断之后会写一个冷静期文件，接下来两小时的定时任务直接退出。
**提高频率必须同时提高退让能力**，否则只是把出错的代价也放大了 48 倍。

### FIT 归档

```bash
uv run python scripts/archive_all_fit.py --max-downloads 45
```

COROS 对 FIT 下载有每日配额（实测约 50 次），而且**配额用完时接口不报错、
只返回空**，和「这条活动本来就没有 FIT」无法区分。所以脚本带熔断：
连续 5 条失败就停并说明原因。`--max-downloads` 把单次运行压在配额以内。

---

## 评测

```bash
uv run python evals/run_evals.py
```

四套回归评测：

| 套件                       | 守什么                                     |
| -------------------------- | ------------------------------------------ |
| `natural_language_routing` | 路由正确性 + **每个入口能看到哪些工具**      |
| `conversation_persistence` | 会话日志重放、压缩不丢数据                  |
| `prompt_injection`         | 边界标签、写闸门、出站检查、限流            |
| `rag_retrieval`            | 检索命中率（需要你自己的知识库，没有则跳过）|

其中两条守的是**契约**而不是效果：

- `loop_tool_exposure`：写工具不该出现在只读入口
- `flow_map_coverage`：每个工具都能映射到架构图上的一个模块

后者防的是一种不报错的坏法——忘了更新映射，图不会崩，**只会说谎**。

还有一套用真实模型跑的轨迹评测：

```bash
uv run python evals/run_agent_trajectory.py
```

它检查的是「问 X 的时候有没有去查 Y」，也就是工具选择本身，而不是回答的文字。

`evals/run_holdout.py` 是**留出集**，刻意不接进默认评测。它的价值完全来自
「从来没参与过任何决定」——调参集已经被反复用于调切片参数和检索策略，
在它上面调出来的最优值不一定能泛化。只在重大改动后跑一次做最终验收，
**跑完不要照着失败用例调参数**，否则这把尺子就废了。

---

## 安全设计

这个项目默认会被挂到公网上，所以有几层是必须的。

### 公开入口只读

Web 控制台没有登录。选择是：保留个人档案的**读**，但**所有写操作都不开放**。
实现方式是上面说的工具表——写工具在只读入口构造时就被跳过了。

### 不可信内容边界

外部来源的文本（书籍原文、B 站字幕、搜索结果）进提示词前会被包进
`<untrusted-data>` 标签，内容里的标签字面量先被打断，防止攻击者自行闭合跳出边界。
系统提示里有一条常驻规则说明标签内只是数据。

### 读→写闸门

比标签更重要的一层。`Tool` 有 `writes` 和 `returns_untrusted` 两个属性，
**主循环一旦读取过外部内容，本轮剩下的写操作一律拒绝**。

注入的典型形态是「先让 agent 读到被投毒的资料，再诱导它去写」。
把这两步隔开就切断了利用链，不依赖模型自己识别攻击。

### 出站检查

发给用户的文本会过一遍 `src/runtime/output_guard.py`：抹掉环境变量里的
真实密钥值，删掉泄露的边界标签。它**只做精确匹配这类零误报的事**——
会误伤正常回答的安全层最终会被关掉，那比没有更糟。

### 两层限流

按来源挡单 IP 高频，按全局挡分散来源。只按 IP 限流保护不了模型账单，
因为账单是按总量算的。真实 IP 取 `X-Forwarded-For` 的最后一段
（反向代理追加的那段，客户端伪造不了）。

搜索另有独立的**每日预算**，因为它按次收费而入口无认证——每分钟限流
不能阻止一天累计烧掉整个额度。

### 提示词不记明文

日志默认只记提示词指纹。里面有成绩、伤病、目标。
需要复现模型异常输出时用 `LOG_PROMPTS=1` 临时开启。

---

## 部署

一台小 VPS 就够。三个 systemd 服务：Web、Discord bot、定时任务。

```
your-agent.service         Web 控制台
your-agent-bot.service     Discord 机器人
agentdeck-bili-sync.timer  每半小时同步字幕
agentdeck-fit-archive.timer 每天归档 FIT
```

前面用 Caddy 反代，HTTPS 自动签：

```caddy
agent.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

**这些是必须留在服务器上、不进仓库的**：`.env`、`agents/coros_report/config.toml`、
整个 `data/` 目录。部署脚本要显式排除它们——一次 `rsync --delete` 忘了加排除
就能把你的会话历史和登录态清空。

---

## 常见问题

**「正在输入」一直亮着 / 请求永远不返回**

多半是 `COROS_MCP_CLIENT` 写成了不带版本的 `mcp-remote`。
它把 OAuth 令牌按版本存在 `~/.mcp-auth/mcp-remote-<版本>/`，
`npx` 拉到新版本时那个目录里没有令牌，于是它停在等待授权那一步永远不返回。
**固定版本号**，升级要主动做并重新授权一次。

**RAG 检索退回了关键词模式**

`embeddings.json` 的向量数和 `chunks.json` 的块数对不上时会触发一致性守卫。
常见原因是换了嵌入模型，或者只给一部分块建了索引——
**索引必须建在全量块上，分类过滤只能是查询期的事**。重跑 `ingest_books.py`。

**B 站同步一直导入 0 条**

先看 `--dry-run` 输出的「列表来源」。如果是「请求返回空且无缓存」，
基本就是触发了风控——而**「被限流」和「这个人没发过视频」返回结构一模一样**，
不看这一行区分不出来。等冷静期过（`.video-index/cooldown.json`），别硬重试。

**问「跑过几场比赛」，它去列了日常训练**

比赛和训练是两个数据源。这个开源版本不含照片记忆能力，
所以没有比赛记录的来源——你需要自己提供一个 `list_races` 工具，
或者接受它只能回答训练相关的问题。

---

## License

MIT。见 [LICENSE](LICENSE)。

书籍、视频字幕等你自己导入的资料**不属于本项目**，它们的版权归原作者。
不要把 `data/` 提交到公开仓库。
