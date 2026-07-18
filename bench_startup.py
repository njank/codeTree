"""Benchmark codetree MCP server startup for every PL/SQL git repo under a root dir.

Usage:
    python bench_startup.py            # warm start (keeps .codetree caches)
    python bench_startup.py --cold     # cold start (deletes .codetree first)
    python bench_startup.py --root D:\\other\\dir

Only repos containing at least one *.pks file under their src/ folder are
started, and the server is given include=["src"] and exclude=["*.sql"] to
match the recommended claude_desktop_config.json setup
(--include src --exclude *.sql).
Each repo is timed in its own subprocess, exactly like Claude Desktop
launching one server per configured repo (includes Python import time).
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

CODETREE_SRC = str(Path(__file__).parent / "src")
PYTHON = sys.executable

# What one server process does at startup, minus the stdio event loop.
# The assert guards against an installed (stale) codetree shadowing the
# live source tree, e.g. an old non-editable pip install in the venv.
CHILD_CODE = (
    "import sys; sys.path.insert(0, sys.argv[2]); "
    "import codetree; "
    "assert codetree.__file__.startswith(sys.argv[2]), "
    "'stale codetree imported from ' + codetree.__file__; "
    "from codetree.server import create_server; "
    "create_server(sys.argv[1], include=['src'], exclude=['*.sql'])"
)


def has_pks_file(repo: Path) -> bool:
    """True if the repo contains at least one *.pks file under src/ (first hit wins)."""
    src = repo / "src"
    return src.is_dir() and next(src.rglob("*.pks"), None) is not None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\git")
    ap.add_argument("--cold", action="store_true",
                    help="delete .codetree before timing (full reindex)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="seconds before a repo is reported as hanging")
    args = ap.parse_args()

    candidates = [d for d in sorted(Path(args.root).iterdir())
                  if d.is_dir() and (d / ".git").exists()]
    repos = [d for d in candidates if has_pks_file(d)]
    print(f"{len(repos)} PL/SQL repos under {args.root} "
          f"({len(candidates) - len(repos)} repos without src/**/*.pks skipped, "
          f"{'COLD' if args.cold else 'warm'} start)\n")

    results = []
    for repo in repos:
        if args.cold:
            shutil.rmtree(repo / ".codetree", ignore_errors=True)
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [PYTHON, "-c", CHILD_CODE, str(repo), CODETREE_SRC],
                capture_output=True, text=True, timeout=args.timeout,
            )
            dt = time.perf_counter() - t0
            note = "" if proc.returncode == 0 else \
                "ERROR: " + proc.stderr.strip().splitlines()[-1]
        except subprocess.TimeoutExpired:
            dt = time.perf_counter() - t0
            note = f"TIMEOUT after {args.timeout}s"
        results.append((dt, repo.name, note))
        print(f"{dt:8.2f}s  {repo.name}  {note}")

    print("\nslowest first:")
    for dt, name, note in sorted(results, reverse=True)[:10]:
        print(f"{dt:8.2f}s  {name}  {note}")
    ok = [r for r in results if not r[2]]
    print(f"\ntotal: {sum(r[0] for r in results):.1f}s, "
          f"{len(ok)}/{len(results)} ok")


if __name__ == "__main__":
    main()
