from __future__ import annotations

import re

from .http import AppError

MARKER = "<!-- pr-understanding:v1 -->"


class GitHub:
    def __init__(self, http, repository: str, number: int, author: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or number < 1:
            raise AppError("Use repository OWNER/REPO and a positive PR number.")
        if not author.strip():
            raise AppError("COMMENT_AUTHOR must identify the token's comment author.")
        self.http = http
        self.repository = repository
        self.number = number
        self.root = f"/repos/{repository}"
        self.author = author

    def fetch_pull_request(self):
        return self.http.request("GET", f"{self.root}/pulls/{self.number}")

    def fetch_files(self):
        files = []
        for page in range(1, 31):
            batch = self.http.request("GET", f"{self.root}/pulls/{self.number}/files?per_page=100&page={page}")
            files.extend(batch)
            if len(batch) < 100:
                break
        return files

    def find_comment(self):
        for page in range(1, 101):
            comments = self.http.request("GET", f"{self.root}/issues/{self.number}/comments?per_page=100&page={page}")
            for comment in comments:
                if (comment.get("body") or "").startswith(MARKER + "\n") and comment["user"]["login"] == self.author:
                    return comment
            if len(comments) < 100:
                return None
        raise AppError("Comment pagination limit reached; refusing to create a possible duplicate.")

    def publish_comment(self, body: str, existing):
        if existing:
            return self.http.request("PATCH", f"{self.root}/issues/comments/{existing['id']}", {"body": body})
        return self.http.request("POST", f"{self.root}/issues/{self.number}/comments", {"body": body})


def revision(pull_request):
    return pull_request["base"]["sha"], pull_request["head"]["sha"]


def validate_pull_request(pull_request, repository):
    if pull_request["state"] != "open" or pull_request.get("draft"):
        raise AppError("Only open, non-draft pull requests are supported.")
    if pull_request["base"]["repo"]["full_name"].lower() != repository.lower():
        raise AppError("Pull request repository mismatch.")
