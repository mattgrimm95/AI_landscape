"""Command-line interface for the AI landscape pipeline.

Usage:
    python -m ailandscape.cli run        scrape new documents, then rebuild
    python -m ailandscape.cli rebuild    rebuild both databases from the corpus
    python -m ailandscape.cli demo       run the flow on the bundled sample feed
    python -m ailandscape.cli stats      show corpus and database statistics
    python -m ailandscape.cli snapshot   export both databases to snapshots/
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

from . import config, corpus, pipeline, reconcile, scraper
from . import feeds as feeds_mod
from .storage_kg import KnowledgeGraphStore
from .storage_raw import RawLogStore

SAMPLE_FEED = config.ROOT / "samples" / "sample_feed.xml"
CORRECTIONS_FILE = config.ROOT / "corrections.json"


def _log(msg):
    print(msg)


def _open_stores():
    config.ensure_dirs()
    return RawLogStore(config.RAW_LOG_DB), KnowledgeGraphStore(config.KG_DB)


def cmd_run(args):
    raw, kg = _open_stores()
    try:
        result = pipeline.run(
            feeds_mod.FEEDS,
            config.CORPUS_FILE,
            raw,
            kg,
            ner_backend=args.ner,
            corrections=reconcile.load_corrections(CORRECTIONS_FILE),
            log=_log,
        )
    finally:
        raw.close()
        kg.close()
    print(json.dumps(result, indent=2))
    return 0


def cmd_rebuild(args):
    raw, kg = _open_stores()
    try:
        result = pipeline.rebuild(
            config.CORPUS_FILE,
            raw,
            kg,
            ner_backend=args.ner,
            corrections=reconcile.load_corrections(CORRECTIONS_FILE),
            log=_log,
        )
    finally:
        raw.close()
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
    raw = RawLogStore(os.path.join(tmp, "raw_log.db"))
    kg = KnowledgeGraphStore(os.path.join(tmp, "knowledge_graph.db"))
    try:
        result = pipeline.rebuild(corpus_path, raw, kg, ner_backend=args.ner, log=_log)
    finally:
        raw.close()
        kg.close()
    print(json.dumps(result, indent=2))
    print("demo ran in %s (real data untouched)" % tmp)
    return 0


def cmd_stats(_args):
    raw, kg = _open_stores()
    try:
        print(
            "Corpus:   %d documents  (%s)"
            % (corpus.count(config.CORPUS_FILE), config.CORPUS_FILE)
        )
        print(
            "Raw log:  %d documents, %d entities"
            % (raw.count_documents(), raw.count_entities())
        )
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
        raw.close()
        kg.close()
    return 0


def cmd_snapshot(_args):
    raw, kg = _open_stores()
    try:
        snapshot = {
            "created_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "raw_log": {
                "documents": raw.documents(),
                "entities": raw.all_entities(),
            },
            "knowledge_graph": {
                "nodes": kg.nodes(),
                "aliases": kg.aliases(),
                "edges": kg.edges(),
            },
        }
    finally:
        raw.close()
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
    for db_path in (config.RAW_LOG_DB, config.KG_DB):
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
    run_p.add_argument("--ner", choices=["rule", "spacy"], default=None)
    run_p.set_defaults(func=cmd_run)

    rebuild_p = sub.add_parser(
        "rebuild", help="rebuild both databases from the corpus (no network)"
    )
    rebuild_p.add_argument("--ner", choices=["rule", "spacy"], default=None)
    rebuild_p.set_defaults(func=cmd_rebuild)

    demo_p = sub.add_parser("demo", help="run the flow on the bundled sample feed")
    demo_p.add_argument("--ner", choices=["rule", "spacy"], default=None)
    demo_p.set_defaults(func=cmd_demo)

    sub.add_parser("stats", help="show corpus and database statistics").set_defaults(
        func=cmd_stats
    )
    sub.add_parser(
        "snapshot", help="export both databases to snapshots/"
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
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
