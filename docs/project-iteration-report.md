# myMinions / coros-report 项目迭代报告

## 1. 项目背景

本项目最初从学习 `pi agent` 开始，目标是理解 CLI/TUI、Agent、Skill、MCP、API 等概念，并在此基础上开发一个个人可扩展的 Agent 系统。

后续项目方向逐渐明确为：构建一个属于自己的多 Agent 平台 `myMinions`，先实现第一个运动分析 Agent：`coros-report`。

`coros-report` 的目标是接入 COROS 官方运动数据，通过大模型生成个性化运动报告，并进一步支持基于跑步书籍的训练问答。

## 2. 项目结构演进

最开始功能集中在 `coros-report` 中。后续为了支持未来多个 Agent，项目结构被调整为：

```text
myMinions/
├── src/
│   ├── bot/              # Discord 交互层
│   ├── runtime/          # 可复用运行时能力
│   ├── integrations/     # 外部服务接入
│   └── registry.py       # capability 注册与命令路由
├── coros-report/
│   ├── agent/            # coros-report 专属 capability 逻辑
│   └── docs/             # coros-report 文档
├── data/
│   ├── memory.json       # 记忆
│   └── knowledge/        # RAG 知识库
├── docs/                 # 项目级文档
└── scripts/
    └── ingest_books.py   # PDF 导入脚本
```

这个调整解决了一个核心问题：如果以后继续添加新的 Agent，不需要每个 Agent 都重复写 Discord、LLM、记忆、RAG、调度器等基础能力。

后续又进一步明确了模块边界：

```text
capability.py = 定义能力包、文本命令和执行上下文
registry.py = 注册能力包并路由命令
coros_capability.py = 把 coros-report 包装成第一个 capability
auto_report.py = coros-report 专属业务逻辑，负责查新运动和生成报告
scheduler.py = 通用触发器，负责什么时候执行
discord_bot.py = 交互入口，只负责接收消息并交给 registry
```

## 3. 已添加的核心功能

### 3.1 COROS MCP 接入

项目接入了 COROS MCP，用于读取运动数据。

在理解 MCP 和 API 的关系时，明确了：

```text
API = 服务本身提供的接口
MCP = 给 Agent 使用 API 的标准工具层
```

也就是说，Agent 并不是不能用 API，而是可以通过 MCP 更标准地调用外部服务。

### 3.2 DeepSeek 大模型接入

项目使用 DeepSeek 作为主要回答模型，负责：

```text
读取 COROS 数据
分析运动表现
生成中文运动报告
给出恢复和训练建议
```

`.env` 中通过以下配置控制：

```env
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

### 3.3 Discord Bot 交互层

为了让 Agent 能远程使用，项目加入 Discord Bot 作为交互入口。

当前支持：

```text
/coros
/coros-tools
/running-ask
/feel
/feelings
/capabilities

!coros
!coros-tools
!running
!feel
!feelings
!capabilities
```

并且限制只能在指定频道使用：

```env
DISCORD_RUNNING_CHANNEL_ID=1537316749622386718
```

这样可以避免机器人在其他频道乱响应。

### 3.4 记忆模块

项目加入了 `memory.json`，用于保存用户长期偏好和 Agent 专属信息。

当前结构包括：

```text
global:
  name
  language
  preferences

agents:
  coros-report:
    goals
    preferences
    injury_notes
    latest_reported_activity_id
```

这个模块为后续个性化训练建议打基础，例如记录用户目标、伤病、训练偏好、近期状态等。

### 3.5 PDF RAG 跑步知识库

用户上传跑步书籍 PDF 后，项目加入 RAG 功能，让 Agent 可以基于书籍回答训练问题。

流程是：

```text
PDF
-> ingest_books.py 切分
-> chunks.json 保存原文片段
-> index.json 保存关键词索引
-> rag.py 检索相关片段
-> DeepSeek 基于片段回答
```

支持的问题示例：

```text
阈值跑怎么练
轻松跑怎么安排
间歇训练怎么做
```

### 3.6 Embedding 向量检索升级

最开始 RAG 使用关键词检索，缺点是中文问题和英文/专业术语不容易匹配。后来升级为 embedding 检索：

```text
用户问题 -> embedding 向量
书籍片段 -> embedding 向量
比较语义相似度
找到最相关片段
```

现在策略是：

```text
如果 embeddings.json 存在且模型配置一致
-> 使用 embedding 检索

否则
-> 自动退回关键词检索
```

这样既提升语义理解，又保留稳定兜底。

### 3.7 自动运动报告模块

项目新增了自动检测新运动并推送报告的能力。

核心流程是：

```text
Discord bot 上线
-> scheduler.py 启动定时任务
-> 每隔指定时间检查 COROS
-> auto_report.py 查询最近运动
-> 找到最新一条 activity
-> 和 memory.json 里的 latest_reported_activity_id 比较
-> 如果是新运动，生成报告并发送到 Discord
-> 更新 memory，避免重复推送
```

自动报告使用的 activity key 由以下字段组合：

```text
labelId:sportType:startTimestamp:endTimestamp
```

这样可以避免只靠日期判断导致误判。

新增配置项：

```env
COROS_AUTO_REPORT_ENABLED=true
COROS_AUTO_REPORT_POLL_MINUTES=15
COROS_AUTO_REPORT_LOOKBACK_DAYS=7
COROS_AUTO_REPORT_SEND_ON_FIRST_RUN=false
```

其中：

```text
COROS_AUTO_REPORT_ENABLED
= 是否开启自动报告

COROS_AUTO_REPORT_POLL_MINUTES
= 每隔几分钟检查一次 COROS

COROS_AUTO_REPORT_LOOKBACK_DAYS
= 每次查询最近几天的运动记录

COROS_AUTO_REPORT_SEND_ON_FIRST_RUN
= 第一次启动时是否立刻发送当前最新运动
```

同时加入了两个手动测试命令：

```text
!coros-auto-check
= 手动触发一次检查，有新运动才发

!coros-auto-report
= 强制对最新一条运动生成报告，方便测试
```

自动模式不需要用户发送命令，只要 bot 持续运行，就会按间隔自动检查。

### 3.8 主框架 Capability / Registry / Router 重构

为了让 `myMinions` 更接近 OpenClaw / Codex 这类“主运行时 + 能力扩展”的结构，项目加入了 capability 注册机制。

核心变化是：

```text
之前：
Discord bot
-> 直接 import coros-report 的 agent / knowledge / feelings / auto_report
-> 直接调用具体函数

现在：
Discord bot
-> registry
-> coros-report capability
-> 对应 command handler
```

新增的通用框架文件：

```text
src/runtime/capability.py
= 定义 Capability、TextCommand、CommandContext

src/registry.py
= 加载所有 capabilities，注册命令，分发文本消息
```

`coros-report` 被包装成第一个 capability：

```text
coros-report/agent/coros_capability.py
= 注册 coros-report 的命令和启动任务
```

当前 `coros-report` capability 注册了这些文本命令：

```text
!coros
!coros-tools
!running
!feel
!feelings
!coros-auto-check
!coros-auto-report
```

同时新增：

```text
!capabilities
/capabilities
```

用于查看当前加载的能力包。

这次重构后，`coros-report` 不再是整个系统本身，而是 `myMinions` 主框架里的第一个能力包。未来新增 `museum-guide`、`calendar-agent`、`study-agent` 等能力时，可以按同样方式新增 capability，而不是继续把逻辑堆进 `discord_bot.py`。

## 4. 主要问题与解决方案

### 问题 1：Skill 配置冲突

启动时出现多个 Skill 配置错误，例如：

```text
description is required
name contains invalid characters
Nested mappings are not allowed
```

原因是 `~/.agents/skills` 下的部分 `SKILL.md` 格式不符合规范。

解决方案：

```text
理解 ~/.agents/skills 是 Agent 技能目录
修正 name、description、YAML frontmatter 格式
避免不同 Skill 命名冲突
```

### 问题 2：OpenAI / Codex API Key 错误

出现：

```text
401 invalid x-api-key
```

原因是 API key 填错或环境变量位置不正确。

解决方案：

```text
确认 API key 应该放在对应配置文件或环境变量中
区分 OpenAI、DeepSeek、Embedding 服务各自的 key
```

### 问题 3：GitHub remote 配置冲突

上传 clone 的仓库到自己的 GitHub 时出现：

```text
remote upstream already exists
remote origin already exists
fatal: protocol 'git@github.com:https' is not supported
```

原因是远程仓库地址配置混乱，把 SSH 和 HTTPS 拼在了一起。

解决方案：

```text
重新检查 git remote
明确 upstream 表示原项目
origin 表示自己的 GitHub 仓库
使用正确 remote URL
```

### 问题 4：COROS MCP 地址不匹配

出现：

```text
Protected resource https://mcpus.coros.com/mcp does not match expected https://mcp.coros.com/mcp
```

原因是 COROS MCP 授权返回的资源地址和配置地址不一致。

解决方案：

```text
改用实际匹配的 COROS MCP URL
保持 COROS_MCP_URL 与授权资源一致
```

### 问题 5：Discord Bot 登录失败

出现：

```text
discord.errors.LoginFailure: Improper token has been passed.
```

原因是 Discord Bot Token 填错。

解决方案：

```text
重新生成 Discord Bot Token
填入 .env 的 DISCORD_BOT_TOKEN
```

### 问题 6：Discord 指令可以在其他频道看到

问题表现：

```text
/ 指令在其他频道仍然显示
```

原因是 Discord slash command 是全局注册，显示和执行权限不是一回事。

解决方案：

```text
在代码执行层检查 channel_id
不在指定频道时拒绝执行
文本命令在非指定频道直接忽略
```

### 问题 7：RAG 中文问题检索不到

出现：

```text
我没有在已导入的跑步书籍里检索到相关内容
```

原因是最初 RAG 只做简单关键词检索，中文问题“阈值跑”无法匹配书中的“乳酸门槛跑 / T跑”。

解决方案：

```text
增强中文切词
加入跑步术语同义词
加入短语加权
后续升级为 embedding 语义检索
```

### 问题 8：Embedding 批量大小超限

生成 embedding 时出现：

```text
batch size is invalid, it should not be larger than 25
```

换模型后又出现：

```text
batch size is invalid, it should not be larger than 20
```

原因是不同 embedding 服务限制单次最多处理的文本数量。

解决方案：

```text
将批量大小从 64 调整为 25
后续根据新模型限制调整为 20
并支持通过 .env 配置 EMBEDDING_BATCH_SIZE
```

### 问题 9：Embedding 文件模型名和当前模型不一致

发现：

```text
embeddings.json 里是 text-embedding-v1
.env 里是 qwen3.7-text-embedding
```

原因是旧模型曾成功生成过 embeddings，新模型生成失败后旧文件没有被覆盖。

解决方案：

```text
在 RAG 中加入模型名检查
如果 embeddings.json 的模型和当前 .env 不一致
自动退回关键词检索
避免不同模型向量混用
```

### 问题 10：API Key 泄露风险

配置过程中曾把 key 直接发到聊天里。

解决方案：

```text
立即废弃旧 key
重新生成新 key
后续只在 .env 本地保存
不提交到 GitHub
不在聊天中明文发送
```

### 问题 11：Scheduler 放在 main.py 无法发送 Discord 消息

最初 scheduler 是从 `main.py` 直接启动的：

```text
main.py
-> load_dotenv()
-> start_scheduler()
-> run_discord_bot()
```

但自动报告需要把结果发送到 Discord 指定频道，因此 scheduler 必须拿到 `discord.Client`。

解决方案：

```text
移除 main.py 里的 start_scheduler()
在 Discord bot 的 on_ready 事件中调用 start_scheduler(client)
确保 bot 登录成功、client 可用后再启动定时任务
```

调整后流程变为：

```text
main.py
-> run_discord_bot()
-> Discord on_ready
-> start_scheduler(client)
-> scheduler 定时触发 auto_report
```

### 问题 12：自动报告容易重复推送旧运动

如果每次启动 bot 都直接分析当前最新运动，会导致重启后反复发送同一条旧报告。

解决方案：

```text
使用 memory.json 保存 latest_reported_activity_id
每次检查时生成当前最新运动的 activity_key
只有 activity_key 变化时才自动推送
首次运行默认只记录当前最新运动，不发送旧报告
```

同时保留手动测试命令：

```text
!coros-auto-report
```

这个命令可以强制对最新运动生成报告，不影响自动模式的去重逻辑。

### 问题 13：Discord bot 开始堆积具体业务逻辑

随着功能增加，`discord_bot.py` 开始直接 import 并调用多个 coros-report 业务函数：

```text
generate_coros_report
answer_running_question
record_feeling
check_and_send_coros_auto_report
```

问题是：如果以后继续添加博物馆、日程、学习等 Agent，Discord bot 会变成一个越来越大的业务入口文件，不利于复用和扩展。

解决方案：

```text
新增 src/runtime/capability.py 定义 capability 标准结构
新增 src/registry.py 统一注册和路由 capabilities
新增 coros-report/agent/coros_capability.py 包装 coros-report
Discord bot 只调用 registry，不再直接依赖 coros-report 的具体实现
```

调整后架构变为：

```text
Discord bot
-> registry
-> capability
-> agent-specific handlers
```

这让 `myMinions` 从单一业务 Agent 应用，升级成一个可以继续扩展多个能力包的小型 Agent 平台。

## 5. 当前项目能力总结

当前 `coros-report` 已经具备：

```text
读取 COROS 运动数据
生成个性化运动报告
通过 Discord 交互
限制指定频道使用
支持 DeepSeek 生成回答
支持本地 memory
支持 PDF 跑步知识库问答
支持 embedding 语义检索
支持关键词检索兜底
支持自动检测新运动并推送报告
支持 scheduler 定时触发
支持 latest_reported_activity_id 防重复推送
支持 capability 注册机制
支持 registry 命令路由
支持查看已加载能力包
```

这已经不只是一个 prompt 问答机器人，而是一个具备外部数据接入、工具调用、知识库检索、长期配置、远程交互入口、自动触发能力和 capability 扩展机制的个人 Agent 平台雏形。

## 6. 后续可继续扩展方向

后续可以继续添加：

```text
训练目标记忆
伤病和疲劳记录
周报 / 月报
训练计划生成
比赛备赛周期规划
Web 后台管理页面
VPS 部署和长期运行
更多 capability 包
```

未来如果加入博物馆项目，也可以复用当前架构：

```text
src/runtime
src/registry.py
src/bot
src/integrations
data/knowledge
memory
```

然后新增不同 Agent，例如：

```text
museum-guide
museum-curator
museum-qa
museum-route-planner
museum-education
```

## 7. 简历表达建议

可以在简历中写成：

```text
设计并实现个人多 Agent 平台 myMinions，基于 Python 构建可复用运行时层和 capability 注册机制，支持 Discord 交互、LLM 调用、MCP 工具接入、长期记忆、RAG 知识库问答和定时任务触发。首个 capability coros-report 接入 COROS 运动数据，通过 DeepSeek 生成个性化训练报告，支持自动检测新运动并推送到 Discord；同时基于跑步书籍 PDF 构建 embedding 检索知识库，实现中文训练问答和运动主观感受记录。项目中解决了 MCP 授权地址匹配、Discord 频道权限控制、RAG 中文检索、embedding 批量限制、模型索引一致性、自动报告防重复推送和多能力路由扩展等问题。
```
