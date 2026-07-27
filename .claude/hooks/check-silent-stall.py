#!/usr/bin/env python3
"""Stop hook: block silent turn-end after a tool failure.

Claude Code does not auto-retry failed tool calls. When a tool returns
"[Tool result missing due to internal error]", an empty payload, or a
"tool use rejected" without a user prompt, the correct behavior is to
react in the SAME turn (retry or surface the failure as text). Ending
the turn after a silent failure stalls the session — the harness will
never re-invoke Claude on its own.

This hook inspects the transcript when Stop fires:
  - If the most recent assistant message has no text content AND a
    preceding tool_result matched a failure marker, exit 2 to block
    the stop. The stderr message goes back to Claude as a reminder.
  - stop_hook_active short-circuits the loop after one block.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FAILURE_MARKERS = (
    "[Tool result missing due to internal error]",
    "tool use rejected",
)


def is_failure_result(content) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        s = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        s = "".join(parts)
    else:
        return False
    if not s.strip():
        return True
    return any(m in s for m in FAILURE_MARKERS)


def assistant_text(msg: dict) -> str:
    content = msg.get("content", [])
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def has_tool_use(msg: dict) -> bool:
    content = msg.get("content", [])
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if payload.get("stop_hook_active"):
        sys.exit(0)

    path = payload.get("transcript_path")
    if not path or not Path(path).exists():
        sys.exit(0)

    entries = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        sys.exit(0)

    if not entries:
        sys.exit(0)

    last_asst_idx = None
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("type") == "assistant":
            last_asst_idx = i
            break
    if last_asst_idx is None:
        sys.exit(0)

    last_msg = entries[last_asst_idx].get("message", {})
    if has_tool_use(last_msg):
        sys.exit(0)
    if assistant_text(last_msg).strip():
        sys.exit(0)

    # Walk backward from the empty assistant turn looking for an unacknowledged
    # tool failure. Stop walking at the previous assistant message (those
    # tool_results belonged to an earlier turn that we already lived through).
    saw_failure = False
    for i in range(last_asst_idx - 1, -1, -1):
        e = entries[i]
        if e.get("type") == "assistant":
            break
        if e.get("type") != "user":
            continue
        content = e.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if is_failure_result(block.get("content")):
                saw_failure = True
                break
        if saw_failure:
            break

    if not saw_failure:
        sys.exit(0)

    sys.stderr.write(
        "Stop blocked: a tool returned an error or empty result and your turn "
        "was about to end without retrying or surfacing the failure as text. "
        "Per CLAUDE.md \"Tool failure handling\", react in the SAME turn — "
        "retry the tool or write a user-facing message explaining what failed.\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
