# SDD Agent Viewer

Professional live monitoring dashboard for the [agent-teams-lite](https://github.com/Gentleman-Programming/agent-teams-lite) SDD orchestrator.

> **This project is an extension of agent-teams-lite.**
> You need to install it first before using this viewer.

Shows the orchestrator and each sub-agent moving through the SDD pipeline in real time, with a product-style dashboard shell around the existing pixel-art workflow scene.

---

## Requirements

1. **[agent-teams-lite](https://github.com/Gentleman-Programming/agent-teams-lite)** — install this first
2. **Python 3.10+**
3. **[opencode](https://opencode.ai)**
4. **`~/.local/bin` in your `$PATH`**

---

## Installation

```bash
# 1. Install agent-teams-lite first (if you haven't)
#    → https://github.com/Gentleman-Programming/agent-teams-lite

# 2. Clone and install this viewer
git clone https://github.com/MiguelSoft2bro/agent_viewer_gentleman.git
cd agent_viewer_gentleman
bash install.sh
```

The installer will:
- ✅ Check that **agent-teams-lite is installed** — if not, it stops and tells you where to get it
- ✅ Create `~/.local/bin/viewer` — global command accessible from anywhere
- ✅ Patch `~/.config/opencode/opencode.json` with:
  - Auto-launch rule → say *"arranca el viewer"* in opencode and it starts
  - VIEWER INTEGRATION block in the SDD orchestrator prompt → every pipeline phase notifies the viewer automatically

---

## Usage

### Start the viewer

```bash
viewer
```

Opens the live dashboard in your browser at `http://localhost:8765`.

### Start from opencode

Just say: **"arranca el viewer"**

### Run the full SDD pipeline demo

```bash
python3 viewer_client.py test
```

Sends all 8 SDD stages (explore → propose → spec → design → tasks → apply → verify → archive) with animated transitions so you can validate the live dashboard, task queue, history strip, and activity feed.

---

## How it works

```
opencode (agent-teams-lite SDD orchestrator)
        │
        │  POST /state  (curl, fire-and-forget)
        ▼
   viewer.py  ──── SSE ──▶  browser dashboard
        │
        └── /tmp/sdd_viewer.port  (port discovery across projects)
```

- `viewer.py` — single-file Python server (stdlib only, zero pip installs)
- `viewer_client.py` — Python client + CLI for pushing state updates
- The orchestrator notifies via plain `curl` — no Python path needed

---

## Files

| File | Description |
|------|-------------|
| `viewer.py` | Server + embedded professional dashboard frontend (single file) |
| `viewer_client.py` | State push client — also usable as CLI |
| `install.sh` | One-command installer |

---

## License

MIT
