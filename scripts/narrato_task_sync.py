#!/usr/bin/env python3
"""Synchronize NarratoAI project tasks to Hermes Kanban and Dida365."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.task_sync_service import (
    DEFAULT_HERMES_BOARD,
    DEFAULT_HERMES_COMMAND,
    DEFAULT_REPORT_PATH,
    DEFAULT_TASK_STORE,
    build_status_report,
    load_tasks,
    save_tasks,
    seed_default_tasks,
    sync_tasks_to_dida,
    sync_tasks_to_hermes,
    write_status_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="NarratoAI task sync helper")
    parser.add_argument("action", choices=["init", "report", "sync", "sync-kanban", "sync-dida"])
    parser.add_argument("--store", default=DEFAULT_TASK_STORE)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--hermes-command", default=DEFAULT_HERMES_COMMAND)
    parser.add_argument("--board", default=DEFAULT_HERMES_BOARD)
    parser.add_argument("--no-dida", action="store_true", help="Skip Dida365 synchronization")
    args = parser.parse_args()

    if args.action == "init":
        path = seed_default_tasks(args.store)
        print(f"task store ready: {path}")
        return 0

    tasks = load_tasks(args.store)
    if not tasks:
        print("no tasks found; run: scripts/narrato_task_sync.py init")
        return 1

    hermes_result = None
    dida_result = None

    if args.action in {"sync", "sync-kanban"}:
        hermes_result = sync_tasks_to_hermes(
            tasks,
            hermes_command=args.hermes_command,
            board=args.board,
        )
        save_tasks(tasks, args.store)

    if args.action in {"sync", "sync-dida"} and not args.no_dida:
        dida_result = sync_tasks_to_dida(tasks)
        save_tasks(tasks, args.store)

    report = build_status_report(tasks, hermes_result=hermes_result, dida_result=dida_result)
    report_path = write_status_report(report, args.report)
    print(report)
    print(f"report written: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
