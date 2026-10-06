from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .analysis import DEFAULT_EXCLUDES, analyze_changes
from .comments import render_comment
from .github import GitHub
from .http import AppError, JsonHttp
from .llm import FixtureProvider, OllamaProvider, OpenAIProvider
from .questions import generate_questions
from .service import run_check
from .snapshots import build_snapshot


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise AppError(f"Missing environment variable: {name}")
    return value


def integer_env(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise AppError(f"{name} must be an integer.") from None
    if not minimum <= value <= maximum:
        raise AppError(f"{name} must be between {minimum} and {maximum}.")
    return value


def build_provider():
    provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
    model = require_env("LLM_MODEL")
    timeout = integer_env("HTTP_TIMEOUT_SECONDS", 120, 1, 600)
    max_tokens = integer_env("MAX_OUTPUT_TOKENS", 2000, 128, 16000)
    if provider == "openai":
        http = JsonHttp(os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                        {"Authorization": "Bearer " + require_env("OPENAI_API_KEY")}, timeout)
        return OpenAIProvider(http, model, max_tokens)
    if provider == "ollama":
        http = JsonHttp(os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"), timeout=timeout)
        context_size = integer_env("OLLAMA_CONTEXT_SIZE", 32768, 2048, 131072)
        return OllamaProvider(http, model, max_tokens, context_size)
    raise AppError("LLM_PROVIDER must be openai or ollama.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate three PR understanding questions (preview by default).")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--pr", type=int)
    parser.add_argument("--post", action="store_true", help="Create or update a GitHub PR comment")
    parser.add_argument("--dry-run", action="store_true", help="Explicitly preview without posting")
    parser.add_argument("--demo", action="store_true", help="Offline fixtures; no API calls")
    parser.add_argument("--output", type=Path, default=Path("work/comment.md"))
    parser.add_argument("--json-output", type=Path, default=Path("work/questions.json"))
    args = parser.parse_args(argv)
    try:
        if args.post and (args.dry_run or args.demo):
            raise AppError("--post cannot be combined with --dry-run or --demo.")
        if args.demo:
            examples = Path(__file__).resolve().parent.parent / "examples"
            fixture = json.loads((examples / "pull_request.json").read_text(encoding="utf-8"))
            analysis = analyze_changes(fixture["pull_request"], fixture["files"])
            questions = generate_questions(FixtureProvider(examples / "questions.json"), analysis)
            snapshot = build_snapshot(analysis, questions, args.repo or "demo/example", args.pr or 1)
            body = render_comment(analysis, questions, snapshot)
        else:
            if not args.repo or not args.pr:
                raise AppError("Provide --repo OWNER/REPO and --pr NUMBER.")
            http = JsonHttp(os.environ.get("GITHUB_API_URL", "https://api.github.com"), {
                "Authorization": "Bearer " + require_env("GITHUB_TOKEN"),
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pr-understanding-mvp",
            }, timeout=30)
            github = GitHub(http, args.repo, args.pr, os.environ.get("COMMENT_AUTHOR", "github-actions[bot]"))
            extra_excludes = tuple(filter(None, (part.strip() for part in os.environ.get("EXCLUDE_GLOBS", "").split(","))))
            analysis, questions, body = run_check(
                github, build_provider(), dry_run=not args.post,
                max_chars=integer_env("MAX_DIFF_CHARS", 24000, 1000, 100000),
                max_files=integer_env("MAX_FILES", 40, 1, 100),
                excludes=DEFAULT_EXCLUDES + extra_excludes,
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        snapshot = build_snapshot(analysis, questions, args.repo or "demo/example", args.pr or 1)
        args.json_output.write_text(json.dumps({"schema_version": 2, **snapshot}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Posted understanding check." if args.post else "Preview generated; no comment posted.")
        return 0
    except (AppError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc) if isinstance(exc, AppError) else "Invalid input or local file operation failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
