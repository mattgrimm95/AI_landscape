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
    python -m ailandscape.cli trends     print temporal trends (volume, new/active entities)
    python -m ailandscape.cli review     audit data quality and accumulate findings in review.json
    python -m ailandscape.cli digest     email the daily digest (opt-in; needs SMTP env vars + recipients)
    python -m ailandscape.cli visualize  render a static interactive HTML graph
    python -m ailandscape.cli serve       run the interactive web app (browser)
    python -m ailandscape.cli correct merge "DoD" "Department of Defense"
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
    briefing, config, corpus, emailer, pipeline, reconcile, report, review,
    scraper, synthesis, trends, visualize,
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
        print(
            "Corpus:   %d documents  (%s)"
            % (corpus.count(config.CORPUS_FILE), config.CORPUS_FILE)
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


def cmd_overview(_args):
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


def cmd_serve(args):
    import uvicorn

    config.ensure_dirs()
    print(
        "AI Landscape web app running at http://127.0.0.1:%d  (Ctrl+C to stop)"
        % args.port
    )
    uvicorn.run(
        "ailandscape.server:app",
        host="127.0.0.1",
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
    sub.add_parser(
        "overview", help="print a statistical overview of the data"
    ).set_defaults(func=cmd_overview)

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

    serve_p = sub.add_parser("serve", help="run the interactive web app")
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.set_defaults(func=cmd_serve)
    sub.add_parser(
        "snapshot", help="export the corpus and databases to snapshots/"
    ).set_defaults(func=cmd_snapshot)

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
