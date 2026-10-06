from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

from .http import AppError

DEFAULT_EXCLUDES = (
    ".env", ".env.*", "*/.env", "*/.env.*", "*.pem", "*.key",
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
)


def analyze_changes(pull_request, files, max_chars=24000, max_files=40, excludes=DEFAULT_EXCLUDES):
    included = []
    omitted = {"excluded": 0, "no_patch": 0, "budget": 0}
    remaining = max_chars
    for changed in files:
        path = changed["filename"]
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in excludes):
            omitted["excluded"] += 1
            continue
        patch = changed.get("patch")
        if not patch:
            omitted["no_patch"] += 1
            continue
        if len(included) >= max_files or remaining < 200:
            omitted["budget"] += 1
            continue
        excerpt = patch[:min(remaining, 6000)]
        remaining -= len(excerpt)
        added = sum(line.startswith("+") for line in patch.splitlines())
        removed = sum(line.startswith("-") for line in patch.splitlines())
        incomplete = len(excerpt) < len(patch) or added < changed["additions"] or removed < changed["deletions"]
        included.append({
            "path": path, "previous_path": changed.get("previous_filename"),
            "status": changed["status"], "extension": PurePosixPath(path).suffix,
            "additions": changed["additions"], "deletions": changed["deletions"],
            "hunks": re.findall(r"^@@[^\n]*", excerpt, re.MULTILINE),
            "partial": incomplete, "patch": excerpt,
        })
    if not included:
        raise AppError("No analyzable text diff remains; no questions or comment were generated.")
    omitted["api_limit"] = max(0, pull_request["changed_files"] - len(files))
    return {
        "title": pull_request["title"][:500],
        "base_sha": pull_request["base"]["sha"], "head_sha": pull_request["head"]["sha"],
        "total_files": pull_request["changed_files"], "omitted": omitted,
        "files": included,
    }
