"""
viewer_client.py — SDD Viewer notification client
Call notify() from the orchestrator to push real-time state to viewer.py.
Silent no-op if viewer is not running.

Usage (Python import):
    from viewer_client import notify
    notify(orchestrator="thinking", current_task="Analyzing codebase...")
    notify(orchestrator="delegating", current_agent="spec",
           agent_state="working", current_task="Writing spec for X")
    notify(orchestrator="idle")

Usage (CLI — from any shell/agent):
    python3 /path/to/viewer_client.py notify '{"orchestrator":"thinking","current_task":"..."}'
    python3 /path/to/viewer_client.py notify --orchestrator thinking --current_task "..."
    python3 /path/to/viewer_client.py test     # run dashboard smoke test
"""

import json
import urllib.request
import urllib.error

PORT_FILE   = "/tmp/sdd_viewer.port"
DEFAULT_PORT = 8765
TIMEOUT      = 2.0   # seconds — fast fail if viewer not running


def _get_port() -> int:
    """Read the port viewer.py chose at startup."""
    try:
        with open(PORT_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return DEFAULT_PORT


def notify(
    orchestrator: str = "idle",
    current_agent: str | None = None,
    agent_state: str = "idle",
    current_task: str = "",
    history: list | None = None,
    tasks: list | None = None,
) -> bool:
    """
    Push a live state update to the running dashboard viewer.
    Returns True on success, False if viewer is unreachable (silent).

    orchestrator : "idle" | "thinking" | "reviewing" | "delegating"
    current_agent: None  | "explore" | "propose" | "spec" | "design"
                          | "tasks"   | "apply"   | "verify" | "archive"
    agent_state  : "idle" | "working" | "done" | "error"
    current_task : short human-readable description of what's happening
    history      : list of {"stage": str, "status": "done"|"error", "ts": float}
    tasks        : list of {"id": str, "title": str,
                            "status": "pending"|"in_progress"|"completed"|"cancelled",
                            "agent": str | None}
    """
    payload = {
        "orchestrator":  orchestrator,
        "current_agent": current_agent,
        "agent_state":   agent_state,
        "current_task":  current_task,
        "history":       history or [],
        "tasks":         tasks or [],
    }
    port = _get_port()
    url  = f"http://localhost:{port}/state"
    data = json.dumps(payload).encode()

    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT):
            pass
        return True
    except Exception:
        return False   # viewer not running — silently skip


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import time

    def _smoke_test():
        import copy

        print("Testing viewer_client.py — make sure viewer.py is running first.")
        print()

        base_tasks = [
            {"id":"t1","title":"explore codebase","status":"pending","agent":None},
            {"id":"t2","title":"write proposal",  "status":"pending","agent":None},
            {"id":"t3","title":"write spec",      "status":"pending","agent":None},
        ]

        def t(updates):
            tasks = copy.deepcopy(base_tasks)
            for tid, status, agent in updates:
                for task in tasks:
                    if task["id"] == tid:
                        task["status"] = status
                        task["agent"]  = agent
            return tasks

        steps = [
            dict(orchestrator="thinking",   current_task="Analyzing the codebase...",
                 tasks=t([])),
            dict(orchestrator="delegating", current_agent="explore", agent_state="working",
                 current_task="Exploring project structure",
                 tasks=t([("t1","in_progress","explore")])),
            dict(orchestrator="delegating", current_agent="explore", agent_state="done",
                 current_task="Exploration complete",
                 history=[{"stage":"explore","status":"done","ts":time.time()}],
                 tasks=t([("t1","completed","explore")])),
            dict(orchestrator="delegating", current_agent="propose", agent_state="working",
                 current_task="Writing proposal",
                 tasks=t([("t1","completed","explore"),("t2","in_progress","propose")])),
            dict(orchestrator="delegating", current_agent="propose", agent_state="done",
                 current_task="Proposal ready",
                 history=[{"stage":"explore","status":"done","ts":time.time()},
                          {"stage":"propose","status":"done","ts":time.time()}],
                 tasks=t([("t1","completed","explore"),("t2","completed","propose")])),
            dict(orchestrator="thinking",   current_task="Waiting for user approval...",
                 tasks=t([("t1","completed","explore"),("t2","completed","propose")])),
            dict(orchestrator="idle",       current_task="",
                 tasks=t([("t1","completed","explore"),("t2","completed","propose")])),
        ]

        for step in steps:
            ok = notify(
                orchestrator  = str(step.get("orchestrator") or "idle"),
                current_agent = step.get("current_agent") or None,       # type: ignore[arg-type]
                agent_state   = str(step.get("agent_state") or "idle"),
                current_task  = str(step.get("current_task") or ""),
                history       = step.get("history"),                      # type: ignore[arg-type]
                tasks         = step.get("tasks"),                        # type: ignore[arg-type]
            )
            status = "✓ sent" if ok else "✗ viewer not running"
            print(f"  {status}: {step}")
            time.sleep(1.5)

        print("\nDashboard smoke test complete.")

    # ── CLI dispatch ──────────────────────────
    # Usage:
    #   python3 viewer_client.py test
    #   python3 viewer_client.py notify '{"orchestrator":"thinking",...}'
    #   python3 viewer_client.py notify --orchestrator thinking --current_agent explore ...

    args = sys.argv[1:]

    if not args or args[0] == "test":
        _smoke_test()

    elif args[0] == "notify":
        rest = args[1:]
        payload: dict = {}

        if rest and rest[0].startswith("{"):
            # JSON blob: viewer_client.py notify '{"orchestrator":"thinking"}'
            payload = json.loads(rest[0])
        else:
            # --key value pairs: viewer_client.py notify --orchestrator thinking --current_task "..."
            i = 0
            while i < len(rest):
                key = rest[i].lstrip("-")
                val = rest[i + 1] if i + 1 < len(rest) else ""
                # Try to parse as JSON for lists/nulls
                try:
                    payload[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    payload[key] = val
                i += 2

        ok = notify(
            orchestrator  = str(payload.get("orchestrator", "idle")),
            current_agent = payload.get("current_agent") or None,
            agent_state   = str(payload.get("agent_state", "idle")),
            current_task  = str(payload.get("current_task", "")),
            history       = payload.get("history") if isinstance(payload.get("history"), list) else None,
            tasks         = payload.get("tasks")   if isinstance(payload.get("tasks"),   list) else None,
        )
        if not ok:
            sys.exit(1)   # non-zero so callers can detect viewer-not-running
    else:
        print(f"Unknown command: {args[0]}", file=sys.stderr)
        print("Usage: viewer_client.py [test | notify <json> | notify --key val ...]", file=sys.stderr)
        sys.exit(2)
