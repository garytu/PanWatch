---
name: execute-task
description: Execute a ready jev-trade implementation task by assigning the code work to a GPT-6 Luna/max subagent, then independently reviewing, fixing, verifying, committing, and completing the task. Use when the user asks to execute or complete a task through this delegated workflow; not for a review-only request.
---

# Execute a task

Use this skill for one implementation card at a time in this repository. The parent agent coordinates and completes the card; one GPT-6 Luna/max subagent owns implementation. A UI click alone does not identify or authorize a task: use the task ID in the user's message, or the board's sole ready next task when the user clearly asks to run the next task.

## Assign

1. Read `AGENTS.md`, `docs/plan/README.md`, the full card, and its dependencies. Confirm the board and card both say `ready`, every dependency is completed in both places, and `git status` is understood. Resolve material ambiguity before implementation. Respect the current user decision over older repository workflow text.
2. In the current checkout, set the board and card to `in_progress`, record the worker as owner, and commit that coordination change. Do not create or switch a worktree. Preserve unrelated changes.
3. Spawn one implementation subagent with `model: "gpt-6-luna"` and `reasoning_effort: "max"`. A model override needs `fork_turns: "none"` or a finite turn count; provide the card ID, checkout, context paths, constraints, and expected handoff explicitly. If dispatch fails or the requested model/effort is unavailable, restore the board/card to `ready` and report it rather than silently substituting another model or leaving the task assigned.
4. Give the worker sole ownership of the card's allowed implementation paths. It reads relevant code and designs, implements the card, runs the card's verification, updates only its card Progress checkpoint and Handoff, and reports `review` or `blocked`. It does not edit the board/status/owner fields or commit. The parent avoids simultaneous edits to worker-owned files.

## Review and complete

1. Wait for the worker's report. Read the complete diff, tests, and handoff against the accepted ADRs/designs, card acceptance criteria, and relevant callers. Treat the worker's reported tests as evidence to check, not approval. Fix defects and add targeted regression coverage where useful. Do not mark a card completed while a material contract conflict remains unresolved.
2. Run the card's required verification and the most relevant integration checks after any fixes. Record exact results and remaining limits. Check the full staged diff, including new files, for whitespace and unintended paths.
3. If acceptance passes, mark the card and board `completed`, update its handoff with coordinator fixes and final results, recompute dependent cards' `ready` status, and commit the reviewed result in the current checkout. Confirm a clean `git status`.
4. Tell the user the task ID, implementation/review outcome, material fixes, verification results, commit, and next ready task. If blocked, leave an accurate checkpoint and report the concrete blocker rather than claiming completion.

Follow the board's coordinator/worker ownership and source-of-truth rules throughout. Do not start the next implementation card without a separate user request.
