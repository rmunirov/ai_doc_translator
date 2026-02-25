# Implement: Quick Component, Function, or Endpoint

Create a component, function, or endpoint with code, tests, and documentation in one flow. Task description: `$ARGUMENTS` (required).

## Workflow

Execute these phases in order. Delegate to the corresponding subagent or follow their instructions in `.cursor/agents/`.

### Phase 1 — Worker

- Task: `Implement: $ARGUMENTS`
- Worker creates code and tests following project conventions.
- See [worker.md](../agents/worker.md) for workflow.
- If scope is unclear, ask the user before proceeding.

### Phase 2 — Test-Runner

- Run tests and linter on created/changed files.
- Fix failures when possible.
- See [test-runner.md](../agents/test-runner.md) for workflow.
- **Stop and report to the user** if tests cannot be fixed. Do not proceed to Phase 3.

### Phase 3 — Documenter

- Add Google-style docstrings and module docs to the new code only.
- See [documenter.md](../agents/documenter.md) for workflow.

### Phase 4 — Report

Produce a summary in this format:

```markdown
## Implement: [brief task name]

### Created
- `path/to/file.py` — [description]
- `tests/test_file.py` — [description]

### Verification
- Tests: passed X / total Y
- Lint/type: [status]

### Documentation
- Docstrings added to: [files/functions]
```

## Constraints

- Do not modify README.md or external documentation unless explicitly asked.
- Strict order: Worker → Test-Runner → Documenter.
- Use project conventions from [project-overview.mdc](../rules/project-overview.mdc).

## Examples

- `/implement Добавь FastAPI эндпоинт GET /health для проверки статуса`
- `/implement Реализуй функцию parse_config в app/services/config.py`
