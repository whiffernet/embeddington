"""CLI: `embeddington-consume update` — pull and apply the shared knowledge graph.

A single ``update`` brings the local stack current: on a fresh install it restores the
latest baseline (snapshot + dump + named graph), then applies any newer diffs; on later
runs it applies only the diffs since the local cursor. It is idempotent and resumable.

Auth: by default it fetches releases via the GitHub CLI (``gh``), so it works against the
PRIVATE shared repo using your own ``gh auth login`` credentials (you must have been added
as a collaborator). Set ``GITHUB_TOKEN`` to use a bearer token instead of ``gh``.
"""

import argparse
import os
import sys

from consumer import release_client, restore_ops, updater, writers
from consumer.fetcher import GhFetcher, HttpFetcher


def _cmd_update(args):
    token = os.environ.get("GITHUB_TOKEN")
    fetcher = HttpFetcher(token=token) if token else GhFetcher(args.repo)
    rc = release_client.ReleaseClient(fetcher, repo=args.repo)
    qdrant = writers.QdrantConsumerWriter.connect(args.qdrant_url, args.collection)
    arango = writers.ArangoConsumerWriter.connect(
        args.arango_url, args.arango_db, args.arango_user, args.arango_password
    )
    baseline_importer = restore_ops.make_baseline_importer(
        rc,
        args.work_dir,
        args.qdrant_url,
        args.collection,
        args.arango_url,
        args.arango_db,
        args.arango_user,
        args.arango_password,
    )
    try:
        result = updater.update(
            rc, qdrant, arango, args.cursor, args.work_dir, baseline_importer
        )
    except updater.BaselineRequired as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    print(
        f"update: {result['mode']}, applied {result['applied']}, cursor {result['cursor']}"
    )
    return 0


def main(argv=None):
    """Parse args and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="embeddington-consume")
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("update", help="pull and apply the latest diffs")
    p_up.add_argument("--repo", required=True, help="owner/name of the releases repo")
    p_up.add_argument("--cursor", default="data/.cursor")
    p_up.add_argument("--work-dir", default="data/work")
    p_up.add_argument("--qdrant-url", default="http://localhost:6333")
    p_up.add_argument("--collection", default="technology")
    p_up.add_argument("--arango-url", default="http://localhost:8529")
    p_up.add_argument("--arango-db", default="technology_kg")
    p_up.add_argument("--arango-user", default="root")
    p_up.add_argument(
        "--arango-password",
        # Same var the consumer docker-compose uses, so one .env serves both.
        default=os.environ.get("ARANGO_ROOT_PASSWORD")
        or os.environ.get("ARANGO_PASSWORD", ""),
    )
    p_up.set_defaults(func=_cmd_update)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
