"""
viewer.py — SDD Workflow Visual Agent Viewer
Real-time: driven by live orchestrator events via SSE + POST /state.
Demo mode: open http://localhost:PORT?demo=1
Run:  python3 viewer.py
"""

import argparse
import json
import os
import signal
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORTS     = [8765, 8766, 8767, 8768, 8769]
PORT_FILE = "/tmp/sdd_viewer.port"

# ── Shared state ─────────────────────────────
_lock          = threading.Lock()
_last_activity = time.time()   # updated on every POST /state
_owner_pid     = None          # PID of the opencode process that launched us
_state         = {
    "orchestrator":  "idle",   # idle | thinking | reviewing | delegating
    "current_agent": None,     # None | explore | propose | spec | design | tasks | apply | verify | archive
    "agent_state":   "idle",   # idle | working | done | error
    "current_task":  "",       # current task description (short)
    "history":       [],       # [{"stage":"...", "status":"done", "ts":...}]
    "tasks":         [],       # [{"id":"t1","title":"...","status":"pending|in_progress|completed|cancelled","agent":null}]
}
_sse_clients = []


def _broadcast(payload: str):
    dead = []
    for wfile in _sse_clients:
        try:
            wfile.write(f"data: {payload}\n\n".encode())
            wfile.flush()
        except (BrokenPipeError, OSError):
            dead.append(wfile)
    for wfile in dead:
        _sse_clients.remove(wfile)


# ─────────────────────────────────────────────
# HTTP Server
# ─────────────────────────────────────────────
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class ViewerHandler(BaseHTTPRequestHandler):
    html_content = ""

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            body = ViewerHandler.html_content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/state":
            with _lock:
                snap = dict(_state)
            self._send_json(snap)

        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            with _lock:
                snap = json.dumps(_state)
                _sse_clients.append(self.wfile)
            try:
                self.wfile.write(f"data: {snap}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                with _lock:
                    if self.wfile in _sse_clients:
                        _sse_clients.remove(self.wfile)
                return

            try:
                while True:
                    time.sleep(15)
                    try:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        break
            finally:
                with _lock:
                    if self.wfile in _sse_clients:
                        _sse_clients.remove(self.wfile)

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/state":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                incoming = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON"}, 400)
                return

            allowed = {"orchestrator", "current_agent", "agent_state",
                       "current_task", "history", "tasks"}
            global _last_activity
            with _lock:
                _last_activity = time.time()
                for k, v in incoming.items():
                    if k not in allowed:
                        continue
                    if k == "history" and isinstance(v, list):
                        # Gap 3 fix: accumulate history, don't replace.
                        # Dedup by stage name — keep latest entry per stage.
                        existing = {e["stage"]: e for e in _state["history"]}
                        for entry in v:
                            if isinstance(entry, dict) and "stage" in entry:
                                existing[entry["stage"]] = entry
                        _state["history"] = list(existing.values())
                    else:
                        _state[k] = v
                payload = json.dumps(_state)
                _broadcast(payload)
            self._send_json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def find_free_port(candidates):
    for port in candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description="SDD Agent Viewer")
    parser.add_argument("--owner-pid", type=int, default=None,
                        help="PID of the opencode process that launched this viewer")
    args = parser.parse_args()

    global _owner_pid
    _owner_pid = args.owner_pid

    port = find_free_port(PORTS)
    if port is None:
        print("ERROR: No free port found in range", PORTS)
        return

    ViewerHandler.html_content = HTML_PAGE.replace("__PORT__", str(port))
    server = ThreadedHTTPServer(("", port), ViewerHandler)
    url = f"http://localhost:{port}"
    print(f"SDD Viewer running at {url}", flush=True)
    if _owner_pid:
        print(f"Watching opencode PID {_owner_pid} — will shut down when it exits.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        with open(PORT_FILE, "w") as f:
            f.write(str(port))
    except OSError:
        pass

    threading.Timer(0.5, lambda: webbrowser.open_new_tab(url)).start()

    # ── Owner watchdog — shuts down when the opencode process that launched us exits ──
    def _owner_watchdog():
        if not _owner_pid:
            return
        while True:
            time.sleep(5)
            try:
                os.kill(_owner_pid, 0)   # signal 0 = just check existence
            except (ProcessLookupError, PermissionError):
                print(f"\nopencode (PID {_owner_pid}) exited — shutting down viewer.", flush=True)
                server.shutdown()
                return

    t = threading.Thread(target=_owner_watchdog, daemon=True)
    t.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        try:
            os.remove(PORT_FILE)
        except OSError:
            pass


# ─────────────────────────────────────────────
# HTML_PAGE — raw string, NOT f-string
# __PORT__ replaced via str.replace()
# ─────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>SDD Workflow — Live Agent Viewer</title>
  <style>
    :root {
      --bg-0:#07111e;
      --bg-1:#0c1828;
      --bg-2:#142338;
      --panel:#0f1b2d;
      --panel-strong:#15243a;
      --panel-soft:rgba(18,31,49,0.82);
      --line:#213856;
      --line-strong:#31527f;
      --text-0:#eef4ff;
      --text-1:#c7d6ee;
      --text-2:#7f97ba;
      --accent:#79c2ff;
      --accent-soft:rgba(121,194,255,0.16);
      --success:#57d59a;
      --warning:#ffb85c;
      --danger:#ff6b6b;
      --violet:#b792ff;
      --shadow:0 24px 70px rgba(0,0,0,0.38);
      --radius-xl:24px;
      --radius-lg:18px;
      --radius-md:12px;
      --radius-sm:999px;
      --font-ui:"Avenir Next","Segoe UI Variable","Trebuchet MS",sans-serif;
      --font-mono:"SFMono-Regular","Consolas","Liberation Mono",monospace;
    }
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      min-height:100vh;
      padding:28px;
      background:
        radial-gradient(circle at top left, rgba(121,194,255,0.18), transparent 34%),
        radial-gradient(circle at top right, rgba(183,146,255,0.14), transparent 26%),
        linear-gradient(160deg, var(--bg-0), var(--bg-1) 52%, #09121d 100%);
      font-family:var(--font-ui);
      color:var(--text-0);
    }
    body::before {
      content:"";
      position:fixed;
      inset:0;
      pointer-events:none;
      background:
        linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
      background-size:48px 48px;
      opacity:0.2;
    }
    #app-shell {
      position:relative;
      z-index:1;
      width:min(1420px, 100%);
      margin:0 auto;
      display:flex;
      flex-direction:column;
      gap:16px;
    }
    .panel {
      background:var(--panel-soft);
      border:1px solid var(--line);
      border-radius:var(--radius-lg);
      box-shadow:var(--shadow);
      backdrop-filter:blur(16px);
    }
    #top-bar {
      display:grid;
      grid-template-columns:minmax(0, 1.4fr) minmax(320px, 1fr);
      gap:16px;
      padding:22px 24px;
    }
    #hero-copy {
      display:flex;
      flex-direction:column;
      gap:10px;
    }
    #eyebrow {
      font-size:11px;
      text-transform:uppercase;
      letter-spacing:0.22em;
      color:var(--accent);
      font-weight:700;
    }
    #title-row {
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:16px;
      flex-wrap:wrap;
    }
    #title {
      font-size:28px;
      line-height:1.05;
      font-weight:700;
      letter-spacing:0.01em;
      color:var(--text-0);
    }
    #mode-badge {
      font-size:11px;
      padding:7px 12px;
      border-radius:var(--radius-sm);
      font-weight:700;
      letter-spacing:0.14em;
      text-transform:uppercase;
      border:1px solid transparent;
      white-space:nowrap;
    }
    .badge-live { background:rgba(87,213,154,0.14); color:var(--success); border-color:rgba(87,213,154,0.4); }
    .badge-demo { background:rgba(255,184,92,0.14); color:var(--warning); border-color:rgba(255,184,92,0.4); }
    .badge-conn { background:rgba(255,107,107,0.14); color:var(--danger); border-color:rgba(255,107,107,0.4); }
    #subtitle {
      max-width:760px;
      font-size:14px;
      line-height:1.5;
      color:var(--text-1);
    }
    #status-grid {
      display:grid;
      grid-template-columns:repeat(3, minmax(0, 1fr));
      gap:12px;
      align-content:start;
    }
    .status-card {
      padding:14px 16px;
      border-radius:var(--radius-md);
      background:linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
      border:1px solid rgba(255,255,255,0.05);
      min-height:92px;
    }
    .status-label {
      font-size:10px;
      text-transform:uppercase;
      letter-spacing:0.18em;
      color:var(--text-2);
      margin-bottom:10px;
      display:block;
    }
    .status-value {
      font-family:var(--font-mono);
      font-size:15px;
      line-height:1.35;
      color:var(--text-0);
      word-break:break-word;
    }
    .status-value.muted { color:var(--text-1); }
    #main-layout {
      display:grid;
      grid-template-columns:minmax(0, 1.6fr) minmax(300px, 0.72fr);
      gap:16px;
      align-items:start;
    }
    #scene-panel {
      padding:18px;
      display:flex;
      flex-direction:column;
      gap:14px;
    }
    .section-head {
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:12px;
      flex-wrap:wrap;
    }
    .section-kicker {
      font-size:10px;
      text-transform:uppercase;
      letter-spacing:0.18em;
      color:var(--text-2);
      margin-bottom:6px;
      display:block;
    }
    .section-title {
      font-size:20px;
      font-weight:650;
      color:var(--text-0);
    }
    .section-note {
      font-size:12px;
      color:var(--text-2);
      max-width:440px;
      line-height:1.45;
    }
    #status-inline {
      display:flex;
      align-items:center;
      gap:8px;
      flex-wrap:wrap;
    }
    .inline-pill {
      padding:6px 10px;
      border-radius:var(--radius-sm);
      border:1px solid var(--line-strong);
      background:var(--accent-soft);
      color:var(--accent);
      font-size:11px;
      font-weight:700;
      letter-spacing:0.08em;
      text-transform:uppercase;
      font-family:var(--font-mono);
    }
    #canvas-wrap {
      position:relative;
      border-radius:20px;
      padding:14px;
      background:linear-gradient(180deg, rgba(255,255,255,0.02), rgba(6,12,20,0.55));
      border:1px solid rgba(255,255,255,0.05);
      overflow:hidden;
    }
    #canvas-wrap::before {
      content:"";
      position:absolute;
      inset:0;
      background:linear-gradient(180deg, rgba(121,194,255,0.08), transparent 32%);
      pointer-events:none;
    }
    canvas {
      position:relative;
      width:100%;
      height:auto;
      display:block;
      border:1px solid rgba(121,194,255,0.12);
      border-radius:16px;
      background:#0b1626;
      image-rendering:pixelated;
    }
    #task-panel {
      padding:18px;
      min-height:100%;
      display:flex;
      flex-direction:column;
      gap:14px;
    }
    #task-panel-title {
      font-size:12px;
      text-transform:uppercase;
      letter-spacing:0.16em;
      color:var(--text-2);
      font-weight:700;
    }
    #task-summary {
      display:flex;
      align-items:center;
      gap:8px;
      flex-wrap:wrap;
      color:var(--text-1);
      font-size:12px;
    }
    .task-stat {
      padding:5px 9px;
      border-radius:var(--radius-sm);
      border:1px solid rgba(255,255,255,0.06);
      background:rgba(255,255,255,0.03);
      font-family:var(--font-mono);
      color:var(--text-1);
    }
    #task-list {
      display:flex;
      flex-direction:column;
      gap:10px;
    }
    .task-empty {
      padding:18px;
      border:1px dashed rgba(255,255,255,0.1);
      border-radius:var(--radius-md);
      color:var(--text-2);
      font-size:13px;
      line-height:1.45;
    }
    .task-item {
      display:flex;
      align-items:flex-start;
      gap:12px;
      padding:14px;
      border-radius:var(--radius-md);
      border:1px solid rgba(255,255,255,0.06);
      background:rgba(255,255,255,0.025);
      transition:border-color 0.25s ease, transform 0.25s ease, background 0.25s ease;
    }
    .task-item.pending { color:var(--text-1); }
    .task-item.in_progress {
      background:linear-gradient(180deg, rgba(255,184,92,0.1), rgba(255,184,92,0.03));
      border-color:rgba(255,184,92,0.34);
      transform:translateY(-1px);
    }
    .task-item.completed {
      background:linear-gradient(180deg, rgba(87,213,154,0.08), rgba(87,213,154,0.025));
      border-color:rgba(87,213,154,0.28);
    }
    .task-item.cancelled {
      color:var(--text-2);
      border-color:rgba(255,107,107,0.16);
      opacity:0.76;
    }
    .task-dot {
      width:10px;
      height:10px;
      border-radius:50%;
      flex-shrink:0;
      margin-top:6px;
      box-shadow:0 0 0 6px transparent;
    }
    .dot-pending { background:#5f7aa0; }
    .dot-in_progress { background:var(--warning); box-shadow:0 0 0 6px rgba(255,184,92,0.12); }
    .dot-completed { background:var(--success); box-shadow:0 0 0 6px rgba(87,213,154,0.12); }
    .dot-cancelled { background:#7f97ba; }
    .task-copy { flex:1; min-width:0; display:flex; flex-direction:column; gap:8px; }
    .task-topline {
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:10px;
      flex-wrap:wrap;
    }
    .task-title {
      font-size:13px;
      line-height:1.45;
      color:var(--text-0);
      word-break:break-word;
    }
    .task-item.cancelled .task-title { text-decoration:line-through; }
    .task-meta {
      display:flex;
      align-items:center;
      gap:8px;
      flex-wrap:wrap;
      font-size:11px;
      color:var(--text-2);
      text-transform:uppercase;
      letter-spacing:0.08em;
    }
    .task-agent-badge,
    .task-status-badge {
      padding:4px 8px;
      border-radius:var(--radius-sm);
      border:1px solid rgba(255,255,255,0.08);
      font-size:10px;
      line-height:1;
      font-family:var(--font-mono);
      white-space:nowrap;
    }
    .task-agent-badge { color:var(--accent); background:rgba(121,194,255,0.1); }
    .task-status-badge.pending { color:var(--text-1); }
    .task-status-badge.in_progress { color:var(--warning); border-color:rgba(255,184,92,0.28); background:rgba(255,184,92,0.12); }
    .task-status-badge.completed { color:var(--success); border-color:rgba(87,213,154,0.28); background:rgba(87,213,154,0.1); }
    .task-status-badge.cancelled { color:var(--danger); border-color:rgba(255,107,107,0.25); background:rgba(255,107,107,0.1); }
    #task-bar,
    #bottom-bar,
    #log-panel {
      padding:18px 20px;
    }
    #task-bar {
      display:grid;
      grid-template-columns:auto minmax(0, 1fr);
      align-items:start;
      gap:14px;
    }
    #task-label,
    #history-label,
    #log-panel-title {
      font-size:11px;
      text-transform:uppercase;
      letter-spacing:0.18em;
      color:var(--text-2);
      font-weight:700;
    }
    #task-text {
      color:var(--text-0);
      font-size:15px;
      line-height:1.55;
      min-height:24px;
    }
    #bottom-bar {
      display:grid;
      grid-template-columns:auto minmax(0, 1fr);
      gap:16px;
      align-items:start;
    }
    #legend { display:flex; gap:12px; flex-wrap:wrap; font-size:11px; color:var(--text-1); }
    .legend-item {
      display:flex;
      align-items:center;
      gap:6px;
      padding:5px 10px;
      border-radius:var(--radius-sm);
      background:rgba(255,255,255,0.03);
      border:1px solid rgba(255,255,255,0.05);
    }
    .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    #history-meta {
      display:flex;
      flex-direction:column;
      gap:10px;
      min-width:0;
    }
    #history-bar { display:flex; gap:8px; flex-wrap:wrap; }
    .hist-chip {
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:7px 10px;
      border-radius:var(--radius-sm);
      background:rgba(255,255,255,0.03);
      border:1px solid rgba(255,255,255,0.08);
      font-size:11px;
      color:var(--text-1);
      font-family:var(--font-mono);
      text-transform:uppercase;
      letter-spacing:0.08em;
    }
    .hist-chip::before {
      content:"";
      width:7px;
      height:7px;
      border-radius:50%;
      background:currentColor;
      opacity:0.92;
    }
    #log-panel {
      max-height:220px;
      overflow-y:auto;
      display:flex;
      flex-direction:column;
      gap:8px;
    }
    .log-entry {
      display:grid;
      grid-template-columns:76px 96px minmax(0, 1fr);
      gap:10px;
      font-size:12px;
      padding:9px 0;
      border-top:1px solid rgba(255,255,255,0.045);
      flex-shrink:0;
    }
    .log-entry:first-of-type { border-top:none; }
    .log-ts { color:var(--text-2); font-size:11px; font-family:var(--font-mono); }
    .log-stage {
      color:var(--accent);
      font-size:11px;
      text-transform:uppercase;
      letter-spacing:0.12em;
      font-family:var(--font-mono);
    }
    .log-msg { color:var(--text-1); line-height:1.45; }
    .log-s-working { color:var(--warning); }
    .log-s-done { color:var(--success); }
    .log-s-error { color:var(--danger); }
    .log-s-thinking { color:var(--violet); }
    .log-s-delegating { color:var(--accent); }
    .log-s-waiting { color:#ffcf94; }
    @media (max-width: 1180px) {
      #top-bar,
      #main-layout {
        grid-template-columns:1fr;
      }
      #task-panel {
        min-height:0;
      }
    }
    @media (max-width: 860px) {
      body { padding:18px; }
      #top-bar,
      #scene-panel,
      #task-panel,
      #task-bar,
      #bottom-bar,
      #log-panel { padding:16px; }
      #status-grid {
        grid-template-columns:1fr;
      }
      #task-bar,
      #bottom-bar,
      .log-entry {
        grid-template-columns:1fr;
      }
      #title { font-size:23px; }
      .section-title { font-size:18px; }
    }
  </style>
</head>
<body>
  <div id="app-shell">
    <div id="top-bar" class="panel">
      <div id="hero-copy">
        <span id="eyebrow">Operational View</span>
        <div id="title-row">
          <div id="title">SDD Workflow Control Surface</div>
          <div id="mode-badge" class="badge-conn">CONNECTING...</div>
        </div>
        <div id="subtitle">Track orchestrator decisions, active stage ownership, task flow, and event telemetry in one live single-file dashboard.</div>
      </div>
      <div id="status-grid">
        <div class="status-card">
          <span class="status-label">Orchestrator</span>
          <div id="status-orchestrator" class="status-value">Idle</div>
        </div>
        <div class="status-card">
          <span class="status-label">Active Stage</span>
          <div id="status-stage" class="status-value muted">No active stage</div>
        </div>
        <div class="status-card">
          <span class="status-label">Current Task</span>
          <div id="status-task" class="status-value muted">Waiting for orchestrator...</div>
        </div>
      </div>
    </div>

    <div id="main-layout">
      <div id="scene-panel" class="panel">
        <div class="section-head">
          <div>
            <span class="section-kicker">Live Pipeline</span>
            <div class="section-title">Animated Stage Activity</div>
          </div>
          <div id="status-inline">
            <span id="scene-connection" class="inline-pill">CONNECTING</span>
            <span id="scene-caption" class="section-note">Live SSE pipeline standby.</span>
          </div>
        </div>
        <div id="canvas-wrap">
          <canvas id="canvas" width="1090" height="460"></canvas>
        </div>
      </div>
      <div id="task-panel" class="panel">
        <div id="task-panel-title">Task Queue</div>
        <div id="task-summary">
          <span class="task-stat" id="task-count">0 tasks</span>
          <span class="task-stat" id="task-progress">0 active</span>
        </div>
        <div id="task-list"><div class="task-empty">No tasks yet. Live `/state` updates will populate the queue here.</div></div>
      </div>
    </div>

    <div id="task-bar" class="panel">
    <span id="task-label">task:</span>
    <span id="task-text">waiting for orchestrator...</span>
    </div>

    <div id="bottom-bar" class="panel">
      <div id="legend">
        <div class="legend-item"><span class="dot" style="background:#5f7aa0"></span>idle</div>
        <div class="legend-item"><span class="dot" style="background:#ffb85c"></span>working</div>
        <div class="legend-item"><span class="dot" style="background:#ffcf94"></span>waiting</div>
        <div class="legend-item"><span class="dot" style="background:#57d59a"></span>done</div>
        <div class="legend-item"><span class="dot" style="background:#ff6b6b"></span>error</div>
      </div>
      <div id="history-meta">
        <span id="history-label">Stage History</span>
        <div id="history-bar"></div>
      </div>
    </div>

    <div id="log-panel" class="panel">
      <div id="log-panel-title">Activity Feed</div>
    </div>
  </div>

<script>
// ─────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────
const PORT       = __PORT__;
const SCALE      = 3;
const PX         = SCALE;
const SPR        = 16;
const SPR_W      = SPR * PX;   // 48px
const SPR_H      = SPR * PX;

const WALK_FRAMES  = 50;
const ANIM_PERIOD  = 18;

// Paper (task card) pixel size
const PAPER_W = 10;   // px on canvas (not scaled)
const PAPER_H = 13;

const STAGE_LABELS = ["explore","propose","spec","design","tasks","apply","verify","archive"];
const N_STAGES     = STAGE_LABELS.length;

const STAGE_COLORS = [
  "rgba(75,130,116,0.18)","rgba(111,91,164,0.18)","rgba(160,121,76,0.18)","rgba(60,140,154,0.18)",
  "rgba(176,117,78,0.18)","rgba(101,145,98,0.18)","rgba(171,90,113,0.18)","rgba(171,136,72,0.18)",
];
const STAGE_META = [
  "Discover scope",
  "Shape direction",
  "Lock requirements",
  "Define approach",
  "Sequence work",
  "Ship changes",
  "Validate behavior",
  "Close out",
];

// ─────────────────────────────────────────────
// Sprite builder
// ─────────────────────────────────────────────
function makeSprite(H, B, L, A, E, frame) {
  const _ = null;
  return [
    [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
    [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
    [_,_,_,_,_,H,E,H,H,E,H,_,_,_,_,_],
    [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
    [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
    [_,_,_,_,A,B,B,B,B,B,B,A,_,_,_,_],
    [_,_,_,A,A,B,B,B,B,B,B,A,A,_,_,_],
    [_,_,_,A,_,B,B,B,B,B,B,_,A,_,_,_],
    [_,_,_,_,_,B,B,B,B,B,B,_,_,_,_,_],
    [_,_,_,_,_,B,B,B,B,B,B,_,_,_,_,_],
    [_,_,_,_,_,B,B,B,B,B,B,_,_,_,_,_],
    frame===0?[_,_,_,_,_,L,L,_,L,L,_,_,_,_,_,_]:[_,_,_,_,L,L,_,_,_,L,L,_,_,_,_,_],
    frame===0?[_,_,_,_,_,L,L,_,L,L,_,_,_,_,_,_]:[_,_,_,L,L,_,_,_,_,_,L,L,_,_,_,_],
    frame===0?[_,_,_,_,_,L,L,_,L,L,_,_,_,_,_,_]:[_,_,_,L,_,_,_,_,_,_,_,L,_,_,_,_],
    [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
    [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  ];
}

const ORC_FRAMES   = [0,1].map(f=>makeSprite("#ffd700","#d4882a","#c8701a","#e8a030","#ffffff",f));
// Per-agent idle/working skins — indexed by STAGE_LABELS order
// [explore, propose, spec, design, tasks, apply, verify]
const AGENT_SKINS = [
  [0,1].map(f=>makeSprite("#66bb44","#336622","#224411","#448833","#ccffaa",f)), // explore — scout green
  [0,1].map(f=>makeSprite("#cc55ff","#7722aa","#551188","#9933cc","#ffccff",f)), // propose — designer purple
  [0,1].map(f=>makeSprite("#cc9955","#8b5c2a","#6b3c0a","#aa7733","#fff0cc",f)), // spec    — scribe beige
  [0,1].map(f=>makeSprite("#22ddcc","#116688","#0a4455","#1199aa","#aaffff",f)), // design  — architect cyan
  [0,1].map(f=>makeSprite("#ff8800","#cc5500","#993300","#ee6600","#ffddb0",f)), // tasks   — manager orange
  [0,1].map(f=>makeSprite("#33ff55","#117722","#004411","#22aa33","#aaffcc",f)), // apply   — builder lime
  [0,1].map(f=>makeSprite("#ff3366","#aa1133","#770011","#cc2244","#ffaacc",f)), // verify  — inspector pink
  [0,1].map(f=>makeSprite("#ddaa22","#996600","#664400","#bb8800","#ffeeaa",f)), // archive — archivist gold
];
const DONE_FRAMES  = [0,0].map(f=>makeSprite("#44ff88","#22884a","#116630","#33bb66","#ccffee",f));
const WAIT_FRAMES  = [0,1].map(f=>makeSprite("#ff8c42","#cc5511","#aa3300","#ee7733","#ffddcc",f));
const ERROR_FRAMES = [0,1].map(f=>makeSprite("#ff4444","#aa1111","#880000","#cc2222","#ffaaaa",f));
const THINK_FRAMES  = [0,1].map(f=>makeSprite("#cc88ff","#7744aa","#552288","#9966cc","#ffffff",f));
const REVIEW_FRAMES = [0,1].map(f=>makeSprite("#88ffcc","#2a9966","#1a6644","#44bb88","#ccffee",f));

// ── Thought bubble icons per agent state ──────
// Unicode symbols safe in Courier New on all platforms (no emoji)
const BUBBLE_ICONS = {
  idle:     null,       // no bubble when fully idle
  thinking: "⚙",       // gear = processing
  working:  null,       // spinner drawn separately via drawLoadingSpinner
  waiting:  "…",       // ellipsis = waiting
  done:     "✓",       // checkmark
  error:    "✗",       // cross
};

function drawSprite(ctx, frames, frameIndex, x, y) {
  const frame = frames[frameIndex % frames.length];
  for (let row=0; row<16; row++) {
    for (let col=0; col<16; col++) {
      const c = frame[row][col];
      if (c !== null) {
        ctx.fillStyle = c;
        ctx.fillRect(Math.round(x+col*PX), Math.round(y+row*PX), PX, PX);
      }
    }
  }
}

// ─────────────────────────────────────────────
// Paper (task card) drawing
// ─────────────────────────────────────────────
function drawPaper(ctx, x, y, label, glowing) {
  // White card
  ctx.fillStyle = glowing ? "#ffffee" : "#ffffff";
  ctx.fillRect(x, y, PAPER_W, PAPER_H);
  // Thin border
  ctx.strokeStyle = glowing ? "#ffd700" : "#aaaaaa";
  ctx.lineWidth = 1;
  ctx.strokeRect(x, y, PAPER_W, PAPER_H);
  // Lines on paper (simulating text)
  ctx.fillStyle = "#aaaacc";
  ctx.fillRect(x+2, y+3, 6, 1);
  ctx.fillRect(x+2, y+6, 5, 1);
  ctx.fillRect(x+2, y+9, 4, 1);
  // Glow
  if (glowing) {
    ctx.shadowColor = "#ffd700";
    ctx.shadowBlur  = 8;
    ctx.strokeStyle = "#ffd700";
    ctx.lineWidth   = 1;
    ctx.strokeRect(x, y, PAPER_W, PAPER_H);
    ctx.shadowBlur  = 0;
  }
  // Label (tiny, below paper)
  if (label) {
    ctx.fillStyle   = "#ffd700";
    ctx.font        = "8px 'Courier New',monospace";
    ctx.textAlign   = "center";
    ctx.fillText(truncate(label, 10), x + PAPER_W/2, y + PAPER_H + 9);
  }
}

function truncate(s, n) {
  return s && s.length > n ? s.slice(0, n-1)+"…" : (s||"");
}

function titleCase(value) {
  return String(value || "idle")
    .replace(/_/g, " ")
    .split(" ")
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function updateStatusSummary(sv, isDemo, connectionState) {
  const orchestrator = document.getElementById("status-orchestrator");
  const stage = document.getElementById("status-stage");
  const task = document.getElementById("status-task");
  const connection = document.getElementById("scene-connection");
  const caption = document.getElementById("scene-caption");
  const activeStage = sv.current_agent ? titleCase(sv.current_agent) : "No active stage";
  const agentState = sv.current_agent ? ` (${titleCase(sv.agent_state || "idle")})` : "";

  orchestrator.textContent = titleCase(sv.orchestrator || (isDemo ? "demo" : "idle"));
  stage.textContent = activeStage + agentState;
  task.textContent = sv.current_task || (isDemo ? "Running demo sequence..." : "Waiting for orchestrator...");
  connection.textContent = connectionState.toUpperCase();

  if (connectionState === "live") {
    connection.style.color = "var(--success)";
    connection.style.borderColor = "rgba(87,213,154,0.34)";
    connection.style.background = "rgba(87,213,154,0.12)";
    caption.textContent = sv.current_agent
      ? `${titleCase(sv.current_agent)} is ${titleCase(sv.agent_state || "idle").toLowerCase()} on the live pipeline.`
      : "Awaiting the next live orchestration update.";
  } else if (connectionState === "demo") {
    connection.style.color = "var(--warning)";
    connection.style.borderColor = "rgba(255,184,92,0.34)";
    connection.style.background = "rgba(255,184,92,0.12)";
    caption.textContent = "Demo mode is simulating the full SDD workflow loop.";
  } else if (connectionState === "disconnected") {
    connection.style.color = "var(--danger)";
    connection.style.borderColor = "rgba(255,107,107,0.34)";
    connection.style.background = "rgba(255,107,107,0.12)";
    caption.textContent = "Connection lost. Retrying the SSE stream automatically.";
  } else {
    connection.style.color = "var(--accent)";
    connection.style.borderColor = "var(--line-strong)";
    connection.style.background = "var(--accent-soft)";
    caption.textContent = "Establishing the live SSE stream...";
  }
}

// ─────────────────────────────────────────────
// Thought bubble — floats above character head
// cx: center X of sprite, topY: Y of sprite top
// icon: string from BUBBLE_ICONS, tick: animation tick
// ─────────────────────────────────────────────
function drawThoughtBubble(ctx, cx, topY, icon, tick, color) {
  // Floating bob: ±3px slow sine
  const bob = Math.sin(tick * 0.07) * 3;
  const bw  = 26, bh = 16;
  const bx  = cx - bw / 2;
  const by  = topY - 30 + bob;

  // Bubble fill
  ctx.fillStyle = "rgba(255,255,255,0.88)";
  ctx.beginPath();
  ctx.roundRect(bx, by, bw, bh, 4);
  ctx.fill();

  // Bubble border (tinted by state color)
  ctx.strokeStyle = color || "rgba(160,160,220,0.9)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(bx, by, bw, bh, 4);
  ctx.stroke();

  // Tail: 3 diminishing dots below bubble, centered
  const tailCx = cx;
  const tailStartY = by + bh + 3;
  const tailSizes  = [2.0, 1.5, 1.0];
  for (let d = 0; d < 3; d++) {
    ctx.fillStyle = "rgba(255,255,255,0.88)";
    ctx.beginPath();
    ctx.arc(tailCx, tailStartY + d * 4, tailSizes[d], 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = color || "rgba(160,160,220,0.7)";
    ctx.lineWidth = 0.5;
    ctx.stroke();
  }

  // Icon centered in bubble
  ctx.font         = "11px 'Courier New',monospace";
  ctx.textAlign    = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle    = color || "#333366";
  ctx.fillText(icon, cx, by + bh / 2);
  ctx.textBaseline = "alphabetic";
}

// ─────────────────────────────────────────────
// Loading spinner — shown above agent head when state==="working"
// cx: center X, topY: sprite top Y, tick: animation tick, color: ring color
// ─────────────────────────────────────────────
function drawLoadingSpinner(ctx, cx, topY, tick, color) {
  // Same floating bob as thought bubble so it feels consistent
  const bob = Math.sin(tick * 0.07) * 3;
  const cy  = topY - 22 + bob;   // center of spinner, above head
  const r   = 7;                  // spinner radius (px)

  // Track ring (faint background circle)
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(255,255,255,0.15)";
  ctx.lineWidth   = 2.5;
  ctx.stroke();

  // Spinning arc — 270° arc, rotates at ~1.5 rev/s at 60fps
  const angle  = (tick * 0.12) % (Math.PI * 2);   // full rotation period ~52 ticks ≈ 0.87s
  const arcLen = Math.PI * 1.5;                    // 270° arc
  ctx.beginPath();
  ctx.arc(cx, cy, r, angle, angle + arcLen);
  ctx.strokeStyle = color || "#ffd700";
  ctx.lineWidth   = 2.5;
  ctx.lineCap     = "round";
  ctx.stroke();
  ctx.lineCap     = "butt";

  // Bright leading dot at arc tip
  const tipX = cx + Math.cos(angle + arcLen) * r;
  const tipY = cy + Math.sin(angle + arcLen) * r;
  ctx.beginPath();
  ctx.arc(tipX, tipY, 1.5, 0, Math.PI * 2);
  ctx.fillStyle = color || "#ffd700";
  ctx.fill();
}

// ─────────────────────────────────────────────
// Layout  (canvas now 1090×460)
// ─────────────────────────────────────────────
const CANVAS_W   = 1090;
const CANVAS_H   = 460;
const HUB_X      = CANVAS_W / 2 - SPR_W / 2;
const HUB_Y      = 60;
const STAGE_Y    = 270;
const ZONE_H     = 160;
const MARGIN     = 30;
const STAGE_SPAN = (CANVAS_W - MARGIN * 2) / N_STAGES;

function stageX(i) {
  return MARGIN + i * STAGE_SPAN + STAGE_SPAN/2 - SPR_W/2;
}
function stageCX(i) { return stageX(i) + SPR_W/2; }   // center X

function lerp(a, b, t) { return a + (b-a) * Math.min(Math.max(t,0),1); }

// ─────────────────────────────────────────────
// State
// ─────────────────────────────────────────────
function createState() {
  return {
    tick: 0,
    loop: 0,

    orc: { x: HUB_X, y: HUB_Y },
    agents: STAGE_LABELS.map((_,i) => ({
      state: "idle",
      x: stageX(i), y: STAGE_Y,
      taskLabel: null,    // task title this agent is working on
      hasPaper: false,    // currently holding the paper
    })),

    // Walk animation
    phase: "idle",          // idle | walking_to | at_stage | delivering | walking_back
    currentStage: -1,
    walkSrc:  { x: HUB_X, y: HUB_Y },
    walkDst:  { x: HUB_X, y: HUB_Y },
    walkT: 0,

    // Paper state
    orcHasPaper: false,       // orchestrator is carrying paper
    paperLabel:  null,        // task title on the paper
    paperDelivering: false,   // mid-delivery animation
    paperDeliverT: 0,         // 0→1 deliver anim progress
    paperSrc: {x:0,y:0},
    paperDst: {x:0,y:0},

    // UI
    statusText: "Waiting for orchestrator...",
    orchestratorState: "idle",

    // Task list (from server)
    tasks: [],

    // Reset scheduling
    _resetHandle: null,          // setTimeout handle — null means no reset pending

    // Demo
    demoPhase: "pause_start",
    demoTimer: 40,
    demoCurrentStage: 0,
    demoTasks: STAGE_LABELS.map((l,i) => ({
      id: `t${i+1}`,
      title: `Task: ${l}`,
      status: "pending",
      agent: null,
    })),
  };
}

// ─────────────────────────────────────────────
// applyServerState
// ─────────────────────────────────────────────
function applyServerState(st, sv) {
  st.orchestratorState = sv.orchestrator || "idle";
  st.statusText        = sv.current_task  || "Orchestrator idle";

  const agent  = sv.current_agent || null;
  const astate = sv.agent_state   || "idle";

  // Sync task list
  if (sv.tasks && sv.tasks.length > 0) {
    st.tasks = sv.tasks;
    renderTaskPanel(sv.tasks);
  }

  if (agent !== null) {
    const idx = STAGE_LABELS.indexOf(agent);
    if (idx !== -1) {

      // Gap 5: explicit idle signal — clear the agent visual immediately
      if (astate === "idle") {
        st.agents[idx].state     = "idle";
        st.agents[idx].hasPaper  = false;
        st.agents[idx].taskLabel = null;
      }

      if (astate === "working" || astate === "waiting") {
        // Find active task title for this agent
        const activeTask = (st.tasks || []).find(t =>
          (t.agent === agent || t.status === "in_progress") && t.title
        );
        const taskTitle = activeTask ? activeTask.title : (sv.current_task || null);

        // If not already walking/at this stage → trigger walk with paper
        if (st.currentStage !== idx ||
            (st.phase === "idle" || st.phase === "walking_back")) {
          st.currentStage = idx;
          st.phase        = "walking_to";
          st.walkSrc      = { x: st.orc.x, y: st.orc.y };
          st.walkDst      = { x: stageX(idx), y: STAGE_Y - SPR_H - 6 };
          st.walkT        = 0;
          st.orcHasPaper  = true;
          st.paperLabel   = taskTitle;
          st.paperDelivering = false;
          st.agents[idx].state     = astate;
          st.agents[idx].taskLabel = taskTitle;
        }
      }

      if (astate === "done" || astate === "error") {
        st.agents[idx].state = astate;
        // Agent drops paper when done
        if (astate === "done") {
          st.agents[idx].hasPaper  = false;
          st.agents[idx].taskLabel = null;
        }
        if (st.currentStage === idx && st.phase === "at_stage") {
          st.phase       = "walking_back";
          st.walkSrc     = { x: st.orc.x, y: st.orc.y };
          st.walkDst     = { x: HUB_X, y: HUB_Y };
          st.walkT       = 0;
          st.orcHasPaper = false;
        }
      }
    }
  }

  if (sv.orchestrator === "idle" && st.phase === "at_stage") {
    st.phase       = "walking_back";
    st.walkSrc     = { x: st.orc.x, y: st.orc.y };
    st.walkDst     = { x: HUB_X, y: HUB_Y };
    st.walkT       = 0;
    st.orcHasPaper = false;
  }

  // ── Full reset when orchestrator goes idle ────
  // Schedule agents back to idle after a short display window
  if (sv.orchestrator === "idle" && agent === null) {
    if (st._resetHandle === null) {
      st._resetHandle = setTimeout(() => {
        st.agents.forEach(a => {
          a.state     = "idle";
          a.hasPaper  = false;
          a.taskLabel = null;
        });
        st.tasks        = [];
        st._resetHandle = null;
        renderTaskPanel([]);
      }, 3000);   // 3 s display window so user can read done states
    }
  } else {
    // Cancel pending reset if orchestrator becomes active again
    if (st._resetHandle !== null) {
      clearTimeout(st._resetHandle);
      st._resetHandle = null;
    }
  }

  if (sv.history && sv.history.length > 0) {
    updateHistoryBar(sv.history);
  }
}

// ─────────────────────────────────────────────
// updateLive — interpolation only
// ─────────────────────────────────────────────
function updateLive(st) {
  st.tick++;

  switch (st.phase) {
    case "walking_to":
      st.walkT += 1 / WALK_FRAMES;
      st.orc.x = lerp(st.walkSrc.x, st.walkDst.x, st.walkT);
      st.orc.y = lerp(st.walkSrc.y, st.walkDst.y, st.walkT);
      if (st.walkT >= 1) {
        st.phase = "delivering";
        st.paperDeliverT = 0;
        const idx = st.currentStage;
        // Paper flies from orc hand → agent
        st.paperSrc = {
          x: Math.round(st.orc.x) + SPR_W + 2,
          y: Math.round(st.orc.y) + SPR_H * 0.4,
        };
        st.paperDst = {
          x: st.agents[idx].x + SPR_W + 4,
          y: st.agents[idx].y + SPR_H * 0.3,
        };
      }
      break;

    case "delivering":
      // Short delivery arc animation
      st.paperDeliverT += 1 / 20;  // 20 frames for delivery
      if (st.paperDeliverT >= 1) {
        st.phase = "at_stage";
        st.orcHasPaper = false;
        const idx = st.currentStage;
        st.agents[idx].hasPaper = true;
        // Gap 2 fix: agent was in 'waiting' while orc was walking over;
        // now that paper is delivered, force it to 'working' if it wasn't already done/error
        if (st.agents[idx].state === "waiting") {
          st.agents[idx].state = "working";
        }
      }
      break;

    case "walking_back":
      st.walkT += 1 / WALK_FRAMES;
      st.orc.x = lerp(st.walkSrc.x, st.walkDst.x, st.walkT);
      st.orc.y = lerp(st.walkSrc.y, st.walkDst.y, st.walkT);
      if (st.walkT >= 1) {
        st.orc.x = HUB_X; st.orc.y = HUB_Y;
        st.phase = "idle";
        st.currentStage = -1;
      }
      break;
  }
}

// ─────────────────────────────────────────────
// updateDemo — full auto-loop
// ─────────────────────────────────────────────
function updateDemo(st) {
  st.tick++;
  const WORK_FRAMES  = 90;
  const PAUSE_FRAMES = 35;
  const i = st.demoCurrentStage;

  switch (st.demoPhase) {
    case "pause_start":
      st.demoTimer--;
      st.statusText = "[ DEMO ] Standing by...";
      if (st.demoTimer <= 0) {
        // Mark task in_progress in demo tasks list
        if (st.demoTasks[i]) st.demoTasks[i].status = "in_progress";
        st.tasks = [...st.demoTasks];
        renderTaskPanel(st.tasks);

        st.demoPhase    = "walking_to";
        st.phase        = "walking_to";
        st.currentStage = i;
        st.walkSrc      = { x: st.orc.x, y: st.orc.y };
        st.walkDst      = { x: stageX(i), y: STAGE_Y - SPR_H - 6 };
        st.walkT        = 0;
        st.orcHasPaper  = true;
        st.paperLabel   = st.demoTasks[i] ? st.demoTasks[i].title : STAGE_LABELS[i];
        st.agents[i].state     = "waiting";
        st.agents[i].taskLabel = st.paperLabel;
        st.statusText = `[ DEMO ] → Assigning [${STAGE_LABELS[i]}]`;
        appendLog("orchestrator", "delegating \u2192 [" + STAGE_LABELS[i] + "]", "delegating");
      }
      break;

    case "walking_to":
      st.walkT += 1 / WALK_FRAMES;
      st.orc.x = lerp(st.walkSrc.x, st.walkDst.x, st.walkT);
      st.orc.y = lerp(st.walkSrc.y, st.walkDst.y, st.walkT);
      if (st.walkT >= 1) {
        st.demoPhase     = "delivering";
        st.phase         = "delivering";
        st.paperDeliverT = 0;
        st.paperSrc = {
          x: Math.round(st.orc.x) + SPR_W + 2,
          y: Math.round(st.orc.y) + SPR_H * 0.4,
        };
        st.paperDst = {
          x: st.agents[i].x + SPR_W + 4,
          y: st.agents[i].y + SPR_H * 0.3,
        };
      }
      break;

    case "delivering":
      st.paperDeliverT += 1 / 20;
      if (st.paperDeliverT >= 1) {
        st.demoPhase           = "working";
        st.phase               = "at_stage";
        st.demoTimer           = WORK_FRAMES;
        st.orcHasPaper         = false;
        st.agents[i].state     = "working";
        st.agents[i].hasPaper  = true;
        st.statusText = `[ DEMO ] [${STAGE_LABELS[i]}] working...`;
        appendLog(STAGE_LABELS[i], "working...", "working");
      }
      break;

    case "working":
      st.demoTimer--;
      if (st.demoTimer <= 0) {
        st.agents[i].state     = "done";
        st.agents[i].hasPaper  = false;
        st.agents[i].taskLabel = null;
        if (st.demoTasks[i]) st.demoTasks[i].status = "completed";
        st.tasks = [...st.demoTasks];
        renderTaskPanel(st.tasks);

        st.demoPhase   = "walking_back";
        st.phase       = "walking_back";
        st.walkSrc     = { x: st.orc.x, y: st.orc.y };
        st.walkDst     = { x: HUB_X, y: HUB_Y };
        st.walkT       = 0;
        st.orcHasPaper = false;
        st.statusText  = `[ DEMO ] [${STAGE_LABELS[i]}] done ✓`;
        appendLog(STAGE_LABELS[i], "done \u2713", "done");
      }
      break;

    case "walking_back":
      st.walkT += 1 / WALK_FRAMES;
      st.orc.x = lerp(st.walkSrc.x, st.walkDst.x, st.walkT);
      st.orc.y = lerp(st.walkSrc.y, st.walkDst.y, st.walkT);
      if (st.walkT >= 1) {
        st.orc.x = HUB_X; st.orc.y = HUB_Y;
        st.phase = "idle";
        st.demoPhase = "pause_end";
        st.demoTimer = PAUSE_FRAMES;
      }
      break;

    case "pause_end":
      st.demoTimer--;
      if (st.demoTimer <= 0) {
        st.demoCurrentStage++;
        if (st.demoCurrentStage >= N_STAGES) {
          st.demoPhase = "loop_reset";
          st.demoTimer = PAUSE_FRAMES * 3;
          st.statusText = "[ DEMO ] Complete! Restarting...";
        } else {
          st.demoPhase = "pause_start";
          st.demoTimer = PAUSE_FRAMES;
        }
      }
      break;

    case "loop_reset":
      st.demoTimer--;
      if (st.demoTimer <= 0) {
        st.loop++;
        st.demoCurrentStage = 0;
        st.demoPhase  = "pause_start";
        st.demoTimer  = PAUSE_FRAMES;
        st.agents.forEach(a => { a.state="idle"; a.hasPaper=false; a.taskLabel=null; });
        st.demoTasks  = STAGE_LABELS.map((l,i) => ({
          id:`t${i+1}`, title:`Task: ${l}`, status:"pending", agent:null
        }));
        st.tasks = [...st.demoTasks];
        renderTaskPanel(st.tasks);
        st.statusText = "[ DEMO ] Standing by...";
      }
      break;
  }
}

// ─────────────────────────────────────────────
// render()
// ─────────────────────────────────────────────
function render(ctx, st) {
  const tick      = st.tick;
  const animFrame = Math.floor(tick / ANIM_PERIOD) % 2;

  // Background
  const bg = ctx.createLinearGradient(0, 0, 0, CANVAS_H);
  bg.addColorStop(0, "#0c1828");
  bg.addColorStop(0.58, "#0a1422");
  bg.addColorStop(1, "#08111d");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
  ctx.fillStyle = "rgba(121,194,255,0.05)";
  ctx.fillRect(0, 0, CANVAS_W, 72);
  ctx.strokeStyle = "rgba(121,194,255,0.16)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, 72);
  ctx.lineTo(CANVAS_W, 72);
  ctx.stroke();

  ctx.fillStyle = "#dfeaff";
  ctx.font = "600 18px 'Avenir Next','Trebuchet MS',sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("Pipeline activity", 24, 30);
  ctx.fillStyle = "#7f97ba";
  ctx.font = "12px 'SFMono-Regular','Consolas',monospace";
  ctx.fillText("Live canvas scene fed by the current /state and SSE contract.", 24, 51);

  // ── Stage zones ──────────────────────────────
  for (let i=0; i<N_STAGES; i++) {
    const zx = MARGIN + i * STAGE_SPAN;
    const zy = STAGE_Y - 18;
    const zw = STAGE_SPAN - 4;
    const ag = st.agents[i];
    const isActive = ag.state === "working" || ag.state === "waiting";

    ctx.fillStyle = STAGE_COLORS[i];
    ctx.beginPath(); ctx.roundRect(zx, zy, zw, ZONE_H, 7); ctx.fill();

    ctx.strokeStyle = isActive ? "#ffb85c"
                    : ag.state==="done"  ? "#57d59a"
                    : ag.state==="error" ? "#ff6b6b"
                    : "rgba(121,194,255,0.16)";
    ctx.lineWidth = isActive ? 2.5 : 1;
    ctx.beginPath(); ctx.roundRect(zx, zy, zw, ZONE_H, 7); ctx.stroke();

    if (isActive) {
      ctx.strokeStyle = "rgba(255,184,92,0.2)";
      ctx.lineWidth = 7;
      ctx.beginPath(); ctx.roundRect(zx + 4, zy + 4, zw - 8, ZONE_H - 8, 9); ctx.stroke();
    }

    // Zone label
    ctx.fillStyle = ag.state==="done" ? "#57d59a"
                  : ag.state==="error" ? "#ff6b6b"
                  : isActive ? "#ffcf94" : "#8fb2dd";
    ctx.font = "bold 10px 'SFMono-Regular','Consolas',monospace";
    ctx.textAlign = "center";
    ctx.fillText(STAGE_LABELS[i].toUpperCase(), zx+zw/2, zy+ZONE_H-18);

    ctx.fillStyle = "#6f86a9";
    ctx.font = "11px 'Avenir Next','Trebuchet MS',sans-serif";
    ctx.fillText(STAGE_META[i], zx+zw/2, zy+ZONE_H-6);

    ctx.fillStyle = "#587497";
    ctx.font = "9px 'SFMono-Regular','Consolas',monospace";
    ctx.fillText(`[${i+1}]`, zx+zw/2, zy+18);
  }

  // ── Connector line ────────────────────────────
  if (st.phase==="walking_to"||st.phase==="at_stage"||
      st.phase==="walking_back"||st.phase==="delivering") {
    const i  = st.currentStage >= 0 ? st.currentStage : 0;
    const tx = stageX(i) + SPR_W/2;
    const ty = STAGE_Y + SPR_H/2;
    ctx.strokeStyle = "rgba(121,194,255,0.16)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4,4]);
    ctx.beginPath();
    ctx.moveTo(HUB_X+SPR_W/2, HUB_Y+SPR_H);
    ctx.lineTo(tx, ty);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // ── Pipeline arrows ───────────────────────────
  for (let i=0; i<N_STAGES-1; i++) {
    const ax = MARGIN + (i+1)*STAGE_SPAN - 4;
    const ay = STAGE_Y + SPR_H/2 + 4;
    ctx.fillStyle = st.agents[i].state==="done" ? "#57d59a" : "rgba(121,194,255,0.3)";
    ctx.font = "13px 'SFMono-Regular','Consolas',monospace";
    ctx.textAlign = "center";
    ctx.fillText("→", ax, ay);
  }

  // ── Sub-agents ────────────────────────────────
    for (let i=0; i<N_STAGES; i++) {
    const ag = st.agents[i];
    let frames = AGENT_SKINS[i];
    let frame  = 0;
    let yOff   = 0;

    if (ag.state==="idle") {
      frames = AGENT_SKINS[i]; frame = 0;
      // Soft breathing: ±1px slow sine, phase-offset per agent so they don't sync
      yOff = Math.sin(tick * 0.04 + i * 1.3) * 1;
    }
    else if (ag.state==="waiting") {
      frames = WAIT_FRAMES; frame = animFrame;
      ctx.globalAlpha = 0.6 + 0.4 * Math.sin(tick * 0.15);
      // no yOff — waiting is still
    }
    else if (ag.state==="working") {
      frames = AGENT_SKINS[i]; frame = animFrame;
      // Typing-style dip: always downward push 0→-3px
      yOff = Math.abs(Math.sin(tick * 0.3 + i)) * -3;
    }
    else if (ag.state==="done")  { frames = DONE_FRAMES;  frame = 0; }
    else if (ag.state==="error") { frames = ERROR_FRAMES; frame = animFrame; }

    drawSprite(ctx, frames, frame, ag.x, ag.y+yOff);
    ctx.globalAlpha = 1;

    // ── Thought bubble / spinner above agent head ─
    // Head color from the agent's own skin (first pixel of first frame, row 0 col 6)
    const skinHeadColor = AGENT_SKINS[i][0][0][6];  // frame0, row0, col6
    if (ag.state === "working") {
      // Loading spinner — replaces thought bubble while agent is processing
      drawLoadingSpinner(ctx, ag.x + SPR_W/2, ag.y + yOff, tick + i*17, skinHeadColor);
    } else {
      const bubIcon  = BUBBLE_ICONS[ag.state];
      const bubColor = (ag.state==="idle")
        ? skinHeadColor
        : { waiting:"#ff8c42", done:"#44ff88", error:"#ff4444" }[ag.state]
          || "rgba(160,160,220,0.9)";
      if (bubIcon) {
        drawThoughtBubble(ctx, ag.x + SPR_W/2, ag.y + yOff, bubIcon, tick + i*17, bubColor);
      }
    }

    // State badge
    const bc = {idle:"#7f97ba",waiting:"#ffcf94",working:"#ffb85c",done:"#57d59a",error:"#ff6b6b"}[ag.state]||"#7f97ba";
    ctx.fillStyle = bc;
    ctx.font = "8px 'SFMono-Regular','Consolas',monospace";
    ctx.textAlign = "center";
    ctx.fillText(ag.state, ag.x+SPR_W/2, ag.y-5);

    // Paper held by agent + task label
    if (ag.hasPaper) {
      const px = ag.x + SPR_W + 3;
      const py = ag.y + 4 + yOff;
      drawPaper(ctx, px, py, ag.taskLabel, ag.state==="working");
    } else if (ag.state==="working" && ag.taskLabel) {
      // Show task name below agent even without paper prop
      ctx.fillStyle   = "#c7d6ee";
      ctx.font        = "8px 'SFMono-Regular','Consolas',monospace";
      ctx.textAlign   = "center";
      ctx.fillText(truncate(ag.taskLabel, 14), ag.x+SPR_W/2, ag.y+SPR_H+12);
    }
  }

  // ── Orchestrator ──────────────────────────────
  const isWalking = st.phase==="walking_to"||st.phase==="walking_back";
  const orcFrames = st.orchestratorState==="thinking"  ? THINK_FRAMES
                  : st.orchestratorState==="reviewing" ? REVIEW_FRAMES
                  : ORC_FRAMES;
  const orcX = Math.round(st.orc.x);
  const orcY = Math.round(st.orc.y);

  drawSprite(ctx, orcFrames, isWalking ? animFrame : 0, orcX, orcY);

  // Paper in orchestrator's hand
  if (st.orcHasPaper && st.phase !== "delivering") {
    drawPaper(ctx, orcX + SPR_W + 2, orcY + SPR_H*0.3, st.paperLabel, true);
  }

  // Orchestrator label
  const orcColor = st.orchestratorState==="thinking"   ? "#cc88ff"
                 : st.orchestratorState==="reviewing"  ? "#88ffcc"
                 : st.orchestratorState==="delegating" ? "#ffd700"
                 : "#8fb2dd";
  ctx.fillStyle = orcColor;
  ctx.font = "bold 9px 'SFMono-Regular','Consolas',monospace";
  ctx.textAlign = "center";
  ctx.fillText("ORCHESTRATOR", orcX+SPR_W/2, orcY-13);
  ctx.font = "8px 'SFMono-Regular','Consolas',monospace";
  ctx.fillText(st.orchestratorState, orcX+SPR_W/2, orcY-4);

  // Hub dot
  ctx.beginPath();
  ctx.arc(HUB_X+SPR_W/2, HUB_Y+SPR_H+5, 3, 0, Math.PI*2);
  ctx.fillStyle = "#ffd700";
  ctx.fill();

  // ── Flying paper delivery animation ───────────
  if (st.phase==="delivering") {
    const t   = Math.min(st.paperDeliverT, 1);
    const arc = Math.sin(t * Math.PI) * 20;   // slight arc
    const px  = lerp(st.paperSrc.x, st.paperDst.x, t);
    const py  = lerp(st.paperSrc.y, st.paperDst.y, t) - arc;
    drawPaper(ctx, px, py, st.paperLabel, true);
  }

  // ── Thinking / reviewing pulse ring ──────────
  if (st.orchestratorState==="thinking" || st.orchestratorState==="reviewing") {
    const ringColor = st.orchestratorState==="reviewing" ? "136,255,204" : "200,136,255";
    const cx = orcX + SPR_W/2;
    const cy = orcY + SPR_H/2;
    const r  = SPR_W*0.8 + Math.sin(tick*0.1)*4;
    ctx.strokeStyle = `rgba(${ringColor},${0.3+0.2*Math.sin(tick*0.1)})`;
    ctx.lineWidth   = 2;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI*2); ctx.stroke();
  }

  // ── Orchestrator thought bubble ───────────────
  if (st.orchestratorState === "thinking") {
    drawThoughtBubble(ctx, orcX + SPR_W/2, orcY, "⚙", tick, "#cc88ff");
  } else if (st.orchestratorState === "reviewing") {
    drawThoughtBubble(ctx, orcX + SPR_W/2, orcY, "◉", tick, "#88ffcc");
  } else if (st.orchestratorState === "delegating") {
    drawThoughtBubble(ctx, orcX + SPR_W/2, orcY, "→", tick, "#ffd700");
  }

  // ── Loop counter (demo) ───────────────────────
  if (st.loop > 0) {
   ctx.fillStyle  = "#7f97ba";
   ctx.font       = "10px 'SFMono-Regular','Consolas',monospace";
    ctx.textAlign  = "right";
    ctx.fillText(`loop #${st.loop+1}`, CANVAS_W-10, 18);
  }
}

// ─────────────────────────────────────────────
// Task panel DOM rendering
// ─────────────────────────────────────────────
function renderTaskPanel(tasks) {
  const list = document.getElementById("task-list");
  const count = document.getElementById("task-count");
  const progress = document.getElementById("task-progress");
  const active = (tasks || []).filter(t => t.status === "in_progress").length;
  const completed = (tasks || []).filter(t => t.status === "completed").length;
  if (count) count.textContent = `${(tasks || []).length} task${(tasks || []).length === 1 ? "" : "s"}`;
  if (progress) progress.textContent = `${active} active / ${completed} done`;
  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<div class="task-empty">No tasks yet. Live `/state` updates will populate the queue here.</div>';
    return;
  }
  list.innerHTML = tasks.map(t => {
    const dotClass   = `dot-${t.status}`;
    const itemClass  = t.status;
    const agentBadge = t.agent
      ? `<span class="task-agent-badge">${t.agent}</span>`
      : "";
    const icon = t.status==="completed" ? "✓ "
               : t.status==="in_progress" ? "▶ "
               : t.status==="cancelled"   ? "✕ "
               : "○ ";
    const statusLabel = t.status === "in_progress"
      ? "in progress"
      : t.status;
    return `<div class="task-item ${itemClass}">
      <div class="task-dot ${dotClass}"></div>
      <div class="task-copy">
        <div class="task-topline">
          <div class="task-title">${icon}${t.title || t.id}</div>
          <span class="task-status-badge ${itemClass}">${statusLabel}</span>
        </div>
        <div class="task-meta">
          ${agentBadge}
          <span>${t.id || "task"}</span>
        </div>
      </div>
    </div>`;
  }).join("");
}

// ─────────────────────────────────────────────
// History bar
// ─────────────────────────────────────────────
function updateHistoryBar(history) {
  const bar = document.getElementById("history-bar");
  bar.innerHTML = history.map(h => {
    const color = h.status==="done" ? "#57d59a" : h.status==="error" ? "#ff6b6b" : "#ffb85c";
    const label = h.status === "error" ? "error" : "done";
    return `<span class="hist-chip" style="border-color:${color};color:${color}">${h.stage} · ${label}</span>`;
  }).join("");
}

// ─────────────────────────────────────────────
// Event Log
// ─────────────────────────────────────────────
let _prevSv = null;

function appendLog(stage, message, status) {
  const panel = document.getElementById("log-panel");
  const now   = new Date();
  const ts    = now.toTimeString().slice(0, 8);
  const cls   = { working:"log-s-working", done:"log-s-done", error:"log-s-error",
                  thinking:"log-s-thinking", delegating:"log-s-delegating",
                  waiting:"log-s-waiting" }[status] || "";
  const entry = document.createElement("div");
  entry.className = "log-entry";
  entry.innerHTML =
    '<span class="log-ts">[' + ts + ']</span>' +
    '<span class="log-stage">' + stage + '</span>' +
    '<span class="log-msg ' + cls + '">' + message + '</span>';
  panel.appendChild(entry);
  panel.scrollTop = panel.scrollHeight;
}

function diffAndLog(sv) {
  const prev = _prevSv;
  _prevSv = sv;
  if (!prev) {
    // First snapshot — log initial state if not idle
    if (sv.orchestrator && sv.orchestrator !== "idle")
      appendLog("orchestrator", "\u2192 " + sv.orchestrator, sv.orchestrator);
    return;
  }
  // Orchestrator state change
  if (sv.orchestrator !== prev.orchestrator)
    appendLog("orchestrator", "\u2192 " + sv.orchestrator, sv.orchestrator);
  // New agent delegated
  if (sv.current_agent !== prev.current_agent) {
    if (sv.current_agent)
      appendLog("orchestrator", "delegating \u2192 [" + sv.current_agent + "]", "delegating");
    else if (prev.current_agent)
      appendLog("orchestrator", "[" + prev.current_agent + "] released", "done");
  }
  // Agent state change
  if (sv.agent_state !== prev.agent_state && sv.current_agent) {
    const extra = (sv.agent_state === "working" && sv.current_task)
                  ? ": " + sv.current_task : "";
    appendLog(sv.current_agent, sv.agent_state + extra, sv.agent_state);
  } else if (sv.current_task !== prev.current_task && sv.current_task && sv.current_agent) {
    appendLog(sv.current_agent, "task \u2192 " + sv.current_task, "working");
  }
  // New history entries (stage completed)
  const prevLen = (prev.history || []).length;
  if (sv.history && sv.history.length > prevLen) {
    sv.history.slice(prevLen).forEach(function(h) {
      appendLog(h.stage, "completed [" + h.status + "]", h.status === "done" ? "done" : "error");
    });
  }
  // Task status changes
  if (sv.tasks && prev.tasks) {
    const prevMap = {};
    prev.tasks.forEach(function(t) { prevMap[t.id] = t.status; });
    sv.tasks.forEach(function(t) {
      if (prevMap[t.id] && prevMap[t.id] !== t.status) {
        const agentTag = t.agent ? " [" + t.agent + "]" : "";
        appendLog("task" + agentTag, t.title + " \u2192 " + t.status, t.status === "completed" ? "done" : t.status === "in_progress" ? "working" : "waiting");
      }
    });
  }
}

// ─────────────────────────────────────────────
// window.onload
// ─────────────────────────────────────────────
window.onload = function () {
  const canvas   = document.getElementById("canvas");
  const ctx      = canvas.getContext("2d");
  const taskText = document.getElementById("task-text");
  const badge    = document.getElementById("mode-badge");
  const isDemo   = new URLSearchParams(window.location.search).has("demo");
  const state    = createState();
  updateStatusSummary({ orchestrator: isDemo ? "demo" : "idle", current_task: "", current_agent: null, agent_state: "idle" }, isDemo, isDemo ? "demo" : "connecting");

  if (isDemo) {
    badge.textContent = "DEMO"; badge.className = "badge-demo";
    function demoLoop() {
      updateDemo(state);
      render(ctx, state);
      taskText.textContent = state.statusText;
      updateStatusSummary({
        orchestrator: "demo",
        current_task: state.statusText,
        current_agent: state.currentStage >= 0 ? STAGE_LABELS[state.currentStage] : null,
        agent_state: state.currentStage >= 0 ? state.agents[state.currentStage].state : "idle",
      }, true, "demo");
      requestAnimationFrame(demoLoop);
    }
    demoLoop();

  } else {
    badge.textContent = "CONNECTING..."; badge.className = "badge-conn";

    function connectSSE() {
      const es = new EventSource(`http://localhost:${PORT}/events`);
      es.onopen = () => {
        badge.textContent="LIVE"; badge.className="badge-live";
        updateStatusSummary({ orchestrator: state.orchestratorState, current_task: state.statusText, current_agent: null, agent_state: "idle" }, false, "live");
      };
      es.onmessage = (e) => {
        try {
          const sv = JSON.parse(e.data);
          applyServerState(state, sv);
          taskText.textContent = sv.current_task || "Orchestrator idle";
          if (sv.tasks && sv.tasks.length>0) renderTaskPanel(sv.tasks);
          diffAndLog(sv);
          updateStatusSummary(sv, false, "live");
        } catch(err) { console.warn("SSE parse error", err); }
      };
      es.onerror = () => {
        badge.textContent="DISCONNECTED"; badge.className="badge-conn";
        updateStatusSummary({ orchestrator: state.orchestratorState, current_task: state.statusText, current_agent: null, agent_state: "idle" }, false, "disconnected");
        es.close(); setTimeout(connectSSE, 2000);
      };
    }
    connectSSE();

    function liveLoop() {
      updateLive(state);
      render(ctx, state);
      requestAnimationFrame(liveLoop);
    }
    liveLoop();
  }
};
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
