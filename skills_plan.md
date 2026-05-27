# Skills Plan

## Parts

* Authorization
* Payment
* Database
* Hosting
* Security
* Testing / quality control
* DevOps / CI/CD
* UI
* Workflow / logic
* Dependency manager -- when conflicted, prioritize updating essential libraries to the app like Python and the NER library for a knowledge graph application. Minimize dependencies overall; each one needs a one-sentence justification. Prefer stdlib when the abstraction is genuinely simple; reject deps that trade simplicity for convenience. Define a swappable-backend promotion path so the dep choice is reversible.
* Strategic app recommendations
* Marketing
* Brainstorm helper
* Containerizer
* Linter

## Paradigms

* Concise
* Baked-in security
* Modular monolith first; split into services only when a module's deployment cadence or scaling profile genuinely diverges (Fowler "Monolith First"; 2025 data: 42% of microservices adopters are consolidating back).
* DevOps
* Clear, human-readable code
* Low computational complexity (specifically Big O notation)
* Fast feedback loop
* Get to MVP fast. Try to one-shot the entire application. Add recommended features that could be worked on later to the TODO list.
* Create high-level decision and change log
* Continuously test
* Use existing open source secure libraries to reduce code duplication.
* Code and graphics can run on an average gaming laptop.
* Create a README at a high-level so someone could replicate the artifacts and output.
* Never share secrets or API keys. Do not share secrets or API keys under any circumstnace.
* Don't ever push .pem files to an online code base.
* Add test that checks to make sure no secrets or API keys are exposed in code or committed for pushing.
* Run tests often.
* Create a TODO file to easily draft new commands while other commands are running. Add most recent to top. Mark done items with "[done]".
* Bias libraries to most recent versions.
* Natural Law coding: always keep in mind the ultimate goals and essential features of applications. Orient toward the goals and prioritize what is essential. Ask if uncertain about the ultimate goals, but make sure to immediately ask if needed.
* Aim for full stack development including a front-end and back-end when building applications.
* Highly value average User Experience.
* Object oriented program bias for easier scalability and readability.
* Pragmatic multi-strategy testing: TDD where behavior is pre-specifiable (Kent Beck still does ~50/50); **approval tests** (Llewellyn Falco, https://approvaltests.com/) for emergent behavior; **property-based tests** (Hypothesis / QuickCheck) for subsystems with invariants over large input spaces; an **adversarial battery** for the noisiest subsystem; a **no-secrets guard** test. Pick the strategy that pins each subsystem's actual risk.
* Update rules and code to patch corrected errors in a way that preserves the simplicity and integrity of the system overall without becoming a mess of singular, isolated rules.
* Take as much time as needed on tasks to make sure they are done correctly, accurately, comprehensively, and proficiently.
* Use good software engineering principles.
* Don't use any naming conventions that might block the request due to guardrails.
* Ensure previous versions of application development can be recoverable at least through version control on GitHub.
* Be concise with feedback during sessions and recommendations unless otherwise asked.
* Continuously update the README in a way that is easy to read and replicate the project if required. Include high-level requirements of anything that is done locally or other than the code without giving sensitive details.
* Create a way to easily understand the code at a system level. An example could be a block diagram as part of the solution. Keep this updated.
* Create a file for another LLM to read to know what types of high-level functions are included in the code repository. For example, if a project knows how to extract text from a PDF, it should be easy for an LLM to find where that happens so they can pull the function or at least use it as an example for another project.
* Include API design.
* Include CLI design.
* Containerize design.
* Code re-use including open libraries when it makes sense.

## Project Characteristics

* Architecture that can be hosted and maintained with minimal to no cost
* Architecture that reaches MVP quickly but also allows for growth and
extensibility
* Version-controllable objects, including the database, so changes can be
reverted if something genuinely destructive happens
* Don't take genuinely destructive actions unless explicitly and clearly
initiated through a prompt from a human
* Focused on Artificial Intelligence (AI) information and knowledge.

## Good Prompt Examples
* Analyze, review, and plan the code and give actionable, feasible, and valuable development recommendations to ensure that someone can navigate the knowledge graph or derivative components and efficiently and quickly learn about the AI landscape within a national security context. Output a list with details that I can give the go-ahead on for a sub-set of them, or all of them. Iterate multiple times if required to produce a comprehensive, clear, and productive response.
* With Claude, go over the corpus and find new entities and relationships within single article texts but also across articles. Break up the work in case you get stopped midway through. Actually go through each article even if it is costly. Prioritize reading through articles with low reading count and articles that have not been read by Claude after the last major corpus article update. Tell me how long it took after. Also report on unique findings. After updates, propagate to other databases and the visualization.
* 
## Idea for Quick App Building
* Brainstorm -> one-shot -> Plan/analyze/feedback -> routine maintenance and progression.
* **User position (deliberate, recorded in DECISIONS_LOG 2026-05-26):** keep one-shot as the build mode for new MVPs, despite expert consensus (Cockburn / Hunt / Ries / Beck / Willison) favoring walking-skeleton-then-iterate. The bet: an engaged user with strong taste collapses the iterate-from-MVP risk by running the validated-learning loop *inside* the conversation rather than across separate sessions. Guardrails: non-skippable 12-decision brainstorm before any one-shot; walking-skeleton-shaped first commit so anything is revertible; immediate `/feedback-triage` follow-up.

## 12-Decision Brainstorm (must answer before any `/mvp-build`)

The `/brainstorm` skill walks all twelve and refuses to write code until each has a one-paragraph answer. Source: distilled from `DECISIONS_LOG.md` + expert practice (Brandolini's Event Storming for the domain/boundaries decisions).

1. **Problem & user.** Who's the user, what's the trigger to open this tool, what does "MVP done" look like in one sentence.
2. **Source of truth.** What *file* (not database) holds the canonical state. What's its schema. **Single-writer or concurrent?** Single-writer → flat file (JSONL) in git is fine. Concurrent → SQLite + WAL.
3. **Derived artifacts.** What's rebuildable from the source of truth and can be gitignored / cached / thrown away. (Determinism is provable: SHA-256 round-trip after `rebuild` matches before.)
4. **Interfaces.** Which subset of {CLI, HTTP API, web UI, scheduled job, email digest} ships at MVP. **Default CLI-first.**
5. **Deps allowance.** Max third-party packages. Each needs a one-sentence justification.
6. **Hosting.** Local-only? Gaming-laptop ceiling? Container? Cloud free tier?
7. **Secrets policy.** Any keys involved? If yes, **cache-first sidecar snapshot** so keyless visitors see content.
8. **Daily / scheduled work.** Anything periodic? Local cron vs. cloud scheduler.
9. **Domain model / ubiquitous language.** 3-7 nouns the user thinks in. What's a row of the source of truth *called* in their language. (One paragraph; not full DDD.)
10. **Failure modes.** What's the noisiest input (malformed feed, missing field, rate-limit, timeout)? What does the app do when it fails — crash, skip, retry, archive? Failing silently is the disallowed default.
11. **Observability.** What three things does every run log (start timestamp, end timestamp, counts)? What's the *one* metric an operator scans to know yesterday's run was healthy?
12. **Data lifecycle.** When does data expire / archive / get deleted? Append-only-forever is a real choice; "rotate after N days" is also a choice. Pick before writing the writer.

## Terminology & file conventions (post-audit 2026-05-26)

These align the project with 2025-2026 expert standards. Going-forward edits use these names; existing files migrate as separate tasks.

| Use | Not | Source |
|---|---|---|
| **approval tests** | "golden snapshot" tests | Llewellyn Falco; https://approvaltests.com/ |
| **MADR**: Context / Decision / Consequences | custom "Why / Change / Verification" | https://adr.github.io/madr/ |
| **`llms-full.txt`** + sibling `llms.txt` | `LLM_INDEX.md` | https://llmstxt.org/ (600+ adopters including Anthropic, Stripe, Solana) |
| **Diátaxis** four-quadrant docs: tutorials (`docs/getting-started.md`) + how-tos (`docs/recipes/`) + reference + explanation | README + Mermaid alone | https://diataxis.fr/ |
| **`.claude/skills/<name>/SKILL.md`** with YAML frontmatter | `.claude/commands/*.md` | Current Claude Code convention; the older path is legacy |
| **CLAUDE.md** stays short (load-once-per-session) | CLAUDE.md as the function index | Anthropic guidance; the index belongs in `llms-full.txt` |
| **"smoke-test-published-image"** as a named CI layer | unnamed verification job | Cohn's test pyramid + CircleCI/AWS guidance |

## Architectural patterns expert consensus validated (do not weaken)

These were stress-tested in the 2026-05-26 audit against named authorities and held up — keep using them:

* **Modular monolith** until deployment cadence or scaling actually diverges (Fowler).
* **CLI-first, web-second**, shared modules. Tests bypass the web layer via TestClient (Unix philosophy: McIlroy, Raymond).
* **Cache-first sidecar snapshots** for any LLM / API-keyed feature so keyless visitors see content (RFC 9111 stale-while-revalidate at the application layer).
* **Non-destructive defaults**: append-only writes, archive-don't-delete sidecar, one destructive verb with `--confirm` (Event Sourcing literature).
* **CI pyramid**: unit tests → container build verify → smoke-test the *published* image polling a real endpoint for 200. Each layer catches what the layer above misses.