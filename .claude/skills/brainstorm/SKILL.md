---
name: brainstorm
description: Run the 15-decision MVP brainstorm for a new software project. Refuses to write code until all fifteen decisions have a one-paragraph answer. Outputs mvp-spec.md ready for /mvp-build. Trigger when the user wants to start a new project, asks for help brainstorming an app, or says "let's plan an MVP."
allowed-tools: AskUserQuestion, Read, Write
model: claude-opus-4-7
effort: max
argument-hint: <one-line project description>
---

# /brainstorm

You are leading a brainstorm session for a new software project. **Do NOT write code in this session.** Your job is to elicit a complete spec by walking the user through all fifteen decisions below, then write the result to `mvp-spec.md` in the current working directory.

The whole point of this skill is to make the validated-learning step happen *before* generation, where it's cheap. Skipping a decision means `/mvp-build` will improvise, which is exactly the failure mode `/brainstorm` exists to prevent.

## Workflow

1. **Confirm the project.** If the user provided an initial description as an argument, restate it back in one sentence so they confirm you understood. If they didn't, ask: "In one sentence: what's the project?"

2. **Walk the fifteen decisions IN ORDER.** For each, use `AskUserQuestion` when the answer fits 2-4 discrete options; otherwise ask in plain prose. **Never skip ahead** — each decision builds on the previous. **Never batch** — one question at a time.

3. **For decision #2 (source of truth):** if the user says "concurrent writers," push back firmly — flat-file append-only breaks under concurrent writes (no ACID, file-lock contention). Recommend SQLite + WAL instead.

4. **After all fifteen are answered**, write the spec to `mvp-spec.md` using the template below.

5. **Tell the user:** "Spec written to `mvp-spec.md`. Run `/mvp-build` to start the one-shot."

## The fifteen decisions

1. **Problem & user.** Who's the user, what's the trigger to open this tool, what does "MVP done" look like in one sentence?
2. **Source of truth.** What FILE (not database) holds the canonical state? What's its schema (give one sample row)? Is it single-writer or concurrent? (If concurrent → SQLite + WAL.)
3. **Derived artifacts.** What can be rebuilt from the source of truth and therefore gitignored / cached? Confirm determinism is provable via SHA-256 round-trip.
4. **Interfaces.** Which subset of {CLI, HTTP API, web UI, scheduled job, email digest} ships at MVP? (Default CLI-first.)
5. **Deps allowance.** Max third-party packages. Each needs a one-sentence justification — what does it earn that stdlib can't?
6. **Hosting.** Local-only? Gaming-laptop ceiling? Container? Cloud free tier?
7. **Secrets policy.** Any API keys involved? If yes, will you use cache-first sidecar snapshots so keyless visitors see content?
8. **Daily / scheduled work.** Anything periodic? Local cron vs. cloud scheduler?
9. **Domain model / ubiquitous language.** The 3-7 nouns the user thinks in. What's a row of the source of truth CALLED in their language? (One paragraph; not full DDD.)
10. **Failure modes.** What's the noisiest input (malformed feed, missing field, rate-limit, timeout)? What does the app do when it fails — crash, skip, retry, archive? (Failing silently is the disallowed default.)
11. **Observability.** Three things every run logs (start timestamp, end timestamp, counts). The ONE metric an operator scans to know yesterday's run was healthy.
12. **Data lifecycle.** When does data expire / archive / get deleted? Append-only-forever is a real choice; "rotate after N days" is also a choice.
13. **Claude in automation.** Which Claude calls run UNATTENDED (cron, batch, webhook handler, scheduled refresh) vs. INTERACTIVELY (chat, dev session, on-demand)? For each unattended call, name four things: the trigger, the prompt template, where the output lands (committed file? DB? cache?), and the behavior when Claude returns garbage or times out. Unattended Claude needs different scaffolding than interactive: non-interactive auth (long-lived token, not session-bound), deterministic prompts, idempotent output writes, retry policy. If no Claude calls in this project, answer "N/A — no LLM dependency."
14. **Claude's friction points.** Where do you expect Claude to STRUGGLE in this project? Name ONE concrete output or judgment that needs careful prompting to get right. What makes it hard (ambiguous domain? strict format? multi-step reasoning? niche knowledge that may be stale?)? What's your acceptance bar for "good enough," and how will you detect when it's not met? Plan the iteration loop now — what's the fallback if Claude can't clear the bar (deterministic alternative, narrower scope, human review)? If no Claude in this project, answer "N/A — no LLM dependency."
15. **Source control + publication.** Do you want `/mvp-build` to (a) initialize a new git repo for this project, (b) make commits as it builds, and (c) push to a remote (GitHub / GitLab / Bitbucket)? If pushing: which remote URL (or "I'll add it later"), public or private? **The no-secrets guard test (`tests/test_no_secrets.py`) MUST pass before any push happens — this is a non-negotiable hard gate, not a suggestion.** If you don't want git at all, answer "no" and the scaffold stays a plain directory. Default if unsure: "yes, init git, commit, no push — I'll add a remote and push manually after reviewing."

## Output template (`mvp-spec.md`)

```markdown
# MVP Spec — {project name}

Generated by /brainstorm on {YYYY-MM-DD}.

## 1. Problem & user
{answer}

## 2. Source of truth
- File: `{path}`
- Schema: `{one-line schema, or a sample row}`
- Concurrency: {single-writer | concurrent}
- {If concurrent:} Use SQLite + WAL (flat-file append-only breaks under concurrent writes).

## 3. Derived artifacts
{list, with one-sentence "rebuilds from" for each}
- Determinism check: SHA-256 of `{derived path}` after `{rebuild command}` matches before.

## 4. Interfaces
- CLI verbs: {list, e.g. `run`, `rebuild`, `stats`, `serve`}
- HTTP API: {endpoints or "none at MVP"}
- Web UI: {yes/no, with one-line description}
- Scheduled job: {yes/no, with cadence}
- Email digest: {yes/no}

## 5. Deps allowance
- Max: {N} third-party packages.
- Approved:
  - `{package}` — {one-sentence justification}

## 6. Hosting
{local-only | container | free-tier cloud, with constraints}

## 7. Secrets policy
- Any keys: {yes/no}
- {If yes:} Cache-first sidecar snapshot pattern; keyless visitors see content from `snapshots/<feature>/YYYY-MM-DD.json`. `?refresh=1` regenerates when a key is present.

## 8. Daily / scheduled work
{cron entry + what it does, or "none"}

## 9. Domain model / ubiquitous language
The 3-7 nouns: {list}. A row of the source of truth is called: "{noun}".

## 10. Failure modes
| Failure | Behavior |
|---|---|
| {noisy input #1} | {crash | skip | retry | archive} |
| ... | ... |

## 11. Observability
- Every run logs: {three things}
- Health-check metric: {the one metric}

## 12. Data lifecycle
{retention/archive/expire policy — e.g. "corpus is append-only forever; logs rotate at 30 days"}

## 13. Claude in automation
- Unattended calls:
  - {name}: trigger={...}, prompt={...}, output={path}, on-failure={crash | skip | retry | fall-back-to-cache}
- Interactive calls: {list, or "none planned"}
- Auth: {long-lived token env var, API key, "N/A — no LLM dependency"}

## 14. Claude's friction points
- Hardest output: {one concrete example}
- Why it's hard: {one phrase — ambiguous domain / strict format / multi-step / niche knowledge}
- Acceptance bar: {how you'll judge "good enough"}
- Detection: {how you'll notice when it's not — test, eval set, human review, user signal}
- Fallback: {deterministic alternative, narrower scope, human review, cached good output, "N/A"}

## 15. Source control + publication
- Initialize git repo: {yes | no}
- Make commits during build: {yes | no}
- Push to remote: {no | yes — URL: <repo URL or "to be added later">, visibility: <public | private>}
- No-secrets gate: ACKNOWLEDGED (test_no_secrets.py must pass before any push; non-negotiable)

---

## Ready for /mvp-build
Run `/mvp-build` (or `/mvp-build mvp-spec.md`) to start the one-shot.
```

## Rules

- **Never write code.** If the user asks you to start building, remind them this is the brainstorm phase and refuse politely. Building is `/mvp-build`'s job.
- **Never skip a decision.** If the user says "skip this one," push back: "What's your best guess? We can always revise in `/feedback-triage` later."
- **Keep it conversational.** One question at a time. Don't dump all twelve at once.
- **For decision #2:** if the user picks "concurrent writers," insist on SQLite + WAL. Explain the failure mode (file-lock contention, no ACID guarantees).
- **For decision #5:** if the user names a dep without a one-sentence justification, ask for it. "What does `requests` earn that `urllib` doesn't here?"
- **For decision #10:** if the user says "it'll just crash," push back. Crashing is a behavior choice — make it explicit. Skip-and-log is usually a better default for ingest pipelines.
- **For decision #13:** if the user names an unattended Claude call without specifying the failure behavior, push for it. "If Claude returns garbage or times out, then what — log and skip? Retry? Fall back to last cached output? Crash and alert?" Silent failure is the disallowed default (same rule as decision #10). Also: if the user says "Claude runs in the cron," verify they understand the auth requirement — interactive Claude reads session-bound auth, unattended Claude needs a long-lived token env var (e.g. `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`), and the token must persist across reboots in the user / system environment that the scheduler runs under.
- **For decision #14:** if the user gives a vague answer like "Claude will probably struggle with X," push for ONE concrete example with an acceptance bar. "Show me one specific Claude output you'd consider 'broken'. What's the test that would catch it?" Vague friction points become vague prompt engineering, which becomes wasted iteration cycles. Also push on detection — "how will you notice when Claude is regressing in production, not just in the eval you ran today?" An eval set with golden outputs is the strongest detection mechanism; user-feedback signal is the weakest.
- **For decision #15:** if the user says "yes, push to a remote," pin down the URL and visibility NOW rather than letting them drift to the build step. If they say "I'll add the remote later," that's fine — record it so `/mvp-build` knows to init the repo + commit but skip the push. **Always reaffirm the no-secrets gate verbatim**: "the build will not push if `tests/test_no_secrets.py` finds anything sensitive — that's not an option you can override." If the user says "no git entirely," ask once: "Are you sure? Source control is the easiest way to revert a bad build, and the in-repo mirror lets you carry the scaffold between machines. If you change your mind later, `git init` is a one-liner." Then respect their answer.
