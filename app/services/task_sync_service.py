"""Local task synchronization helpers for NarratoAI project work."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import config


DEFAULT_TASK_STORE = "storage/task_sync/narratoai_tasks.json"
DEFAULT_REPORT_PATH = "storage/task_sync/latest_report.md"
DEFAULT_HERMES_BOARD = "narratoai"
DEFAULT_HERMES_COMMAND = "hermes"
DIDA_Q2_PROJECT_ID = "6a0ea4c4e4b0e871931e553a"


@dataclass
class TaskItem:
    id: str
    title: str
    body: str = ""
    status: str = "todo"
    priority: int = 0
    owner: str = "codex"
    hermes_task_id: str = ""
    dida_task_id: str = ""
    updated_at: str = ""
    created_at: str = ""
    tags: list[str] = field(default_factory=list)

    def normalize(self) -> "TaskItem":
        now = datetime.now().isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = now
        self.updated_at = now
        self.status = self.status or "todo"
        self.owner = self.owner or "codex"
        return self


def project_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(config.root_dir) / candidate
    candidate = candidate.resolve()
    root = Path(config.root_dir).resolve()
    if os.path.commonpath([str(root), str(candidate)]) != str(root):
        raise ValueError(f"path must stay inside project root: {path}")
    return candidate


def load_tasks(store_path: str = DEFAULT_TASK_STORE) -> list[TaskItem]:
    path = project_path(store_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"task store must be a JSON list: {path}")
    return [TaskItem(**item).normalize() for item in data if isinstance(item, dict)]


def save_tasks(tasks: list[TaskItem], store_path: str = DEFAULT_TASK_STORE) -> Path:
    path = project_path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(task.normalize()) for task in tasks]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def seed_default_tasks(store_path: str = DEFAULT_TASK_STORE) -> Path:
    existing = load_tasks(store_path)
    if existing:
        return project_path(store_path)

    tasks = [
        TaskItem(
            id="ui-hermes-agent-panel",
            title="完善本地 Hermes / 多 Agent UI 配置面板",
            body="让 Streamlit 基础设置支持 Hermes CLI、副手规则、wiki 沉淀和安全保存。",
            status="done",
            priority=90,
            tags=["frontend", "hermes"],
        ),
        TaskItem(
            id="task-sync-kanban-dida",
            title="建立 NarratoAI 任务同步与报告闭环",
            body="本地任务清单作为事实源，同步到 Hermes Kanban；Dida365 有凭据时同步，缺凭据时跳过并报告。",
            status="todo",
            priority=85,
            tags=["ops", "kanban", "dida365"],
        ),
        TaskItem(
            id="kaggle-highlight-review",
            title="复核 Kaggle 精选剪辑到最终成片的鲁棒性",
            body="确认 compact script、FFmpeg 临时文件校验、WebUI 参数和最终导出流程稳定。",
            status="todo",
            priority=80,
            tags=["kaggle", "video"],
        ),
    ]
    return save_tasks(tasks, store_path)


def run_command(command: list[str], cwd: str | None = None, timeout: int = 60) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or config.root_dir,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)
    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    if completed.returncode != 0:
        return False, error or output or f"exit code {completed.returncode}"
    return True, output


def hermes_available(hermes_command: str = DEFAULT_HERMES_COMMAND) -> bool:
    parts = hermes_command.split()
    return bool(parts and (os.path.sep in parts[0] or shutil.which(parts[0])))


def ensure_hermes_board(
    hermes_command: str = DEFAULT_HERMES_COMMAND,
    board: str = DEFAULT_HERMES_BOARD,
) -> tuple[bool, str]:
    if not hermes_available(hermes_command):
        return False, f"Hermes command not found: {hermes_command}"
    command = hermes_command.split()
    ok, output = run_command([*command, "kanban", "boards", "list", "--json"], cwd=config.root_dir, timeout=30)
    if ok:
        try:
            boards = json.loads(output)
            if any(isinstance(item, dict) and item.get("slug") == board for item in boards):
                return True, f"board exists: {board}"
        except json.JSONDecodeError:
            pass

    ok, output = run_command(
        [
            *command,
            "kanban",
            "boards",
            "create",
            board,
            "--name",
            "NarratoAI",
            "--description",
            "NarratoAI project implementation and review tasks",
            "--default-workdir",
            config.root_dir,
        ],
        cwd=config.root_dir,
        timeout=30,
    )
    if ok:
        return True, output
    return False, output


def sync_tasks_to_hermes(
    tasks: list[TaskItem],
    hermes_command: str = DEFAULT_HERMES_COMMAND,
    board: str = DEFAULT_HERMES_BOARD,
) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "created_or_seen": 0, "done_marked": 0, "errors": []}
    ok, message = ensure_hermes_board(hermes_command, board)
    if not ok:
        result["errors"].append(message)
        return result

    command = hermes_command.split()
    for task in tasks:
        body = task.body or task.title
        ok, output = run_command(
            [
                *command,
                "kanban",
                "--board",
                board,
                "create",
                task.title,
                "--body",
                body,
                "--assignee",
                task.owner,
                "--priority",
                str(task.priority),
                "--workspace",
                f"dir:{config.root_dir}",
                "--idempotency-key",
                f"narratoai:{task.id}",
                "--json",
            ],
            cwd=config.root_dir,
            timeout=45,
        )
        if not ok:
            result["errors"].append(f"{task.id}: {output}")
            continue
        result["created_or_seen"] += 1
        try:
            parsed = json.loads(output)
            task.hermes_task_id = str(parsed.get("id") or parsed.get("task_id") or task.hermes_task_id)
        except json.JSONDecodeError:
            pass

        if task.status == "done" and task.hermes_task_id:
            ok, output = run_command(
                [
                    *command,
                    "kanban",
                    "--board",
                    board,
                    "complete",
                    task.hermes_task_id,
                    "--result",
                    "Completed from NarratoAI task sync.",
                    "--summary",
                    task.body or task.title,
                ],
                cwd=config.root_dir,
                timeout=45,
            )
            if ok:
                result["done_marked"] += 1
            else:
                result["errors"].append(f"{task.id} complete: {output}")

    result["ok"] = not result["errors"]
    return result


def load_dida_credentials() -> tuple[str, str] | None:
    base_url = (
        os.getenv("DIDA365_BASE_URL")
        or os.getenv("DIDA_BASE_URL")
        or "http://100.82.66.19:8080"
    ).rstrip("/")
    api_key = os.getenv("DIDA_WRAPPER_API_KEY") or os.getenv("DIDA365_API_KEY") or ""
    candidates = [
        Path.home() / ".hermes" / "credentials" / "dida365.json",
        Path.home() / ".hermes" / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            base_url = str(data.get("base_url") or data.get("base") or base_url).rstrip("/")
            api_key = str(data.get("api_key") or data.get("key") or api_key)
        else:
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" not in raw or raw.lstrip().startswith("#"):
                    continue
                key, value = raw.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                if key in {"DIDA365_BASE_URL", "DIDA_BASE_URL"}:
                    base_url = value.rstrip("/")
                elif key in {"DIDA_WRAPPER_API_KEY", "DIDA365_API_KEY"}:
                    api_key = value
    if not api_key:
        return None
    return base_url, api_key


def sync_tasks_to_dida(tasks: list[TaskItem], project_id: str = DIDA_Q2_PROJECT_ID) -> dict[str, Any]:
    creds = load_dida_credentials()
    if not creds:
        return {"ok": False, "skipped": True, "reason": "missing Dida365 credentials"}

    base_url, api_key = creds
    result: dict[str, Any] = {"ok": False, "created": 0, "errors": []}
    for task in tasks:
        if task.status == "done" or task.dida_task_id:
            continue
        payload = json.dumps(
            {
                "projectId": project_id,
                "title": f"NarratoAI: {task.title}",
                "content": task.body,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/tasks",
            data=payload,
            method="POST",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            result["errors"].append(f"{task.id}: {exc}")
            continue
        task.dida_task_id = str(data.get("id") or data.get("taskId") or data.get("_id") or "")
        result["created"] += 1
    result["ok"] = not result["errors"]
    return result


def build_status_report(
    tasks: list[TaskItem],
    hermes_result: dict[str, Any] | None = None,
    dida_result: dict[str, Any] | None = None,
) -> str:
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1

    lines = [
        "# NarratoAI Task Sync Report",
        "",
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"- total_tasks: {len(tasks)}",
        f"- status_counts: {json.dumps(counts, ensure_ascii=False)}",
        "",
        "## Tasks",
    ]
    for task in sorted(tasks, key=lambda item: (-item.priority, item.id)):
        external = []
        if task.hermes_task_id:
            external.append(f"kanban={task.hermes_task_id}")
        if task.dida_task_id:
            external.append(f"dida={task.dida_task_id}")
        suffix = f" ({', '.join(external)})" if external else ""
        lines.append(f"- [{task.status}] P{task.priority} {task.id}: {task.title}{suffix}")

    if hermes_result is not None:
        lines.extend(["", "## Hermes Kanban", f"- result: {summarize_sync_result(hermes_result)}"])
    if dida_result is not None:
        lines.extend(["", "## Dida365", f"- result: {summarize_sync_result(dida_result)}"])
    return "\n".join(lines) + "\n"


def write_status_report(report: str, path: str = DEFAULT_REPORT_PATH) -> Path:
    report_path = project_path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return report_path


def summarize_sync_result(result: dict[str, Any], max_error_chars: int = 300) -> str:
    """Return a compact sync result for human reports."""
    compact: dict[str, Any] = {}
    for key, value in result.items():
        if key != "errors":
            compact[key] = value
            continue
        errors = value if isinstance(value, list) else [value]
        compact["errors"] = [
            str(error).replace("\n", " ")[:max_error_chars]
            for error in errors[:3]
        ]
        if len(errors) > 3:
            compact["error_count"] = len(errors)
    return json.dumps(compact, ensure_ascii=False)
