---
name: feedback-triage
description: Convert numbered user feedback notes about a running MVP into a triaged action list (fix-now / fix-next / TODO) with file paths and minimal-diff plans. Runs after the user has touched the MVP. Trigger when the user pastes numbered notes about what's wrong with the app, says "here's feedback from using the app," or asks to triage feedback.
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, AskUserQuestion
model: claude-opus-4-7
effort: max
argument-hint: [paste of numbered notes, or path to a notes file]
---

# /feedback-triage

The user has run the MVP, made notes, and pasted them here (or pointed you at a file with them). Your job is to convert the notes into a triaged action list with concrete file paths and minimum-viable diffs. **You do not execute fixes in this skill unless the user explicitly approves the fix-now batch at the end.**

## Workflow

### Step 1 — Get the notes

1. Read the argument. If it looks like prose (probably pasted notes), use it directly. If it looks like a path, read the file.
2. If notes aren't numbered (1., 2., 3., ...), ask the user to number them. Stable references matter — the triage will cite "note 3" as a fix-now item.
3. If notes are very long (50+ items), ask the user to either:
   - Pick the top 10 to focus on this session, OR
   - Confirm they want all of them triaged (set expectation that the doc will be long).

### Step 2 — Triage each note

For each note, classify into one bucket:

- **fix-now** — small (under ~20 lines), single file, no behavior change beyond the user's stated wish. Examples: typo, wrong color, off-by-one, missing CLI flag, broken link.
- **fix-next** — medium (multi-file or multi-commit, but you know exactly what to do). Examples: add a new CLI verb, add a new API endpoint, refactor that the user explicitly asked for.
- **TODO** — large or uncertain. Examples: "make it faster" without a budget, "rewrite the layout engine," anything needing its own `/brainstorm`-style spec.

**When classification is genuinely ambiguous, ask via `AskUserQuestion`.** Do not guess. Wrong triage is worse than asking.

### Step 3 — Locate the relevant files

For each note, search the codebase (`Grep` / `Glob`) to find the file(s) most likely affected. Cite the file:line in the triage. If you can't find the file:
- Ask the user where it lives, OR
- If the note describes a *missing* feature (no file exists yet), say so explicitly in the triage.

### Step 4 — Write the triage

Write to `feedback-triage-<YYYY-MM-DD>.md` in the current directory. Use the template below.

If a triage file for today already exists, append to it under a new `## Session N` heading. Multiple feedback sessions in one day are normal.

### Step 5 — Offer to execute fix-now inline

After writing the triage, ask the user:

> "fix-now bucket has {N} items totaling ~{M} lines across {K} files. Want me to execute them now? I'll commit one per item (or one batch commit if they're trivially related), then re-run the test suite."

If they say yes:
- Execute each fix-now item.
- Run the test suite after each commit (or after the batch).
- Report which committed cleanly and which failed.

If they say no:
- The triage file is the deliverable. Stop here.

### Step 6 — fix-next and TODO are NOT executed in this skill

For fix-next items, the triage's one-paragraph implementation note is sufficient — the user executes them later (possibly in another session).

For TODO items, propose a one-line entry suitable for `TODO.txt` (most-recent at top, following the project's convention). Offer to add them.

## Output template (`feedback-triage-YYYY-MM-DD.md`)

```markdown
# Feedback triage — {YYYY-MM-DD}

## Session 1

### Source notes
> {paste of the user's numbered notes — verbatim}

### fix-now ({N} items)

#### 1. {one-line summary of note 1}
- **File**: `path/to/file.py:42`
- **Change**: {minimal diff in prose — e.g. "rename 'Hye' to 'Hype' in the modal title"}
- **Test**: {what test pins this — existing test name, or "new test in tests/test_<module>.py"}
- **Risk**: {none | low | medium — usually "none" for fix-now}

#### 2. ...

### fix-next ({N} items)

#### {note number}. {one-line summary}
- **Files**: `path/to/foo.py`, `path/to/bar.py`
- **Plan**: {one paragraph explaining the change}
- **Test**: {what tests to add or update}
- **Estimated commit count**: {N}
- **Depends on**: {other note numbers, or "nothing"}

### TODO ({N} items)

| Note # | Item | Why it's TODO |
|---|------|---------------|
| {N} | {one-line, grep-friendly} | {usually "no budget / no acceptance criteria" or "needs its own /brainstorm"} |

### Recommended next step
{one of:}
- "Approve the fix-now batch (~{M} lines, {K} files) and I'll execute now."
- "Open /brainstorm for the {topic} TODO."
- "Both: I'll execute fix-now, then we'll /brainstorm {topic}."
- "Nothing actionable — these are all uncertain; pick one to drill into."
```

## Rules

- **Always cite file:line for fix-now items.** If you can't find the file, ask — don't guess.
- **The fix-now bucket exists for immediate wins.** Never classify something as fix-now if it touches more than one file OR risks regression OR you're not sure of the diff.
- **If fix-now has more than 8 items, your threshold is too loose.** Push half to fix-next.
- **Don't execute fixes without explicit user approval.** The triage IS the artifact; execution is a separate go-ahead.
- **TODO one-liners must be grep-friendly.** Noun phrase, no fluff. "Layout engine rewrite for >5k nodes" beats "It would be nice if the layout was faster on big graphs."
- **If the user's notes are vague** ("the colors are weird"), ask one clarifying question before classifying. Vague feedback usually becomes a TODO; specific feedback can become fix-now.
- **If a note describes a regression** (something used to work and now doesn't), surface it at the top of the triage as **REGRESSION** — it gets priority over everything else.
- **After execution**, write a short summary back to the user: "Executed N fix-now items, all tests still passing. Ready for `git push` if you want."
