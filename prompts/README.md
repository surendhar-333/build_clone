# prompts/ — handoff packs for other models

Purpose: this repo is worked on by several AI models. Claude does the *thinking*
(architecture, review, interview prep). The heavy code generation is handed to
whichever model has spare quota (Gemini, ox-alpha/stealth, Copilot, Jules).

## How to use a task pack

1. Open the task file, e.g. `prompts/TASK-01-....md`.
2. Paste `prompts/00-CONTEXT.md` **first** into the new model's session.
3. Then paste the task file.
4. The task file states its own acceptance criteria. Do not accept output that
   fails them.
5. Commit the result on a branch named in the task file, then have Claude review
   the diff before merging.

## Rules for whoever writes code here

- **Notebooks are Databricks source format.** First line must be
  `# Databricks notebook source`, cells separated by `# COMMAND ----------`,
  markdown cells via `# MAGIC %md`. Breaking this stops the notebook rendering
  in the workspace.
- **Do not change the table/column contract** without updating every phase that
  reads it. The contract is documented in `docs/PROJECT_STATE.md`.
- **Config constants live at the top of each notebook** (catalog / schema /
  volume / paths). Keep them identical across phases.
- **Idempotent DDL only**: `CREATE ... IF NOT EXISTS`, `mode("overwrite")`, or
  MERGE. Every notebook must survive being re-run.
- **No new external dependencies** — Free Edition is serverless with restricted
  outbound internet. Standard PySpark + Delta only.
- **No cloud-specific paths.** Storage is a Unity Catalog volume today. If a task
  is about moving to S3, it will say so explicitly.

## Task index

| Task | What | Status |
|---|---|---|
| `00-CONTEXT.md` | Paste-first context block. Not a task. | — |
| `TASK-02-fix-generator.md` | Make all 6 `match_status` values + both dispositions reachable; seed DQ rejects; remove the `coalesce(1)` scale ceiling | ready |
| `TASK-03-case-identity.md` | Stable hash-based `case_id`, `MERGE` with case lifecycle, null-safe status | ready (needs TASK-02) |
| `../jules/tasks/TASK-01-recon-tests.md` | Extract recon logic to `src/` + pytest suite | ready (pre-existing) |
| `MOCK-INTERVIEW.md` | Mock interviewer prompt. Not a code task. | — |

Do TASK-02 before TASK-03 — TASK-03's acceptance criteria need all six match
statuses actually firing.

## Why this exists

The owner did not hand-write the original notebooks and is now taking ownership
of them for interviews. Therefore: **every task pack must require the generated
code to be explainable.** Prefer readable PySpark over clever one-liners, and
require inline comments that state *why*, not *what*.
