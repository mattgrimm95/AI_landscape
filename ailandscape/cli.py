"""Command-line interface for the AI landscape pipeline.

Usage:
    python -m ailandscape.cli run        scrape new documents, then rebuild
    python -m ailandscape.cli rebuild    rebuild the NER log + graph from the corpus
    python -m ailandscape.cli demo       run the flow on the bundled sample feed
    python -m ailandscape.cli stats      show corpus and database statistics
    python -m ailandscape.cli overview   print a statistical overview of the data
    python -m ailandscape.cli visualize  render an interactive HTML graph
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

from . import config, corpus, pipeline, reconcile, report, scraper, visualize
from . import feeds as feeds_mod
from .storage_kg import KnowledgeGraphStore
from .storage_ner import NEROutputLog

SAMPLE_FEED = config.ROOT / "samples" / "sample_feed.xml"
CORRECTIONS_FILE = config.ROOT / "corrections.json"


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
            ner_backend=args.ner,
            corrections=reconcile.load_corrections(CORRECTIONS_FILE),
            log=_log,
        )
    finally:
        ner_log.close()
        kg.close()
    print(json.dumps(result, indent=2))
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
