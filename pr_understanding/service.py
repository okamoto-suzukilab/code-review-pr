from .analysis import analyze_changes
from .comments import render_comment
from .github import revision, validate_pull_request
from .http import AppError
from .questions import generate_questions


def run_check(github, provider, *, dry_run=True, max_chars=24000, max_files=40, excludes=None):
    pull_request = github.fetch_pull_request()
    validate_pull_request(pull_request, github.repository)
    files = github.fetch_files()
    options = {"max_chars": max_chars, "max_files": max_files}
    if excludes is not None:
        options["excludes"] = excludes
    analysis = analyze_changes(pull_request, files, **options)
    if revision(github.fetch_pull_request()) != revision(pull_request):
        raise AppError("PR changed while fetching its diff; rerun for the latest revision.")
    questions = generate_questions(provider, analysis)
    body = render_comment(analysis, questions)
    if not dry_run:
        existing = github.find_comment()
        latest = github.fetch_pull_request()
        validate_pull_request(latest, github.repository)
        if revision(latest) != revision(pull_request):
            raise AppError("PR changed while generating questions; no comment was posted. Rerun.")
        github.publish_comment(body, existing)
    return analysis, questions, body
