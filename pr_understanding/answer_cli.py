import argparse
import json
import os
import sys
from pathlib import Path

from .__main__ import build_provider, require_env
from .analysis import DEFAULT_EXCLUDES
from .evaluation import run_answer
from .github import GitHub
from .http import AppError, JsonHttp


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate an issue_comment event (preview by default).")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--post", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        event = json.loads(Path(require_env("GITHUB_EVENT_PATH")).read_text(encoding="utf-8"))
        repository = require_env("GITHUB_REPOSITORY")
        http = JsonHttp(os.environ.get("GITHUB_API_URL", "https://api.github.com"), {
            "Authorization": "Bearer " + require_env("GITHUB_TOKEN"),
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-understanding-feedback",
        }, timeout=30)
        github = GitHub(http, repository, event["issue"]["number"], os.environ.get("COMMENT_AUTHOR", "github-actions[bot]"))
        extras = tuple(filter(None, (part.strip() for part in os.environ.get("EXCLUDE_GLOBS", "").split(","))))
        body = run_answer(github, build_provider, event, dry_run=not args.post, excludes=DEFAULT_EXCLUDES + extras)
        if body:
            output = Path("work/feedback.md")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(body, encoding="utf-8")
            print("Feedback posted." if args.post else "Feedback preview written.")
        else:
            print("Answer skipped (unsupported, unauthorized, edited, or already evaluated).")
        return 0
    except (AppError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc) if isinstance(exc, AppError) else "Invalid event or local file operation failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
