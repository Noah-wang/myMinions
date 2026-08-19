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

### 3.19 上下文压缩

会话历史原来只有丢弃机制：超过 6 轮就把最老的一轮直接删掉。这样做简单可靠，但滚出窗口的信息会永久消失，尤其是那些没有对应档案字段的内容，比如「训练内容主要是间歇」「每三天一个休息日」。

现在把丢弃改成压缩：窗口溢出时，把最老的几轮交给模型压缩成摘要，摘要随后注入 prompt 继续参与对话。

```text
COMPRESS_BATCH_TURNS = 3   每次折叠 3 轮，不是每轮都压
SUMMARY_MAX_CHARS = 700    摘要本身有上限，不会无限增长
新摘要 = 压缩(已有摘要 + 这批对话)，滚动继承
```

批量折叠是关键的一个取舍。如果每轮溢出都压一次，等于每轮多一次 LLM 调用；按 3 轮一批之后，10 轮对话只触发 2 次压缩。

摘要不是作为消息插进 history，而是和长期记忆、RAG 片段一样注入到 user prompt 里：

```text
Question -> Summary of earlier turns -> Follow-up questions -> Long-term memory -> Knowledge excerpts
```

这样避免了在多轮消息里插入额外 system 消息带来的角色顺序问题，也和项目里已有的注入方式保持一致。

压缩提示词明确要求保留具体数字、配速和日期，不许改写成约数，也不许推断新信息，因为摘要最大的风险就是把「天气热导致后程掉速」压成「比赛发挥不佳」，那样信息就废了。

另外两个防护：

```text
压缩失败（超时、报错）时退回原来的丢弃行为，不影响本次回答
CONVERSATION_COMPRESSION_ENABLED=false 可以整体关掉，退回纯丢弃
```

需要说明的是，单条消息 1200 字符的截断保留了，它防的是单条超长消息把上下文撑爆，和窗口滚动是两件事。

### 3.20 RAG 分块策略重做与知识库质检

原来的分块是一套固定参数（`CHUNK_SIZE=1200`、`CHUNK_OVERLAP=180`）同时套在丹尼尔斯 PDF 和 B站字幕上。写了一个 `scripts/inspect_chunks.py` 体检之后发现，这套参数几乎全线失效：

```text
304 块里有 72 块是空的（占 24%），每一块还白算了一条向量
p25 长度只有 30 字符，中位 364，远小于设定的 1200
287 页里只有 10 页产生了多于 1 块 —— CHUNK_OVERLAP 几乎从未生效过
同一句表注重复 18 次，水印行重复 286 次
9 个块从句子中间开头，全是每页的第一块
```

最后一条暴露了根因：切块是**按页**做的，所以跨页的句子被硬切，而 overlap 只在页内起作用。1200 这个参数对绝大多数书页根本没机会生效，块大小实际由 PDF 排版决定。

重做后的策略：

```text
跨页合并      先把一个来源的所有页拼成整篇再切，overlap 真正生效
语义边界切分  优先在句末标点断开，找不到退而求其次找逗号顿号，都没有才硬切
重叠对齐      重叠起点也对齐到句子边界，否则每块都会从半句话开始
按行去噪      合并之前先删掉铺满全书的页眉页脚
文档级前缀    每块嵌入时拼上来源身份，原文不动
```

重建后的结果：

```text
145 块，0 空块，0 过短块，0 重复
长度 p25 1162 / 中位 1179，分布终于集中在设定值附近
从句子中间开头 1 块（0.7%）
```

有两个细节值得记下来，它们都是启发式误判：

**按绝对次数判定页眉页脚会误伤正文。** 第一版用「重复 5 次以上的短行」当阈值，结果把「热身，然后进行下列训练：」也删了——这句话在训练章节里本来就会反复出现。改成按页数取相对阈值（铺满 5% 以上页面才算页面装饰）之后，只删水印和表注两种。

**检测「从句子中间开头」的规则本身也会误判。** 第一版把「在」「其」「但」「是」都算作句中字符，于是「在跑步机上跑步的优势之一是……」这种完整句子被误报，指标一度显示 13%。收紧到只认真正不可能作句首的助词之后，实际值是 0.7%。

这两件事说明同一个道理：**启发式规则本身也需要被检验**，否则你会拿一个错的指标去调一个对的系统。

### 3.21 导入资料后自动质检

体检逻辑抽到 `src/runtime/knowledge_health.py`，同时被两处复用：

```text
scripts/inspect_chunks.py     人工命令行查看
inspect_knowledge_index 工具   Agent 通过 tool call 调用
```

这样保证人看到的和 Agent 看到的是同一份数据。

`!running-video` 导入 B站视频后，会跑一次只带质检工具的 tool loop，让模型自己调用工具、读统计数据、再用人话给出结论，附在导入结果后面。

这里的分工是刻意的：**质检本身是确定性的代码，一定会跑；但要不要提醒、提醒什么，交给模型判断。** 如果把整件事都做成确定性输出，用户看到的就是一堆 JSON；如果把质检本身也交给模型，它可能压根不查就开始编。

### 3.22 检索质量评测

3.20 重做分块之后有个绕不过去的问题：**怎么证明它真的变好了？** 空块从 72 降到 0、长度分布集中，这些都只是"形状"指标，说明不了检索是不是更准。没有这个证明，调 `CHUNK_SIZE` 就永远是拍脑袋。

所以补了第二个标准 eval `rag_retrieval`，和已有的 `natural_language_routing` 并列。

它和路由评测有个本质区别：**路由评测刻意不打真实服务**，用固定的 LLM 输出样本测后处理；而检索评测**必须打真实链路**，查询要过 embedding、候选要过真实索引，否则测的就不是检索。带来的约束是它只能在有索引的环境跑，所以索引缺失时整个 suite 跳过而不是判失败。

几个设计决定：

```text
判定用关键词而不是页码
  页码会随分块策略漂移，而分块策略正是这个评测要衡量的东西，
  拿会漂移的东西当基准等于没有基准

报告实际检索模式
  rag.py 在向量数量对不上时会静默降级到关键词检索，
  不报告这一点的话指标好看但可能根本没测到向量链路

视频单独出指标
  视频只占索引 6%，专门用视频独有的术语出题，
  防止它被书淹没

阈值设在基线略下方
  它的作用是回归护栏，不是许愿
```

首次基线（145 块、向量检索）：

```text
22/22 通过
hit_rate_at_5   1.00
mrr_at_5        0.90
book_hit_rate   1.00
video_hit_rate  1.00
```

`video_hit_rate = 1.00` 是个意外结果。之前担心视频占比只有 6% 会被淹没，实际上只要用视频独有的术语提问就能稳定命中——**这个担心被数据否掉了**，也说明"来源加权"这件事在当前语料规模下不需要做。

过程中还暴露出一个值得记的坑：`重复跑和间歇跑有什么区别` 一开始判失败，查下来是**基准本身写错了**——书里主要用「R训练」（104 次）和「R配速」（87 次），而「重复跑」只出现 23 次。检索其实成功了，排第一的块同时含 R训练 和 R配速，是我用了书里的低频说法当基准。

这和 3.20 里那两个启发式误判是同一类错误：**衡量工具本身也会错，而且错的时候会让你去修一个根本没坏的东西。** 定基准时必须先确认这个词在语料里真的是主流说法。

### 3.23 父子块实验：一次被数据否掉、再被数据救回的改动

3.22 建好检索评测之后，第一件事就是用它做了一组真实实验。

起因是一个假设：**大块（1200 字符）会稀释定点查询**——问「T 配速累计量不能超过周跑量的百分之多少」这种有确切答案的问题时，具体数字被埋在一大段论述里，向量会被周围内容拉偏。解决方案是父子块：子块小、负责向量匹配，父块大、负责投喂给模型。

先往评测集加了 8 道定点题、加上 `hit@1` 和 `pinpoint_hit_rate` 指标，然后量基线：

```text
单层块 145 个
hit@1                   0.83
pinpoint_hit_rate_at_1  0.88   ← 比整体还高
```

**假设当场被推翻。** 定点题表现比概念题更好，因为它们自带「周跑量的10%」「90~120秒」这类罕见词，向量区分度反而更强；概念题措辞泛，才容易撞车。

即便如此还是做了父子块，理由换成了「语料会持续增长，竞争者变多时精度更重要」。第一版子块设 400 字符：

```text
hit@5  1.00 → 0.97      book_hit  1.00 → 0.96
hit@1  0.83 → 0.80      pinpoint@1 0.88 → 0.75
mrr    0.91 → 0.88      pinpoint@5 1.00 → 0.88
```

七项里六项变差，定点题降幅最大。原因是子块切得太碎：一个 400 字的子块可能整段都是展开论述，主题句落在前一个子块里；而且「按父块取子块最高分」等于在 4 个有噪声的估计里取最大值，放大了假阳性。失败那题很典型——返回的全是正确块的邻居页，唯独漏了它本身。

把子块调到 700 字符再测：

```text
hit@5  1.00    hit@1  0.87    mrr  0.92     ← 整体略优于单层块
pinpoint@1 0.75                             ← 仍略低于单层块
```

但拆开看，**这些差异全都只有一个用例**：整体 hit@1 是 26/30 对 25/30，定点 hit@1 是 6/8 对 7/8。30 个用例的样本分辨不出高下，只能说两者打平，而 400 字符那版是确实更差。

最终保留子块 700，理由写清楚了：**不是因为它现在更好，而是因为语料增长后子块粒度更有优势，且它现在不更差。** 三组实验的完整数据记进了 spec 的 `experiments` 字段，以后语料涨了重跑一次就能看到拐点。

过程中还修了一个自己引入的 bug：评测里报告检索模式的函数仍在比较「向量数 == 块数」，父子块之后这个条件恒不成立，一度误报「关键词兜底」。**如果没发现，我会拿一组以为是关键词检索的数字去否定父子块。** 现在它会明确报出「向量检索（父子块，290 条向量 / 145 个块）」，判断条件也和 `search_knowledge` 里的兜底逻辑对齐了。

这是这一轮第四次「衡量工具本身出错」——前三次是页眉去噪误删正文、句中检测规则误判、评测基准用了低频词。共同点始终是：**尺子坏掉的时候，它会让你去修一个根本没坏的东西。**

### 3.24 索引缓存

做父子块时顺带量到一个一直存在的浪费：`search_knowledge` 每次查询都从磁盘重读并解析 `embeddings.json`。

```text
单层块  加载 68ms + 相似度 18ms = 86ms
父子块  加载 137ms + 相似度 39ms = 175ms
```

真正的"搜索"只占一小部分，大头是在反复解析一个几 MB 的 JSON，而这个文件只在重建索引时才变。

加了按 `(路径, mtime)` 的进程内缓存：

```text
首次加载      133 ms
之后          0.05 ms
每次检索开销   175ms → 36ms
```

用 mtime 当缓存键，重建索引后会自动失效，不需要手动清理，也不怕 web 和 bot 两个进程各自持有旧数据。

这一步之后，父子块带来的额外成本基本消失了：向量数翻倍带来的相似度计算从 18ms 涨到 36ms，相对于 embedding API 的往返完全可以忽略。

### 3.25 检索向量化

3.24 加了索引缓存之后，每次检索的本地开销从 175ms 降到 36ms，剩下的几乎全是余弦相似度计算——而它是一个纯 Python 的逐元素循环。

在决定"要不要换向量库"之前先查了业界做法，结论和直觉相反：**10 万条向量以下，NumPy 暴力搜索是推荐方案，不是权宜之计**。原因除了够快，还有两点：

```text
召回率 100%   ANN 索引是近似的，会静默漏掉相关内容，
              而这种漏检发生在模型看到之前，根本察觉不到
零运维        "数据库"就是一个文件，没有端口、容器和额外进程
```

本项目只有 290 条子向量，离那个阈值差三个数量级。所以真正该做的不是换存储，是**别用 Python 循环做数值计算**。

改动是把向量整理成一个归一化后的矩阵，检索时退化成一次矩阵乘法：

```text
建索引时  向量一次性归一化并缓存，余弦相似度退化成点积
检索时    matrix @ query 得到全部子块分数
归并时    np.maximum.at 按父块取最高分，一步完成
```

实测（290 条子向量）：

```text
纯 Python  每次排序 38.37 ms
NumPy      每次排序  0.19 ms      快约 200 倍
```

有两个细节是刻意的：

**numpy 做成软依赖。** `import` 失败时自动退回原来的逐条计算路径，两条路径都保留。新增一个依赖不应该变成新的故障点。

**验收标准不是"评测通过"，而是"两条路径结果完全一致"。** 这是纯重构，指标本来就该一动不动。所以先用 30 个查询逐一比对 numpy 路径和纯 Python 路径的 top-3 块 id，确认完全相同，再跑评测确认七项指标逐位不变。如果只看评测通过，是发现不了"结果变了但恰好还在阈值内"这种情况的。

按这个结果，向量库要等到语料涨到几万块才需要考虑，届时也应该先试 faiss / sqlite-vec 这类纯库方案，而不是引入独立服务。

### 3.26 混合检索：一个被数据否掉的主流方案

资料里对 RAG 有一条几乎是共识的建议：**向量检索要和 BM25 关键词检索融合**，只用其中一个会损失准确率。理由是纯向量对罕见词（型号、错误码、具体数字）表现差，关键词正好补这一块。

项目里两个召回器本来就都有，只是 `search_knowledge_keyword` 一直只作为向量不可用时的兜底，不是并行融合。

融合方式选了 RRF（Reciprocal Rank Fusion）：

```text
score(块) = Σ 权重 / (60 + 该召回器给它的名次)
```

选它是因为**只看名次不看分数**。余弦相似度和 TF-IDF 分数量纲完全不同，直接加权求和要先归一化，而归一化系数又要调参；RRF 天然免疫这个问题，也不受 `_phrase_boost` 那种硬加 100 分的影响。

接进主链路之前先发现关键词检索单次要 **207 毫秒**——它每次都在遍历 32 万个 token 重建词频和文档频率表。这两张表完全由索引文件决定，加上按 mtime 的缓存后降到 **23 毫秒**。这个优化本身是净收益，因为关键词检索仍然是兜底路径。

然后是实测结果：

```text
                 纯向量    等权融合   向量权重3   向量权重8
hit@k             0.97      0.93       0.93       1.00
hit@1             0.87      0.83       0.80       0.80
mrr@k             0.91      0.88       0.86       0.87
book_hit          1.00      0.91       0.91       1.00
```

等权融合直接跌破两项阈值。更说明问题的是**权重曲线**：向量权重从 1 加到 8（此时关键词几乎不起作用）指标才逐步回到纯向量水平——**关键词的每一分贡献都是负的，不是权重没调好**。

结构性原因很清楚：**纯向量已经接近饱和，没有留给融合的提升空间，只有被挤出去的风险。** 资料说混合补的是"向量对罕见词差"，但本项目专门设计的 8 道定点事实题（「周跑量的10%」「90~120秒」这类罕见词）在纯向量下 `pinpoint_hit_rate_at_k` 就已经是 1.00。

所以默认关闭，代码和 `RAG_HYBRID_ENABLED` 开关保留。语料规模变大、向量召回不再饱和之后，打开重跑评测就能重新判断。

这条记下来的价值在于：**主流建议是在别人的语料规模和失败模式上得出的，不一定适用。有评测才敢说"这条不适用于我"，没评测就只能照抄。**

**补充（见 3.29）：** 后来把关键词那一路从 TF-IDF 换成 BM25 又重测了一次，总体判决没变，但暴露出一个之前被掩盖的细节：

```text
              纯向量    混合 + BM25
book_hit       1.00       0.91      ← 书上丢了
video_hit      0.86       1.00      ← 视频全中
```

混合不是全面变差，是**在视频上赢、在书上输，而书占语料 94%**，所以总账是亏的。赢的那一项正是资料预言的场景：`video_blood_volume` 里的「总血量」是罕见词，BM25 能精确命中，纯向量下它排第 4、top-3 拿不到。

之前用 TF-IDF 时这个 trade-off 完全看不出来，因为写死的 `+100` 短语加权让关键词排序退化成了规则匹配。**换成 BM25 不是为了让混合翻盘，而是为了让这个 trade-off 变得可见。**

### 3.27 评测口径与生产不一致

一个更隐蔽的问题：生产环境检索 top-3（`RETRIEVAL_TOP_K = 3`），但评测 judge 里写死的是 `TOP_K = 5`。**评测一直在衡量一个生产并不使用的配置。**

按真实的 k=3 重新测：

```text
              评测报告(k=5)   实际生产(k=3)
hit_rate          1.00           0.97
video_hit_rate    1.00           0.86
```

`video_blood_volume` 这道题命中块排第 4，线上根本拿不到，而评测报告里它一直算命中。

修法不是把 judge 里的 5 改成 3，而是**把常量收敛到一个地方**：`src/runtime/rag.py` 定义 `DEFAULT_TOP_K`，`knowledge.py` 和评测 judge 都从那里读。两处各写各的迟早还会漂移。

指标名也一并从 `hit_rate_at_5` 改成 `hit_rate_at_k`——写死数字的名字在 k 变化时会说谎。

这是这一轮第五次「衡量工具本身出错」。前四次是页眉去噪误删正文、句中检测规则误判、评测基准用了低频词、检索模式误报关键词兜底。这次的形态又不一样：**尺子本身没坏，但它量的不是你要的那个东西。**

### 3.28 增量嵌入与删除传播

原来每导入一份资料，整个索引都会从零重建：遍历全部 PDF 和字幕重新解析、重新切块，
再把**所有**子块重新调 embedding API。丹尼尔斯那 272 个子块内容一个字没变，
却每次都要重算一遍向量。

```text
新增一个约 10 个子块的视频
  实际需要嵌入  10 个
  实际会嵌入    300 个
  浪费          97%
```

改法是给每个子块算内容 sha256，`embeddings.json` 里存下来，嵌入前先查：

```text
首次迁移      复用 0/290，全量重算（不可避免的一次性成本）
内容未变      复用 290/290，零 API 调用
新增一份视频  复用 290/291，只调 1 次（原来 15 次）
```

**用内容哈希而不是块 ID 作缓存键。** 块 ID 里带页码，页码会随分块策略变化；
而内容没变就没必要重算。哈希覆盖了「文档级前缀 + 正文」，所以页码变了会正确地触发重算。

有一个关键取舍：**只对嵌入做增量，切块保持全量重建。**

切块是纯本地字符串处理，实测服务器上 6.6 秒，其中 96% 是 pypdf 解析 PDF，不花钱。
花这几秒换到的是**删除传播**——而且是零代码实现的：

```text
build_chunks() 每次从 glob 现扫目录，从不读取旧的 chunks.json
    ↓
文件没了 → 扫不到 → 不产生块 → 四个索引文件里都没有它
```

整个 ingest 脚本里**没有一行删除逻辑**。这是声明式重建的性质：
`索引 = f(文件系统)` 是纯函数，只要每次完整重新求值，删除就不需要被「实现」。

如果连切块也做增量，`chunks` 就从纯函数输出变成了携带历史的状态，
那一刻删除传播就失效了，而且不会有任何报错。这也是本项目刻意不做切块增量的原因。

顺带澄清一个概念：**幂等和原子性不是一回事。**

```text
幂等    做一次和做多次结果一样，失败可以放心重跑     ✅ 本项目有
原子    要么全成要么全不成，不留中间态             🔴 本项目缺
```

实测连跑两次，chunks.json 和 index.json 的 sha256 完全相同；手动删掉 5 条向量
制造撕裂状态后重跑，只补算那 5 条就恢复了。**幂等 + 检索层的一致性护栏叠加，
把缺原子性的后果压得很轻**：崩了会静默降级到关键词检索但不出错，下次重跑自动修好。

### 3.29 关键词检索改为 BM25

`search_knowledge_keyword` 原来是 TF-IDF：原始词频 × IDF，再加一个写死的 `+100` 短语加权。

BM25 相对 TF-IDF 多两件事：

```text
词频饱和      出现 20 次不等于出现 1 次的 20 倍，曲线要压平（k1 控制）
长度归一化    长文档天然包含更多的每个词，要除掉这个优势（b 控制）
```

实测下来这两项对本项目的影响差别很大：

**长度归一化几乎用不上。** 跨页合并加语义边界切分之后，块长度极其均匀
（最短 1122、中位 1178、最长 1200，最长/最短仅 1.1 倍），
长度归一化要解决的问题本来就不存在。这是分块做得好带来的意外好处。

**而那个 `+100` 的影响是决定性的：**

```text
TF-IDF 原始分   最低 1.5 | 中位 10.7 | 最高 77.1
短语加权        +100，145 块里有 16 块命中
```

加分是中位分的 9.3 倍，等于**排序完全由那 16 条手写规则决定**，
TF-IDF 那部分算什么基本无所谓，退化成了规则匹配。

改造后把加权表达成「相当于多命中一个最强查询词」，随语料规模自动缩放，
加权/中位分从 9.3 倍降到 1.39 倍。

换完之后关键词排序确实变了——30 个查询里 top-1 只有 15 个相同、top-20 无一相同、
top-10 平均重合度 65%——但**混合检索的总体判决没有改变**，见 3.26 的补充。

原因在于 RRF 只用名次不用分数。分数分布正常了，不改变「关键词这一路的排名
整体上不如向量」这个事实。实测把加权完全关掉（`KEYWORD_PHRASE_BOOST=0`），
七项指标一个数字都没变。

### 3.30 公开网页入口改为只读

网页部署在公开域名上、没有任何认证，而 `WEB_AGENT_MODE=real` 意味着它走的是
和 Discord 完全相同的真实能力。排查后发现这条链路上有两类问题：

```text
读    个人训练档案通过 format_memory_for_prompt 无条件注入 prompt
      COROS 真实运动记录、主观感受记录都能读
写    save_running_profile 工具可以往长期记忆里写任意内容
      feel 记录感受、running-video 往知识库导入、kitchen 改库存
```

**写污染比读泄露更麻烦**：陌生人塞进长期记忆的内容，之后会被当成用户说过的事实
用于生成训练建议，而用户不会察觉。

按需求保留了个人档案的只读展示（网页要能演示个性化教练能力），但用三层拦住所有写：

```text
① 命令白名单    WEB_COMMANDS 去掉 feel 和 running-video 这类纯写命令
② 动作级拦截    kitchen 读写混在一个命令里，按动作判读还是写
③ 工具集裁剪    Web 入口的工具注册表不含 save_running_profile
```

第三层最关键：前两层拦的是命令，而 `save_running_profile` 是模型在 `running`
对话内部自主调用的，**命令白名单根本管不到它**。

另外 `_running_video` 和 `_record_feeling` 在能力层也各加了一道 `read_only` 拒绝，
防止将来有人把命令加回白名单时绕过防护。还有一处隐患是
`dispatch_web_text` 的默认参数里仍留着写命令——`web_server.py` 每次都显式传参
所以没暴露，但任何不传这个参数的调用方都会拿到旧的宽集合，已一并收窄。

线上实测：`!feel`、`!running-video` 被白名单拒绝，`!kitchen bought` 被动作级拦截，
`!kitchen pantry` 正常放行，`memory.json` 未被这些请求改动。

### 3.31 索引写入原子化

`ingest_books.py` 原来是顺序直接写四个索引文件，而最后一个 `embeddings.json`
要等完 embedding API 才写得出来。实测这个窗口：

```text
全部命中缓存      约 0.5 秒
导入一个新视频    约 1~2 秒
换一本新书        30~60 秒（约 300 条向量、15 次 API 往返）
```

这期间 `chunks.json` 已经是新的、`embeddings.json` 还是旧的。中途任何中断
（报错、Ctrl+C、断网、OOM）都会留下撕裂索引。开发过程中真的踩到两次，
都是改代码时留下的低级错误（键名写错、`datetime` 没导入）。

改成先写 `.tmp`、最后一起 `rename`：

```text
写 4 个 .tmp   ┃━━━━━━━━━━━━┫   慢，但崩了只留个临时文件
rename × 4     ┃┫                微秒级，且每次 rename 都是原子的
```

危险窗口从几十秒缩到几微秒。

有两个细节是刻意的：

**捕获 `BaseException` 而不是 `Exception`。** Ctrl+C 抛的 `KeyboardInterrupt`
不属于 `Exception`，而它恰恰是最现实的中断方式——写到一半觉得不对想停下来。

**`build_embedding_payload` 不再自己写盘，改成返回 payload。** 四个文件要一起生效，
写盘统一交给 `main()`，否则嵌入那一步会自己提前落盘，原子性就破了。

验收方式和之前 NumPy 向量化那次一样：**这是纯正确性修复，指标必须一动不动。**
在嵌入阶段人为抛出 `KeyboardInterrupt`，四个文件的 sha256 一个字节没变、
临时文件清理干净；正常跑一次之后内容与改造前完全一致，评测七项逐位相同。

**这个技巧为什么成立。** 目录本质上是一张「名字 → 内容块」的对照表：

```text
write_text  改的是内容：清空原内容块再逐字节写入
            名字始终指着同一块内容，整个写入过程都是中间态

rename      改的是指针：目录表里那一行从旧内容块改指向新内容块
            改一个指针可以不可分割，改几 MB 内容不可能
```

所以本质不是「写得更快」，而是**把慢的部分挪到一个没人在看的名字上，
只留一次指针切换暴露给读者**。

用一个并发实验验证过：两个必须保持一致的文件，一个读者每 10ms 检查一次版本是否匹配。
中间同样等 0.5 秒模拟 API 调用——

```text
直接顺序写   读到版本不一致 41 次（例如「新 + 旧」）
先写 tmp 再 rename   0 次
```

**旧内容什么时候消失。** 靠引用计数：目录里的名字和进程打开的句柄都算引用。
rename 之后旧内容失去名字，但只要还有进程开着它，内容就一直存在到那个进程关闭。

推论是：**在 rename 之前就打开文件的读者，会完整读完旧版本，不会读到一半被换掉。**
所以 ingest 替换索引时，正在检索的 bot 或 web 进程拿到的始终是一致快照。
代价只是旧内容在读者关闭前不释放，磁盘短暂多占一份——6.3MB 的索引不值得管。

**一条必须说清的边界：原子不等于持久。**

```text
atomicity    要么全成要么全不成，不存在中间态       ✅ rename 保证
durability   一旦说成功了，断电也不会丢             ❌ 需要 fsync
```

`write_text` 之后数据可能还在页缓存里没落盘，rename 也只是改目录项。完整持久化要
`fsync(tmp)` → `rename` → `fsync(目录)`。本项目没做 fsync，所以准确说法是
**atomic but not durable**：进程崩溃、Ctrl+C、报错都能防住（这是实际会遇到的），
整机断电理论上仍可能丢。数据库会做完整 fsync，代价是每次写盘慢一个数量级；
对「手动跑 ingest、崩了重跑一次就自愈」的场景不值得。

英文术语：这套做法叫 **atomic write** 或 **atomic file replacement**，
实现手段叫 **write-rename pattern**，底层是 POSIX 的 `rename(2)`。
Python 用 `os.replace()` 而非 `os.rename()`，后者在 Windows 上目标存在会报错。
描述这类保证的词是 **crash consistency**。

这一步补完之后，「数据接入」这一层在 RAG 全流程对照里只剩「文档级元数据」一项待补，
不再有标红。

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
支持上下文批量压缩，滚出窗口的对话折叠成摘要而不是丢弃
支持 pending 追问状态，把用户的回答绑定回 Agent 的问题
支持基于 tools 参数的真实 tool call 和多轮工具循环
支持 VDOT 配速换算、比赛倒计时和模型主动写入长期记忆三个工具
支持知识库分块质检，导入新资料后由 Agent 自动 tool call 检查
支持 rag_retrieval 检索质量评测，用 golden 问题量化分块策略的好坏
支持父子块检索：子块匹配保精度，父块投喂保上下文，子块大小可配置
支持索引进程内缓存，按 mtime 失效
支持 NumPy 向量化检索，numpy 缺失时自动退回纯 Python
支持关键词索引缓存，兜底检索从 207ms 降到 23ms
支持 RRF 混合检索开关（实测在当前语料上更差，默认关闭）
支持按内容哈希的增量嵌入，导入新资料只算变化的块
支持声明式重建带来的删除传播，删掉源文件索引自动清空
支持 BM25 关键词检索，参数可配
支持公开 Web 入口只读，三层拦截所有写操作
支持索引写入原子化，中断不会留下撕裂索引
支持跨页合并、语义边界切分和页眉页脚去噪的 RAG 分块策略
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
