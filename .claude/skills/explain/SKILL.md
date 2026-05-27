---
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion
description: Explain a part of the AI Landscape codebase (or whatever Python project the user is in) using a deterministic structural report as the foundation. Trigger when the user says "explain X", "how does X work", "walk me through X", "what does X module do", "show me the system", or asks to understand critical paths, dependencies, tests, or trust signals of a code component.
model: sonnet
argument-hint: <module name | "system" | CLI verb | "/api/path">
---

# /explain

The user wants to understand a part of the codebase well enough to trust it. Your job is to ground the explanation in **deterministic structural facts**, then add narrative on top — never the other way around.

The project ships a CLI verb that does the structural work for you: `python -m ailandscape.cli explain`. **Run it first; explain second.** Do not invent dependency relationships, test counts, or commit history from your own knowledge — the CLI is the source of truth for those.

## Workflow

### Step 1 — Resolve the target

If the user named a target (module, CLI verb, API path, or "system"), use it.

If they didn't, ask via `AskUserQuestion`:

> "What do you want me to explain?"
> - "System overview (32 modules at a glance)" → system
> - "A specific module" → ask which
> - "A CLI verb" → ask which
> - "An API endpoint" → ask which

For the system path, set `$TARGET = ""` (no argument; the CLI defaults to system).

### Step 2 — Run the deterministic report

```bash
python -m ailandscape.cli explain $TARGET
```

(If `$TARGET` is "system" or empty, just `python -m ailandscape.cli explain` — no positional.)

Read the output carefully. It contains: target name, file path, description, public definitions with signatures, internal/external imports, reverse deps, CLI verbs that use this module, API endpoints that use this module, test files + counts, trust signals (docstring presence, TODO markers, last commit).

If the CLI errors (e.g. "no module named 'X'"), do NOT guess at a fix. Ask the user to clarify the target or list candidates by running `python -m ailandscape.cli explain` (system overview lists every module).

### Step 3 — Add narrative on top

Below the structural report, write 4-8 short paragraphs:

1. **What this part does and why it exists.** Use the description + public signatures. If the docstring is sparse, name that.
2. **How it fits in.** Reverse deps + CLI verbs + API endpoints together tell you who depends on this. Explain the consumers in plain language.
3. **Critical paths.** Trace the most important flow: e.g. "the daily cron's path through this module is `pipeline.run → generate_daily_syntheses → claude_cli.summarize`." For system-level, name the 2-3 highest-leverage flows (daily-cron path, web-app path).
4. **What the trust signals tell us.** If `test coverage = 0 files`, say so explicitly. If `TODO markers > 0`, mention them. If `last commit` is months old, flag it. If they all look healthy, say "looks healthy."
5. **One concrete next step.** Either "verify X by doing Y" or "consider improving Z" or "no obvious follow-up." Be specific. Vague suggestions are worthless.

### Step 4 — Offer deeper dives

After the narrative, offer 2-3 follow-ups the user might want:

> Want me to:
> - Walk the actual code in `<path>:<entry-function>`?
> - Explain `<reverse-dep module>` next (the largest caller)?
> - Trace the full daily-cron critical path through every module it touches?
> - Show what changed in the last commit (`<sha>`)?

Pick what makes sense for the target — don't list all of them.

## Rules

- **Run the CLI first.** Always. No exceptions. The structural facts ground everything else.
- **Do not invent dependency or test data.** If the CLI says "1 test file, 13 tests," repeat that — don't speculate that "there are probably more."
- **Be specific about file paths.** Cite `path/to/file.py:42` not "somewhere in the synthesis module."
- **Name gaps explicitly.** If the trust signals show no tests / no docstring / years-old commit, surface it. The whole point of `/explain` is to help the user *decide whether to trust* — hiding bad signals defeats the purpose.
- **Keep the narrative shorter than the structural output.** The CLI did the heavy lifting; you're adding interpretation, not re-listing.
- **For "system" target, the narrative is different.** Don't go module-by-module. Instead: name the 3-5 highest-leverage modules, the 2-3 critical paths (daily cron, web app, anything else), the overall health (test coverage by module, recent commit activity, any concerning gaps).
- **If `--narrative` is something the user wants from the CLI itself** (i.e. they want Claude to write the narrative inline in the CLI's output, suitable for capture / log), tell them to run `python -m ailandscape.cli explain <target> --narrative` — that path uses the same Claude transport but stays inside the CLI's tooling. This skill is for interactive, deeper conversations.
- **Do not commit anything** unless the user explicitly asks. Reading code is read-only by default.

## When NOT to use this skill

- The user asks "what's in this file" / "show me the file" — that's just `Read`, no CLI needed.
- The user asks to make changes — use `Edit`/`Write`, not `/explain`.
- The user wants a system diagram — that's a separate thing; this skill produces text, not images.
- The user is in a project that ISN'T AI Landscape (no `ailandscape/cli.py`) — fall back to running `Grep`/`Glob`/`Read` directly and explaining from primary sources. The skill assumes the explain CLI verb exists.
