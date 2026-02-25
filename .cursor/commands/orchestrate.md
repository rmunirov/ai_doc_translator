# Orchestrate: Full Feature with Planning and Auto-Fixes

Create a full feature with planning, automatic fixes, quality review, and documentation. Task description: `$ARGUMENTS` (required).

## Workflow

Execute phases in order. Delegate to subagents or follow their instructions in `.cursor/agents/`.

### Phase 0 — Planner

- Task: `$ARGUMENTS`
- Planner breaks into numbered subtasks (AUTH-001, AUTH-002, …) with dependencies.
- See [planner.md](../agents/planner.md).
- If scope is unclear, ask the user before proceeding.

### Phase 1..N — For Each Subtask (in dependency order)

1. **Worker** — Implement the subtask (code + tests). See [worker.md](../agents/worker.md).

2. **Test-Runner** — Run tests and linter. See [test-runner.md](../agents/test-runner.md).

3. **If tests fail** — Delegate to Debugger. Max 3 attempts. After each attempt, re-run Test-Runner. If tests still fail after 3 attempts — stop, report to user, do not proceed to the next subtask.

4. **Review** — Check code quality. See [review.md](../agents/review.md).

5. **If Critical issues found** — Delegate to Debugger to fix. Max 2 attempts. After each, re-run Review. Record Suggestion and Nice to have in the report; do not block.

6. **Documenter** — Add docstrings to the subtask code. See [documenter.md](../agents/documenter.md).

### Phase Final — Report

Summarize all subtasks: what was created, test status, Review notes (Suggestion/Nice to have if any), documentation.

## Error Handling

- **Test-Runner**: Max 3 Debugger attempts. On failure — stop and report.
- **Review Critical**: Max 2 Debugger attempts. On failure — note in report, then continue.
- **Planner**: If task cannot be decomposed — ask the user for clarification.

## Report Format

```markdown
## Orchestrate: [feature name]

### Plan
1. AUTH-001: [description] — done
2. AUTH-002: [description] — done
...

### Per subtask
**AUTH-001:**
- Created: `path/to/file.py`, `tests/...`
- Tests: passed X/Y
- Review: Critical resolved / Suggestion noted
- Docstrings: added

**AUTH-002:**
...

### Summary
- Total subtasks: N
- All tests passing
- Remaining Review notes: [list if any]
```

## Constraints

- Do not modify README.md or external documentation unless explicitly asked.
- Execute subtasks strictly in dependency order from the plan.
- Use project conventions from [project-overview.mdc](../rules/project-overview.mdc).

## vs /implement

| Command | Use case |
|---------|----------|
| **/implement** | Single artifact: Worker → Test-Runner → Documenter. Fast. |
| **/orchestrate** | Multi-step feature: Planner → per-subtask cycle with Debugger and Review. Full quality control. |

## Example

- `/orchestrate Добавь систему аутентификации с email/password и OAuth`
