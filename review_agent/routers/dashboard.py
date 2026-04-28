from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from ..core.enums import SessionStatus
from ..core.storage import Storage


def make_router(storage: Storage):
    api = APIRouter()

    @api.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        active = storage.list_sessions(status=SessionStatus.ACTIVE)
        failed = storage.list_sessions(status=SessionStatus.FAILED)
        closed = storage.list_sessions(status=SessionStatus.CLOSED)[:20]
        rows = []
        for s in active:
            rows.append(
                f"<tr><td>{s.id}</td><td>{s.requester_oid}</td><td>{s.subject or '-'}</td>"
                f"<td>{s.stage.value}</td><td>{s.round_no}</td></tr>"
            )
        active_html = "".join(rows) or "<tr><td colspan=5>(none)</td></tr>"

        failed_rows = "".join(
            f"<tr><td>{s.id}</td><td>{s.failed_stage}</td>"
            f"<td>{(s.last_error or '')[:80]}</td></tr>"
            for s in failed
        ) or "<tr><td colspan=3>(none)</td></tr>"

        closed_rows = "".join(
            f"<tr><td>{s.id}</td><td>{s.subject or '-'}</td><td>{s.verdict}</td>"
            f"<td>{s.closed_at or '-'}</td></tr>"
            for s in closed
        )

        return HTMLResponse(
            f"""<!doctype html><html><head><meta charset=utf-8>
<title>review-agent dashboard</title>
<style>body{{font-family:sans-serif}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:4px;font-size:13px}}h2{{margin-top:24px}}</style>
</head><body>
<h1>review-agent dashboard</h1>
<h2>Active sessions ({len(active)})</h2>
<table><tr><th>id</th><th>requester</th><th>subject</th><th>stage</th><th>round</th></tr>
{active_html}</table>
<h2>Failed sessions ({len(failed)})</h2>
<table><tr><th>id</th><th>failed_stage</th><th>last_error</th></tr>{failed_rows}</table>
<h2>Recent closed</h2>
<table><tr><th>id</th><th>subject</th><th>verdict</th><th>closed_at</th></tr>{closed_rows}</table>
</body></html>"""
        )

    return api
