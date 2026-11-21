#!/usr/bin/env python3
"""Claude Code PreToolUse hook: `dibs run` must go through a background Bash call.

A background command re-invokes the agent when it exits, so the job's output and
exit code come back into the same thread as its next message. A foreground call
would block the turn and hit the Bash timeout. Deny never grants permission.
"""
import json, re, sys

e = json.load(sys.stdin)
t = e.get("tool_input") or {}
# Drop quoted text so `grep "dibs run"` or a heredoc mention is not a call; then match
# dibs run only in command position (line start or after ; & | && || ( `).
cmd = re.sub(r"'[^']*'|\"(\\.|[^\"\\])*\"", "''", t.get("command", ""))
if (e.get("tool_name") == "Bash" and not t.get("run_in_background")
        and re.search(r"(^|[;&|(`]|\n)\s*(\S*/)?dibs\s+run\b", cmd, re.M)):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "dibs run must be started with run_in_background: true. "
            "Re-issue the same command that way. Do not poll or tail it: when it exits you are "
            "re-invoked automatically with its full output and exit code.",
    }}))
