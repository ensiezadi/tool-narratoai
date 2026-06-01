# NarratoAI Task Sync Workflow

Created: 2026-06-01
Last updated: 2026-06-01 17:32:33 CST

NarratoAI uses a small local task store as the source of truth, then syncs
outward when integrations are available.

## Rules

- Local JSON remains authoritative: `storage/task_sync/narratoai_tasks.json`.
- Hermes Kanban is used for agent coordination and status reporting.
- Dida365 is user-facing task management; sync only runs when credentials are available.
- Missing Dida365 credentials must not block local work or Kanban sync.
- Each external task uses a stable idempotency key: `narratoai:<task-id>`.

## Commands

```bash
.venv/bin/python scripts/narrato_task_sync.py init
.venv/bin/python scripts/narrato_task_sync.py report
.venv/bin/python scripts/narrato_task_sync.py sync-kanban
.venv/bin/python scripts/narrato_task_sync.py sync
.venv/bin/python scripts/check_highlight_script.py resource/scripts/part4_benchmark_compact_script.json
```

The latest report is written to:

```text
storage/task_sync/latest_report.md
```

## Highlight Script Quality Gate

Run `scripts/check_highlight_script.py` before rendering a Kaggle-selected
highlight script. It checks timestamp format, segment duration, score threshold,
OST/narration consistency, visual evidence, event distribution, and first-clip
hook strength.

The current `resource/scripts/part4_benchmark_compact_script.json` check passes:
7 clips, 89.0 seconds total, 11.5-14.0 seconds per clip, with 5
`strong_reaction`, 1 `puzzle_progress`, and 1 `death_fail` segment.

## Dida365 Credentials

The script reads credentials from environment variables or
`~/.hermes/credentials/dida365.json`.

Supported environment variables:

- `DIDA365_BASE_URL`
- `DIDA_WRAPPER_API_KEY`
- `DIDA365_API_KEY`

Do not store tokens in project files, wiki notes, or commit history.
