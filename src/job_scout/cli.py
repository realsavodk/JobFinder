from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from job_scout.discover import run_discover
from job_scout.eval_extraction import run_eval_extraction
from job_scout.pipeline.run import run_all
from job_scout.resume_import import ResumeImportError, import_resume


def main() -> None:
    parser = argparse.ArgumentParser(prog="job-scout")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch postings for all configured companies"
    )
    fetch_parser.add_argument(
        "--companies-file", type=Path, default=Path("companies.yaml")
    )
    fetch_parser.add_argument("--profile-file", type=Path, default=Path("profile.yaml"))

    serve_parser = subparsers.add_parser("serve", help="Run the dashboard web server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    eval_parser = subparsers.add_parser(
        "eval-extraction",
        help="Evaluate LLM extraction accuracy against hand-labeled fixtures",
    )
    eval_parser.add_argument(
        "--fixtures-dir", type=Path, default=Path("evals/postings")
    )
    eval_parser.add_argument(
        "--labels-file", type=Path, default=Path("evals/labels.yaml")
    )

    resume_parser = subparsers.add_parser(
        "import-resume",
        help="Read skills and years of experience from a resume into profile.yaml",
    )
    resume_parser.add_argument("resume", type=Path, help="PDF, .txt or .md resume")
    resume_parser.add_argument(
        "--profile-file", type=Path, default=Path("profile.yaml")
    )

    discover_parser = subparsers.add_parser(
        "discover",
        help="Find companies' job boards in Common Crawl and list the live ones",
    )
    discover_parser.add_argument(
        "--crawls", type=int, default=12, help="how many recent crawls to search"
    )
    discover_parser.add_argument(
        "--out", type=Path, default=Path("discovered_companies.yaml")
    )
    discover_parser.add_argument("--cache-dir", type=Path, default=Path(".discovery"))
    discover_parser.add_argument(
        "--cached-only",
        action="store_true",
        help="skip downloading index pages; just (re)check companies already found",
    )
    discover_parser.add_argument(
        "--region",
        type=str.upper,
        help="only list companies with open jobs in this state/province, e.g. ON",
    )

    args = parser.parse_args()
    if args.command == "fetch":
        results = asyncio.run(run_all(args.companies_file, args.profile_file))
        for status in results:
            print(f"{status.company}: {status.message}")
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("job_scout.web.app:app", host=args.host, port=args.port)
    elif args.command == "eval-extraction":
        asyncio.run(run_eval_extraction(args.fixtures_dir, args.labels_file))
    elif args.command == "discover":
        asyncio.run(
            run_discover(
                args.crawls,
                args.out,
                args.cache_dir,
                args.region,
                cached_only=args.cached_only,
            )
        )
    elif args.command == "import-resume":
        try:
            lines = asyncio.run(import_resume(args.resume, args.profile_file))
        except ResumeImportError as exc:
            raise SystemExit(f"Cannot import resume: {exc}") from exc
        print("\n".join(lines))


if __name__ == "__main__":
    main()
