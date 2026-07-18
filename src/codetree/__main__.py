import argparse
import os
from .server import run

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Path to repo root (default: current directory)")
    parser.add_argument(
        "--include", action="append", default=None, metavar="DIR",
        help="Only index files under this folder relative to root, e.g. --include src. "
             "Repeatable; default is the whole repo.",
    )
    parser.add_argument(
        "--exclude", action="append", default=None, metavar="PATTERN",
        help="Skip files matching this glob pattern, e.g. --exclude *.sql "
             "or --exclude src/generated/*. Matched against the file name "
             "and the path relative to root. Repeatable.",
    )
    args = parser.parse_args()
    run(os.path.abspath(args.root), include=args.include, exclude=args.exclude)

if __name__ == "__main__":
    main()
