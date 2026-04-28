"""BitableBackend — read/write Lark Bitables as structured review input/output.

Handles:
- Input: Requester submits a Bitable URL → fetch all records → normalize as markdown
- Output: Write review findings/stats to a configured Bitable

Bitable URL pattern:
  https://<tenant>.feishu.cn/base/<app_token>?table=<table_id>&view=<view_id>
  https://<tenant>.larksuite.com/base/<app_token>?table=<table_id>&view=<view_id>

Requires the bot to have access to the Bitable (share with bot app).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .base import IngestBackend, IngestRejected, IngestResult

if TYPE_CHECKING:
    from ...lark.client import LarkClient


_BITABLE_URL_RE = re.compile(
    r"https?://[^/\s]+\.(?:feishu\.cn|larksuite\.com)"
    r"/base/(?P<app_token>[A-Za-z0-9_-]+)"
    r"(?:\?.*?table=(?P<table_id>[A-Za-z0-9_-]+))?"
)

_LARK_SHEET_URL_RE = re.compile(
    r"https?://[^/\s]+\.(?:feishu\.cn|larksuite\.com)"
    r"/sheets/(?P<spreadsheet_token>[A-Za-z0-9_-]+)"
)


def extract_bitable_urls(text: str) -> list[tuple[str, str, str]]:
    """Find Bitable URLs in text. Returns list of (url, app_token, table_id)."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in _BITABLE_URL_RE.finditer(text):
        url = m.group(0)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, m.group("app_token"), m.group("table_id") or ""))
    return out


def extract_sheet_urls(text: str) -> list[tuple[str, str]]:
    """Find Lark Sheet URLs in text. Returns list of (url, spreadsheet_token)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _LARK_SHEET_URL_RE.finditer(text):
        url = m.group(0)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, m.group("spreadsheet_token")))
    return out


def _bitable_records_to_markdown(
    records: list[dict], field_names: list[dict],
) -> str:
    """Convert Bitable records to markdown table.

    field_names is list of dicts with field_name/type from get_bitable_fields().
    """
    if not records:
        return "(empty table)"

    # Build header row from field schema
    cols = [f.get("field_name", f"col{i}") for i, f in enumerate(field_names)]
    if not cols:
        # fallback: use keys from first record's fields
        cols = list(records[0].get("fields", {}).keys())

    rows: list[str] = []
    rows.append("| " + " | ".join(cols) + " |")
    rows.append("| " + " | ".join("---" for _ in cols) + " |")

    for rec in records:
        fields = rec.get("fields", {})
        vals = []
        for col_name in cols:
            raw = fields.get(col_name, "")
            # Handle complex field types: user IDs, choices, etc.
            if isinstance(raw, list):
                vals.append(", ".join(str(v) for v in raw))
            elif isinstance(raw, dict):
                # Single choice: { "text": "Option A" }
                if "text" in raw:
                    vals.append(raw["text"])
                # Link: { "link": "...", "text": "..." }
                elif "link" in raw:
                    vals.append(raw.get("text", raw["link"]))
                else:
                    vals.append(str(raw))
            else:
                vals.append(str(raw) if raw is not None else "")
        rows.append("| " + " | ".join(vals) + " |")

    return "\n".join(rows)


class BitableBackend(IngestBackend):
    name = "bitable"
    kind = "text"

    def __init__(self, lark_client: LarkClient | None = None):
        self.lark = lark_client

    def can_handle(self, mime: str, ext: str) -> bool:
        return False  # invoked directly by dispatcher

    async def ingest(self, input_path: Path) -> IngestResult:  # pragma: no cover
        raise IngestRejected("BitableBackend 只能通过 fetch_bitable_urls() 调用")

    async def fetch_bitable_urls(
        self, urls: list[tuple[str, str, str]],
    ) -> IngestResult:
        """Fetch Bitable content. urls is list of (url, app_token, table_id)."""
        if self.lark is None:
            raise IngestRejected(
                "BitableBackend 没有 lark_client 注入。配置 bug，让 admin 看一下。"
            )
        if not urls:
            raise IngestRejected("URL 列表是空的。")

        results: list[str] = []
        for url, app_token, table_id in urls:
            try:
                text = await self._fetch_one(app_token, table_id)
                results.append(f"## {url}\n\n{text}")
            except Exception as e:
                results.append(
                    f"## {url}\n\n> ⚠ 无法读取 Bitable：{e}"
                )

        if all("⚠" in r for r in results):
            raise IngestRejected(
                "Bitable 全部读取失败 — 检查 bot 是否有权限访问。"
                "（方式：Bitable 右上角「分享」→ 添加应用 → 搜你的 bot）"
            )

        combined = "\n\n---\n\n".join(results)
        return IngestResult(
            backend="bitable",
            normalized=f"[📊 已从 {len(urls)} 个 Bitable 读取内容]\n\n{combined}",
            note=f"fetched {len(urls)} Bitables, {len(combined)} chars",
        )

    async def _fetch_one(self, app_token: str, table_id: str | None) -> str:
        if table_id:
            # Fetch schema fields first for column names
            fields = await self.lark.get_bitable_fields(app_token, table_id)
            # Then fetch all records
            data = await self.lark.get_bitable_records(app_token, table_id)
            records = data.get("items", data.get("records", []))
            if fields and records:
                return _bitable_records_to_markdown(records, fields)
            elif records:
                # No field schema — render raw
                lines = []
                for rec in records:
                    lines.append(str(rec.get("fields", {})))
                return "\n".join(lines) or "(empty table)"
            return "(empty table)"
        else:
            # No table_id specified — list tables and show structure
            tables = await self.lark.list_bitable_tables(app_token)
            if not tables:
                return "Bitable 中没有找到表格。"
            lines = [f"Bitable 包含 {len(tables)} 个表格："]
            for t in tables:
                tname = t.get("name", t.get("id", "?"))
                tid = t.get("table_id", t.get("id", ""))
                lines.append(f"- [{tname}](base://{app_token}/{tid})")
            return "\n".join(lines)


class SheetBackend(IngestBackend):
    name = "sheet"
    kind = "text"

    def __init__(self, lark_client: LarkClient | None = None):
        self.lark = lark_client

    def can_handle(self, mime: str, ext: str) -> bool:
        return False  # invoked directly by dispatcher

    async def ingest(self, input_path: Path) -> IngestResult:  # pragma: no cover
        raise IngestRejected("SheetBackend 只能通过 fetch_sheet_urls() 调用")

    async def fetch_sheet_urls(
        self, urls: list[tuple[str, str]],
    ) -> IngestResult:
        """Fetch Lark Sheet content. urls is list of (url, spreadsheet_token)."""
        if self.lark is None:
            raise IngestRejected(
                "SheetBackend 没有 lark_client 注入。配置 bug，让 admin 看一下。"
            )

        results: list[str] = []
        for url, token in urls:
            try:
                text = await self._fetch_one(token)
                results.append(f"## {url}\n\n{text}")
            except Exception as e:
                results.append(f"## {url}\n\n> ⚠ 无法读取 Sheet：{e}")

        combined = "\n\n---\n\n".join(results)
        return IngestResult(
            backend="sheet",
            normalized=f"[📗 已从 {len(urls)} 个 Sheet 读取内容]\n\n{combined}",
            note=f"fetched {len(urls)} Sheets, {len(combined)} chars",
        )

    async def _fetch_one(self, token: str) -> str:
        meta = await self.lark.get_sheet_meta(token)
        sheets = meta.get("sheets", [])
        if not sheets:
            return "(empty spreadsheet)"

        parts: list[str] = []
        for sheet in sheets:
            sheet_id = sheet.get("sheet_id", "")
            title = sheet.get("title", "Sheet")
            grid = sheet.get("grid_properties", {})
            row_count = min(grid.get("row_count", 50), 200)  # cap at 200 rows
            col_count = grid.get("column_count", 10)
            if col_count > 26:
                col_count = 26  # cap at Z column
            col_letter = chr(64 + col_count) if col_count <= 26 else "Z"
            sheet_range = f"{sheet_id}!A1:{col_letter}{row_count}"

            try:
                values = await self.lark.get_sheet_values(token, sheet_range)
                if not values:
                    continue
                md = _values_to_markdown(values)
                parts.append(f"### {title}\n\n{md}")
            except Exception as e:
                parts.append(f"### {title}\n\n> ⚠ 读取失败：{e}")

        return "\n\n".join(parts) if parts else "(could not read sheet content)"


def _values_to_markdown(values: list[list[str]]) -> str:
    """Convert a 2D array of cell values to markdown table."""
    if not values:
        return "(empty range)"

    rows: list[str] = []
    # Header row
    header = [str(v) if v else "" for v in values[0]]
    rows.append("| " + " | ".join(header) + " |")
    rows.append("| " + " | ".join("---" for _ in header) + " |")

    for row in values[1:]:
        padded = [str(v) if v else "" for v in row]
        while len(padded) < len(header):
            padded.append("")
        rows.append("| " + " | ".join(padded[:len(header)]) + " |")

    return "\n".join(rows)
