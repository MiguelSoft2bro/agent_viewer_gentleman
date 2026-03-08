"""
viewer.py — SDD Workflow Visual Agent Viewer
Real-time: driven by live orchestrator events via SSE + POST /state.
Demo mode: open http://localhost:PORT?demo=1
Run:  python3 viewer.py
"""

import json
import os
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORTS     = [8765, 8766, 8767, 8768, 8769]
PORT_FILE = "/tmp/sdd_viewer.port"

# ── Shared state ─────────────────────────────
_lock        = threading.Lock()
_state       = {
    "orchestrator":  "idle",   # idle | thinking | delegating
    "current_agent": None,     # None | explore | propose | spec | design | tasks | apply | verify
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
            with _lock:
                for k, v in incoming.items():
                    if k in allowed:
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
    port = find_free_port(PORTS)
    if port is None:
        print("ERROR: No free port found in range", PORTS)
        return

    ViewerHandler.html_content = HTML_PAGE.replace("__PORT__", str(port))
    server = ThreadedHTTPServer(("", port), ViewerHandler)
    url = f"http://localhost:{port}"
    print(f"SDD Viewer running at {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        with open(PORT_FILE, "w") as f:
            f.write(str(port))
    except OSError:
        pass

    threading.Timer(0.5, lambda: webbrowser.open_new_tab(url)).start()

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
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      background:#0d0d1a;
      display:flex; flex-direction:column; align-items:center; justify-content:center;
      min-height:100vh;
      font-family:'Courier New',monospace; color:#e0e0ff;
    }
    #top-bar { display:flex; align-items:center; gap:20px; margin-bottom:10px; }
    #title { font-size:17px; font-weight:bold; letter-spacing:2px; color:#a0a0ff; text-transform:uppercase; }
    #mode-badge { font-size:11px; padding:3px 8px; border-radius:4px; font-weight:bold; letter-spacing:1px; }
    .badge-live { background:#1a3a1a; color:#44ff88; border:1px solid #44ff88; }
    .badge-demo { background:#3a2a1a; color:#ffd700; border:1px solid #ffd700; }
    .badge-conn { background:#3a1a1a; color:#ff6666; border:1px solid #ff6666; }

    #main-layout { display:flex; gap:12px; align-items:flex-start; }

    canvas { border:2px solid #2a2a4a; border-radius:8px; background:#1a1a2e; image-rendering:pixelated; }

    /* ── Task panel ─────────────────────────── */
    #task-panel {
      width:240px; min-height:460px;
      background:#12121f; border:1px solid #2a2a4a; border-radius:8px;
      padding:10px 12px; display:flex; flex-direction:column; gap:6px;
    }
    #task-panel-title {
      font-size:11px; font-weight:bold; letter-spacing:1px; color:#5050aa;
      text-transform:uppercase; margin-bottom:4px; border-bottom:1px solid #2a2a4a; padding-bottom:6px;
    }
    .task-item {
      display:flex; align-items:flex-start; gap:7px;
      font-size:11px; padding:5px 6px; border-radius:4px;
      border:1px solid transparent; transition: border-color 0.3s;
    }
    .task-item.pending  { color:#4a4a7a; border-color:#1e1e3a; }
    .task-item.in_progress { color:#ffd700; border-color:#5a4a00; background:#1a1600; }
    .task-item.completed   { color:#44ff88; border-color:#1a3a22; }
    .task-item.cancelled   { color:#555566; border-color:#1a1a2a; text-decoration:line-through; }
    .task-dot {
      width:8px; height:8px; border-radius:50%; flex-shrink:0; margin-top:2px;
    }
    .dot-pending    { background:#2a2a5a; }
    .dot-in_progress{ background:#ffd700; box-shadow:0 0 4px #ffd700; }
    .dot-completed  { background:#44ff88; }
    .dot-cancelled  { background:#333344; }
    .task-agent-badge {
      font-size:9px; padding:1px 4px; border-radius:2px;
      background:#1a2a3a; color:#5588ff; border:1px solid #2a3a5a;
      white-space:nowrap;
    }

    #task-bar {
      margin-top:8px; width:100%;
      background:#12121f; border:1px solid #2a2a4a; border-radius:6px;
      padding:7px 14px; font-size:12px; color:#8888cc;
      min-height:30px; display:flex; align-items:center; gap:10px;
    }
    #task-label { color:#5050aa; }
    #task-text  { color:#ccccff; flex:1; }

    #bottom-bar { display:flex; gap:20px; align-items:center; margin-top:8px; }
    #legend { display:flex; gap:18px; font-size:11px; }
    .legend-item { display:flex; align-items:center; gap:5px; }
    .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }

    #history-bar { display:flex; gap:5px; flex-wrap:wrap; }
    .hist-chip {
      font-size:10px; padding:2px 6px; border-radius:3px;
      background:#1a2a1a; color:#44ff88; border:1px solid #2a4a2a;
    }
  </style>
</head>
<body>
  <div id="top-bar">
    <div id="title">⬡ SDD Workflow — Live Agent Viewer</div>
    <div id="mode-badge" class="badge-conn">CONNECTING...</div>
  </div>

  <div id="main-layout">
    <canvas id="canvas" width="960" height="460"></canvas>
    <div id="task-panel">
      <div id="task-panel-title">📋 Tasks</div>
      <div id="task-list"><span style="color:#2a2a5a;font-size:11px">no tasks yet...</span></div>
    </div>
  </div>

  <div id="task-bar">
    <span id="task-label">task:</span>
    <span id="task-text">waiting for orchestrator...</span>
  </div>

  <div id="bottom-bar">
    <div id="legend">
      <div class="legend-item"><span class="dot" style="background:#4a4a8a"></span>idle</div>
      <div class="legend-item"><span class="dot" style="background:#ffd700"></span>working</div>
      <div class="legend-item"><span class="dot" style="background:#ff8c42"></span>waiting</div>
      <div class="legend-item"><span class="dot" style="background:#44ff88"></span>done</div>
      <div class="legend-item"><span class="dot" style="background:#ff4444"></span>error</div>
    </div>
    <div id="history-bar"></div>
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

const STAGE_LABELS = ["explore","propose","spec","design","tasks","apply","verify"];
const N_STAGES     = STAGE_LABELS.length;

const STAGE_COLORS = [
  "#1a2a1a","#2a1a2a","#2a2a1a","#1a2a2a",
  "#2a1a1a","#1a1a2a","#1a2a20",
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
const AGENT_FRAMES = [0,1].map(f=>makeSprite("#5588ff","#2244aa","#113388","#3366cc","#aaddff",f));
const DONE_FRAMES  = [0,0].map(f=>makeSprite("#44ff88","#22884a","#116630","#33bb66","#ccffee",f));
const WAIT_FRAMES  = [0,1].map(f=>makeSprite("#ff8c42","#cc5511","#aa3300","#ee7733","#ffddcc",f));
const ERROR_FRAMES = [0,1].map(f=>makeSprite("#ff4444","#aa1111","#880000","#cc2222","#ffaaaa",f));
const THINK_FRAMES = [0,1].map(f=>makeSprite("#cc88ff","#7744aa","#552288","#9966cc","#ffffff",f));

// ── Thought bubble icons per agent state ──────
// Unicode symbols safe in Courier New on all platforms (no emoji)
const BUBBLE_ICONS = {
  idle:     null,       // no bubble when fully idle
  thinking: "⚙",       // gear = processing
  working:  "✎",       // pencil = writing/coding
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
// Layout  (canvas now 960×460)
// ─────────────────────────────────────────────
const CANVAS_W   = 960;
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
    _resetScheduled: false,

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
    if (!st._resetScheduled) {
      st._resetScheduled = true;
      setTimeout(() => {
        st.agents.forEach(a => {
          a.state     = "idle";
          a.hasPaper  = false;
          a.taskLabel = null;
        });
        st.tasks           = [];
        st._resetScheduled = false;
        renderTaskPanel([]);
      }, 3000);   // 3 s display window so user can read done states
    }
  } else {
    // Cancel pending reset if orchestrator becomes active again
    st._resetScheduled = false;
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
  ctx.fillStyle = "#1a1a2e";
  ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);

  // ── Stage zones ──────────────────────────────
  for (let i=0; i<N_STAGES; i++) {
    const zx = MARGIN + i * STAGE_SPAN;
    const zy = STAGE_Y - 18;
    const zw = STAGE_SPAN - 4;
    const ag = st.agents[i];
    const isActive = ag.state === "working" || ag.state === "waiting";

    ctx.fillStyle = STAGE_COLORS[i];
    ctx.beginPath(); ctx.roundRect(zx, zy, zw, ZONE_H, 7); ctx.fill();

    ctx.strokeStyle = isActive ? "#ffd700"
                    : ag.state==="done"  ? "#44ff88"
                    : ag.state==="error" ? "#ff4444"
                    : "#2a2a5a";
    ctx.lineWidth = isActive ? 2 : 1;
    ctx.beginPath(); ctx.roundRect(zx, zy, zw, ZONE_H, 7); ctx.stroke();

    // Zone label
    ctx.fillStyle = ag.state==="done" ? "#44ff88"
                  : ag.state==="error" ? "#ff4444"
                  : isActive ? "#ffd700" : "#5050aa";
    ctx.font = "bold 10px 'Courier New',monospace";
    ctx.textAlign = "center";
    ctx.fillText(STAGE_LABELS[i].toUpperCase(), zx+zw/2, zy+ZONE_H-10);

    ctx.fillStyle = "#30306a";
    ctx.font = "9px 'Courier New',monospace";
    ctx.fillText(`[${i+1}]`, zx+zw/2, zy+ZONE_H-22);
  }

  // ── Connector line ────────────────────────────
  if (st.phase==="walking_to"||st.phase==="at_stage"||
      st.phase==="walking_back"||st.phase==="delivering") {
    const i  = st.currentStage >= 0 ? st.currentStage : 0;
    const tx = stageX(i) + SPR_W/2;
    const ty = STAGE_Y + SPR_H/2;
    ctx.strokeStyle = "rgba(255,215,0,0.12)";
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
    ctx.fillStyle = st.agents[i].state==="done" ? "#33aa55" : "#2a2a5a";
    ctx.font = "13px 'Courier New',monospace";
    ctx.textAlign = "center";
    ctx.fillText("→", ax, ay);
  }

  // ── Sub-agents ────────────────────────────────
    for (let i=0; i<N_STAGES; i++) {
    const ag = st.agents[i];
    let frames = AGENT_FRAMES;
    let frame  = 0;
    let yOff   = 0;

    if (ag.state==="idle") {
      frames = AGENT_FRAMES; frame = 0;
      // Soft breathing: ±1px slow sine, phase-offset per agent so they don't sync
      yOff = Math.sin(tick * 0.04 + i * 1.3) * 1;
    }
    else if (ag.state==="waiting") {
      frames = WAIT_FRAMES; frame = animFrame;
      ctx.globalAlpha = 0.6 + 0.4 * Math.sin(tick * 0.15);
      // no yOff — waiting is still
    }
    else if (ag.state==="working") {
      frames = AGENT_FRAMES; frame = animFrame;
      // Typing-style dip: always downward push 0→-3px
      yOff = Math.abs(Math.sin(tick * 0.3 + i)) * -3;
    }
    else if (ag.state==="done")  { frames = DONE_FRAMES;  frame = 0; }
    else if (ag.state==="error") { frames = ERROR_FRAMES; frame = animFrame; }

    drawSprite(ctx, frames, frame, ag.x, ag.y+yOff);
    ctx.globalAlpha = 1;

    // ── Thought bubble above agent head ──────────
    const bubIcon  = BUBBLE_ICONS[ag.state];
    const bubColor = {
      idle:"rgba(100,100,180,0.7)", working:"#ffd700",
      waiting:"#ff8c42", done:"#44ff88", error:"#ff4444"
    }[ag.state] || "rgba(160,160,220,0.9)";
    if (bubIcon) {
      drawThoughtBubble(ctx, ag.x + SPR_W/2, ag.y + yOff, bubIcon, tick + i*17, bubColor);
    }

    // State badge
    const bc = {idle:"#4a4a8a",waiting:"#ff8c42",working:"#ffd700",done:"#44ff88",error:"#ff4444"}[ag.state]||"#4a4a8a";
    ctx.fillStyle = bc;
    ctx.font = "8px 'Courier New',monospace";
    ctx.textAlign = "center";
    ctx.fillText(ag.state, ag.x+SPR_W/2, ag.y-5);

    // Paper held by agent + task label
    if (ag.hasPaper) {
      const px = ag.x + SPR_W + 3;
      const py = ag.y + 4 + yOff;
      drawPaper(ctx, px, py, ag.taskLabel, ag.state==="working");
    } else if (ag.state==="working" && ag.taskLabel) {
      // Show task name below agent even without paper prop
      ctx.fillStyle   = "#aaaadd";
      ctx.font        = "8px 'Courier New',monospace";
      ctx.textAlign   = "center";
      ctx.fillText(truncate(ag.taskLabel, 14), ag.x+SPR_W/2, ag.y+SPR_H+12);
    }
  }

  // ── Orchestrator ──────────────────────────────
  const isWalking = st.phase==="walking_to"||st.phase==="walking_back";
  const orcFrames = st.orchestratorState==="thinking" ? THINK_FRAMES : ORC_FRAMES;
  const orcX = Math.round(st.orc.x);
  const orcY = Math.round(st.orc.y);

  drawSprite(ctx, orcFrames, isWalking ? animFrame : 0, orcX, orcY);

  // Paper in orchestrator's hand
  if (st.orcHasPaper && st.phase !== "delivering") {
    drawPaper(ctx, orcX + SPR_W + 2, orcY + SPR_H*0.3, st.paperLabel, true);
  }

  // Orchestrator label
  const orcColor = st.orchestratorState==="thinking"   ? "#cc88ff"
                 : st.orchestratorState==="delegating" ? "#ffd700"
                 : "#888899";
  ctx.fillStyle = orcColor;
  ctx.font = "bold 9px 'Courier New',monospace";
  ctx.textAlign = "center";
  ctx.fillText("ORCHESTRATOR", orcX+SPR_W/2, orcY-13);
  ctx.font = "8px 'Courier New',monospace";
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

  // ── Thinking pulse ring ───────────────────────
  if (st.orchestratorState==="thinking") {
    const cx = orcX + SPR_W/2;
    const cy = orcY + SPR_H/2;
    const r  = SPR_W*0.8 + Math.sin(tick*0.1)*4;
    ctx.strokeStyle = `rgba(200,136,255,${0.3+0.2*Math.sin(tick*0.1)})`;
    ctx.lineWidth   = 2;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI*2); ctx.stroke();
  }

  // ── Orchestrator thought bubble ───────────────
  if (st.orchestratorState === "thinking") {
    drawThoughtBubble(ctx, orcX + SPR_W/2, orcY, "⚙", tick, "#cc88ff");
  } else if (st.orchestratorState === "delegating") {
    drawThoughtBubble(ctx, orcX + SPR_W/2, orcY, "→", tick, "#ffd700");
  }

  // ── Loop counter (demo) ───────────────────────
  if (st.loop > 0) {
    ctx.fillStyle  = "#30306a";
    ctx.font       = "10px 'Courier New',monospace";
    ctx.textAlign  = "right";
    ctx.fillText(`loop #${st.loop+1}`, CANVAS_W-10, 18);
  }
}

// ─────────────────────────────────────────────
// Task panel DOM rendering
// ─────────────────────────────────────────────
function renderTaskPanel(tasks) {
  const list = document.getElementById("task-list");
  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<span style="color:#2a2a5a;font-size:11px">no tasks yet...</span>';
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
    return `<div class="task-item ${itemClass}">
      <div class="task-dot ${dotClass}"></div>
      <div style="flex:1">
        <div>${icon}${t.title || t.id}</div>
        ${agentBadge}
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
    const color = h.status==="done" ? "#44ff88" : h.status==="error" ? "#ff4444" : "#ffd700";
    return `<span class="hist-chip" style="border-color:${color};color:${color}">${h.stage} ✓</span>`;
  }).join("");
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

  if (isDemo) {
    badge.textContent = "DEMO"; badge.className = "badge-demo";
    function demoLoop() {
      updateDemo(state);
      render(ctx, state);
      taskText.textContent = state.statusText;
      requestAnimationFrame(demoLoop);
    }
    demoLoop();

  } else {
    badge.textContent = "CONNECTING..."; badge.className = "badge-conn";

    function connectSSE() {
      const es = new EventSource(`http://localhost:${PORT}/events`);
      es.onopen = () => { badge.textContent="LIVE"; badge.className="badge-live"; };
      es.onmessage = (e) => {
        try {
          const sv = JSON.parse(e.data);
          applyServerState(state, sv);
          taskText.textContent = sv.current_task || "Orchestrator idle";
          if (sv.tasks && sv.tasks.length>0) renderTaskPanel(sv.tasks);
        } catch(err) { console.warn("SSE parse error", err); }
      };
      es.onerror = () => {
        badge.textContent="DISCONNECTED"; badge.className="badge-conn";
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
