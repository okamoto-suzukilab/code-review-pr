from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict

from .http import AppError
from .questions import parse_questions

PREFIX = "<!-- pr-understanding-snapshot:v1:"


class AnswerError(AppError):
    pass


def digest_document(document):
    serialized = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def build_snapshot(analysis, questions, repository, number):
    document = {
        "version": 1, "repository": repository, "pr_number": number,
        "base_sha": analysis["base_sha"], "head_sha": analysis["head_sha"],
        "questions": [{**asdict(question), "files": list(question.files)} for question in questions],
        "evidence": [{
            "path": changed["path"], "patch_chars": len(changed["patch"]),
            "sha256": hashlib.sha256(changed["patch"].encode("utf-8")).hexdigest(),
            "partial": changed["partial"],
        } for changed in analysis["files"]],
    }
    document["set_id"] = digest_document(document)
    return document


def encode_snapshot(document):
    encoded = base64.b64encode(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
    if len(encoded) > 50000:
        raise AppError("Question snapshot exceeds the safe size limit; reduce MAX_FILES.")
    return PREFIX + encoded + " -->"


def decode_snapshot(body, repository, number):
    matches = re.findall(re.escape(PREFIX) + r"([A-Za-z0-9+/=]+) -->", body)
    if len(matches) != 1 or len(matches[0]) > 50000:
        raise AnswerError("この設問は回答評価に未対応です。出題Actionsを再実行し、最新の設問を使ってください。")
    try:
        document = json.loads(base64.b64decode(matches[0], validate=True))
        expected = {"version", "repository", "pr_number", "base_sha", "head_sha", "questions", "evidence", "set_id"}
        if not isinstance(document, dict) or set(document) != expected:
            raise ValueError()
        unsigned = {key: value for key, value in document.items() if key != "set_id"}
        if document["version"] != 1 or document["set_id"] != digest_document(unsigned):
            raise ValueError()
        if document["repository"].lower() != repository.lower() or document["pr_number"] != number:
            raise ValueError()
        if not all(re.fullmatch(r"[a-f0-9]{40}", document[key]) for key in ("base_sha", "head_sha")):
            raise ValueError()
        evidence = document["evidence"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 100:
            raise ValueError()
        paths = set()
        for entry in evidence:
            if set(entry) != {"path", "patch_chars", "sha256", "partial"}:
                raise ValueError()
            if not isinstance(entry["path"], str) or entry["path"] in paths:
                raise ValueError()
            if type(entry["patch_chars"]) is not int or not 1 <= entry["patch_chars"] <= 6000:
                raise ValueError()
            if type(entry["partial"]) is not bool or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
                raise ValueError()
            paths.add(entry["path"])
        serialized_questions = []
        for index, question in enumerate(document["questions"], start=1):
            if set(question) != {"id", "topic", "question", "files"} or question["id"] != f"Q{index}":
                raise ValueError()
            serialized_questions.append({key: value for key, value in question.items() if key != "id"})
        parse_questions(json.dumps({"questions": serialized_questions}), paths)
        return document
    except (ValueError, TypeError, KeyError, AttributeError, AppError):
        raise AnswerError("設問の保存情報を確認できません。出題Actionsを再実行してください。") from None


def restore_evidence(snapshot, question, files):
    available = {changed["filename"]: changed for changed in files}
    fingerprints = {entry["path"]: entry for entry in snapshot["evidence"]}
    evidence = []
    for path in question["files"]:
        changed, fingerprint = available.get(path), fingerprints[path]
        if changed is None or not isinstance(changed.get("patch"), str):
            raise AnswerError("出題時の差分を取得できません。出題Actionsを再実行してください。")
        excerpt = changed["patch"][:fingerprint["patch_chars"]]
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != fingerprint["sha256"]:
            raise AnswerError("差分が出題時と一致しません。最新の設問へ回答してください。")
        evidence.append({"path": path, "patch": excerpt, "partial": fingerprint["partial"]})
    return evidence
