# myMinions 项目迭代报告

## 1. 项目背景

本项目最初从学习 `pi agent` 开始，目标是理解 CLI/TUI、Agent、Skill、MCP、API 等概念，并在此基础上开发一个个人可扩展的 Agent 系统。

后续项目方向逐渐明确为：构建一个属于自己的多 Agent 平台 `myMinions`，先实现运动分析能力 `coros-report`，再扩展厨房采购能力 `kitchen-assistant`。

`coros-report` 的目标是接入 COROS 官方运动数据，通过大模型生成个性化运动报告，并进一步支持基于跑步书籍和跑步长视频字幕的训练问答。

`kitchen-assistant` 的目标是接入 B 站做菜视频字幕，从视频中提取菜谱，保存菜谱库，并支持选择菜谱加入采购清单、记录买回来的库存、提醒快过期食材和推荐今天可以做什么。

## 2. 项目结构演进

最开始功能集中在 `coros-report` 中。后续为了支持未来多个 Agent，项目结构被调整为：

```text
myMinions/
├── src/
│   ├── bot/              # Discord 交互层
│   ├── runtime/          # 可复用运行时能力
│   ├── integrations/     # 外部服务接入
│   ├── orchestrator.py   # 主 Agent 调度层
│   └── registry.py       # capability 注册与命令映射
├── agents/
│   ├── coros-report/
│   │   ├── agent/        # coros-report 专属 capability 逻辑
│   │   └── docs/         # coros-report 文档
│   └── kitchen-assistant/
│       └── agent/        # kitchen-assistant 专属 capability 逻辑
├── data/
│   ├── memory.json             # 记忆
│   ├── knowledge/              # RAG 知识库
│   └── kitchen-assistant/      # 菜谱、采购清单、库存数据
├── docs/                 # 项目级文档
└── scripts/
    └── ingest_books.py   # PDF / 跑步视频字幕导入脚本
```

这个调整解决了一个核心问题：如果以后继续添加新的 Agent，不需要每个 Agent 都重复写 Discord、LLM、记忆、RAG、调度器等基础能力。

后续又进一步明确了模块边界：

```text
capability.py = 定义能力包、文本命令和执行上下文
registry.py = 注册能力包并维护 command -> capability 映射
orchestrator.py = 主 Agent 调度层，负责频道权限、统一上下文和能力分发
coros_capability.py = 把 coros-report 包装成第一个 capability
auto_report.py = coros-report 专属业务逻辑，负责查新运动和生成报告
scheduler.py = 通用触发器，负责什么时候执行
discord_bot.py = 交互入口，只负责接收消息并交给 orchestrator
kitchen_capability.py = kitchen-assistant 的命令入口
pantry.py = 菜谱、采购清单、库存、保质期和今日推荐逻辑
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
/running-video
/feel
/feelings
/capabilities
/kitchen-add
/kitchen-recipes
/kitchen-plan
/kitchen-shopping
/kitchen-remove-shopping
/kitchen-bought
/kitchen-use
/kitchen-pantry
/kitchen-today
/kitchen-expiring

!coros
!coros-tools
!running
!running-video
!feel
!feelings
!capabilities
!kitchen add
!kitchen recipes
!kitchen plan
!kitchen shopping
!kitchen remove-shopping
!kitchen bought
!kitchen use
!kitchen pantry
!kitchen today
!kitchen expiring
```

并且按 capability 限制指定频道使用：

```env
DISCORD_RUNNING_CHANNEL_ID=1537316749622386718
DISCORD_COOKING_CHANNEL_ID=1537873359130333184
```

这样可以避免机器人在其他频道乱响应。运动相关命令只在 running 频道生效，厨房相关命令只在 cooking 频道生效。

后续 Discord 交互层加入错误回传。命令执行、slash command 和普通消息处理出现异常时，不只在终端打印，也会向当前频道发送简短错误信息：

```text
执行 `running` 失败。
<错误摘要>
```

错误消息会截断长度，避免把完整 traceback、环境变量或敏感信息直接发到 Discord。

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
    athlete_profile
    goals
    preferences
    injury_notes
    latest_reported_activity_id
```

这个模块为后续个性化训练建议打基础，例如记录用户目标、伤病、训练偏好、近期状态等。

后续 `running` 问答加入了自动长期记忆抽取。用户在训练问题中明确提供的稳定信息会写入：

```text
athlete_profile:
  body_metrics:
    age
    height_cm
    weight_kg
  current_times:
    half_marathon
    marathon
    five_k
    ten_k
  training_context:
    training_days_per_week
    weekly_mileage_km
    recent_long_run_km
  goals
  race_notes
  injury_notes
  preferences
```

例如用户说“我现在半马 1:40，全马 4:30”，系统会把半马和全马当前成绩写入 `athlete_profile.current_times`。如果用户补充“全马后半程抽筋、补给不足”，则作为 `race_notes` 保存，方便后续判断全马短板时复用。

为了避免污染记忆，系统只保存用户明确说出的信息，不根据模型猜测写入年龄、身高、体重、成绩、伤病或目标。

### 3.5 PDF RAG 跑步知识库

用户上传跑步书籍 PDF 后，项目加入 RAG 功能，让 Agent 可以基于书籍回答训练问题。

流程是：

```text
PDF / 跑步视频字幕
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

后续 RAG 来源从单一 PDF 扩展到 B站跑步长视频字幕：

```text
!running-video <B站BV号或链接>
/running-video
```

新增流程：

```text
用户发送跑步教学长视频链接
-> 复用 B站字幕抓取能力
-> 保存完整字幕到 data/knowledge/coros-report/videos/
-> 重新执行 ingest_books.py
-> 将 PDF 和视频字幕统一切分成 chunks
-> 更新 keyword index / embedding index
-> 后续 running 问答和训练安排可以检索这些视频知识
```

这个设计和厨房 Agent 的视频处理不同：厨房 Agent 会从视频字幕中提取菜谱结构；跑步 Agent 不先总结视频，而是把完整字幕保存成“可检索资料”，让 RAG 在制定训练内容时按问题检索相关片段。

RAG 回答会在正文后追加 Markdown 引用块，展示本次检索到的短原文：

```markdown
## 引用原文

**[1] Daniels Running Training Method.pdf p.12**

> 原文短摘录...
```

这样 Discord 会显示成带竖线的引用样式，便于区分“Agent 解释”和“知识库原文依据”。

`running` 问答也从“直接回答”升级为“先判断信息是否足够”。当用户给出的问题不足以支持精准判断时，Agent 必须：

```text
先给临时判断
说明为什么只是候选解释
结合 RAG 知识指出可能方向
追问关键上下文
避免把缺失信息包装成确定答案
```

例如用户说“半马 1:40，全马 4:30，想提高全马”，Agent 应识别出全马成绩明显慢于半马能力推算，先把“马拉松专项耐力、补给、配速、长距离、天气、抽筋或伤病”等列为候选瓶颈，再追问当时全马发生了什么、近 8 周跑量、最长跑、年龄、身高体重和目标日期等信息。

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

### 3.8 主框架 Capability / Registry / Orchestrator 重构

为了让 `myMinions` 更接近 OpenClaw / Codex 这类“主运行时 + 能力扩展”的结构，项目加入了 capability 注册机制。

核心变化是：

```text
之前：
Discord bot
-> 直接 import coros-report 的 agent / knowledge / feelings / auto_report
-> 直接调用具体函数

现在：
Discord bot
-> MainAgentOrchestrator
-> CapabilityRegistry
-> coros-report / kitchen-assistant capability
-> 对应 command handler
```

新增的通用框架文件：

```text
src/runtime/capability.py
= 定义 Capability、TextCommand、CommandContext，并支持 capability 声明所属频道环境变量

src/registry.py
= 加载所有 capabilities，注册命令，并维护 command -> capability 的映射

src/orchestrator.py
= 主 Agent 调度层，负责频道权限判断、统一 CommandContext、文本消息分发和 startup handler 执行
```

`coros-report` 被包装成第一个 capability：

```text
agents/coros-report/agent/coros_capability.py
= 注册 coros-report 的命令和启动任务
```

`kitchen-assistant` 被包装成第二个平行 capability：

```text
agents/kitchen-assistant/agent/kitchen_capability.py
= 注册 kitchen-assistant 的命令入口
```

当前执行链路是：

```text
Discord slash / ! 文本消息
-> discord_bot.py
-> MainAgentOrchestrator
-> CapabilityRegistry
-> coros-report 或 kitchen-assistant
-> 返回 Discord
```

频道权限从 `discord_bot.py` 中抽离，改为由 capability 自己声明：

```text
coros-report -> DISCORD_RUNNING_CHANNEL_ID
kitchen-assistant -> DISCORD_COOKING_CHANNEL_ID
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

这次重构后，`coros-report` 不再是整个系统本身，而是 `myMinions` 主框架里的第一个能力包；`kitchen-assistant` 是第二个平行能力包。未来新增 `museum-guide`、`calendar-agent`、`study-agent` 等能力时，可以按同样方式新增 capability，由主 Agent 统一调度，而不是继续把逻辑堆进 `discord_bot.py`。

### 3.9 Python 项目迁移到 uv

为了让本地和服务器部署环境更稳定，项目从单纯的 `requirements.txt` 迁移到 `uv` 管理。

新增：

```text
pyproject.toml
= 定义 Python 项目依赖

uv.lock
= 锁定实际安装版本
```

新的运行方式：

```bash
uv sync
uv run python src/main.py
```

导入跑步书籍：

```bash
uv run python scripts/ingest_books.py
```

原来的 `.venv` 使用方式仍然可以保留，但后续本地和 VPS 更推荐用 `uv sync` 和 `uv run`。

### 3.10 coros-report 引入 LangGraph 工作流

为了让运动报告不再只是一个“大函数”直接生成，`coros-report` 内部被改造成 LangGraph StateGraph 工作流。

这次改造没有改变外部交互方式。用户仍然通过：

```text
!coros 我想看一下今天的训练分析
/coros
```

触发运动报告。变化发生在 `coros-report` 内部：

```text
之前：
coros_capability.py
-> agent.py generate_coros_report()
-> 一次性完成工具规划、COROS 数据获取和报告生成

现在：
coros_capability.py
-> graph.py generate_coros_graph_report()
-> LangGraph 分节点执行
-> agent.py 提供具体 COROS 和 LLM 业务函数
```

新增文件：

```text
agents/coros-report/agent/graph.py
= 定义 CorosGraphState、LangGraph 节点、条件边和 generate_coros_graph_report()

agents/coros-report/agent/shadowrunner_prompt.py
= 使用 ShadowRunner 跑者决策框架作为新的运动报告系统提示词

agents/coros-report/agent/tool_planner_prompt.py
= 单独保存 COROS MCP 工具规划提示词，避免继续依赖旧 prompt.py
```

`agent.py` 被拆分为更细的业务函数，供 LangGraph 节点调用：

```text
plan_coros_tools()
= 根据用户请求和 COROS MCP 工具列表规划工具调用

fetch_coros_results()
= 执行 COROS MCP 工具调用并收集结果

render_coros_report()
= 基于 COROS 结果、memory 和 prompt 生成报告

generate_coros_report()
= 保留旧入口，方便回退或复用
```

后续报告风格从原来的 `prompt.py` 切换为 ShadowRunner 框架：

```text
阶段 / 瓶颈
适用域
边际收益
最小可逆实验
停止条件
边界说明
```

旧的 `agents/coros-report/agent/prompt.py` 暂时保留，但报告生成和自动报告不再从它导入 `REPORT_SYSTEM_PROMPT`。

当前 LangGraph 节点流程：

```text
START
-> route_request
-> plan_tools
-> fetch_data
-> generate_report
-> critic_review
-> 条件分支：
   review_passed = true  -> final_report
   review_passed = false -> revise_report -> final_report
-> END
```

其中：

```text
route_request
= 当前先固定为 workout_report，后续可升级为自然语言意图识别

plan_tools
= 调用 agent.py 中的 plan_coros_tools()

fetch_data
= 调用 agent.py 中的 fetch_coros_results()

generate_report
= 调用 agent.py 中的 render_coros_report()

critic_review
= 使用 LLM 检查报告是否编造数据、泄露内部 ID / 坐标 / FIT、给出医疗诊断或缺少固定结构

revise_report
= 如果审查不通过，使用 LLM 根据审查意见修订报告

final_report
= 返回最终报告
```

这次升级的核心价值是：

```text
可观察：每一步都有明确 state 字段，例如 tool_calls、tool_results、draft_report、review_notes、final_report
可扩展：后续可以插入天气、主观感受、RAG、周训练负荷等节点
可分支：例如室内跑跳过天气节点，户外跑进入天气分析节点
可评测：可以分别评测工具规划、数据获取、报告初稿、审查结果和最终报告
更可靠：增加 critic_review / revise_report，形成基础 Reflection 链路
```

当前需要注意的是：LangGraph 只改变了 `coros-report` 的内部工作流；自然语言自动唤起能力由 `src/orchestrator.py` 中的主 Agent 路由层处理。

### 3.11 主 Agent 自然语言路由

为了让交互不再完全依赖 `!coros`、`!kitchen` 这类显式命令，`src/orchestrator.py` 新增了 LLM 自然语言路由。

原来的触发方式：

```text
!coros 我想看一下今天的训练分析
!kitchen bought 鸡腿 1000g
```

现在也支持在对应频道中直接发送：

```text
我想看一下今天的训练分析
轻松跑应该怎么判断强度
今天腿很沉，RPE 7
我今天买了鸡腿 1000g
今天有什么快过期的食材
今天能做什么菜
https://www.bilibili.com/video/BV...
把这个跑步长视频加入知识库 BVxxxx
```

主 Agent 会把这些自然语言转成内部命令：

```text
我想看一下今天的训练分析
-> coros

轻松跑应该怎么判断强度
-> running

今天腿很沉，RPE 7
-> feel

我今天买了鸡腿 1000g
-> kitchen bought 鸡腿 1000g

今天有什么快过期的食材
-> kitchen expiring

今天能做什么菜
-> kitchen today

B站链接 / BV号
-> kitchen add <video>

把这个跑步长视频加入知识库 BVxxxx
-> running-video BVxxxx
```

当前策略是 LLM 结构化路由。主 Agent 会把当前频道允许的命令和用户原话发给 DeepSeek，让模型返回：

```json
{
  "command": "coros",
  "argument": "我想看一下今天的训练分析",
  "confidence": 0.91,
  "reason": "用户想看训练分析"
}
```

系统不会直接相信模型输出，而是继续做四层校验：

```text
command 必须在当前频道 allowed_commands 里
confidence 必须高于阈值
kitchen 参数必须符合内部 action 格式
command = none 时不触发任何能力
```

路由仍然遵守频道限制：

```text
running 频道
-> 只触发 coros-report / running / running-video / feel / feelings

cooking 频道
-> 只触发 kitchen-assistant
```

因此在 cooking 频道说“我想看今天训练分析”不会触发运动报告；在 running 频道说“我今天买了鸡腿 1000g”也不会触发厨房入库。

新增开关：

```env
NATURAL_LANGUAGE_ROUTING_ENABLED=true
```

如果需要临时关闭自然语言路由，可以设置：

```env
NATURAL_LANGUAGE_ROUTING_ENABLED=false
```

还可以调整 LLM 路由置信度阈值：

```env
NATURAL_LANGUAGE_ROUTING_CONFIDENCE=0.7
```

### 3.12 kitchen-assistant 厨房采购能力

项目新增第二个 capability：`kitchen-assistant`。它和 `coros-report` 平行，不是写进 `coros-report` 里的子功能。

核心流程从最初的“发送视频后直接加入采购清单”，调整为更可控的两段式流程：

```text
发送 B站 BV号或链接
-> 抓取视频字幕
-> DeepSeek 从字幕提取菜名、食材、调料、步骤
-> 只保存菜谱
-> 用户选择某个菜谱
-> 再加入采购清单
```

这样可以避免每收藏一个视频都自动污染下次购物清单。

当前 kitchen 文本命令：

```text
!kitchen add <B站BV号或链接>
= 抓字幕并保存菜谱，不自动加入采购清单

!kitchen recipes
= 查看已保存菜谱

!kitchen plan <菜谱ID或菜名>
= 选择菜谱并加入采购清单

!kitchen shopping
= 查看待采购清单

!kitchen remove-shopping <食材>
= 从待采购清单移除一项

!kitchen bought <食材> <数量>
= 记录买回来的食材，并自动估算保质期

!kitchen use <食材> <数量>
= 记录消耗食材，让它从当前库存中消失

!kitchen pantry
= 查看当前库存

!kitchen expiring
= 查看未来 3 天内快过期食材

!kitchen today
= 根据库存匹配已保存菜谱，推荐今天可以做什么
```

对应 Discord slash command：

```text
/kitchen-add
/kitchen-recipes
/kitchen-plan
/kitchen-shopping
/kitchen-remove-shopping
/kitchen-bought
/kitchen-use
/kitchen-pantry
/kitchen-expiring
/kitchen-today
```

### 3.13 Agent 评估体系

为了避免“先射箭再画靶”，项目将原来的简单回归测试升级为更标准的 eval 结构：先定义评估目标、指标和阈值，再用 golden dataset 和 judge 执行评分。

```text
evals/
├── specs/       # 评估目标、指标、阈值
├── datasets/    # golden cases 和反例
├── fixtures/    # 后续外部输入样本
├── judges/      # 评分器
├── traces/      # 后续 Agent 执行轨迹
├── README.md
└── run_evals.py
```

当前第一批标准 eval 是 `natural_language_routing`：

```text
spec:
evals/specs/natural_language_routing.json

dataset:
evals/datasets/natural_language_routing.json

judge:
evals/judges/natural_language_routing.py
```

它覆盖主 Agent 自然语言路由，不真实调用 DeepSeek、Discord、COROS 或 B站服务，而是用固定的 LLM 输出样本测试主 Agent 的后处理和安全校验。

当前指标和阈值：

```text
route_accuracy >= 0.90
rejection_accuracy >= 1.00
cross_channel_rejection >= 1.00
low_confidence_rejection >= 1.00
invalid_argument_rejection >= 1.00
```

测试样例覆盖：

```text
running 频道允许 coros / running / running-video / feel / feelings
cooking 频道只允许 kitchen
LLM 返回跨频道 command 时会被拒绝
LLM 返回 confidence 低于阈值时会被拒绝
LLM 返回 kitchen 参数格式不完整时会被拒绝
LLM 返回 running-video 但没有 B站链接或 BV号时会被拒绝
典型自然语言能映射到预期内部命令
```

运行方式：

```bash
uv run python evals/run_evals.py
```

当前结果：

```text
Suite: natural_language_routing
Cases: 15/15 passed
- route_accuracy: 1.00 >= 0.90 PASS
- rejection_accuracy: 1.00 >= 1.00 PASS
- cross_channel_rejection: 1.00 >= 1.00 PASS
- low_confidence_rejection: 1.00 >= 1.00 PASS
- invalid_argument_rejection: 1.00 >= 1.00 PASS
```

这套评估目前属于离线、确定性 eval，重点验证主 Agent 的路由安全边界和稳定性。后续可以继续增加：

```text
COROS LangGraph 节点评估
COROS 报告结构评估
critic_review 是否能发现不合格报告
RAG 检索命中率评估
kitchen 字幕提取 JSON 质量评估
真实 LLM 路由 golden cases
```

`kitchen-bought` 最初需要输入食材、数量、保存方式、保质期。后续为了降低使用成本，改成只输入：

```text
食材 + 数量
```

例如：

```text
!kitchen bought 鸡腿 1000g
```

系统根据食材名自动估算保质期：

```text
水产：1天
肉类：2天
豆制品：2天
叶菜：3天
蔬菜：4天
水果：5天
奶制品：7天
鸡蛋：21天
主食：30天
调料：180天
默认：7天
```

数据文件：

```text
data/kitchen-assistant/recipes.json
= 已保存菜谱

data/kitchen-assistant/shopping_list.json
= 采购清单，使用 pending / bought / removed 状态

data/kitchen-assistant/pantry.json
= 当前库存，使用 active / used 状态
```

这个设计保留历史记录，但列表展示时只显示当前有效项。

### 3.14 多轮对话会话历史

在此之前，每一次用户消息都是一次独立的、无历史的 LLM 调用，Agent 只能依赖 `data/memory.json` 里的结构化长期档案。这导致跑步教练追问用户之后，拿到答案却无法把答案和自己刚问过的问题对应起来。

为此新增了通用运行时模块 `src/runtime/conversation.py`：

```text
按 (conversation_id, topic) 隔离会话
保留最近 6 轮 user / assistant 消息
单条消息截断到 1200 字符
使用 threading.Lock 保护，兼容 Web 的 ThreadingHTTPServer
提供 get_history / append_turn / last_user_message / clear_history
```

`src/runtime/llm.py` 的 `complete_text` 增加可选的 `history` 参数，把历史作为真实的多轮 messages 插在 system 和当前问题之间：

```text
system  -> 角色和输出规则
user    -> 上一轮用户消息
assistant -> 上一轮 Agent 回答
user    -> 本轮用户消息
```

会话 ID 的来源按入口区分：

```text
CommandContext 增加 conversation_id 字段
orchestrator 优先读取 channel 自带的 conversation_id
Discord 侧回落到 channel:{频道ID}，按频道隔离
Web 侧由前端 sessionStorage 生成 UUID 随请求发送
web_server 对 session_id 做白名单过滤并加 web: 前缀
```

这里 Web 侧必须单独处理，因为 `WebChannel.id` 恒为 `-1`，如果直接用它当会话 ID，所有访问者会共用同一份对话历史。

同时 `answer_running_question` 的知识库检索 query 也做了调整。多轮追问时用户这一轮往往只是「1 每周40公里 2 主要是间歇」这样的裸答案，单独拿它去检索会跑偏，因此在有历史时拼上上一轮用户消息来保留话题。

### 3.15 会话边界与 pending 追问状态

有了会话历史之后，接着要回答一个问题：在 Discord 里，怎么判断一条消息属于上一段对话，还是一段全新的对话。

第一层边界是**频道硬隔离**，这一层项目本来就有，不需要新增代码：

```text
coros-report 绑定 DISCORD_RUNNING_CHANNEL_ID
kitchen-assistant 绑定 DISCORD_COOKING_CHANNEL_ID
做饭频道的自然语言白名单里只有 kitchen，路由不到 running
conversation_id 本身就是 channel:{频道ID}，历史天然按频道分开
```

所以跑步的对话不可能出现在做饭频道，反之亦然。

第二层边界是**空闲超时**。原来一个频道等于一段永不结束的对话，只靠 6 轮滑动窗口自然遗忘，隔几天再说话仍然会接上旧上下文。现在会话记录 `updated_at`，读取时发现超过空闲时间就整个丢弃：

```text
CONVERSATION_IDLE_MINUTES 控制，默认 60 分钟
过期时 history 和 pending 一起清空
过期判断发生在读取时，不需要额外的清理定时器
```

在此基础上加入了 **pending 追问状态**，用来把用户的回答和 Agent 的问题绑定起来。教练回答生成后，从「还需要确认 / 仍需确认」小节里抽出问题列表存进会话；下一条消息进来时：

```text
orchestrator 发现该会话有 pending -> 跳过自然语言路由，直接投给 running
knowledge.py 把这批问题原文拼进 prompt，告诉模型用户正在按顺序回答它们
模型据此走输出模式 B
新一轮回答重新抽取问题，没有追问就等于清空 pending
```

跳过自然语言路由这一步同时解决了一个隐患：像「1 每周40公里 2 主要是间歇」这样的裸答案，交给路由器很容易被判成 `none` 或置信度不足而被拒绝，用户会觉得 Agent 突然不理人了。

需要强调的是，pending 捷径不绕过频道权限。它先检查 `is_allowed_for_command(channel_id, "running")`，做饭频道即使有残留 pending 也不会被触发。

### 3.16 Web 控制台重构为对话产品

原来的 Web 页面是三栏「控制台」布局：左侧能力列表和运行时说明，中间对话，右侧执行轨迹和架构图。它把置信度、工具名、LangGraph 节点、内部 capability 名和命令全都摊在页面上，更像一个调试面板而不是产品。顶部还有四个示例按钮，容易让人误以为需要先手动选择 Agent。

实际上 Web 入口早就走的是和 Discord 同一套自然语言路由，用户从来不需要选能力：

```text
dispatch_web_text -> _route_natural_language_from_allowed -> DeepSeek 输出 intent
WEB_AGENT_MODE=real 时调用真实 capability
WEB_AGENT_MODE=demo 时走本地关键词假路由，只用于离线演示
```

所以这次重构的重点是把这件事表达出来，而不是新增能力：

```text
三栏改为单栏居中对话，最大宽度 740px
移除能力列表、运行时面板、执行轨迹、置信度、工具、流程节点和架构图
顶部四个示例按钮改为空状态下的三个建议，开始对话后消失
新增「新对话」按钮，重新生成 session_id 并清空记录
「正在…」这类进度提示改为临时的思考中指示器，不再留在对话记录里
回答按 Markdown 渲染标题、引用、有序和无序列表
输入框支持自适应高度、Enter 发送、Shift+Enter 换行
```

后端也收敛了对外暴露的信息，`/api/capabilities` 不再返回内部能力名、路由提示和命令列表，只保留空状态用的示例问题；Demo 模式的回复文案也从「已路由到 coros-report」这种内部叙述改成正常的用户视角回答。

### 3.17 Discord 等待反馈

Discord 侧一次请求要走自然语言路由、MCP 取数或知识库检索，再加上生成，通常十几秒才有第一条回复，期间界面上完全没有动静，容易让人以为 bot 挂了。

这里先用 Discord 原生的「正在输入」指示器补上等待反馈：

```text
斜杠命令 -> interaction.channel.typing() 包住 dispatch_command
自然语言消息 -> message.channel.typing() 包住 dispatch_text
```

自然语言那条路径加了一个判断，只在能力频道亮指示器。因为 `on_message` 会收到 bot 可见的所有频道的消息，非能力频道 `dispatch_text` 会立刻返回，亮指示器不但没意义，还会让 bot 看起来在到处打字。

需要说明的是，这不是流式输出。Discord 没有流式消息接口，真正的流式只能靠反复 `message.edit()` 模拟，而且受每频道约 5 次 / 5 秒的编辑速率限制和单条 2000 字符上限约束。更前置的问题是 `complete_text` 目前没有开启 `stream=True`，DeepSeek 是一次性返回完整响应，所以现阶段并没有可以流式输出的内容。真流式留作后续。

### 3.18 引入真正的 tool call

项目此前虽然有「工具调用」，但只有 COROS 那一处，而且是手搓的：把 MCP 工具清单塞进 prompt，让模型用 `complete_json` 吐出一个 `tool_calls` 数组，代码再逐个执行。模型侧从来没有用过 `tools` 参数，本质上只是在填 JSON 模板。这种一次性规划有个硬伤：模型必须在看到任何数据之前就决定调什么，拿到结果后没有第二次机会。

这次新增了通用的工具运行时 `src/runtime/tools.py`：

```text
Tool         = 名称、描述、JSON Schema 参数、处理函数
ToolRegistry = 注册表，负责生成 schema 和执行调用
run_tool_loop = 执行循环，模型要工具就执行并把结果喂回去，直到它给出文本回答
```

`src/runtime/llm.py` 相应增加了 `complete_with_tools`，真正把 `tools` 和 `tool_choice` 传给模型。

有两个设计约束是刻意的：

```text
轮数上限（默认 4）用尽后，收掉工具再要一次最终回答，避免无限循环
工具往返只存在于单次调用内部，不写进会话历史
```

第二条尤其重要。`conversation.py` 的历史只存 user / assistant 两种消息，如果把 `assistant(tool_calls)` 和 `role="tool"` 也存进去，6 轮窗口很快就会被中间过程撑爆，而这些中间过程对下一轮理解用户意图没有价值。

首批接入三个工具，都在 `agents/coros-report/agent/running_tools.py`：

```text
training_paces        由比赛成绩算 VDOT 和 E/M/T/I/R 配速，并预测其他距离成绩
race_countdown        返回今天日期，以及距离目标比赛还有多少天和多少周
save_running_profile  把用户明确说出的长期信息写入跑步档案
```

选这三个的共同理由是：**模型自己做这些事不可靠**。配速和 VDOT 是纯数学，模型心算经常出错而且错得很隐蔽；模型不知道今天几号，而周期化训练必须知道还剩几周；档案写入原来是每条消息都无条件跑一次 LLM 抽取，哪怕用户只是问「什么是阈值跑」，改成工具后由模型判断有没有东西要存。

`training_paces` 用 Daniels/Gilbert 公式实现，实测与书中表格吻合：

```text
半马 1:40:00 -> VDOT 45.1（表格 45，对应半马 1:40:20）
T 配速 4:37/km（表格 4:38）
I 配速 4:14/km（表格 4:16）
5k 20:00 -> VDOT 49.8（表格 49.8）
```

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
新增 src/registry.py 统一注册 capabilities，并维护 command -> capability 映射
新增 src/orchestrator.py 作为主 Agent 调度层
新增 agents/coros-report/agent/coros_capability.py 包装 coros-report
新增 agents/kitchen-assistant/agent/kitchen_capability.py 包装 kitchen-assistant
Discord bot 只调用 orchestrator，不再直接依赖具体业务实现
```

调整后架构变为：

```text
Discord bot
-> MainAgentOrchestrator
-> CapabilityRegistry
-> capability
-> agent-specific handlers
```

其中频道权限从 Discord 层抽出，变成 capability 自己声明所属频道：

```text
coros-report -> DISCORD_RUNNING_CHANNEL_ID
kitchen-assistant -> DISCORD_COOKING_CHANNEL_ID
```

这让 `myMinions` 从单一业务 Agent 应用，升级成一个可以继续扩展多个能力包、并由主 Agent 统一调度的小型 Agent 平台。

### 问题 14：B站字幕工具安装后找不到 MCP 模块

运行：

```bash
bilibili-subtitle-fetch init
```

出现：

```text
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

原因是 `bilibili-subtitle-fetch` 依赖旧版 MCP 的 `mcp.server.fastmcp` 路径，但 uv tool 安装时解析到了 `mcp 2.0.0`，新版 MCP 中路径已经变化。

解决方案：

```bash
uv tool install --python 3.13 --force bilibili-subtitle-fetch --with "mcp[cli]<2.0.0"
```

这样把 `bilibili-subtitle-fetch` 的独立工具环境限制在兼容的 MCP 1.x 版本。

### 问题 15：B站 Cookie 配置文件不在默认路径

最初运行字幕抓取时出现：

```text
Error: Credential config not found. Run `bilibili-subtitle-fetch init` first.
```

原因是 Cookie 配置被放到了 `kitchen-assistant` 目录下，而工具默认只会去：

```text
~/.config/bilibili-subtitle-fetch/config.toml
```

解决方案：

```text
在 subtitle_fetcher.py 中固定传入 --config
默认读取 agents/kitchen-assistant/config.toml
同时支持 BILIBILI_SUBTITLE_CONFIG 环境变量覆盖
```

并在 `.gitignore` 中忽略：

```text
agents/kitchen-assistant/config.toml
```

避免把 B站 Cookie 上传到 GitHub。

### 问题 16：添加视频后自动加入采购清单不够可控

最初 `!kitchen add` 的行为是：

```text
抓字幕
-> 提取菜谱
-> 自动把所有食材加入采购清单
```

问题是：用户只是想收藏一个菜谱时，也会污染下次采购清单。

解决方案：

```text
!kitchen add
= 只保存菜谱

!kitchen recipes
= 查看已保存菜谱

!kitchen plan <菜谱ID或菜名>
= 用户确认后，再把该菜谱加入采购清单
```

调整后，菜谱库和采购计划被拆开，使用更接近真实采购流程。

### 问题 17：采购入库命令输入成本太高

最初 `kitchen-bought` 需要输入：

```text
食材、数量、保存方式、保质期
```

问题是实际买菜时录入太麻烦。

解决方案：

```text
用户只输入食材和数量
系统根据食材类别自动估算保质期
库存记录保留 category、shelf_life_days、expires_at
```

这样 `!kitchen bought 鸡腿 1000g` 就能自动判断为肉类，默认 2 天过期。

### 问题 18：采购清单和库存需要删除/消耗

如果用户不想买某个采购项，或者食材已经做饭用掉，列表需要能变干净。

解决方案：

```text
!kitchen remove-shopping <食材>
= 把待采购项从 pending 标记为 removed

!kitchen use <食材> <数量>
= 把库存项从 active 标记为 used
```

这不是物理删除，而是状态变更。当前列表不再显示这些项目，但历史数据仍然保留，后续可以用于统计和复盘。

### 问题 19：Training Hub 能看到运动，但自动报告查不到记录

用户跑完步一个多小时后，COROS Training Hub 网站已经能看到本次运动，但自动报告没有推送。终端日志显示：

```text
coros-auto-report activity_lookup records=0
COROS auto report skipped: no recent activity found.
```

一开始排查了 Discord、scheduler、`.env` 和 memory：

```text
bot 进程正常运行
COROS_AUTO_REPORT_ENABLED=true
COROS_AUTO_REPORT_POLL_MINUTES=15
DISCORD_RUNNING_CHANNEL_ID 已配置
memory.json 里 latest_reported_activity_id 为空
```

随后直接调用 COROS MCP，发现 `queryUserInfo` 和 `queryDevices` 能返回数据，说明授权并没有完全失败。进一步扩大日期范围并查询 `querySportRecords` 后，发现 COROS MCP 实际返回了 2026-08-14 的 10.01 km 室内跑：

```text
Indoor Run — 2026-08-14
Distance: 10.01 km
Average Pace: 4:43 /km
Avg HR: 162 bpm
LabelId: 479624756220428391
SportType: 101
```

根因是：COROS MCP 的 `querySportRecords` 返回内容是 `content[0].text` 里的文本摘要，而不是结构化 JSON。原来的 `_activity_records()` 只会解析 JSON 对象，所以把真实存在的 8 条运动记录误判为 0 条。

解决方案：

```text
在 auto_report.py 中新增文本解析逻辑
从 COROS 文本摘要中提取 LabelId、SportType、startTimestamp、endTimestamp、日期和距离
让 latest_coros_activity() 能识别文本格式返回的活动记录
自动检查日志中增加 activity_key、labelId、sportType、时间戳等诊断信息
```

修复后，自动报告可以正确识别最新运动：

```text
activity_key=479624756220428391:101:1786737854:1786741430
date=2026-08-14
distanceKm=10.01
sportType=101
```

### 问题 20：切换 LangGraph 后 graph.py 找不到 agent.py 中的函数

在将 `coros-report` 切换为 LangGraph 后，启动时出现：

```text
ImportError: cannot import name 'fetch_coros_results' from 'agent'
```

原因是 `graph.py` 已经开始按节点导入：

```text
fetch_coros_results
plan_coros_tools
render_coros_report
```

但 `agent.py` 仍然只有旧的 `generate_coros_report()` 大函数，没有拆出这些节点可复用的业务函数。

解决方案：

```text
将 agent.py 中的一体化流程拆成三个函数：
- plan_coros_tools()
- fetch_coros_results()
- render_coros_report()

保留 generate_coros_report() 作为兼容入口
让 graph.py 调用拆分后的函数
让 coros_capability.py 调用 generate_coros_graph_report()
```

修复后验证通过：

```bash
uv run python -m compileall src agents/coros-report/agent agents/kitchen-assistant/agent
uv run python -c "import src.main; print('main import ok')"
```

### 问题 21：主 Agent 只能靠命令触发，不能理解自然语言

在引入 `MainAgentOrchestrator` 后，主 Agent 已经可以统一调度不同 capability，但用户仍然需要显式发送：

```text
!coros 我想看一下今天的训练分析
!kitchen bought 鸡腿 1000g
```

如果直接发送：

```text
我想看一下今天的训练分析
我今天买了鸡腿 1000g
```

系统不会自动唤起对应能力。

解决方案：

```text
在 src/orchestrator.py 中新增自然语言路由
使用 DeepSeek 输出结构化 JSON intent
running 频道只允许路由到 coros-report 相关命令
cooking 频道只允许路由到 kitchen-assistant
支持 NATURAL_LANGUAGE_ROUTING_ENABLED 开关
支持 NATURAL_LANGUAGE_ROUTING_CONFIDENCE 置信度阈值
```

第一版支持：

```text
训练分析 / 运动报告 / 配速 / 心率 / 恢复 -> coros
轻松跑 / 阈值 / 间歇 / 训练方法问题 -> running
RPE / 腿沉 / 疲劳 / 酸痛 -> feel
B站链接 / BV号 -> kitchen add
买了食材和数量 -> kitchen bought
用了食材和数量 -> kitchen use
快过期 / 保质期 -> kitchen expiring
采购清单 / 购物清单 -> kitchen shopping
库存 / 冰箱 -> kitchen pantry
今天做什么菜 -> kitchen today
```

为了降低 LLM 误路由风险，主 Agent 增加了白名单校验：

```text
LLM 返回 command = coros，但当前是 cooking 频道 -> 拒绝
LLM 返回 command = kitchen，但当前是 running 频道 -> 拒绝
LLM 返回 confidence 低于阈值 -> 拒绝
LLM 返回 kitchen bought 但没有食材或数量 -> 拒绝
```

这样自然语言路由由 LLM 负责理解语义，但最终执行仍由主 Agent 控制。

### 问题 22：多轮追问后 Agent 不记得自己问过什么

跑步教练在信息不足时会先给临时判断，然后追问 6 个问题。但用户按编号回答之后，Agent 并没有「拿到答案」的反应，而是又输出了一遍临时判断和一批新问题，像是重新开了一段对话。

排查后发现是四个层面叠加造成的：

```text
knowledge.py 的 answer_running_question 只接收当前这一条消息
llm.py 的 complete_text 每次只发 system + 单条 user
orchestrator 的自然语言路由同样无状态，把答案当成全新意图重新路由
RUNNING_KNOWLEDGE_PROMPT 把「还需要确认」写死成固定输出模板
```

对模型来说，第二轮输入是一条突然出现的带编号陈述句，它无法知道这是对自己上一轮提问的回答，只能当新问题重新处理。即使补上历史，写死的模板也会让它继续重复提问。

解决方案分两部分。第一部分是加入会话历史，见 3.14。第二部分是把输出模板从固定四段式改成两个互斥模式：

```text
模式 A = 首轮，或用户还没回答过任何追问
         临时判断 / 为什么这么判断 / 还需要确认 / 现在可以先做什么

模式 B = 本轮用户消息是在回答之前的追问
         结论更新 / 依据 / 接下来怎么练 / 仍需确认
```

并补充了配套的状态规则：

```text
历史是模型自己的记忆，不是外部资料
已问过或已回答的问题绝不重复提问
用户回复带编号列表时，按顺序映射回上一轮的问题
模式 B 的「仍需确认」最多 2 条，且必须是没问过的
追问满两轮后必须停止提问，直接给方案并标注假设
```

需要注意的是，长期记忆的抽取器仍然只看当前单条消息，`athlete_profile` 的 schema 也装不下「训练内容主要是间歇」「每三天一个休息日」这类信息。所以目前 Agent 能在当前会话内记住这些回答，但进程重启后会丢失，这两点留作后续改进。

### 问题 23：Web 端发完一条消息之后就再也发不出第二条

Web 控制台重构后发现，第一条消息能正常收到回答，但发送按钮之后一直保持禁用，用户发不出第二条消息。这等于让刚做好的多轮对话在网页端完全用不上。

排查发现问题出在 SSE 的连接头上：

```text
_stream_chat 显式发送了 Connection: keep-alive
BaseHTTPRequestHandler.send_header 读到 keep-alive 会把 close_connection 设为 False
handler 返回后连接不关闭
前端 reader.read() 永远等不到 done
streamChat 不返回，finally 里的 setBusy(false) 就不会执行
```

两端一起修：

```text
后端把 SSE 的 Connection 改成 close，让流结束后释放连接
前端收到 {"type": "done"} 事件就主动 cancel reader 并返回，不再依赖连接关闭
```

顺带修掉了另一个部署相关的坑：静态文件原来带 `Cache-Control: public, max-age=300`，而前端文件名没有版本号，所以每次部署后的 5 分钟内用户拿到的仍然是旧页面。现在静态资源统一改为 `no-cache`，每次回源校验。

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
支持解析 COROS MCP 文本格式活动列表
支持自动检查日志和 activity_key 诊断
支持 scheduler 定时触发
支持 latest_reported_activity_id 防重复推送
支持 capability 注册机制
支持 registry 命令映射
支持 MainAgentOrchestrator 主控调度
支持主 Agent 自然语言路由
支持 evals/specs/datasets/judges 标准评估结构
支持 LangGraph StateGraph 内部工作流
支持 critic_review / revise_report 基础 Reflection 链路
支持多轮对话会话历史，追问后能承接用户的回答
支持按 Discord 频道和 Web 浏览器会话隔离对话历史
支持会话空闲超时自动刷新
支持 pending 追问状态，把用户的回答绑定回 Agent 的问题
支持基于 tools 参数的真实 tool call 和多轮工具循环
支持 VDOT 配速换算、比赛倒计时和模型主动写入长期记忆三个工具
支持查看已加载能力包
```

当前 `kitchen-assistant` 已经具备：

```text
接入 B站字幕抓取工具
从字幕中提取菜谱
保存菜谱库
按菜谱 ID 或菜名加入采购清单
查看和移除待采购项
记录已采购食材
根据食材类型自动估算保质期
查看当前库存
记录食材消耗
查看快过期食材
根据库存推荐今天可以做什么
限制只在 cooking 频道响应
支持主 Agent 自然语言路由触发常用厨房操作
```

这已经不只是一个 prompt 问答机器人，而是一个具备主 Agent 调度层、自然语言路由、LangGraph 工作流、标准评估体系、外部数据接入、工具调用、知识库检索、长期配置、远程交互入口、自动触发能力、采购库存状态管理和 capability 扩展机制的个人 Agent 平台雏形。

## 6. 后续可继续扩展方向

后续可以继续添加：

```text
训练目标记忆
伤病和疲劳记录
周报 / 月报
训练计划生成
比赛备赛周期规划
kitchen 菜谱去重
kitchen 食材重量精确扣减
kitchen 购物清单合并同类项
kitchen 菜谱偏好记忆
kitchen 周菜单规划
kitchen 自动过期提醒
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
设计并实现个人多 Agent 平台 myMinions，基于 Python 构建 MainAgentOrchestrator 主控调度层、可复用运行时层和 capability 注册机制，支持 Discord 交互、自然语言意图路由、LLM 调用、MCP 工具接入、长期记忆、RAG 知识库问答、定时任务触发和多频道能力路由。首个 capability coros-report 接入 COROS 运动数据，并引入 LangGraph StateGraph 将工具规划、数据获取、报告生成、质量审查和修订输出拆成可观测工作流，通过 DeepSeek 生成个性化训练报告，支持自动检测新运动并推送到 Discord；同时基于跑步书籍 PDF 构建 embedding 检索知识库，实现中文训练问答和运动主观感受记录。第二个 capability kitchen-assistant 接入 B站字幕抓取工具，从做菜视频中提取菜谱，支持菜谱库、选择菜谱加入采购清单、采购入库、自动保质期估算、库存消耗、快过期提醒和基于库存的做菜推荐。项目搭建 evals 标准评估结构，以 spec 定义目标和阈值，dataset 固化 golden cases，judge 评估主 Agent 自然语言路由、跨频道拒绝、置信度阈值和工具参数校验。项目中解决了 MCP 授权地址匹配、Discord 频道权限控制、RAG 中文检索、embedding 批量限制、模型索引一致性、自动报告防重复推送、COROS MCP 文本格式解析、B站字幕工具版本兼容、Cookie 安全配置、多能力路由扩展、LangGraph 节点拆分和自然语言误路由等问题。
```
