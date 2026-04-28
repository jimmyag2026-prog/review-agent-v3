"""Issue #3b + #7: format Q&A findings as Lark `post` rich-text payloads.

Lark `post` supports bold / italic / underline / strikethrough but no real font
color. We convey severity through emoji and human-friendly Chinese labels.

Issue #7 redesign principle: requester sees ONLY:
- severity (in human terms, not "BLOCKER")
- the issue + the action
- a short reply menu

NEVER expose internal fields (pillar / source / id / round / pending counts).
Those live in db / annotations.jsonl for audit only.
"""
from __future__ import annotations

import re

# Severity → human-readable Chinese label
_SEVERITY_LABEL = {
    "BLOCKER":      ("🔴", "必须修一下"),
    "IMPROVEMENT":  ("🟡", "建议改一下"),
    "NICE-TO-HAVE": ("⚪", "可选 · 改不改都行"),
}

# Concise reply menu (used in every finding)
_OPTION_LINE = "回复：a 改 · b 不同意 · c 我有自己的版本 · pass 跳 · done 够了"

# Welcome message used by auto-register (Issue #7c)
def welcome_message(*, requester_name: str, responder_name: str) -> str:
    return (
        f"你好 {requester_name} 👋\n"
        f"我是 {responder_name} 的会前 review 助手 —— 帮你把要给 {responder_name} 看的"
        f"东西先过一遍，把 {responder_name} 一定会问的问题先解决掉。\n"
        f"\n"
        f"怎么用：\n"
        f"① 把要给 {responder_name} 看的东西发我（草稿、提案、1:1 议程，文字 / PDF / Lark 文档都行）\n"
        f"② 我会用 {responder_name} 的眼光挑几个问题问你\n"
        f"③ 你回复我（一般是 a / b / c / pass / done），几轮后我整理成 brief 直接发给 {responder_name}\n"
        f"\n"
        f"试一下吧，把你想让 {responder_name} 看的内容发过来 👇"
    )


def admin_notify_message(*, requester_name: str, requester_oid: str) -> str:
    return (
        f"📬 新 Requester 自动注册：{requester_name} (`{requester_oid}`)\n"
        f"如不希望此人发起 review，VPS 上跑：\n"
        f"`review-agent remove-user {requester_oid}` "
        f"或在 secrets.env 设 `REVIEW_AGENT_AUTO_REGISTER=false`"
    )


def _t(text: str, *, style: list[str] | None = None) -> dict:
    d = {"tag": "text", "text": text}
    if style:
        d["style"] = style
    return d


def _split_body(body_text: str) -> tuple[str, str]:
    """Body comes as `问题: ...\n建议: ...` from the LLM (qa_emit_finding prompt
    contract). Split into (issue, suggest); fall back gracefully if format drifts."""
    issue, suggest = "", body_text.strip()
    m = re.search(r"问题\s*[:：]\s*(.+)", body_text)
    if m:
        issue = m.group(1).strip()
    m2 = re.search(r"建议\s*[:：]\s*(.+)", body_text)
    if m2:
        suggest = m2.group(1).strip()
    if not issue and "\n" in body_text:
        first, _, rest = body_text.partition("\n")
        issue, suggest = first.strip(), rest.strip()
    if not issue:
        issue = body_text.strip()
        suggest = ""
    return issue, suggest


def build_finding_post(
    *, finding_id: str, pillar: str, severity: str, source: str,
    body_text: str, round_no: int, max_rounds: int,
    remaining: int, deferred: int,
) -> list[list[dict]]:
    """Return Lark post `content` paragraphs for one finding.

    Issue #7: pillar/id/source/round are NOT shown to requester (only used in
    audit logs); requester sees friendly severity emoji + label + body + menu.
    Signature kept compatible with caller.
    """
    sev_em, sev_label = _SEVERITY_LABEL.get(severity, ("•", severity))
    issue, suggest = _split_body(body_text)

    paragraphs: list[list[dict]] = [
        [_t(f"{sev_em}  ", style=[]), _t(sev_label, style=["bold"])],
    ]
    if issue:
        paragraphs.append([_t("")])
        paragraphs.append([
            _t("问题  ", style=["bold"]),
            _t(issue),
        ])
    if suggest:
        paragraphs.append([
            _t("建议  ", style=["bold"]),
            _t(suggest, style=["italic"]),
        ])
    paragraphs.extend([
        [_t("")],
        [_t("─────────────")],
        [_t(_OPTION_LINE)],
    ])
    return paragraphs


def build_text_fallback(
    *, finding_id: str, pillar: str, severity: str, source: str,
    body_text: str, round_no: int, max_rounds: int,
    remaining: int, deferred: int,
) -> str:
    """Plain-text version (used when Lark client can't send post — testability)."""
    sev_em, sev_label = _SEVERITY_LABEL.get(severity, ("•", severity))
    issue, suggest = _split_body(body_text)
    parts = [f"{sev_em}  {sev_label}", ""]
    if issue:
        parts.append(f"问题  {issue}")
    if suggest:
        parts.append(f"建议  {suggest}")
    parts += ["", "─────────────", _OPTION_LINE]
    return "\n".join(parts)


def build_text_simple(text: str) -> str:
    """For non-finding DMs — keep plain text."""
    return text
