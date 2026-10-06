#!/usr/bin/env python3
"""Group local Codex conversations by project, across folder renames.

Two sources are combined and deduplicated:

1. The Codex desktop project assignment map in
   ``~/.codex/.codex-global-state.json`` (thread -> project). This keeps working
   after a project folder is renamed or moved.
2. ``session_meta.cwd`` inside each rollout file, used as a fallback and as a
   supplement for sessions that predate the assignment map.

Read-only. The session store, state database, and global state are never written.

Examples:
  python scan_project_conversations.py --project "E:\\work\\proj"
  python scan_project_conversations.py --project . --list
  python scan_project_conversations.py --project . --alias "E:\\old\\path"
  python scan_project_conversations.py --project . --session <thread_id>
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"
DEFAULT_SESSIONS_ROOT = CODEX_HOME / "sessions"
DEFAULT_ARCHIVE_ROOT = CODEX_HOME / "archived_sessions"
DEFAULT_STATE_FILE = CODEX_HOME / ".codex-global-state.json"
DEFAULT_STATE_DB = CODEX_HOME / "state_5.sqlite"

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

MAX_STORED_CHARS = 20000


def norm_path(value: object) -> str:
    text = str(value or "")
    if text.startswith("\\\\?\\"):
        text = text[4:]
    text = text.replace("\\", "/").rstrip("/")
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
    """Drop app-injected wrappers, keep the real user text when salvageable."""
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
    stripped = re.sub(r"^#+\s*My request for Codex:\s*", "", stripped, flags=re.IGNORECASE)
    return stripped


def read_meta(path: Path) -> dict | None:
    """Read only the first JSONL record, which holds session_meta."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return None
                if event.get("type") != "session_meta":
                    return None
                payload = event.get("payload")
                return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


def read_session(path: Path) -> tuple[dict | None, list[dict], list[dict]]:
    """Return (meta, user_messages, assistant_messages) for one rollout file."""
    meta: dict | None = None
    users: list[dict] = []
    assistants: list[dict] = []

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None, users, assistants

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

    return meta, users, assistants


def load_assignments(state_file: Path) -> dict:
    """thread_id -> assignment dict, from the Codex desktop global state."""
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    assignments = data.get("thread-project-assignments")
    return assignments if isinstance(assignments, dict) else {}


def resolve_project_ids(assignments: dict, wanted: list[str]) -> set[str]:
    """Project ids whose recorded path (new or old) is the requested project."""
    project_ids: set[str] = set()
    for info in assignments.values():
        if not isinstance(info, dict):
            continue
        for candidate in (info.get("path"), info.get("cwd")):
            normalized = norm_path(candidate)
            if normalized and any(is_under(normalized, want) for want in wanted if want):
                project_id = info.get("projectId")
                if project_id:
                    project_ids.add(str(project_id))
                break
    return project_ids


def collect_assigned(assignments: dict, project_ids: set[str]) -> dict:
    """Every thread belonging to the resolved project ids.

    This is what survives a folder rename: some records only carry the old cwd,
    but they still share the same project id as the renamed project.
    """
    if not project_ids:
        return {}
    return {
        thread_id: info
        for thread_id, info in assignments.items()
        if isinstance(info, dict) and str(info.get("projectId")) in project_ids
    }


def load_thread_rows(db_path: Path, wanted_ids: set[str]) -> dict:
    """thread_id -> row metadata from state_5.sqlite, when available."""
    if not db_path.exists() or not wanted_ids:
        return {}
    rows: dict[str, dict] = {}
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        for thread_id, title, archived, rollout_path, created in con.execute(
            "select id, title, archived, rollout_path, created_at from threads"
        ):
            if thread_id in wanted_ids:
                rows[thread_id] = {
                    "title": title,
                    "archived": archived,
                    "rollout_path": rollout_path,
                    "created_at": created,
                }
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return rows


def build_rollout_index(roots: list[Path]) -> dict[str, list[Path]]:
    """thread_id -> rollout files (a thread can have several, e.g. after resume)."""
    index: dict[str, list[Path]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("rollout-*.jsonl")):
            stem = path.stem
            thread_id = stem[-36:] if len(stem) >= 36 else stem
            index.setdefault(thread_id, []).append(path)
    return index


def format_date(value: object) -> str:
    if not value:
        return "unknown-date"
    text = str(value)
    if text.replace(".", "").isdigit():
        try:
            stamp = float(text)
            if stamp > 1e11:
                stamp /= 1000
            return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return text[:19]
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
            f"- 归属依据：{session['reason']}",
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
    parser = argparse.ArgumentParser(
        description="按项目汇总 Codex 本地会话记录（只读，支持项目文件夹改名）"
    )
    parser.add_argument("--project", default=".", help="项目目录，默认当前目录")
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="同一项目的旧路径，可重复；用于文件夹改名或移动后的补充匹配",
    )
    parser.add_argument("--sessions-root", default=str(DEFAULT_SESSIONS_ROOT))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--state-db", default=str(DEFAULT_STATE_DB))
    parser.add_argument("--include-archived", action="store_true", help="同时扫描归档会话")
    parser.add_argument(
        "--no-desktop-state",
        action="store_true",
        help="不使用 Codex 桌面端的项目归属表，只按 rollout 里的 cwd 匹配",
    )
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--list", action="store_true", help="只列出会话，输出紧凑清单")
    parser.add_argument("--explain", action="store_true", help="在清单里显示归属依据")
    parser.add_argument("--max-user-chars", type=int, default=300)
    parser.add_argument("--limit", type=int, default=50, help="最多输出多少个会话")
    parser.add_argument("--session", help="输出某个会话的完整对话")
    parser.add_argument("--out", help="写入文件而不是打印")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    project_path = Path(args.project).expanduser().resolve()
    project = norm_path(project_path)
    wanted = [project] + [norm_path(Path(a).expanduser()) for a in args.alias if a]

    roots = [Path(args.sessions_root).expanduser()]
    if args.include_archived:
        roots.append(DEFAULT_ARCHIVE_ROOT)

    index = build_rollout_index(roots)

    assigned: dict[str, dict] = {}
    if not args.no_desktop_state:
        assignments = load_assignments(Path(args.state_file))
        project_ids = resolve_project_ids(assignments, wanted)
        assigned = collect_assigned(assignments, project_ids)

    thread_rows = load_thread_rows(Path(args.state_db), set(assigned))

    meta_cache: dict[Path, dict | None] = {}

    def cached_meta(path: Path) -> dict | None:
        if path not in meta_cache:
            meta_cache[path] = read_meta(path)
        return meta_cache[path]

    candidates: dict[str, list[tuple[Path, str, dict]]] = {}

    def add_candidate(thread_id: str, path: Path, reason: str, meta: dict) -> None:
        bucket = candidates.setdefault(thread_id, [])
        if all(existing[0] != path for existing in bucket):
            bucket.append((path, reason, meta))

    # 1) Threads named by the desktop project assignment map. Prefer the rollout
    #    path recorded in state_5.sqlite, which may point at archived_sessions.
    for thread_id in assigned:
        paths: list[Path] = []
        row = thread_rows.get(thread_id) or {}
        recorded = row.get("rollout_path")
        if recorded:
            candidate = Path(str(recorded))
            if candidate.exists():
                paths.append(candidate)
        if not paths:
            paths.extend(index.get(thread_id, []))
        for path in paths:
            meta = cached_meta(path)
            if meta is not None:
                add_candidate(thread_id, path, "项目归属表", meta)

    # 2) Everything else, matched by the working directory recorded in the rollout.
    for thread_id, paths in index.items():
        if candidates.get(thread_id):
            continue
        for path in paths:
            meta = cached_meta(path)
            if meta is None:
                continue
            cwd = norm_path(meta.get("cwd"))
            meta_roots = [norm_path(r) for r in (meta.get("runtime_workspace_roots") or [])]
            if any(is_under(cwd, want) for want in wanted if want):
                add_candidate(thread_id, path, "工作目录", meta)
            elif not cwd and any(want in meta_roots for want in wanted if want):
                add_candidate(thread_id, path, "工作区根目录", meta)

    # A thread assigned to the project may have no rollout under the scanned roots.
    missing_assigned = [
        thread_id for thread_id in assigned if thread_id not in candidates
    ]

    sessions: dict[str, dict] = {}
    for thread_id, entries in candidates.items():
        best: dict | None = None
        for path, reason, meta in entries:
            parsed_meta, users, assistants = read_session(path)
            if parsed_meta is None:
                continue
            session = {
                "session_id": thread_id,
                "path": str(path),
                "cwd": parsed_meta.get("cwd"),
                "workspace_roots": [str(r) for r in (parsed_meta.get("runtime_workspace_roots") or [])],
                "started": parsed_meta.get("timestamp")
                or (users[0]["timestamp"] if users else None),
                "title": flatten(users[0]["text"])[:80] if users else "(没有用户消息)",
                "users": users,
                "assistants": assistants,
                "reason": reason,
            }
            if best is None:
                best = session
                continue
            weight = (len(session["users"]) + len(session["assistants"]), str(session["started"] or ""))
            best_weight = (len(best["users"]) + len(best["assistants"]), str(best["started"] or ""))
            if weight > best_weight:
                best = session
        if best is not None:
            sessions[thread_id] = best

    matched = list(sessions.values())
    matched.sort(key=lambda s: str(s.get("started") or ""), reverse=True)

    if not args.list and args.format == "md" and missing_assigned:
        print(
            f"注意：归属表里有 {len(missing_assigned)} 个会话在本机找不到 rollout 文件，已跳过。",
            file=sys.stderr,
        )

    if args.session:
        needle = args.session.lower()
        chosen = None
        for session in matched:
            if needle in str(session["session_id"]).lower():
                chosen = session
                break
        if chosen is None:
            print(f"未找到匹配的会话：{args.session}", file=sys.stderr)
            return 1
        lines = [f"# {chosen['session_id']}", "", f"项目：`{chosen['cwd']}`", ""]
        for role, items in (("用户", chosen["users"]), ("助手", chosen["assistants"])):
            for item in items:
                text = item["text"]
                cap = args.max_user_chars * 4 if args.max_user_chars else 0
                if cap and len(text) > cap:
                    text = text[:cap] + "…"
                lines += [f"## {role} · {item['timestamp']}", "", text, ""]
        output = "\n".join(lines) + "\n"
    elif args.list:
        rows = []
        for session in matched[: args.limit]:
            line = (
                f"{format_date(session['started'])} | {session['session_id']} | "
                f"{len(session['users'])} turns | {session['title']}"
            )
            if args.explain:
                line += f" | {session['reason']}"
            rows.append(line)
        output = "\n".join(rows) + "\n"
    elif args.format == "json":
        payload = [
            {
                "session_id": s["session_id"],
                "started": s["started"],
                "cwd": s["cwd"],
                "title": s["title"],
                "reason": s["reason"],
                "user_turns": len(s["users"]),
                "assistant_turns": len(s["assistants"]),
                "path": s["path"],
                "user_messages": [flatten(i["text"])[: args.max_user_chars] for i in s["users"]],
            }
            for s in matched[: args.limit]
        ]
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
