# Git Commit & Push

Create a semantic commit for all current changes and optionally push to the remote.

## Steps

### 1. Inspect Changes

Run the following commands to understand what has changed:

```bash
git status
git diff
git diff --staged
```

Identify all modified, added, and deleted files. Group related changes to determine the appropriate commit scope.

### 2. Determine Commit Type

Choose the type based on the nature of the changes:

| Type | When to use |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation only (README, PRD, .mdc rules, comments) |
| `style` | Formatting, whitespace — no logic change |
| `refactor` | Code restructuring without new features or bug fixes |
| `test` | Adding or updating tests |
| `chore` | Dependencies, configs, CI, tooling, build scripts |
| `perf` | Performance improvement |

### 3. Determine Scope (optional)

The scope describes the area of the codebase affected. Use short, lowercase names:

- `agent` — changes in `app/agent/`
- `tools` — changes in `app/tools/`
- `api` — changes in `app/api/`
- `services` — changes in `app/services/`
- `models` — changes in `app/models/`
- `ui` — changes in `app/templates/` or `app/static/`
- `rules` — changes in `.cursor/rules/`
- `deps` — dependency changes (`requirements.txt`, etc.)
- Omit scope if changes span multiple areas

### 4. Write the Commit Message

Format: `<type>(<scope>): <subject>`

Rules for the subject:
- Imperative mood: "add", "fix", "update" — not "added", "fixed"
- No capital letter at start
- No period at the end
- Max 72 characters
- In English

Good examples:
```
feat(agent): add BaseAgent class with sync and async run methods
fix(tools): return error as string instead of raising exception
docs(rules): add tool-patterns cursor rule
refactor(api): extract translation router to separate module
chore(deps): add langchain-gigachat to requirements.txt
```

### 5. Stage and Commit

```bash
# Stage all changes (or specific files if needed)
git add .

# Create the commit
git commit -m "<type>(<scope>): <subject>"
```

If there is a longer explanation needed, use a body:
```bash
git commit -m "$(cat <<'EOF'
feat(agent): add BaseAgent class with sync and async run methods

Introduces abstract BaseAgent with SYSTEM_PROMPT class constant,
Pydantic Input/Output models, and both run() / arun() methods.
All concrete agents must inherit from BaseAgent.
EOF
)"
```

### 6. Ask Before Pushing

**IMPORTANT: Always ask the user for confirmation before running `git push`.**

Present the commit that was just created:
```
Commit created: feat(agent): add BaseAgent class with sync and async run methods

Do you want to push this commit to the remote? (yes / no)
```

Wait for explicit confirmation. Do NOT push automatically.

### 7. Push (only after confirmation)

```bash
git push
```

If the branch has no upstream yet:
```bash
git push -u origin <branch-name>
```

---

## Safety Rules

- **Never force push** (`--force`, `-f`) unless the user explicitly requests it
- **Never amend** a commit that has already been pushed to remote
- **Never skip hooks** (`--no-verify`) unless the user explicitly requests it
- **Never commit** files that likely contain secrets (`.env`, credentials, API keys)
- If unsure about the commit scope or type — ask the user before committing
