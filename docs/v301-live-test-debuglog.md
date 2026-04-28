# debug log — 2026-04-28 live test 后

## 用户反馈的 3 个问题

### #1 LLM 模型配置不透明
**症状**：用户问"vps 上的大模型 API 是哪个环节填的？为什么我的直接用 deepseek？怎么改？"

**根因**：
- 现状：`secrets.env` 只列了 `DEEPSEEK_API_KEY`，`config.py` 硬编码 `provider="deepseek"` + `default_model="deepseek-v4-pro"`，install.sh 没引导填模型选择
- 我的本地 keychain 里有 deepseek key，部署时直接写了 secrets.env，所以"默认 deepseek"是因为我替你做了选择，不是设计如此

**修法**（保持 v0 范围，不上多 provider 抽象 — 那个留 v3.1）：
- `secrets.env.example` 加注释说明：当前只支持 deepseek，若要换 openai/anthropic 需要改 `llm/` 实现 + 改 config provider
- `config.toml` 模板里清楚标 `[llm] provider / default_model / fast_model`，用户可改 model 字符串（如 `deepseek-v4-pro` ↔ `deepseek-v4-flash`）
- 加 CLI 子命令 `review-agent set-model <model>` 直接改 config.toml + restart 提示
- INSTALL.md 加"How to change LLM model"段
- doctor 增加：检查 config.toml 选的 provider 是否有对应 API key

### #2 unknown sender 应该自动注册为 Requester
**症状**：admin 之外的人 DM bot，现在回"找 admin 加你"，需要后台 add-user。用户期望：自动注册 + 给 admin 发条通知 DM。

**修法**（dispatcher._handle_incoming 改逻辑）：
- 当 sender 在 db 里不存在时：
  1. 找单 Admin（v0 只 1 个）+ 其 paired Responder（一般等于 Admin 自己）
  2. 自动 INSERT 该 sender 为 `Requester`，pair 给那个 Responder
  3. DM 该新 Requester 一句欢迎语（"你好，我是你的 review 助手，发草稿/提案给我..."）
  4. DM admin 一条通知（"<name> 刚被自动注册为 Requester，查 review-agent list-users"）
  5. **继续处理当前消息**（不用让用户再发一次）
- 配置开关 `[review] auto_register_requesters = true`（默认 true，可在 config.toml 关闭做白名单模式）
- 边界：如果完全没 admin（首次安装未 setup）→ 仍然拒绝（防止裸装公网开放给任何人）

### #3 对话格式 + 最终总结发给双方
**3a · summary 给双方**：用户期望 close 时 admin（Responder）和 requester 都收到完整 summary。
- 现状：`pipeline/deliver.py::load_targets` 默认 targets 已经包含 `responder-dm` + `requester-dm` 都收 summary，应该 work
- 但要测：solo-test 模式下 admin == responder == requester 时是否会重复 / 漏发？需要 integration test 覆盖
- 行动：写一个端到端 fake LLM test 跑完整 close chain，断言 outbound 表里 admin/requester 各有一条且内容含 summary

**3b · 对话过程的格式 + 字体颜色**：当前 bot 用 Lark `text` 消息类型，纯文本。
- Lark 富文本选项：
  - `post`（rich text）：支持 bold / italic / underline / 内联 code / 链接 / @ — **没有真正的字体颜色**，只能用 emoji 染色
  - `interactive`（卡片）：支持 header 颜色（红/黄/灰/蓝）+ Markdown body + 可点按钮 — 体验最好但需要 bot 处理 card.action callback（新事件类型）
- v0 取舍：上 `post` 富文本（emoji 染色 + bold + italic），不上卡片按钮（按钮要 callback 后端，是 v3.1 工作量）
- 设计每条 finding 的格式：
  ```
  🔴【BLOCKER · Intent · r1】       <- 加粗 + 颜色 emoji 按 severity
  
  问题：<issue text>                 <- 加粗
  建议：<suggest text>               <- 斜体
  
  ─────
  a 接受 │ b 不同意 │ c 改成 │ pass 跳过 │ more │ done
  ```
- severity emoji：🔴 BLOCKER · 🟡 IMPROVEMENT · ⚪ NICE-TO-HAVE
- pillar emoji：🎯 Intent · 📚 Background · 📊 Materials · 🧭 Framework
- 实现：`lark/client.py` 加 `send_dm_post()` 方法；`prompts/qa_emit_finding.md.j2` 不变（让 LLM 仍出文本），dispatcher 在 emit 之前用 helper 把文本套上格式

## 实现 + 测试顺序

1. 先在本地 `~/code/review-agent-v3/src/` 改 + 加 tests，**不动 VPS**
2. 全 pytest 绿之后再 rsync 上 VPS
3. 让 Tester 把当前 session 跑完（或 force_close），再重启 service 用新版
4. 用第二个测试再走一遍：清掉 Tester 用户 → Tester 重新 DM → 验证 auto-register + 富文本 + close 后 summary 双发
5. 全部通过后 → commit 一组 + push GitHub

## #4 propose_close 后 session 卡死（live test 发现）
**症状**：Tester 把 5 个 BLOCKER 全 accept 了之后，cursor.current_id=null + pending=[]，再发 "a" 完全无响应。她连发 4 个 "a" + "发过去了吗" 都石沉大海。

**根因**：`qa_loop.handle_reply` 当 cursor.current_id 是 None 且 intent 不是 MORE/DONE/FORCE_CLOSE 时直接返回 `no_op`，没有意识到这是 propose_close 之后等待用户确认的 state。dispatcher 在收到 propose_close 时只发 DM 不改 session.stage，所以下一条消息进来 cursor 还是 null → no_op 闭环。

**修法**：
- `enums.py` 加 `Stage.AWAITING_CLOSE_CONFIRMATION`
- `dispatcher` 收到 qa_loop.propose_close 时**同时**把 stage 转到 AWAITING_CLOSE_CONFIRMATION（不只是发 DM）
- 新增 `_handle_close_confirmation()` handler 专门解析这个 stage 的回复：a/done/yes → 进 close chain；more/b → pull deferred + 回 qa_active；其他文字 → 给清晰提示菜单（不再 no_op）
- 4 个 unit tests 覆盖：transition / a triggers close / more pulls deferred / unknown nudges

## 本地实现完成（2026-04-28）— 待 VPS 验证

| Issue | 状态 | 主要改动 |
|---|---|---|
| #1 LLM 配置 | ✅ 本地完成 | `secrets.env.example` 加注释；config.py 加 4 个新 env override；CLI `set-model` / `show-config`；doctor 增加 provider×key 检查；INSTALL.md 加 §B.6.2 |
| #2 auto-register | ✅ 本地完成 | dispatcher.`_maybe_auto_register()` + `_lookup_display_name()`；config `auto_register_requesters=true`（默认）；CLI `remove-user`；admin 收 notify DM；INSTALL.md 加 §B.6.1 |
| #3a 双发 summary | ✅ 验证通过 | 新增 `test_close_delivery.py` 端到端走完 close chain 断言 outbound 双发；架构本来就 work，是确认+加测试 |
| #3b 富文本 | ✅ 本地完成 | `lark/client.py::send_dm_post()` 新增；新建 `pipeline/_format.py`（severity 🔴🟡⚪ + pillar emoji + bold header + italic suggest + 横线 + 选项行）；prompt `qa_emit_finding.md.j2` 简化为只产 `问题:/建议:` 两行；dispatcher fallback 到纯文本 |
| #4 卡死修复 | ✅ 本地完成 | enums 加 AWAITING_CLOSE_CONFIRMATION；dispatcher propose_close 时转 stage；新 handler 解析 a/more/其他；4 tests |
| #5 Bug A — restart 中断 | ✅ 本地完成 | dispatcher 加 BUSY_STAGES 表（SCANNING/MERGING/FINAL_GATING/CLOSING）；用 `storage.has_llm_call_for_stage()` 判断是否被中断；中断 → 自动重启 stage；进行中 → 友好 nudge；4 tests |
| #5 Bug B — subject 被材料填满 | ✅ 本地完成 | confirm_topic._trim_subject() 截断到 60 字 + 第一句；3 tests |
| #6 LLM 空 content / 烂 JSON | ✅ 本地完成 | 加 `LLMOutputParseError(LLMTerminalFailure)` 子类；`_json.extract()` 改 raise 它 → dispatcher 现成 except 自动接 → fail_session 走完整路径（不再 worker silent crash + session 卡死）；2 tests |

## live test 教训（v4-flash 不能撑 4 柱扫描）
- 默认 model 改回 `deepseek-v4-pro`（reasoning 模型，能稳产 JSON）
- `fast_model` 留 `deepseek-v4-flash` 给 confirm_topic 用（轻量任务它能扛）
- 之前我图快全切 v4-flash → scan_four_pillar 返回空 content 导致一连串问题
- v4-flash 适合：confirm_topic（短输出），summary 文本（如果想省成本）
- v4-flash 不适合：scan / final_gate 等需要严格 JSON schema 输出的复杂 prompt

**Tests**: 86 pass (新增 6 set-model + 5 auto-register + 6 format + 2 close_delivery，原有 67)。

**未做（设计明确推迟）**：
- 多 LLM provider（v3.1）
- Lark interactive card + 按钮（v3.1）
- 真正字体颜色（需要 card）

**最后阶段**：
1. 等 Tester 跑完当前 session
2. rsync src/ → VPS reviewer:~/code/review-agent + restart
3. 清掉 Tester user（让她触发 auto-register）+ Tester 重新 DM
4. 验证：auto-register 通知 admin / 富文本 finding / close 后 summary 双发
5. 一次性 commit + push GitHub（包含 INSTALL.md / docs / src 全部）
