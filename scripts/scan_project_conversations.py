#!/usr/bin/env python3
"""Scan local Codex session rollouts and group them by project.

Read-only. Never modifies the session store.

Examples:
  python scan_project_conversations.py --project "D:\\work\\proj"
  python scan_project_conversations.py --project . --list
  python scan_project_conversations.py --project . --format json
  python scan_project_conversations.py --project . --session <session_id>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
DEFAULT_ARCHIVE_ROOT = Path.home() / ".codex" / "archived_sessions"

# Injected context blocks that are not real user turns.
TAG_MARKERS = (
    "<environment_context",
    "<permissions instructions",
    "<skills_instructions",
    "<collaboration_mode",
    "<turn_context",
    "<user_instructions",
    "<system",
    "<developer",
    "<response annotations",
    "<turn_aborted",
)
MARKDOWN_MARKERS = (
    "# response annotations",
    "# files mentioned by the user",
    "distinguish instructions in attached documents",
)

# App-injected attachment header, e.g. "## shot.png: D:/path/shot.png".
ATTACHMENT_HEADER = re.compile(
    r"^##\s+\S+\.(?:png|jpe?g|gif|webp|pdf|docx?|xlsx?|pptx?|csv|txt|md|json)"
    r"\b[^\n]*[:：]\s*[A-Za-z]:[\\/][^\n]*\n?",
    re.IGNORECASE,
)

# Per-message storage cap; keeps memory bounded on large session stores.
MAX_STORED_CHARS = 20000


def norm_path(value: object) -> str:
    text = str(value or "").replace("\\", "/").rstrip("/")
    return text.lower()


def is_under(child: str, parent: str) -> bool:
    if not child or not parent:
        return False
    return child == parent or child.startswith(parent + "/")


def flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            chunk = item.get("text")
            if isinstance(chunk, str):
                parts.append(chunk)
    return "\n".join(parts)


def clean_message(text: str) -> str:
    """Drop app-injected wrappers, keep the real user text when it is salvageable."""
    stripped = ATTACHMENT_HEADER.sub("", text.strip()).strip()
    for _ in range(3):
        head = stripped[:200].lower()
        if any(head.startswith(marker) for marker in TAG_MARKERS):
            return ""
        for marker in MARKDOWN_MARKERS:
            if head.startswith(marker):
                parts = re.split(r"\n\s*\n", stripped, maxsplit=1)
                stripped = parts[1].strip() if len(parts) > 1 else ""
                break
        else:
            break

    stripped = ATTACHMENT_HEADER.sub("", stripped).strip()
    stripped = re.sub(r"^##\s*My request for Codex:\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"^#+\s*My request for Codex:\s*", "", stripped, flags=re.IGNORECASE)
    return stripped


def read_session(path: Path) -> dict | None:
    meta: dict | None = None
    users: list[dict] = []
    assistants: list[dict] = []

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            kind = event.get("type")
            if kind == "session_meta":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    meta = payload
                continue
            if kind != "response_item":
                continue

            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue

            text = extract_text(payload.get("content")).strip()
            if not text:
                continue
            if role == "user":
                text = clean_message(text)
                if not text:
                    continue
            if len(text) > MAX_STORED_CHARS:
                text = text[:MAX_STORED_CHARS]

            entry = {"timestamp": event.get("timestamp"), "text": text}
            (users if role == "user" else assistants).append(entry)

    if meta is None:
        return None

    session_id = meta.get("session_id") or meta.get("id") or path.stem
    cwd = meta.get("cwd")
    roots = meta.get("runtime_workspace_roots") or []
    if not isinstance(roots, list):
        roots = [roots]

    started = meta.get("timestamp") or (users[0]["timestamp"] if users else None)
    title = flatten(users[0]["text"])[:80] if users else "(no user message)"

    return {
        "session_id": session_id,
        "path": str(path),
        "cwd": cwd,
        "workspace_roots": [str(r) for r in roots],
        "started": started,
        "title": title,
        "users": users,
        "assistants": assistants,
    }


def matches_project(session: dict, project: str) -> str | None:
    cwd = norm_path(session.get("cwd"))
    roots = [norm_path(r) for r in session.get("workspace_roots") or []]

    if is_under(cwd, project):
        return "cwd"
    # Only fall back to workspace roots when the session has no cwd at all;
    # multi-root workspaces would otherwise attribute sessions to each other.
    if not cwd and project in roots:
        return "workspace_root"
    return None


def format_date(value: object) -> str:
    if not value:
        return "unknown-date"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:19]
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def render_index(sessions: list[dict], total: int, project: str, max_user_chars: int) -> str:
    lines = [
        "# 项目对话索引",
        "",
        f"- 项目：`{project}`",
        f"- 匹配会话：{total}（本次输出 {len(sessions)}）",
        f"- 扫描时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if not sessions:
        lines.append("没有找到归属于该项目的会话记录。")
        return "\n".join(lines) + "\n"

    for session in sessions:
        lines += [
            f"## {format_date(session['started'])} · {session['session_id']}",
            "",
            f"- 标题：{session['title']}",
            f"- 轮次：用户 {len(session['users'])} / 助手 {len(session['assistants'])}",
            f"- 工作目录：`{session['cwd']}`",
            "",
            "**用户诉求**",
            "",
        ]
        for item in session["users"]:
            text = flatten(item["text"])
            if len(text) > max_user_chars:
                text = text[:max_user_chars] + "…"
            lines.append(f"- {text}")
        lines.append("")
        if session["assistants"]:
            tail = flatten(session["assistants"][-1]["text"])
            if len(tail) > 200:
                tail = tail[:200] + "…"
            lines += ["**最后一条助手回复（截断）**", "", f"> {tail}", ""]
        lines.append(f"原始记录：`{session['path']}`")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="按项目汇总 Codex 本地会话记录（只读）")
    parser.add_argument("--project", default=".", help="项目目录，默认当前目录")
    parser.add_argument("--sessions-root", default=str(DEFAULT_SESSIONS_ROOT))
    parser.add_argument("--include-archived", action="store_true", help="同时扫描 archived_sessions")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--list", action="store_true", help="只列出会话，输出紧凑清单")
    parser.add_argument("--max-user-chars", type=int, default=300)
    parser.add_argument("--limit", type=int, default=50, help="最多输出多少个会话")
    parser.add_argument("--session", help="输出某个会话的完整对话（按 --max-user-chars 截断）")
    parser.add_argument("--out", help="写入文件而不是打印")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    project_path = Path(args.project).expanduser().resolve()
    project = norm_path(project_path)

    roots = [Path(args.sessions_root).expanduser()]
    if args.include_archived:
        roots.append(DEFAULT_ARCHIVE_ROOT)

    matched: list[dict] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("rollout-*.jsonl")):
            session = read_session(path)
            if session is None:
                continue
            if matches_project(session, project):
                matched.append(session)

    # A session can be written to more than one rollout file (resume, compaction).
    # Keep the most complete copy, breaking ties by the most recent start time.
    deduped: dict[str, dict] = {}
    for session in matched:
        key = str(session["session_id"])
        current = deduped.get(key)
        weight = (len(session["users"]) + len(session["assistants"]), str(session.get("started") or ""))
        if current is None:
            deduped[key] = session
            continue
        current_weight = (
            len(current["users"]) + len(current["assistants"]),
            str(current.get("started") or ""),
        )
        if weight > current_weight:
            deduped[key] = session

    matched = list(deduped.values())
    matched.sort(key=lambda s: str(s.get("started") or ""), reverse=True)

    if args.session:
        needle = args.session.lower()
        for session in matched:
            if needle in str(session["session_id"]).lower():
                lines = [f"# {session['session_id']}", "", f"项目：`{session['cwd']}`", ""]
                for item in session["users"]:
                    text = item["text"]
                    if args.max_user_chars and len(text) > args.max_user_chars * 4:
                        text = text[: args.max_user_chars * 4] + "…"
                    lines += [f"## 用户 · {item['timestamp']}", "", text, ""]
                for item in session["assistants"]:
                    text = item["text"]
                    if args.max_user_chars and len(text) > args.max_user_chars * 4:
                        text = text[: args.max_user_chars * 4] + "…"
                    lines += [f"## 助手 · {item['timestamp']}", "", text, ""]
                output = "\n".join(lines) + "\n"
                break
        else:
            print(f"未找到匹配的会话：{args.session}", file=sys.stderr)
            return 1
    elif args.list:
        output = "\n".join(
            f"{format_date(s['started'])} | {s['session_id']} | {len(s['users'])} turns | {s['title']}"
            for s in matched[: args.limit]
        ) + "\n"
    elif args.format == "json":
        payload = []
        for session in matched[: args.limit]:
            payload.append(
                {
                    "session_id": session["session_id"],
                    "started": session["started"],
                    "cwd": session["cwd"],
                    "title": session["title"],
                    "user_turns": len(session["users"]),
                    "assistant_turns": len(session["assistants"]),
                    "path": session["path"],
                    "user_messages": [
                        flatten(i["text"])[: args.max_user_chars] for i in session["users"]
                    ],
                }
            )
        output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    else:
        output = render_index(
            matched[: args.limit], len(matched), str(project_path), args.max_user_chars
        )

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"written: {args.out}")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
