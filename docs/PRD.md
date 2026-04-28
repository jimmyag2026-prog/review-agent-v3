# review-agent v3 — PRD（功能视角）

版本：**v3 PRD draft 2** · 2026-04-27 · 修订自 draft 1（吸收 deepseek-v4-pro round-1 review 反馈）

## v2 Patch Notes（相对 draft 1 的变更）

按 round-1 review 5 blocker + 7 improvement + 4 nit 增量补丁。完整 review 见
`reviews/round-1.md`。

### 影响 PRD 的关键变更

- **B2/B3 失败 & 重开状态机**（§10 Cursor + §13 Final-Gate + §16 NFR）：
  - 新增 session 状态 `failed`（任意 pipeline 阶段终极失败后落入）+ `qa_active_reopened`（final-gate FAIL 后回 Q&A 的过渡态）
  - 任意 stage LLM 三次重试失败后：session.status = `failed`，给 Requester 明确的失败原因（非 stacktrace），双方收通知，admin dashboard 可手动 resubmit
  - cursor.json 增加 `regression_rescan: bool` 标记，FAIL 后回 Q&A 时把回归 BLOCKER append 到 pending 队首
- **B5 多 Requester 队列隔离**（§16 NFR）：
  - 改"全局单 worker"为"per-Requester 虚拟队列 + 单 consumer 轮询"。单 Requester 内严格串行（保证状态机一致），跨 Requester 通过 round-robin **公平调度**
  - **v2.1 修订（round-2 NB1）**：v0 单 consumer 仍然是 FIFO，长 LLM 调用会让其他 Requester 等待，**真正并行留 v1 多 worker**。文档不再宣称 v0 可并行
- **I1 Subject Confirmation 回复合约显式化**（§11 IM）：
  - subject_confirmation 阶段也支持 `a/b/c/pass/custom` 回复，与 Q&A loop 同一套 shortcut 解析
- **I6 与 openclaw 的 Lark App 隔离**（§15 部署）：
  - 明确：review-agent 注册**独立 Lark Self-Built App**（独立 app_id / event URL `/lark/webhook` 与 openclaw 路径不冲突），doctor 检查应用重叠
- **I7 Delivery backend 抽象**（§9 输出）：
  - v0 仍只实现 `lark_dm` + `local_path`，但 PRD 显式声明 backend 是开放抽象，v1 加 `email` / `group_chat` 不需要重构 deliver pipeline
- **N4 Summary 增 timeline**（§9 输出）：
  - summary §1 议题摘要追加一行 timeline（上次讨论 / 上次承诺 / 外部 deadline），帮 Responder 5 分钟读完时拿到时间感

### 不进入 v0 / 已显式延后

- **I4 组织政治维度**（"7th challenge dimension: 组织动态"）：v0 不上，理由：缺乏 profile 默认数据来源，强行 prompt 很容易产幻觉。v1 起作为 Admin 可选启用的扩展维度
- **I3 fs/sqlite 两阶段提交**：v0 只在 close 阶段加 fs+db 一致性事务（关键的最后一步），中间阶段不做（worker 重试 + idempotent handler 已经够用）

——以下为 PRD 正文（draft 2）——

---

版本：v3 PRD draft 1 · 2026-04-27 · 基于 v2.4.2（含 v2.3.0 hardening）功能合集 + Explore agent 全源码分析

> v3 是从零的 FastAPI + SQLite 重写，**不依赖 openclaw / hermes**。后端独立跑在 VPS，
> 直接接 Lark Open API。本 PRD 只描述功能，架构与代码在 ARCH.md / src/。

---

## 1. 产品定位

**一句话**：上级（Responder）的会前异步审阅代理。当下级（Requester）通过 Lark DM 把
draft / 提案 / 1:1 议程送进来，agent 按"挑刺者"姿态把材料推到"签字即用"状态，
然后把决策-ready 6 节 brief + 异议日志一次性递给上级。

**理论根**：1942 美军 Completed Staff Work（CSW）—— "the chief only signs yes or no;
all the thinking has been done by staff."

**差异化**：市面上 2026 的 pre-meeting AI 都是 **bottom-up**（给 receiver 做 pre-read）。
v3 走 **top-down**：在 receiver 看到东西之前，**把 briefer 训练到位**。

---

## 2. 角色模型

| 角色 | 中文 | Lark 上的标识 | 角色定义 |
|---|---|---|---|
| **Responder** | 上级 / 评审标准持有者 | `open_id` | 标准 owner。其 `profile.md` 定义"我审东西的标准"；session close 时收 brief。 |
| **Requester** | 下级 / 提交者 | `open_id` | 通过 Lark DM 把 draft / 文件 / Lark Doc 链接送进来；接受 Q&A loop 的挑战；改稿；force-close 自己的 session。 |
| **Admin** | 管理员 | `open_id` | 装 / 配 / 加人 / 改共享 review_rules / 改任意 Responder profile / 任意 session force-close / 配 delivery target。 |

**默认安装**：单人同时是 `Admin + Responder`（自审场景或最小 deployment）。
**多 Responder** 是 v1 路线图，v0 单 Responder。
一个 Lark 用户可以同时持有多个角色（角色用 `roles: ["Admin","Responder"]` 数组存）。

### 权限矩阵

| 动作 | Admin | Responder | Requester |
|---|---|---|---|
| 跑 setup 初始化 | ✓ | ✗ | ✗ |
| Add Responder | ✓ | ✗ | ✗ |
| Add Requester | ✓ | ✓（绑给自己） | ✗ |
| Remove user | ✓ | ✗ | ✗ |
| 改 `review_rules.md`（共享） | ✓ | ✗ | ✗ |
| 改 Responder profile | ✓ 任意 | ✓ 自己的 | ✗ |
| 改共享 `delivery_targets.json` | ✓ | ✗ | ✗ |
| 改自己的 `delivery_override.json` | ✓ | ✓ 自己的 | ✗ |
| 看 dashboard | ✓ 全部 session | ✓ 自己作为 Responder 的 session | ✓ 自己的 session |
| Force-close session | ✓ 任意 | ✓ 自己的 | ✓ 自己的 |
| close 时收 summary + final | — | ✓（被绑定的 Responder） | ✓（自己） |

权限在 webhook 入口处依据 `users/<open_id>/meta.json.roles` 强制；
请求方所属角色不允许跑的 op 直接 403，不进 LLM。

---

## 3. 核心 Use Cases（功能视角的 8 个流程）

> 每个 UC 是用户视角，不写实现细节。所有"agent 发"都通过 Lark DM。

### UC-1 · Admin 装 + 自我注册为 Responder
1. Admin 通过 CLI 或 web 后台跑 `setup --admin-open-id ou_xxx`
2. 系统 `users/ou_xxx/{meta.json,profile.md}` 落盘，roles=`["Admin","Responder"]`
3. profile.md 用模板默认值（功能型 senior reviewer，可直接用，**也提示 Admin 编辑以提升 review 质量**）
4. 提示装完，给 Lark bot 加 admin 自己为联系人

### UC-2 · Admin / Responder 邀请一个 Requester
1. 通过 CLI 或 dashboard 输入 Requester 的 Lark `open_id` + 显示名
2. 创建 `users/<requester_oid>/{meta.json,owner.json}` 并绑定 responder
3. Bot 主动 DM Requester 一句 onboarding（自我介绍 + 怎么用，3 行内）
4. Admin / Responder 看到 dashboard 上多了一个 pairing 行

### UC-3 · Requester 发起首次 review
1. Requester DM bot：发文本 / PDF / 图片 / 音频 / Lark Doc URL / Google Doc URL
2. Bot 立刻识别"这是 review 触发信号"，**不开对话框问"你想干嘛"**，直接进 INTAKE
3. Bot 创建 `sessions/<id>/`，把材料归档到 `input/`，多模态归一为 `normalized.md`
4. 失败（缺 pdftotext / tesseract / whisper）→ Bot 直接告诉 Requester 替代方案：
   "扫描类 PDF 我现在不能解，你直接贴正文给我也行" —— **不抛 stacktrace**

### UC-4 · Subject Confirmation
1. Bot 读 normalized.md + 历史聊天，推断 2-4 个候选**单一决策主题**
2. Bot 发一条消息（≤300 字 + 选项块）：
   ```
   要做的决定听起来是这几个里的哪个？
   (a) <选项 1>
   (b) <选项 2>
   (c) <选项 3>
   (pass) 跳过这一条，先看下一条
   (custom) 其他——直接打字告诉我
   ```
3. Requester 回 `a` / `b` / `c` / `pass` / `custom <自由文本>`
4. 主题对齐前 **不开始 four-pillar scan**

### UC-5 · 4-Pillar Scan + Responder Simulation
1. 主题对齐后 bot 跑两层 LLM：
   - **Layer A** 四柱扫描（Background / Materials / Framework / Intent）
   - **Layer B** Responder 模拟：扮演被绑定的 Responder（用其 profile.md），列他最关心的 top-5 问题，对照 normalized.md 挑出"还没回答"的那些
2. 合并两层 findings 到 `annotations.jsonl`，按 `severity` (BLOCKER>IMPROVEMENT>NICE-TO-HAVE) 排队
3. 默认只发 **top-5**（`REVIEW_AGENT_TOP_N` 可配），剩下入 `cursor.deferred`
4. cursor 指向第一条 BLOCKER

### UC-6 · Q&A Loop
1. Bot 一次只发 **一条** finding（不发墙）
2. 风格映射：
   - Pillar 4 (Intent) BLOCKER → **直接**："把 ask 改成 '请 X 在 Y 前批准 Z'"
   - Pillar 1 (Background) BLOCKER → 直接："加一段 5 句的背景"
   - Pillar 2 (Materials) → **Socratic**："如果数据是 100 而不是 10000，推荐会变吗？"
   - Pillar 3 (Framework) → Socratic："你想让 Responder 按哪个维度选？"
   - Responder Simulation → Socratic（本来就是问句）
   - NICE-TO-HAVE → 本轮 < 3 条才发
3. 末尾选项块：
   ```
   (a) accept     接受，会改
   (b) reject     不同意（"(b) 理由是..."）
   (c) modify     "(c) 我要改成..."
   (pass) 跳过这一条，先看下一条
   (more) 还想再看下一批 deferred
   (done) 我觉得可以 close 了
   (custom) 自由文本
   ```
4. Requester 回复后 bot 分类意图：
   - **accepted**：status=accepted，cursor 推进
   - **rejected + reason**：status=rejected，append 到 `dissent.md`，cursor 推进
   - **modified**：status=modified，记录他的版本
   - **question**：澄清，不推进 cursor
   - **skip**：更新 cursor，跳条
   - **force-close**：进 close 流程
5. 一条处理完后下一条 finding（同样选项块）
6. 当 `cursor.pending` 空 + Intent pillar 已 pass → bot 提议"准备 close 了，确认？"
7. **硬上限**：3 轮（Requester 显式要求可加到 5）。超限剩余 BLOCKER 自动 `unresolvable` 进 open_items

### UC-7 · Document Merge（条件）
依 `admin_style.md.document_editing` 三档：
| permission | bot 行为 |
|---|---|
| `none` | 只给 findings，Requester 自己改完上传到 `final/` |
| `suggest`（v0 默认）| Bot 基于 accepted findings 生成 `final/revised.md`，附 unified diff，Requester accept / modify / reject 整体 |
| `direct`（v1）| 直接改 Lark Doc / Google Doc（需 OAuth scope） |

### UC-8 · Final Gate + Close + Forward
1. `final-gate` 重扫 `final/<primary>` 文件，按 4 柱聚合 verdict：
   - `READY` — 全 pass
   - `READY_WITH_OPEN_ITEMS` — 有 unresolvable 但都进了 open_items + A/B framing
   - `FORCED_PARTIAL` — Requester force-close，留有 BLOCKER 但记录了理由
   - `FAIL` — 重扫发现新 BLOCKER 或 Intent pillar regression → **拒绝 close**，回到 Q&A
2. Pass 后 `_build_summary.py` 调 LLM 合 6 节 `summary.md`（见 §9）
3. `deliver` 按 `delivery_targets.json` 同时：
   - DM Responder：`summary.md` + `final/<primary>.md` + `dissent.md`
   - DM Requester：`summary.md`（他已知细节）
   - 本地归档：summary + summary_audit + final + conversation + annotations + dissent
   - 可选：post 到原 group chat（如果 trigger 来自 group @）
   - 可选：邮件（funding / board 类标记）
4. 更新 dashboard，把 session 移进 `sessions/_closed/YYYY-MM/`

---

## 4. 4-Pillar 框架（**核心方法学，逐字保留 v2 定义**）

> 通用任意场景：决策 brief / 状态汇报 / 设计 review / 1:1 议程 / 投资 memo / 方向讨论。

### Pillar 1 · 背景 (Background)
- **Pass**：what / why now / current state / who cares 都讲清；Responder 进会能直接进入讨论
- **Fail**：开头跳到选项 / "as discussed" 没真讨论过 / 只 what 不 why now
- **默认 severity**：IMPROVEMENT；Responder 没背景就无法决策时升 BLOCKER

### Pillar 2 · 资料 (Materials)
- **Pass**：决策依据齐（数据带来源+日期、可比对象、相关案例）；内部 + 外部锚都在
- **Fail**：数据没来源 / 只内部观点 / 关键 data point 缺失
- **默认 severity**：IMPROVEMENT；缺失直接让决策无意义时升 BLOCKER
- 决策 brief 场景的 sub-checklist：Evidence Freshness / Red Team / Stakeholder（来自旧 7 轴沉降）

### Pillar 3 · 框架 (Framework)
- **Pass**：讨论维度明确（cost/speed/reliability/team fit）；Responder 该给的答复类型清晰（yes-no / A-B / range / advice）
- **Fail**：开放性 brainstorm / 多选项无比较维度 / 把 Responder 置于"你觉得呢"
- **默认 severity**：IMPROVEMENT；讨论方向完全开放升 BLOCKER

### Pillar 4 · 意图 (Intent) — **CSW GATE**
- **Pass**：单一具体 ask（批准 / 否决 / 选 A 或 B / 给某方向反馈 / 审批预算）；Requester 已做完所有自己能做的功课；Responder 下一步动作 ≤ 1 个
- **Fail**：含糊"想讨论一下" / "听听看法" / 把决策推回 Responder / 文末反问一堆
- **Severity**：**ALWAYS BLOCKER**。本柱 fail → session 不能 close → 材料不能送 Responder

---

## 5. 六个挑战维度（横切所有 pillar，"怎么挑"）

每条 finding 至少能落到这六维度之一；六维度是 LLM 在每个 pillar 内挑刺时的提问 checklist。

| # | 维度 | 挑战形式（示例） |
|---|---|---|
| 1 | **数据完整性** | "你说'用户增长不错'，但素材里没具体数字。补一下 DAU / 留存。" |
| 2 | **逻辑自洽性** | "你要砍 A，但前面又说 A 是核心卖点。怎么调和？" |
| 3 | **方案可行性** | "3 个工程师做 2 个月，但团队只 1 个人。怎么算？" |
| 4 | **利益相关方** | "没提法务/合规。这项目涉及数据合规，他们的意见呢？" |
| 5 | **风险评估** | "Plan B 是什么？素材里没看到。" |
| 6 | **ROI 清晰度** | "预期收益 100 万，但成本估算呢？没看到。" |

**Persona 硬规则**：禁替写 / 禁总结 / 禁赞美 / 禁替 Requester 做功课。
只追问、只挑刺、要具体（"前 3 行没说要什么。建议改成 X"，不是"需要更清晰"）。

---

## 6. Responder Simulation 层

**机制**：4 柱扫描完后，另起一次 LLM call，让模型扮演被绑定的 Responder：
- 输入：Responder profile.md（pet peeves / 决策风格 / always-ask 问题）+ normalized.md
- Prompt："你是 {responder_name}。按你的 profile 和平时思考方式，读完这份材料，
  你前 5 个最关心的问题是什么？按你自己的 priority 排，1 最要紧。"
- 对每个模拟问题：检查 normalized 是否答了；没答 → emit finding（`source=responder_simulation`，
  默认 IMPROVEMENT，涉及意图/基本信息缺失升 BLOCKER）

**为什么需要这层**：4 柱是 baseline，模拟层让标准自动适配到**这个特定 Responder**。
（一个创始人会问"过去 14 天和 ≥3 个真实用户聊过吗"；一个融资 partner 会问"TAM 和 comps 估值倍数"。）

**成本**：每次 scan 多一次 LLM call (~+50% LLM 成本)。
**幻觉缓解**：只 emit 在 profile 里有明确依据的问题。

---

## 7. Session 6 阶段（用户视角）

```
INTAKE → SUBJECT CONFIRMATION → 4-PILLAR SCAN + RESPONDER SIM
       → Q&A LOOP → DOCUMENT MERGE (conditional) → FINAL GATE + CLOSE + FORWARD
```

**Frozen-at-start config**：session 创建瞬间把以下 4 份文件 snapshot 到 `sessions/<id>/`：
- `admin_style.md` — Admin 的 agent-行为偏好（语言 mirroring / tone / 节奏 / 长度 cap / 文档编辑权限默认）
- `review_rules.md` — 共享 review 协议（4 柱 + 轮数上限 + dissent 处理 + gate 标准）
- `profile.md` — 该 Responder 的内容标准（pet peeves / axis thresholds / always-ask）
- 可选 `review_criteria.md` — 本次 session 特殊门槛覆盖

**理由**：Admin / Responder 中途改 live 配置不影响在飞 session，只影响下一个新 session。

---

## 8. 输入规范（Ingest 范围）

| 类型 | 支持？ | 处理 | 失败行为 |
|---|---|---|---|
| 纯文本 / markdown | ✓ | 直接当 normalized | — |
| PDF（文本类） | ✓ | `pdftotext` 或 `pdfminer.six` | 缺工具 → "让 Admin 装一下，或你直接贴正文" |
| PDF（扫描类） | ✓ | tesseract OCR | 缺 → 同上 |
| 图片（jpg/png） | ✓ | tesseract OCR | 缺 → 同上 |
| 音频 / voice note | ✓ | whisper | 缺 → "你贴文字也行" |
| Lark Doc / Wiki URL | ✓ | Lark Open API `docx:document` + `wiki:wiki:readonly` | 无 scope → 提示用户授权或粘正文 |
| Google Doc URL | ✓（v1） | Google Drive API | v0 提示粘正文 |
| 其他 URL | ✗ v0 | — | 提示粘正文 |

**Size guardrails**：
- PDF > 20MB 或 > 100 页 → 拒绝，要小一点的版本
- 图片 > 10MB → 拒绝
- 音频 > 50MB 或 > 30 min → 拒绝

**关键纪律**：Ingest 失败**不抛 stacktrace 给 Requester**，永远 fallback 到"贴正文"路径。

---

## 9. 输出规范

### `summary.md`（主产物，递给 Responder 的 5 分钟预读）

6 节固定结构：

```
# 会前简报 — <subject>

_Requester: <name> (<open_id>) · Rounds: N · 产出时间: <ts>_

## 1. 议题摘要
  一句话（20-40 字）+ 三行背景（why now / what's at stake / where it stands）
  **+ Timeline 一行**（v2 新增 round-1 N4）：上次讨论日期 / 上次承诺事项 / 外部 deadline，
    若三项无即写 "—"

## 2. 核心数据
  关键数字带来源+日期；无来源的明确标「未给出来源」作为风险信号

## 3. 团队自检结果
  - Findings 被挑战了几条
  - Requester 响应分布：接受并修改 / 保留异议 / 无解
  - Agent 对响应质量判断：强 / 中 / 弱 / 对抗性，带具体理由

## 4. 待决策事项
  - 主 ask（一句话，Responder 被要求做什么决定）
  - 需要讨论的开放项 ≤ 3 条，带 A/B 判断框架

## 5. 建议时间分配
  每议题建议时长 + 原因。若材料未达 decision-ready，可建议「不开」或「改短会对齐」

## 6. 风险提示（Agent 认为团队可能遗漏或低估的点）
  从六维度 + Responder 模拟综合，≤3 条
  每条标维度（数据/逻辑/可行/stakeholder/风险/ROI）+ 建议 Responder 开会时具体追问什么
```

风格：挑刺者视角，不软化 dissent，不隐藏 open items。

### `summary_audit.md`（审计产物，本地归档，**不**默认推 Lark）

deterministic 从 `annotations.jsonl` 聚合，不经 LLM：4 柱状态表 / 最终材料清单 /
已接受 findings（按柱）/ Requester 修改版 / 保留异议（按柱，含 reviewer 建议 + Requester 理由）/
未闭合进入讨论 / Responder 模拟追问（按 priority）/ force-close 时仍未解决的。

### Delivery 默认

```jsonc
{
  "on_close": [
    {"name":"responder-lark-dm","backend":"lark_dm","open_id":"{{RESPONDER}}",
     "payload":["summary","final"],"role":"responder"},
    {"name":"requester-lark-dm","backend":"lark_dm","open_id":"{{REQUESTER}}",
     "payload":["summary"],"role":"requester"},
    {"name":"archive-local","backend":"local_path","path":"...",
     "payload":["summary","summary_audit","final","conversation","annotations","dissent"]}
  ]
}
```

可选 backend：`email`（funding/board 标记）、`group_chat`（trigger 来自 group @ 时）。

---

## 10. Annotation 数据模型

每行一个 JSON 写到 `sessions/<id>/annotations.jsonl`：

```jsonc
{
  "id": "p1" | "r1" | "m1",  // p=four_pillar_scan, r=responder_simulation, m=manual
  "round": 1,
  "created_at": "...",
  "source": "four_pillar_scan" | "responder_simulation" | "manual",
  "pillar": "Background" | "Materials" | "Framework" | "Intent",
  "severity": "BLOCKER" | "IMPROVEMENT" | "NICE-TO-HAVE",
  "anchor": {
    "source": "normalized.md", "section":"...", "line_range":[3,5],
    "text_hash": "sha256:...", "snippet":"原文片段 ≤120 字"
  },
  "issue": "简述（一句话）",
  "suggest": "动词开头，含替换文本",
  "simulated_question": "(only for responder_simulation)",
  "priority": 1,  // responder_simulation 用
  "status": "open" | "accepted" | "rejected" | "modified" | "unresolvable",
  "reply": "Requester 文本（rejected 必填）",
  "replied_at": "...",
  "unresolvable_reason": "...",
  "extra": {"framing_a":"若倾向 A 则...", "framing_b":"若倾向 B 则..."},
  "escalated_to_open_items": false
}
```

**Status lifecycle**：`open → accepted | rejected | modified | unresolvable`，
闭合后重开需要新 id（不 reuse）。

**Cursor**：
```jsonc
{
  "current_id":"p1",
  "pending":["p3","r1","p5"],
  "deferred":["p7","r3"],
  "done":["p2"],
  "regression_rescan": false   // ⓘ v2 新增：final-gate FAIL 回 Q&A 时置 true，
                                // 回归性 BLOCKER append 到 pending 队首
}
```

**Dissent 流**：任何 status=rejected 入 `dissent.md`（带 reviewer 建议 + Requester 理由），
summary 从 dissent.md 取，**不**直接 scan annotations。

---

## 11. IM 交互合约

### 节奏
- 一条消息只发**一个** finding 或一个回应（不发墙）
- 中文 ≤ 300 字，英文 ≤ 100 words
- 发完一条等回复，**禁连发 2+**
- Session 进行中**永不主动 push** Responder（pull-only dashboard），仅 close 时一次性 push

### 选项块（每条问 Requester 时**强制**附）
```
(a) <选项 A>
(b) <选项 B>
(c) <选项 C>     ← 可选
(pass) 跳过这一条
(custom) 其他——直接打字
```
Q&A 阶段额外有 `(more)` 拉 deferred、`(done)` 触发 close 提议。

### 回复识别
- 单字母 `a`/`b`/`c` → 对应建议
- `p` / `pass` / `跳过` / `skip` / `next` → 跳过
- `more` / `继续` / `下一批` → 拉 deferred（仅 Q&A loop 阶段）
- `done` / `close` / `结束` → close 提议（仅 Q&A loop 阶段）
- `custom` / 其他 / 直接打字（>20 字）→ 自由文本，按内容语义分类（accepted / rejected / modified / question / force-close）
- **同一套 shortcut 在 subject_confirmation 与 qa_loop 两个阶段都生效**（v2 新增 round-1 I1）。pipeline 提供共享的 `parse_reply_intent(text)` helper，dispatcher 按 stage 解释结果（subject_confirmation 阶段 `a/b/c` = 选候选主题，`pass` = 跳过本候选；qa_loop 阶段 `a/b/c` = accept/reject/modify）

### 语言
- 镜像 Requester 语言（中→中，英→英，双语→双语）
- 禁企业套话 / "great question" / "感谢分享"
- 禁空洞建议（"需要更完整" / "可以再考虑"）

### 输出卫生
- **不漏 reasoning / thinking process**（v2.1.0 踩过的坑）
- **不漏 tool 调用预览 / stderr / traceback**
- **不发 markdown thinking 标题**（如 "## Thinking Process:"）

---

## 12. 配置项

### Admin 控制（全局）
| 文件 | 控制 |
|---|---|
| `~/.review-agent/admin_style.md` | agent 行为：语言镜像、tone、消息节奏、formatting、emoji policy、message 长度 cap、文档编辑权限默认值 |
| `~/.review-agent/rules/review_rules.md` | 共享 review 协议：4 柱定义、轮数上限、dissent 处理规则、gate 标准 |
| `~/.review-agent/delivery_targets.json` | 共享默认投递目标 |
| `~/.review-agent/users/<oid>/profile.md` | 任意 Responder 的内容标准（Admin 可改任意 Responder 的） |

### Responder 控制（自己的）
| 文件 | 控制 |
|---|---|
| `~/.review-agent/users/<self>/profile.md` | 自己的内容标准 |
| `~/.review-agent/users/<self>/delivery_override.json` | 覆盖共享 delivery |

### Per-session（frozen at start）
- `sessions/<id>/admin_style.md`
- `sessions/<id>/review_rules.md`
- `sessions/<id>/profile.md`
- 可选 `sessions/<id>/review_criteria.md`

### Secrets（不入文件 / 不入 git）
- Lark `app_id` / `app_secret` / `verification_token` / `encrypt_key`
- Lark Open API `tenant_access_token`（运行时换）
- LLM API key（DeepSeek for v3）
- 推荐：`/etc/review-agent/secrets.env` (mode 600) 或 systemd `LoadCredential`

---

## 13. Final-Gate 判定

`final-gate` 重扫 `final/<primary>` 文件，按 pillar 聚合 JSON verdict：

```jsonc
{
  "verdict": "READY" | "READY_WITH_OPEN_ITEMS" | "FORCED_PARTIAL" | "FAIL",
  "csw_gate_pillar": "Intent",
  "csw_gate_status": "pass" | "fail" | "unresolvable",
  "pillar_verdict": {
    "Background":"pass","Materials":"fail","Framework":"pass","Intent":"pass"
  },
  "pillar_counts": {/* per pillar: pass/open_blocker/unresolvable/total */},
  "by_source": {"four_pillar_scan":9,"responder_simulation":5,"legacy":0},
  "regressions": []
}
```

**Intent pillar 是 CSW gate** — 单独这个柱 fail → verdict=FAIL → 拒绝 close → 回 Q&A。
`close-session` 强制此规则除非 `--force`。

**FAIL 后的回 Q&A 状态机**（v2 新增明确路径）：
1. final_gate 写 verdict=FAIL，session.stage 转移 `final_gating → qa_active_reopened`（过渡态）
2. dispatcher 立刻把 cursor.regression_rescan=true，回归 BLOCKER append 到 pending 队首
3. emit 一条 DM 给 Requester："final gate 发现 X 处回归（具体 X），我们再过一轮。第一条："
4. 第一条 finding emit 后 stage 自动转回 `qa_active`，cursor.regression_rescan 置 false
5. 若同一 session 进 final_gating 已 ≥ 2 次还 FAIL → 强制 verdict=FORCED_PARTIAL，admin dashboard 高亮"反复 fail"，等 admin 介入

**Backward compat**：legacy 7-axis annotations（< 2026-04-21）按以下 map 自动转柱：
- BLUF / Decision Readiness → Intent
- Completeness → Framework
- Assumptions / Evidence / Red Team / Stakeholder → Materials

---

## 14. 文档编辑权限

`admin_style.md` 全局默认；`profile.md` 可 per-Responder 覆盖；session start 可 per-subject 覆盖。

| 值 | bot 行为 |
|---|---|
| `none` | 只 feedback，Requester 全 own 编辑 (CSW-pure) |
| `suggest`（**v0 默认**） | bot 产 `final/revised.md` 作为 suggested rewrite；Requester accept / modify / reject 整体；原文保 `input/` |
| `direct`（v1） | 直接改 Lark Doc / Google Doc（需 OAuth + scope `docx:document`） |

v0 ceiling = `suggest`。

---

## 15. 部署期望

- **平台**：Linux VPS（DO droplet 159.65.75.97 已在用，与 openclaw 共存）
- **Lark App 独立**（v2 新增 round-1 I6）：review-agent 在 Lark 开放平台**注册一个独立的 Self-Built App**，独立 app_id / scope / event subscription URL，**不与 openclaw 的 Lark bot 共用**。doctor 命令检查"两个 bot 各自的 webhook URL 不冲突 / scope 不互踩 / 同 droplet 上 systemd 端口不冲突"
- **Python**：3.11+（FastAPI + httpx + sqlite3）
- **Process supervisor**：systemd unit（独立 user `review-agent`，不复用 openclaw 用户）
- **HTTPS**：Caddy / nginx 反代到 FastAPI uvicorn
- **Lark webhook URL**：`https://<host>/lark/webhook`
- **Dashboard**：`http://127.0.0.1:8765`，不暴露公网，SSH tunnel 访问
- **存储**：`/var/lib/review-agent/`（per-user file tree）+ `/var/lib/review-agent/state.db`（SQLite，跨 user 索引/dashboard 用）
- **Logs**：`/var/log/review-agent/{access,app,llm}.log` + journald
- **Backup**：每日 cron tar `/var/lib/review-agent/` 到 `/var/backups/review-agent/`，30 天保留

---

## 16. 非功能要求

### 隔离（v2 痛过，v3 起就要做对）
- **Per-pairing 数据隔离**：FastAPI handler 在路由分发后只读写 `users/<requester_oid>/sessions/<id>/`，
  代码层强约束（不靠"主 agent 不要 cat 别人的"discipline）
- **路径穿越硬防护**（v2 新增 round-1 B1）：所有访问 session 文件的入口都过 `resolve_session_path(requester_oid, session_id, rel)` 单点函数，函数内强制 `os.path.realpath()` 之后必须以 `/var/lib/review-agent/fs/users/<requester_oid>/sessions/<session_id>/` 开头，否则 raise，不调用任何外部进程也不读
- **systemd 沙箱**：`PrivateTmp=true` + `ProtectSystem=strict` + `ReadWritePaths=` 白名单
- **Session frozen config**：admin / responder 中途改 live 不影响在飞
- **跨 session 不继承 context**：每次 LLM call 只注入本 session 的 frozen 文件 + normalized + cursor 指向的 finding

### 失败状态机（v2 新增 round-1 B2）
- 任意 pipeline 阶段 LLM 三次重试失败 → session.status = `failed`，session.failed_stage 记录哪一阶段
- Requester 收到一条明确 DM："这次卡在 X 阶段，admin 已收到，可以重试或换条材料"
- Responder 收到 admin notify（仅当 session 已经 commit 到 Responder 那边，比如 close 中 deliver 失败时）
- admin dashboard 显示 failed sessions 列表 + "Resubmit" 按钮
- 同一 stage 自动重试上限 3，超过后**不**自动重试，必须 admin 介入

### 性能 / 上限（v2.1 round-2 NB1 修订）
- 一个 Requester 同时最多 1 个 active LLM call（per-Requester 串行，避免 race）
- **队列隔离 + 公平调度**：dispatcher 维护 `Dict[requester_oid, asyncio.Queue]`；单 consumer round-robin 拉任务
- **v0 已知限制**：单 consumer 一次只跑一条 task —— 长 LLM call 会阻塞其他 Requester 的下一条。**v1 起多 worker（按 oid hash 分配）才能真正并行**
- 全局并发：webhook 入口允许多并发；LLM 调用按 per-Requester key 排队
- LLM 超时 90s；失败 retry 2 次（exponential backoff）；最终失败 → 给 Requester "我的脑子卡了，再发一次"
- **轮数硬上限**：3 轮（`REVIEW_AGENT_MAX_ROUNDS=5` 可调，硬绝顶 5）

### 性能 / 上限
- 一个 session 同时最多 1 个 active LLM call（per Requester 串行，避免 race）
- 全局并发：webhook 入口允许多并发；LLM 调用按 per-Requester key 排队
- LLM 超时 90s；失败 retry 2 次（exponential backoff）；最终失败 → 给 Requester "我的脑子卡了，再发一次"
- **轮数硬上限**：3 轮（`REVIEW_AGENT_MAX_ROUNDS=5` 可调，硬绝顶 5）

### 可观测性
- 每次 LLM call 落 jsonl：model / prompt token / completion token / latency / pillar / source
- 每条 inbound DM 落 jsonl：sender / type / size / handled-by / decision
- Dashboard 实时显示：active sessions / pending findings / 最近 100 条 LLM call 成本
- DeepSeek 用 cache_hit 字段计开销

### 安全
- Lark webhook 强校验 `verification_token` + `encrypt_key`（如开了加密）
- 入口 rate-limit（per IP + per open_id）
- 不记 Lark 用户消息明文到日志（只记 hash + 类型 + size + 摘要 ≤30 字）
- Secrets 走 `/etc/review-agent/secrets.env` mode 600，systemd unit 用 `EnvironmentFile=`，**不入 git / 不进 sqlite**
- 所有 LLM prompt 用模板（外部输入只能进 user message，不能进 system prompt 或 tool 名）— 防 prompt injection

### 升级 / 卸载
- `update.sh` 拉新代码 + 数据库 migration（用 alembic 或简易 SQL 脚本表）+ 不动用户数据
- `uninstall.sh` 默认只卸 service + 代码，**不**碰 `/var/lib/review-agent/`；带 `--purge` 才删用户数据，先打 tar 备份到 `/var/backups/review-agent/uninstall-<ts>.tgz`

### 可测试性
- 所有 LLM 调用走单一 client adapter；测试用 fake adapter 注入固定 response
- 4-pillar 扫描 / cursor / dissent 状态机 / final-gate 都有单元测试（不打真 LLM）
- 端到端冒烟：mock Lark webhook → 走完一个 session → 比对 summary.md 6 节齐全

---

## 17. v3 相对 v2 的取舍

### 保留（核心方法学，逐字搬）
- 三角色模型 + 权限矩阵
- 4-pillar + Responder Simulation 双层 review
- 6 个挑战维度作为 persona 硬规则
- 6 节 decision-ready summary
- Annotation schema + cursor + dissent log
- 文档编辑权限 spectrum (none / suggest / direct)
- final-gate 4 verdict + Intent CSW gate
- Frozen-at-start session config
- IM shortcut keys + 选项块约定 + 节奏 / 语言纪律
- 输出卫生（不漏 thinking / tool / stderr）
- Top-5 default + `more` / `done` 命令
- Ingest hard-fail with friendly fallback（不抛 stacktrace）
- Ingest size guardrails

### 去掉（openclaw / hermes 特定 glue）
- ❌ MEMORY.md SOP routing（v3 用 FastAPI dispatcher，确定性路由）
- ❌ Per-peer subagent dirs / `workspace-feishu-ou_*`（v3 用单 service + per-pairing namespace）
- ❌ Watcher daemon poll filesystem（v3 用 webhook 同步 spawn，没必要）
- ❌ monitor.js patch / patch_openclaw_json.py / patch_hermes_config.py（v3 没这些底座）
- ❌ sandbox.binds 修复（v3 没 sandbox）
- ❌ `send-lark.sh` / `lark-fetch.sh` 等 shell wrappers（v3 用 httpx 直接调）
- ❌ `agent_persona.md` 通过脚本临时拼 prompt（v3 在 `prompts/` 下管 system prompt 模板）
- ❌ `unauthorized_dm_behavior` / `dynamicAgentCreation` 等 openclaw config（v3 dispatcher 自己管 unknown sender）
- ❌ Legacy 7-axis（v3 起就 4 柱，不带 backward compat）

### 简化
- 装 / 卸 / 升级合到一个 `bin/review-agent` CLI 子命令，不再 5+ shell scripts
- Dashboard 走 FastAPI 自带 router，不再单独 8765 端口（也可以选保留 8765 作 admin-only port）
- "User mgmt + session lifecycle + delivery" 全是 SQLite 表 + 一组 ORM-light helper（不上 SQLAlchemy，sqlite3 + dataclass）

---

## 18. 范围（v0 / v1 / v2 路线图）

### v0（本次实现）
- 三角色 / 单 Responder
- 4-pillar + Responder Sim
- Q&A loop with shortcut keys
- `suggest` 文档编辑权限
- summary 6 节 + dissent
- DM delivery + local archive
- Dashboard 基础视图
- Lark Doc 创建（output 用），不做 inline comment

### v1（路线图）
- Multi-Responder（一个 admin 多个 Responder）
- `direct` 文档编辑权限（直接改 Lark Doc）
- Lark Doc inline comments（用 `/open-apis/docx/v1/documents/:doc_id/comments`）
- Email backend
- Group chat trigger（@ bot in group）
- Google Doc ingest

### v2（远期）
- 多 agent 协作：研究 subagent 补 evidence + stakeholder subagent 扮演反方
- Responder follow-up 命令（"more" / "deepen" 在 Responder 收到 brief 后追问）
- Per-Responder 多 profile（按 brief 类型切换）

---

## 附录 A · 关键 SLA / 业务指标

| 指标 | 目标 |
|---|---|
| Session close 后 Responder 收到 summary 时延 | < 30s |
| 一次 4-pillar scan 时延（含 Responder Sim） | < 90s |
| 一次 Q&A turn 回复时延 | < 15s |
| 全 session 平均 LLM cost | < $0.20 |
| Intent gate 误判率（pass 但 Responder 觉得 ask 不清） | < 10% |
| Cross-session 数据泄漏事件 | **= 0**（架构强约束） |

---

## 附录 B · 反 case / 不要做什么

- ❌ Bot 替 Requester 写 brief（违反 CSW）
- ❌ Bot 把"建议 Responder 也 review 一下 X"放进 finding（违反 no-boss-burden）
- ❌ Bot 主动 push Responder 中间状态（仅 close 时一次性 push）
- ❌ Bot 答 Requester "great question 你想得很全面"（赞美开场）
- ❌ Bot 一次发 5 条 NICE-TO-HAVE（噪音）
- ❌ Bot 跨 round 持续要求加内容（gate 上限内就降级 NICE-TO-HAVE）
- ❌ 把 Lark 用户消息原文落明文 log
- ❌ Secret 入 git
- ❌ Webhook 不验签
- ❌ 卸载默认删用户数据（必须 `--purge` 且预先打 tar 备份）
