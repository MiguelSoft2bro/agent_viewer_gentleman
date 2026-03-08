# SDD Agent Viewer

Visual dashboard for the [Gentleman Skills](https://github.com/gentleman-org/Gentleman-Skills) SDD orchestrator.

Shows the orchestrator and each sub-agent as pixel-art characters animating through the SDD pipeline in real time.

![viewer demo](https://raw.githubusercontent.com/gentleman-org/agent_viewer/main/docs/demo.gif)

---

## What it does

When you run an SDD command (`/sdd-new`, `/sdd-ff`, `/sdd-apply`, etc.) in opencode, the orchestrator pushes live state to the viewer via HTTP. You see:

- Which agent is currently working (explore, propose, spec, design, tasks, apply, verify, archive)
- The current task description
- History of completed stages
- Task list with statuses

---

## Requirements

- Python 3.10+
- [opencode](https://opencode.ai) with the [Gentleman Skills](https://github.com/gentleman-org/Gentleman-Skills) SDD system prompt
- `~/.local/bin` in your `$PATH`

---

## Installation

```bash
git clone https://github.com/gentleman-org/agent_viewer.git
cd agent_viewer
bash install.sh
```

The installer does two things:

1. **Creates `~/.local/bin/viewer`** — a global wrapper so you can run `viewer` from anywhere.
2. **Patches `~/.config/opencode/opencode.json`** — adds the auto-launch rule so you can just say *"arranca el viewer"* inside opencode and it starts automatically.

> If `opencode.json` doesn't exist yet, run opencode once first, then re-run `install.sh`.

---

## Usage

### Start manually

```bash
viewer
```

Opens the dashboard in your browser at `http://localhost:8765` (or next available port).

### Start from opencode

Just say: **"arranca el viewer"** — the orchestrator will launch it automatically.

### Simulate the full SDD pipeline (demo)

```bash
python3 viewer_client.py test
```

Sends all pipeline stages with 1.5s delays so you can see the animations.

---

## How it works

```
opencode SDD orchestrator
        │
        │  POST /state  (curl, fire-and-forget)
        ▼
   viewer.py  ──── SSE ──▶  browser dashboard
        │
        └── writes /tmp/sdd_viewer.port  (port discovery)
```

- `viewer.py` — single-file Python server (stdlib only, no pip installs)
- `viewer_client.py` — Python client + CLI for sending state updates
- `/tmp/sdd_viewer.port` — port file written at startup for cross-project discovery
- The orchestrator uses `curl` directly (no Python path required)

---

## Files

| File | Description |
|------|-------------|
| `viewer.py` | Main server — HTTP + SSE + pixel-art HTML/JS frontend |
| `viewer_client.py` | Python client for sending state; also usable as CLI |
| `install.sh` | One-command installer |

---

## Manual opencode integration (if install.sh can't patch it)

Add this to the `instructions` array in `~/.config/opencode/opencode.json`:

```
VIEWER AUTO-LAUNCH RULE:
If the user says anything like "abre el viewer", "arranca el viewer", "start viewer" or similar
— immediately run Bash:
  _OWNER=$(ps -o ppid= -p $PPID | tr -d ' '); viewer --owner-pid $_OWNER > /tmp/viewer.log 2>&1 &
then confirm: "✅ Viewer lanzado — http://localhost:<port>"
Do NOT ask for confirmation. Do NOT open a new terminal.
```

---

## License

MIT
