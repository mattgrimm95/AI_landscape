"""Command-line interface for the AI landscape pipeline.

Usage:
    python -m ailandscape.cli run        scrape new documents, then rebuild
    python -m ailandscape.cli rebuild    rebuild the NER log + graph from the corpus
    python -m ailandscape.cli sbir       pull AI-related SBIR/STTR awards, then rebuild
    python -m ailandscape.cli jbooks     pull AI-related R&D items from DoD J-Books, then rebuild
    python -m ailandscape.cli backfill   re-fetch corpus documents that stored only a summary
    python -m ailandscape.cli demo       run the flow on the bundled sample feed
    python -m ailandscape.cli stats      show corpus and database statistics
    python -m ailandscape.cli overview   print a statistical overview of the data
    python -m ailandscape.cli briefing   print a generated briefing of the landscape
    python -m ailandscape.cli explain                print a structural overview of the whole system
    python -m ailandscape.cli explain <module>       deep-dive a module (deps, tests, trust signals)
    python -m ailandscape.cli explain <module> --narrative   add a Claude-generated narrative
    python -m ailandscape.cli trends     print temporal trends (volume, new/active entities)
    python -m ailandscape.cli review     audit data quality and accumulate findings in review.json
    python -m ailandscape.cli digest     email the daily digest (opt-in; needs SMTP env vars + recipients)
    python -m ailandscape.cli visualize  render a static interactive HTML graph
    python -m ailandscape.cli serve       run the interactive web app (browser)
    python -m ailandscape.cli discover-feeds         probe new AI/nat-sec RSS feeds
    python -m ailandscape.cli discover-feeds --health-check    audit existing feeds
    python -m ailandscape.cli enrich plan.json       fetch articles + synthesis, rebuild
    python -m ailandscape.cli synthesize-daily        write today's hype+narrative sidecar snapshot
    python -m ailandscape.cli synthesize-daily --force  regenerate today's snapshot even if it exists
    python -m ailandscape.cli correct merge "DoD" "Department of Defense"
    python -m ailandscape.cli correct-from-review --merges --ignores --acronyms   bulk-apply review.json
    python -m ailandscape.cli overview --diff   show KPI deltas between the last two runs
    python -m ailandscape.cli snapshot   export the corpus and databases to snapshots/
    python -m ailandscape.cli reset --confirm   delete the derived databases

The corpus (corpus/documents.jsonl) is the source of truth; `rebuild`
regenerates the databases from it deterministically.
"""

import argparse
import datetime
import json
import os
import sys
import tempfile

from . import (
    ai_terms, briefing, config, corpus, emailer, enrich, explain as explain_mod,
    feed_discovery, pipeline, reconcile, report, review, scraper, synthesis,
    synthesis_cache, trends, visualize,
)
from . import feeds as feeds_mod
from .storage_kg import KnowledgeGraphStore
from .storage_ner import NEROutputLog

SAMPLE_FEED = config.ROOT / "samples" / "sample_feed.xml"
CORRECTIONS_FILE = config.CORRECTIONS_FILE


def _log(msg):
    print(msg)


def _open_stores():
    config.ensure_dirs()
    return NEROutputLog(config.NER_OUTPUT_DB), KnowledgeGraphStore(config.KG_DB)


def cmd_run(args):
    ner_log, kg = _open_stores()
    try:
        result = pipeline.run(
            feeds_mod.FEEDS,
            config.CORPUS_FILE,
            ner_log,
            kg,
            sbir_queries=feeds_mod.SBIR_QUERIES,
            jbook_sources=feeds_mod.JBOOK_SOURCES,
            ner_backend=args.ner,
            corrections=reconcile.load_corrections(CORRECTIONS_FILE),
            log=_log,
        )
    finally:
        ner_log.close()
        kg.close()
    print(json.dumps(result, indent=2))
    return 0


def cmd_jbooks(args):
    """Pull AI-related R&D items from DoD J-Books into the corpus, rebuild."""
    config.ensure_dirs()
    result = pipeline.scrape_jbooks_into_corpus(
        feeds_mod.JBOOK_SOURCES, config.CORPUS_FILE, log=_log
    )
    if result["jbooks_added"]:
        ner_log, kg = _open_stores()
        try:
            rebuilt = pipeline.rebuild(
                config.CORPUS_FILE,
                ner_log,
                kg,
                ner_backend=args.ner,
                corrections=reconcile.load_corrections(CORRECTIONS_FILE),
                log=_log,
            )
        finally:
            ner_log.close()
            kg.close()
        result["graph"] = rebuilt["graph"]
    else:
        _log("no new J-Book items added — the graph is unchanged.")
    print(json.dumps(result, indent=2))
    return 0


def cmd_sbir(args):
    """Pull AI-related SBIR/STTR awards into the corpus, then rebuild."""
    config.ensure_dirs()
    result = pipeline.scrape_sbir_into_corpus(
        feeds_mod.SBIR_QUERIES, config.CORPUS_FILE, log=_log
    )
    if result["sbir_added"]:
        ner_log, kg = _open_stores()
        try:
            rebuilt = pipeline.rebuild(
                config.CORPUS_FILE,
                ner_log,
                kg,
                ner_backend=args.ner,
                corrections=reconcile.load_corrections(CORRECTIONS_FILE),
                log=_log,
            )
        finally:
            ner_log.close()
            kg.close()
        result["graph"] = rebuilt["graph"]
    else:
        _log("no new SBIR awards added — the graph is unchanged.")
    print(json.dumps(result, indent=2))
    return 0


def cmd_backfill(_args):
    """Re-fetch corpus documents that stored only a short feed summary."""
    config.ensure_dirs()
    result = pipeline.backfill_corpus_text(config.CORPUS_FILE, log=_log)
    print(json.dumps(result, indent=2))
    if result["repaired"]:
        print("run 'rebuild' to regenerate the databases with the repaired text.")
    return 0


def cmd_rebuild(args):
    ner_log, kg = _open_stores()
    try:
        result = pipeline.rebuild(
            config.CORPUS_FILE,
            ner_log,
            kg,
            ner_backend=args.ner,
            corrections=reconcile.load_corrections(CORRECTIONS_FILE),
            log=_log,
        )
    finally:
        ner_log.close()
        kg.close()
    print(json.dumps(result, indent=2))
    return 0


def cmd_demo(args):
    # The demo runs entirely in a throwaway directory so it never touches
    # the real corpus or databases.
    tmp = tempfile.mkdtemp(prefix="ailandscape-demo-")
    corpus_path = os.path.join(tmp, "documents.jsonl")
    for article in scraper.scrape_fixture(SAMPLE_FEED, "Sample Feed"):
        corpus.append(corpus_path, pipeline.make_record(article))
    ner_log = NEROutputLog(os.path.join(tmp, "ner_output_log.db"))
    kg = KnowledgeGraphStore(os.path.join(tmp, "knowledge_graph.db"))
    try:
        result = pipeline.rebuild(corpus_path, ner_log, kg, ner_backend=args.ner, log=_log)
    finally:
        ner_log.close()
        kg.close()
    print(json.dumps(result, indent=2))
    print("demo ran in %s (real data untouched)" % tmp)
    return 0


def cmd_stats(_args):
    ner_log, kg = _open_stores()
    try:
        reading = corpus.reading_stats(config.CORPUS_FILE)
        print(
            "Corpus:   %d documents  (%s)"
            % (reading["documents"], config.CORPUS_FILE)
        )
        if reading["documents"]:
            print(
                "  Claude reads: %d ever-read, %d fresh, %d stale, %d never-read"
                % (
                    reading["ever_read"],
                    reading["fresh"],
                    reading["stale"],
                    reading["never_read"],
                )
            )
        print("NER log:  %d entities" % ner_log.count_entities())
        print(
            "Graph:    %d nodes, %d edges"
            % (kg.count_nodes(), kg.count_edges())
        )
        top = kg.top_nodes(10)
        if top:
            print("\nTop entities by mentions:")
            for node in top:
                print(
                    "  %-28s %-13s %3d mentions / %d docs"
                    % (
                        node["canonical_name"][:28],
                        node["type"],
                        node["mention_count"],
                        node["document_count"],
                    )
                )
    finally:
        ner_log.close()
        kg.close()
    return 0


def cmd_overview(args):
    if getattr(args, "diff", False):
        diff = report.diff_runs(config.RUN_HISTORY_FILE)
        print(report.render_diff(diff))
        return 0
    ner_log, kg = _open_stores()
    try:
        documents = corpus.load(config.CORPUS_FILE)
        text = report.render_overview(
            report.build_overview(
                documents, ner_log, kg, config.RUN_HISTORY_FILE
            )
        )
    finally:
        ner_log.close()
        kg.close()
    print(text)
    return 0


def cmd_explain(args):
    """Render a deterministic structural report on the codebase.

    Default target is the whole system (one-line module summaries, every
    CLI verb, every API endpoint, total test count). Pass a module short
    name (e.g. `synthesis`, `synthesis_cache`, `pipeline`) to drill into
    one module: public surface, imports, reverse-deps, which CLI verbs
    and API endpoints reach it, test coverage, and trust signals
    (docstring presence, TODO markers, last commit).

    The structural output is always emitted first. With `--narrative`,
    the same data is then handed to Claude (via the Claude Code CLI
    transport) for a prose explanation; if the transport isn't
    available, the structural output still prints and the narrative
    falls back to an explanatory note.
    """
    try:
        data = explain_mod.explain(args.target)
    except FileNotFoundError as exc:
        print("explain: {}".format(exc))
        return 1
    print(explain_mod.render(data))
    if args.narrative:
        try:
            from . import claude_cli
            if not claude_cli.is_available():
                print(
                    "\n[narrative is opt-in: Claude Code CLI not found on "
                    "PATH and ANTHROPIC_API_KEY fallback not configured. "
                    "Structural report above is the full deterministic "
                    "output.]"
                )
            else:
                text = explain_mod.narrate(data)
                print("\nCLAUDE NARRATIVE\n" + "-" * 62 + "\n" + text)
        except Exception as exc:
            print("\n[narrative failed: {}]".format(exc))
    return 0


def cmd_briefing(args):
    """Print a generated briefing of the AI national-security landscape."""
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        documents = corpus.load(config.CORPUS_FILE)
        data = briefing.build_briefing(documents, kg, days=args.days)
    finally:
        kg.close()
    print(briefing.render_briefing(data))
    if args.narrative:
        if not synthesis.is_configured():
            print(
                "\n[narrative synthesis is opt-in: set ANTHROPIC_API_KEY "
                "to enable it]"
            )
        else:
            try:
                narrative = synthesis.summarize_briefing(data)
                print("\nANALYST NARRATIVE\n" + "-" * 62 + "\n" + narrative)
            except synthesis.SynthesisError as exc:
                print("\n[narrative synthesis failed: %s]" % exc)
    return 0


def cmd_digest(args):
    """Compose the daily digest; with --preview, print it; otherwise send it
    to the recipients in data/email_recipients.txt via configured SMTP."""
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        documents = corpus.load(config.CORPUS_FILE)
        if args.preview:
            print(emailer.build_digest(documents, kg, days=args.days))
            return 0
        result = emailer.daily_digest(
            documents, kg, config.EMAIL_RECIPIENTS_FILE, days=args.days
        )
    finally:
        kg.close()
    print(json.dumps(result, indent=2))
    return 0


def cmd_review(_args):
    """Audit data quality; accumulate findings in review.json."""
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        documents = corpus.load(config.CORPUS_FILE)
        data = review.build_review(documents, kg)
    finally:
        kg.close()
    print(review.render_review(data))
    counts = review.save_review(data, config.REVIEW_FILE)
    print(
        "\n%d new merge suggestion(s), %d new ignore suggestion(s) recorded in %s"
        % (counts["merges"], counts["ignores"], config.REVIEW_FILE)
    )
    if counts["merges"]:
        print(
            "apply merges with: ailandscape correct merge "
            "\"<from>\" \"<into>\""
        )
    if counts["ignores"]:
        print(
            "apply ignores with: ailandscape correct ignore \"<name>\""
        )
    return 0


def cmd_trends(_args):
    """Print temporal trends: document volume and new / active entities."""
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        documents = corpus.load(config.CORPUS_FILE)
        text = trends.render_trends(trends.build_trends(documents, kg))
    finally:
        kg.close()
    print(text)
    return 0


def cmd_visualize(args):
    config.ensure_dirs()
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        nodes = kg.nodes()
        edges = kg.edges()
    finally:
        kg.close()
    if not nodes:
        print("the graph is empty — run 'rebuild' first.", file=sys.stderr)
        return 1
    try:
        sel_nodes, sel_edges = visualize.select_subgraph(
            nodes,
            edges,
            focus=args.focus,
            node_type=args.type,
            min_mentions=args.min_mentions,
            max_nodes=args.max_nodes,
            min_weight=args.min_weight,
            relations_only=args.relations_only,
        )
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    output = args.output or str(config.GRAPH_HTML)
    heading = (
        "AI Landscape - %s" % args.focus
        if args.focus
        else "AI Landscape Knowledge Graph"
    )
    visualize.render(sel_nodes, sel_edges, output, title=heading)
    print(
        "wrote %d nodes, %d edges -> %s"
        % (len(sel_nodes), len(sel_edges), output)
    )
    print("open it in a browser to explore (zoom, pan, click a node).")
    return 0


def cmd_correct(args):
    path = CORRECTIONS_FILE
    data = {"merge": {}, "ignore": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("corrections.json is not valid JSON; aborting.", file=sys.stderr)
            return 1
    data.setdefault("merge", {})
    data.setdefault("ignore", [])
    if args.action == "merge":
        if len(args.terms) != 2:
            print(
                "merge needs two arguments: <surface form> <canonical name>",
                file=sys.stderr,
            )
            return 1
        surface, canonical = args.terms
        data["merge"][surface] = canonical
        print("recorded merge: %r -> %r" % (surface, canonical))
    else:  # ignore
        if len(args.terms) != 1:
            print(
                "ignore needs one argument: <surface form>", file=sys.stderr
            )
            return 1
        term = args.terms[0]
        if term not in data["ignore"]:
            data["ignore"].append(term)
        print("recorded ignore: %r" % term)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("updated %s" % path)
    print("run 'rebuild' to apply the correction.")
    return 0


def cmd_correct_from_review(args):
    """Walk `review.json` suggestions and bulk-apply approved ones.

    The accumulating `review.json` store can carry tens of partial-name
    merge suggestions and structural-noise ignores after a single sweep.
    Applying them via `correct merge <a> <b>` one at a time means dozens
    of CLI invocations and a separate rebuild each. This command iterates
    each suggestion, prompts y/n/skip, batches the approvals into
    `corrections.json`, and (unless `--no-rebuild`) runs one rebuild at
    the end. With `--yes`, every suggestion is auto-approved without a
    prompt — useful when the curator has already reviewed the file.
    """
    review_path = config.REVIEW_FILE
    if not review_path.exists():
        print("no review.json found — run `review` first.", file=sys.stderr)
        return 1
    try:
        review_store = json.loads(review_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("review.json is not valid JSON; aborting.", file=sys.stderr)
        return 1

    corrections_path = CORRECTIONS_FILE
    corrections = {"merge": {}, "ignore": []}
    if corrections_path.exists():
        try:
            corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(
                "corrections.json is not valid JSON; aborting.",
                file=sys.stderr,
            )
            return 1
    corrections.setdefault("merge", {})
    corrections.setdefault("ignore", [])

    merges = review_store.get("suggested_merges", []) if args.merges else []
    ignores = review_store.get("suggested_ignores", []) if args.ignores else []
    acros = (
        review_store.get("acronym_suggestions", []) if args.acronyms else []
    )
    if not merges and not ignores and not acros:
        print(
            "nothing to apply: use --merges, --ignores, and/or --acronyms"
            " to select."
        )
        return 0

    applied_merges = 0
    applied_ignores = 0
    applied_acros = 0
    for suggestion in merges:
        bare = suggestion.get("from")
        full = suggestion.get("into")
        if not bare or not full:
            continue
        if bare in corrections["merge"]:
            continue
        if not _ask_yes(args.yes, 'merge "%s" -> "%s"?' % (bare, full)):
            continue
        corrections["merge"][bare] = full
        applied_merges += 1

    for suggestion in ignores:
        name = suggestion.get("name")
        if not name or name in corrections["ignore"]:
            continue
        if not _ask_yes(args.yes, 'ignore "%s"?' % name):
            continue
        corrections["ignore"].append(name)
        applied_ignores += 1

    for suggestion in acros:
        acronym = suggestion.get("acronym")
        expansion = suggestion.get("expansion")
        if not acronym or not expansion:
            continue
        if acronym in corrections["merge"]:
            continue
        prompt = 'map acronym "%s" -> "%s" (corroborated in %d docs)?' % (
            acronym, expansion, suggestion.get("documents", 0)
        )
        if not _ask_yes(args.yes, prompt):
            continue
        corrections["merge"][acronym] = expansion
        applied_acros += 1

    if not (applied_merges or applied_ignores or applied_acros):
        print("no suggestions applied — corrections.json is unchanged.")
        return 0

    corrections_path.write_text(
        json.dumps(corrections, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "applied %d merge(s), %d ignore(s), %d acronym(s) to %s"
        % (applied_merges, applied_ignores, applied_acros, corrections_path)
    )
    if args.no_rebuild:
        print("run 'rebuild' when ready to regenerate the graph.")
        return 0
    ner_log, kg = _open_stores()
    try:
        result = pipeline.rebuild(
            config.CORPUS_FILE,
            ner_log,
            kg,
            ner_backend=args.ner,
            corrections=reconcile.load_corrections(CORRECTIONS_FILE),
            log=_log,
        )
    finally:
        ner_log.close()
        kg.close()
    print(json.dumps({"rebuilt": result["graph"]}, indent=2))
    return 0


def _ask_yes(force_yes, prompt):
    """Y/N prompt that respects an interactive --yes override."""
    if force_yes:
        return True
    try:
        answer = input(prompt + " [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def cmd_history(args):
    """Print the ingest-history log as a readable table.

    Reads snapshots/run-history.jsonl (the per-run record the daily
    pipeline appends to) and surfaces the last N runs with finish time,
    fetched / added / filtered counts, document totals, and any errors
    in the per-feed scorecard so a missed cron, a broken feed, or a
    quality slip is visible at a glance over time.

    With --full, includes the per-feed scorecard for each run; otherwise
    only the runs with non-empty errors get their bad feeds enumerated.
    """
    path = config.RUN_HISTORY_FILE
    if not path.exists():
        legacy = config._LEGACY_RUN_HISTORY
        if legacy.exists():
            print("(reading legacy %s -- consider running 'rebuild' once to migrate)"
                  % legacy)
            path = legacy
        else:
            print("No run-history file yet. Run `ailandscape run` once.")
            return 0
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    runs = [json.loads(ln) for ln in lines]
    runs = runs[-args.limit:] if args.limit > 0 else runs
    if not runs:
        print("(empty)")
        return 0

    # Header
    print("Pipeline run history (most recent at bottom)")
    print("-" * 96)
    print("%-20s  %5s  %5s  %5s  %6s  %6s  %5s  %7s  %5s"
          % ("finished_at (UTC)", "fetch", "add", "filt", "scrape", "rebld",
             "docs", "nodes", "typed"))
    print("-" * 96)
    for r in runs:
        finished = (r.get("finished_at") or "").replace("T", " ")[:19]
        print("%-20s  %5s  %5s  %5s  %5.0fs  %5.0fs  %5d  %7d  %5d"
              % (finished,
                 r.get("fetched", "-"),
                 r.get("added", "-"),
                 r.get("filtered_non_ai", "-"),
                 r.get("scrape_seconds", 0) or 0,
                 r.get("rebuild_seconds", 0) or 0,
                 r.get("documents", 0) or 0,
                 r.get("nodes", 0) or 0,
                 r.get("typed_relations", 0) or 0))
        # Always show broken feeds (per-feed scorecard with non-empty error)
        feeds = (r.get("feeds") or {})
        bad = [(name, info.get("error", ""))
               for name, info in feeds.items()
               if info.get("error")]
        for name, err in bad:
            print("    feed-error: %-30s %s" % (name[:30], err[:60]))
        if args.full and feeds:
            for name, info in feeds.items():
                if not info.get("error"):
                    print("    %-32s  fetched=%-4d  added=%-4d  filt=%-4d"
                          % (name[:32], info.get("fetched", 0),
                             info.get("added", 0),
                             info.get("filtered_non_ai", 0)))
    return 0


def _is_protected_doc(doc):
    """Return True for docs that bypass the AI gate (always kept).

    Claude syntheses are operator-authored summary docs; SBIR / J-Book
    records went through their own AI gate at ingest time.
    """
    src = doc.get("source", "")
    meta = doc.get("metadata") or {}
    return (src == "Claude synthesis"
            or meta.get("synthesis")
            or meta.get("data_source") in ("SBIR", "J-Book"))


def _append_archive(docs, reason):
    """Append `docs` to the corpus archive with archived_at + reason."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    config.CORPUS_ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config.CORPUS_ARCHIVE_FILE.open("a", encoding="utf-8") as handle:
        for doc in docs:
            record = dict(doc)
            record["archived_at"] = now
            record["archived_reason"] = reason
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _load_archive():
    """Return list of archived docs (each with archived_at + archived_reason)."""
    path = config.CORPUS_ARCHIVE_FILE
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def cmd_audit_corpus_ai(args):
    """Scan the corpus for documents that don't pass the AI relevance bar.

    Default (no flag): audit-only -- report counts per source + 20 titles.

    With --prune: MOVE dropped docs to corpus/archived.jsonl (NOT discard).
    The archive lives alongside the active corpus and is tracked in git,
    so the work to scrape those articles is never wasted. If the AI
    filter parameters change later, --reinstate scans the archive for
    docs that now pass the gate and moves them back into the active
    corpus -- no re-scraping required.

    Always preserves Claude syntheses and SBIR/J-Book records (those
    went through their own AI gate at ingest time).
    """
    config.ensure_dirs()

    # --- --reinstate path: pull archived docs back into the active corpus
    if args.reinstate:
        archive = _load_archive()
        if not archive:
            print("Archive is empty -- nothing to reinstate.")
            return 0
        active_hashes = corpus.hashes(config.CORPUS_FILE)
        bring_back = []
        keep_archived = []
        for doc in archive:
            chash = doc.get("content_hash", "")
            if chash and chash in active_hashes:
                # Already in the active corpus (someone re-ingested);
                # drop the archive copy so we don't double-store.
                continue
            text = (doc.get("title", "") + " "
                    + (doc.get("raw_text", "") or ""))
            if ai_terms.is_ai_relevant(text) or _is_protected_doc(doc):
                bring_back.append(doc)
            else:
                keep_archived.append(doc)
        deduped = len(archive) - len(bring_back) - len(keep_archived)
        print("Archive reinstate:")
        print("  archived docs: %d" % len(archive))
        print("  reinstating:   %d (now pass the AI gate)" % len(bring_back))
        print("  keep archived: %d (still don't pass)" % len(keep_archived))
        if deduped:
            print("  deduped:       %d (already in active corpus -- dropped from archive)"
                  % deduped)
        # Strip the archive-only fields before appending to the active corpus.
        for doc in bring_back:
            doc.pop("archived_at", None)
            doc.pop("archived_reason", None)
            corpus.append(config.CORPUS_FILE, doc)
        # Rewrite the archive in any case where its contents changed -- the
        # dedupe path (archived hash already in active corpus) also needs
        # to drop the archive copy so we don't double-store.
        if bring_back or deduped:
            path = config.CORPUS_ARCHIVE_FILE
            path.write_text(
                "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True)
                         for d in keep_archived) + ("\n" if keep_archived else ""),
                encoding="utf-8",
            )
        if bring_back:
            print()
            print("Run 'rebuild' to fold the reinstated docs into the graph.")
        return 0

    # --- audit / prune path
    documents = corpus.load(config.CORPUS_FILE)
    keep = []
    drop = []
    for doc in documents:
        if _is_protected_doc(doc):
            keep.append(doc)
            continue
        text = (doc.get("title", "") + " "
                + (doc.get("raw_text", "") or ""))
        if ai_terms.is_ai_relevant(text):
            keep.append(doc)
        else:
            drop.append(doc)

    import collections
    by_src = collections.Counter(d.get("source", "?") for d in drop)
    print("Corpus audit (AI gate)")
    print("  total docs:       %d" % len(documents))
    print("  would keep:       %d" % len(keep))
    print("  would drop:       %d  (%.0f%%)"
          % (len(drop), 100.0 * len(drop) / max(1, len(documents))))
    archive_count = sum(1 for _ in _load_archive())
    if archive_count:
        print("  already archived: %d  (use --reinstate to re-evaluate)"
              % archive_count)
    print()
    if by_src:
        print("Drop counts per source:")
        for src, n in by_src.most_common():
            print("  %-30s %d" % (src[:30], n))
    print()
    print("First 20 titles that would drop:")
    for d in drop[:20]:
        print("  [%s] %s" % (d.get("source", "")[:18],
                             (d.get("title", "") or "")[:80]))
    if not args.prune:
        print()
        print("(audit only -- pass --prune to MOVE drops to "
              "corpus/archived.jsonl)")
        return 0
    if not drop:
        print("Nothing to drop -- corpus is already clean.")
        return 0
    # Move (not discard): archive first, then rewrite corpus.
    _append_archive(drop, reason="ai_filter")
    corpus.save(config.CORPUS_FILE, keep)
    print()
    print("Pruned %d doc(s) -> corpus/archived.jsonl. Corpus now %d docs."
          % (len(drop), len(keep)))
    print("Archive total: %d doc(s). Run 'rebuild' to regenerate the graph."
          % (archive_count + len(drop)))
    return 0


def cmd_synthesize_daily(args):
    """Generate today's hype + briefing-narrative sidecar snapshot.

    Reads the freshest corpus + graph and calls the Anthropic API for the
    two syntheses, then writes `snapshots/syntheses/YYYY-MM-DD.json`.
    Silent no-op without ANTHROPIC_API_KEY (matches every other LLM path
    in the project).

    Skips silently if today's snapshot already exists, unless --force is
    passed — so a cron that runs once a day is cheap to retry, while an
    operator who wants to regenerate after a big news drop can force it.

    The written file is under version control (`snapshots/` is NOT
    gitignored), so committing it with the daily corpus update means
    visitors who pull the repo see today's synthesis without needing
    their own API key.
    """
    config.ensure_dirs()
    documents = corpus.load(config.CORPUS_FILE)
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        snapshot = pipeline.generate_daily_syntheses(
            documents, kg, log=_log, force=args.force,
        )
    finally:
        kg.close()
    summary = {
        "date": snapshot.get("date", ""),
        "generated_at": snapshot.get("generated_at", ""),
        "key_configured": synthesis.is_configured(),
        "sections": {
            name: {
                "available": snapshot.get(name, {}).get("available", False),
                "error": snapshot.get(name, {}).get("error", ""),
                "documents_used": snapshot.get(name, {}).get(
                    "documents_used", 0
                ),
            }
            for name in synthesis_cache.SECTION_NAMES
        },
        "snapshot_path": str(synthesis_cache.snapshot_path()),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_enrich(args):
    """Run an entity-enrichment plan: fetch the listed articles + optional
    synthesis, append them to the corpus, then rebuild so the new entities
    propagate into the NER log and the knowledge graph.

    A plan is a JSON file shaped {entity, articles[], synthesis{}}. See
    ailandscape/enrich.py for the schema. `--no-rebuild` lets the caller
    chain several enrichment plans together and rebuild once at the end.
    """
    config.ensure_dirs()
    with open(args.plan, "r", encoding="utf-8") as handle:
        plan = json.load(handle)
    result = enrich.enrich_from_plan(
        config.CORPUS_FILE, plan, log=_log,
        allow_non_ai=args.allow_non_ai,
    )
    print(json.dumps(result, indent=2))
    if args.no_rebuild:
        return 0
    if not (result["articles_added"] or result["synthesis_added"]):
        print("nothing new added — skipping rebuild.")
        return 0
    ner_log, kg = _open_stores()
    try:
        rebuilt = pipeline.rebuild(
            config.CORPUS_FILE,
            ner_log,
            kg,
            ner_backend=args.ner,
            corrections=reconcile.load_corrections(CORRECTIONS_FILE),
            log=_log,
        )
    finally:
        ner_log.close()
        kg.close()
    print(json.dumps({"rebuilt": rebuilt["graph"]}, indent=2))
    return 0


def cmd_discover_feeds(args):
    """Probe candidate RSS URLs for AI/national-security organizations.

    Verified candidates are printed; nothing is auto-added to feeds.py — the
    feeds list is curated by hand and version-controlled, so the user
    decides which suggestions land. With --health-check, run the verify
    step against every URL already in feeds.FEEDS and report broken ones.
    """
    if args.health_check:
        healthy, unhealthy = feed_discovery.health_check_existing(
            feeds_mod.FEEDS
        )
        print("Healthy feeds (%d):" % len(healthy))
        for h in healthy:
            print("  OK   %-30s %4d entries  %s"
                  % (h["name"][:30], h["status"]["entries"], h["url"]))
        if unhealthy:
            print("\nBroken feeds (%d):" % len(unhealthy))
            for u in unhealthy:
                print("  ERR  %-30s %s  (%s)" %
                      (u["name"][:30], u["url"], u["status"]["error"][:50]))
        return 0
    seeds = feed_discovery.DEFAULT_SEEDS
    print("Probing %d organizations for RSS feeds…" % len(seeds))
    verified = feed_discovery.discover_candidates(seeds)
    if not verified:
        print("No feeds discovered.")
        return 0
    print("\nDiscovered %d feed(s):" % len(verified))
    known_urls = {f["url"] for f in feeds_mod.FEEDS}
    for f in verified:
        marker = "*ALREADY*" if f["url"] in known_urls else "NEW"
        print("  %-9s %-32s %4d entries  %s"
              % (marker, f["name"][:32], f["entries"], f["url"]))
    new_feeds = [f for f in verified if f["url"] not in known_urls]
    if new_feeds:
        print(
            "\nAdd a discovered feed by appending to ailandscape/feeds.py:"
        )
        for f in new_feeds[:3]:
            print('  {"name": %r, "category": "defense", "url": %r},'
                  % (f["name"], f["url"]))
    return 0


def cmd_serve(args):
    import uvicorn

    config.ensure_dirs()
    # Show 127.0.0.1 in the URL when bound to the loopback (the common local
    # case) and the actual host string otherwise -- "http://0.0.0.0:8000" is
    # not a real address you can click, so when bound to all interfaces we
    # print 0.0.0.0 verbatim as a "listening on every interface" hint and
    # let the operator type their LAN IP / container hostname.
    print(
        "AI Landscape web app running at http://%s:%d  (Ctrl+C to stop)"
        % (args.host, args.port)
    )
    uvicorn.run(
        "ailandscape.server:app",
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


def cmd_snapshot(_args):
    ner_log, kg = _open_stores()
    try:
        snapshot = {
            "created_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "corpus": {"documents": corpus.load(config.CORPUS_FILE)},
            "ner_output": {"entities": ner_log.all_entities()},
            "knowledge_graph": {
                "nodes": kg.nodes(),
                "aliases": kg.aliases(),
                "edges": kg.edges(),
            },
        }
    finally:
        ner_log.close()
        kg.close()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = config.SNAPSHOT_DIR / ("snapshot-%s.json" % stamp)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print("wrote snapshot: %s" % path)
    return 0


def cmd_reading(args):
    """Show Claude-reading coverage of the corpus.

    With --reset, flip every document's `claude_read_fresh` flag to False
    (use after a major corpus update when a fresh re-read is warranted).
    With --list-stale, print the URLs Claude should re-read next.
    """
    if args.reset:
        n = corpus.invalidate_freshness(config.CORPUS_FILE)
        print("reset claude_read_fresh on %d documents" % n)
        return 0

    stats = corpus.reading_stats(config.CORPUS_FILE)
    print("Documents:       %d" % stats["documents"])
    if not stats["documents"]:
        return 0
    pct = lambda n: 100.0 * n / stats["documents"]
    print(
        "Ever read:       %d  (%.0f%%)" % (stats["ever_read"], pct(stats["ever_read"]))
    )
    print(
        "Fresh (current): %d  (%.0f%%)" % (stats["fresh"], pct(stats["fresh"]))
    )
    print(
        "Stale (read but invalidated): %d  (%.0f%%)"
        % (stats["stale"], pct(stats["stale"]))
    )
    print(
        "Never read:      %d  (%.0f%%)"
        % (stats["never_read"], pct(stats["never_read"]))
    )
    print(
        "Total Claude reads logged: %d   (max on one doc: %d)"
        % (stats["total_reads"], stats["max_reads_one_doc"])
    )

    if args.list_stale:
        documents = corpus.load(config.CORPUS_FILE)
        stale = [
            d for d in documents
            if not d.get("claude_read_fresh")
            and int(d.get("claude_read_count", 0) or 0) >= 0
        ]
        # Sort: never-read first (so they show on top), then oldest read.
        stale.sort(key=lambda d: (
            int(d.get("claude_read_count", 0) or 0),
            d.get("claude_last_read", "") or "",
        ))
        print("\nDocuments due for re-read (first %d):" % min(20, len(stale)))
        for d in stale[:20]:
            reads = int(d.get("claude_read_count", 0) or 0)
            print(
                "  [%dx] %s  %s"
                % (reads, d.get("published", "")[:16], d.get("title", "")[:70])
            )
    return 0


def cmd_reset(args):
    # Destructive, but only for the *derived* databases — the corpus (the
    # source of truth) is never touched, and even this needs --confirm.
    if not args.confirm:
        print(
            "reset deletes the derived databases (the corpus is preserved). "
            "Re-run with --confirm to proceed.",
            file=sys.stderr,
        )
        return 1
    for db_path in (config.NER_OUTPUT_DB, config.KG_DB):
        if db_path.exists():
            db_path.unlink()
            print("deleted %s" % db_path)
    print("corpus left intact; run 'rebuild' to regenerate the databases.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ailandscape",
        description="AI national-security landscape knowledge-graph pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run", help="scrape new documents into the corpus, then rebuild"
    )
    run_p.add_argument("--ner", choices=["rule", "spacy", "hybrid"], default=None)
    run_p.set_defaults(func=cmd_run)

    rebuild_p = sub.add_parser(
        "rebuild", help="rebuild the NER log and graph from the corpus"
    )
    rebuild_p.add_argument(
        "--ner", choices=["rule", "spacy", "hybrid"], default=None
    )
    rebuild_p.set_defaults(func=cmd_rebuild)

    sbir_p = sub.add_parser(
        "sbir",
        help="pull AI-related SBIR/STTR awards into the corpus, then rebuild",
    )
    sbir_p.add_argument(
        "--ner", choices=["rule", "spacy", "hybrid"], default=None
    )
    sbir_p.set_defaults(func=cmd_sbir)

    jbook_p = sub.add_parser(
        "jbooks",
        help="pull AI-related R&D items from DoD J-Books, then rebuild",
    )
    jbook_p.add_argument(
        "--ner", choices=["rule", "spacy", "hybrid"], default=None
    )
    jbook_p.set_defaults(func=cmd_jbooks)

    sub.add_parser(
        "backfill",
        help="re-fetch corpus documents that stored only a short summary",
    ).set_defaults(func=cmd_backfill)

    demo_p = sub.add_parser("demo", help="run the flow on the bundled sample feed")
    demo_p.add_argument(
        "--ner", choices=["rule", "spacy", "hybrid"], default=None
    )
    demo_p.set_defaults(func=cmd_demo)

    sub.add_parser("stats", help="show corpus and database statistics").set_defaults(
        func=cmd_stats
    )
    overview_p = sub.add_parser(
        "overview", help="print a statistical overview of the data"
    )
    overview_p.add_argument(
        "--diff", action="store_true",
        help="show run-over-run KPI deltas instead of the full overview",
    )
    overview_p.set_defaults(func=cmd_overview)

    brief_p = sub.add_parser(
        "briefing", help="print a generated briefing of the landscape"
    )
    brief_p.add_argument(
        "--days", type=int, default=7,
        help="recency window (days) for the recent-documents section",
    )
    brief_p.add_argument(
        "--narrative", action="store_true",
        help="also generate an LLM analyst narrative (needs ANTHROPIC_API_KEY)",
    )
    brief_p.set_defaults(func=cmd_briefing)

    explain_p = sub.add_parser(
        "explain",
        help="print a structural report on the system or a module "
             "(deps, tests, trust signals)",
    )
    explain_p.add_argument(
        "target", nargs="?", default="system",
        help="'system' (default) for the whole-system overview, or a "
             "module short name like 'synthesis' / 'pipeline'",
    )
    explain_p.add_argument(
        "--narrative", action="store_true",
        help="also ask Claude to weave a prose explanation from the report",
    )
    explain_p.set_defaults(func=cmd_explain)

    sub.add_parser(
        "trends", help="print temporal trends from the corpus and graph"
    ).set_defaults(func=cmd_trends)

    sub.add_parser(
        "review",
        help="audit data quality; accumulate merge suggestions in review.json",
    ).set_defaults(func=cmd_review)

    digest_p = sub.add_parser(
        "digest",
        help="send (or --preview) the daily email digest; opt-in via env+file",
    )
    digest_p.add_argument(
        "--days", type=int, default=7,
        help="recency window (days) for the briefing inside the digest",
    )
    digest_p.add_argument(
        "--preview", action="store_true",
        help="print the digest body instead of sending it",
    )
    digest_p.set_defaults(func=cmd_digest)

    viz_p = sub.add_parser(
        "visualize", help="render an interactive HTML graph visualization"
    )
    viz_p.add_argument(
        "--focus", default=None,
        help="center the view on one entity and its neighborhood",
    )
    viz_p.add_argument(
        "--type", default=None, help="only include entities of this type"
    )
    viz_p.add_argument("--min-mentions", type=int, default=0, dest="min_mentions")
    viz_p.add_argument("--max-nodes", type=int, default=70, dest="max_nodes")
    viz_p.add_argument("--min-weight", type=int, default=3, dest="min_weight")
    viz_p.add_argument(
        "--relations-only", action="store_true", dest="relations_only",
        help="show only typed semantic relationships, dropping co-occurrence",
    )
    viz_p.add_argument("--output", default=None, help="output HTML file path")
    viz_p.set_defaults(func=cmd_visualize)

    correct_p = sub.add_parser(
        "correct", help="record a manual correction in corrections.json"
    )
    correct_p.add_argument("action", choices=["merge", "ignore"])
    correct_p.add_argument(
        "terms",
        nargs="+",
        help="merge: <surface form> <canonical name>;  ignore: <surface form>",
    )
    correct_p.set_defaults(func=cmd_correct)

    from_review_p = sub.add_parser(
        "correct-from-review",
        help="walk review.json suggestions and bulk-apply approved ones",
    )
    from_review_p.add_argument(
        "--merges", action="store_true",
        help="apply partial-name merge suggestions from review.json",
    )
    from_review_p.add_argument(
        "--ignores", action="store_true",
        help="apply structural-noise ignore suggestions from review.json",
    )
    from_review_p.add_argument(
        "--acronyms", action="store_true",
        help="apply corroborated acronym ↔ expansion mappings from review.json",
    )
    from_review_p.add_argument(
        "--yes", "-y", action="store_true",
        help="auto-approve every suggestion (no per-item prompt)",
    )
    from_review_p.add_argument(
        "--no-rebuild", action="store_true",
        help="write corrections.json but skip the rebuild — run 'rebuild' later",
    )
    from_review_p.add_argument(
        "--ner", choices=["rule", "spacy", "hybrid"], default=None,
    )
    from_review_p.set_defaults(func=cmd_correct_from_review)

    serve_p = sub.add_parser("serve", help="run the interactive web app")
    serve_p.add_argument("--port", type=int, default=8000)
    # Default to loopback so a local `ailandscape serve` doesn't accidentally
    # expose the dev server to the LAN. Containers override to 0.0.0.0 in the
    # Dockerfile CMD so docker's port forwarding can reach it.
    serve_p.add_argument(
        "--host", default="127.0.0.1",
        help="bind address (use 0.0.0.0 in containers)",
    )
    serve_p.set_defaults(func=cmd_serve)

    history_p = sub.add_parser(
        "history",
        help="show the daily ingest history table (timing, counts, errors)",
    )
    history_p.add_argument(
        "--limit", type=int, default=20,
        help="how many most-recent runs to show (default 20, 0 = all)",
    )
    history_p.add_argument(
        "--full", action="store_true",
        help="also print the per-feed scorecard for each run",
    )
    history_p.set_defaults(func=cmd_history)

    audit_p = sub.add_parser(
        "audit-corpus-ai",
        help="scan the corpus for docs that don't pass the AI relevance "
             "bar (with --prune, rewrite the corpus dropping them)",
    )
    audit_p.add_argument(
        "--prune", action="store_true",
        help="MOVE non-AI docs from corpus/documents.jsonl to "
             "corpus/archived.jsonl (preserved, not discarded). "
             "Syntheses and SBIR/J-Book records are always kept.",
    )
    audit_p.add_argument(
        "--reinstate", action="store_true",
        help="re-evaluate corpus/archived.jsonl against the current AI "
             "filter and pull back into the active corpus any archived "
             "docs that now pass. Use after tuning ai_terms.py.",
    )
    audit_p.set_defaults(func=cmd_audit_corpus_ai)

    synth_p = sub.add_parser(
        "synthesize-daily",
        help="generate today's hype + briefing-narrative sidecar snapshot",
    )
    synth_p.add_argument(
        "--force", action="store_true",
        help="regenerate even if today's snapshot already exists",
    )
    synth_p.set_defaults(func=cmd_synthesize_daily)

    enrich_p = sub.add_parser(
        "enrich",
        help="execute an entity-enrichment plan (fetch articles + synthesis),"
             " then rebuild",
    )
    enrich_p.add_argument(
        "plan", help="path to a JSON enrichment plan (see ailandscape/enrich.py)",
    )
    enrich_p.add_argument(
        "--ner", choices=["rule", "spacy", "hybrid"], default=None,
    )
    enrich_p.add_argument(
        "--no-rebuild", action="store_true",
        help="append to the corpus but don't trigger the rebuild — chain"
             " several plans, then run `rebuild` once at the end",
    )
    enrich_p.add_argument(
        "--allow-non-ai", action="store_true",
        help="bypass the AI-relevance gate (which rejects a plan whose"
             " synthesis + articles contain no AI/ML/autonomy term). Use"
             " sparingly: the corpus is scoped to AI national-security"
             " reporting and off-topic content dilutes the graph.",
    )
    enrich_p.set_defaults(func=cmd_enrich)

    discover_p = sub.add_parser(
        "discover-feeds",
        help="probe candidate RSS URLs for AI/national-security organizations",
    )
    discover_p.add_argument(
        "--health-check", action="store_true",
        help="verify every URL already in feeds.FEEDS; do not probe new candidates",
    )
    discover_p.set_defaults(func=cmd_discover_feeds)
    sub.add_parser(
        "snapshot", help="export the corpus and databases to snapshots/"
    ).set_defaults(func=cmd_snapshot)

    reading_p = sub.add_parser(
        "reading",
        help="show Claude reading coverage; --reset invalidates freshness",
    )
    reading_p.add_argument(
        "--reset", action="store_true",
        help="flip every claude_read_fresh flag to False (use after major corpus updates)",
    )
    reading_p.add_argument(
        "--list-stale", action="store_true",
        help="also print the top documents Claude should re-read next",
    )
    reading_p.set_defaults(func=cmd_reading)

    reset_p = sub.add_parser(
        "reset", help="delete the derived databases (the corpus is kept)"
    )
    reset_p.add_argument(
        "--confirm", action="store_true", help="required to actually delete"
    )
    reset_p.set_defaults(func=cmd_reset)

    return parser


def main(argv=None):
    # Force UTF-8 output so a print of text containing characters outside the
    # platform default encoding (common on Windows with a redirected stdout)
    # can never crash the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
