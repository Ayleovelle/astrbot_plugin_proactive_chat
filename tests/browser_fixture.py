"""Local UI fixture using real LogsView and WebAdminServer, synthetic journal only."""

import asyncio
import os
import sys
import types
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_log_center as f
from fastapi.responses import FileResponse, HTMLResponse
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
DEPS = Path(os.environ["PROACTIVE_QA_NODE_MODULES"])
temp = tempfile.TemporaryDirectory(prefix="proactive-ui-")
center = f.LogCenter(temp.name, {"max_entries": 5000, "debug_enabled": True})
asyncio.run(f.FakePlugin(center).check_and_chat("SYNTHETIC_UI_SESSION"))
for i in range(160):
    center.record(
        "condition_checked",
        trace_id="ui-run",
        session_id="SYNTHETIC_UI_SESSION",
        details={
            "condition": "session_enabled",
            "value": True,
            "expected": True,
            "allowed": True,
        },
    )
center.record(
    "run.completed",
    trace_id="ui-run",
    session_id="SYNTHETIC_UI_SESSION",
    details={
        "execution_outcome": "completed",
        "delivery_outcome": "partial_success",
        "accepted_segments": 1,
        "failed_segments": 0,
        "unknown_segments": 1,
        "delivery_unknown": True,
    },
)
f.flush(center)
plugin = types.SimpleNamespace(
    config={"web_admin": {"password": "ui-fixture"}}, log_center=center
)
server = f.WebAdminServer(plugin)
server._auth_enabled = False


@server.app.get("/qa")
async def page():
    return HTMLResponse(
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Proactive Log Center QA</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/css/style.css"><style>body{margin:0;background:#faf9fd;padding:24px}#root{max-width:1100px;margin:auto}</style></head><body><div id="root"></div><script src="/qa-vendor/react"></script><script src="/qa-vendor/react-dom"></script><script src="/qa-vendor/mui"></script><script>window.AuthUtil={withAuthHeaders:(h)=>h};</script><script src="/js/views/LogsView.js"></script><script>ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MaterialUI.ThemeProvider,{theme:MaterialUI.createTheme()},React.createElement(LogsView)));</script></body></html>"""
    )


@server.app.get("/qa-vendor/{name}")
async def vendor(name: str):
    paths = {
        "react": "react/umd/react.production.min.js",
        "react-dom": "react-dom/umd/react-dom.production.min.js",
        "mui": "@mui/material/umd/material-ui.production.min.js",
    }
    return FileResponse(DEPS / paths[name], media_type="application/javascript")


server.app.router.routes.sort(
    key=lambda route: 0 if getattr(route, "path", "").startswith("/qa") else 1
)
try:
    uvicorn.run(server.app, host="127.0.0.1", port=8765, log_level="error")
finally:
    center.close()
    temp.cleanup()
