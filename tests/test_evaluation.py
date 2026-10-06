from __future__ import annotations

import copy
import json
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from pr_understanding.analysis import analyze_changes
from pr_understanding.answer_cli import main as answer_main
from pr_understanding.comments import render_comment
from pr_understanding.evaluation import feedback_marker, generate_feedback, parse_answer, run_answer
from pr_understanding.github import GitHub, MARKER
from pr_understanding.http import AppError
from pr_understanding.llm import OpenAIProvider
from pr_understanding.questions import parse_questions
from pr_understanding.snapshots import AnswerError, build_snapshot, decode_snapshot, encode_snapshot, restore_evidence

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
FEEDBACK = {"status": "needs_work", "strengths": ["POSTでタスク解放を依頼する目的を理解しています。"],
            "gaps": ["送信継続とサーバー処理の完了保証が混同されています。"],
            "hints": ["ページ離脱後、サーバーの処理結果を必ず確認できるでしょうか？"]}


def fixture():
    sample = json.loads((EXAMPLES / "pull_request.json").read_text(encoding="utf-8"))
    sample["pull_request"]["head"]["repo"] = {"full_name": "demo/example"}
    analysis = analyze_changes(sample["pull_request"], sample["files"])
    questions = parse_questions((EXAMPLES / "questions.json").read_text(encoding="utf-8"), {"src/releaseTask.ts"})
    snapshot = build_snapshot(analysis, questions, "demo/example", 1)
    return sample, analysis, questions, snapshot


class SnapshotTests(unittest.TestCase):
    def test_snapshot_roundtrip_binds_repository_pr_revision_and_questions(self):
        sample, analysis, questions, snapshot = fixture()
        body = render_comment(analysis, questions, snapshot)
        self.assertEqual(decode_snapshot(body, "demo/example", 1), snapshot)
        self.assertIn(f"/answer {snapshot['set_id']} Q1", body)
        self.assertEqual(restore_evidence(snapshot, snapshot["questions"][0], sample["files"])[0]["patch"], sample["files"][0]["patch"])

    def test_wrong_repository_pr_or_modified_question_is_rejected(self):
        _, _, _, snapshot = fixture()
        for repository, number in (("other/repo", 1), ("demo/example", 2)):
            with self.subTest(repository=repository), self.assertRaises(AnswerError):
                decode_snapshot(encode_snapshot(snapshot), repository, number)
        snapshot["questions"][0]["question"] = "modified"
        with self.assertRaises(AnswerError):
            decode_snapshot(encode_snapshot(snapshot), "demo/example", 1)

    def test_legacy_and_duplicate_snapshots_require_regeneration(self):
        _, _, _, snapshot = fixture()
        for body in (MARKER + "\nold questions", encode_snapshot(snapshot) * 2):
            with self.subTest(body=body[:50]), self.assertRaises(AnswerError):
                decode_snapshot(body, "demo/example", 1)

    def test_missing_or_mismatched_patch_is_rejected(self):
        sample, _, _, snapshot = fixture()
        for files in ([], [dict(sample["files"][0], patch="+different")]):
            with self.subTest(files=files), self.assertRaises(AnswerError):
                restore_evidence(snapshot, snapshot["questions"][0], files)


class AnswerParsingTests(unittest.TestCase):
    def test_multiline_answer_preserves_its_text(self):
        self.assertEqual(parse_answer("/answer 0123456789abcdef Q2\nページ離脱時の\n送信を続けます。"),
                         ("0123456789abcdef", "Q2", "ページ離脱時の\n送信を続けます。"))

    def test_missing_set_id_invalid_question_empty_long_and_multiple_answers_are_rejected(self):
        for body in ("/answer Q1 answer", "/answer 0123456789abcdef Q4 answer", "/answer 0123456789abcdef Q1 ",
                     "/answer 0123456789abcdef Q1 " + "a" * 4001,
                     "/answer 0123456789abcdef Q1 one\n/answer 0123456789abcdef Q2 two"):
            with self.subTest(body=body[:40]), self.assertRaises(AnswerError):
                parse_answer(body)


class FeedbackTests(unittest.TestCase):
    def test_feedback_uses_question_answer_and_matching_evidence_as_untrusted_data(self):
        sample, _, _, snapshot = fixture()
        provider = Mock()
        provider.generate_json.return_value = json.dumps(FEEDBACK)
        question = snapshot["questions"][0]
        evidence = restore_evidence(snapshot, question, sample["files"])
        self.assertEqual(generate_feedback(provider, snapshot, question, "回答", evidence), FEEDBACK)
        instruction, context, schema = provider.generate_json.call_args.args
        self.assertIn("模範解答", instruction)
        self.assertIn("信頼しない", instruction)
        self.assertEqual(json.loads(context)["answer"], "回答")
        self.assertEqual(json.loads(context)["files"], evidence)
        self.assertNotIn("score", schema["properties"])

    def test_malformed_or_unhelpful_feedback_is_not_postable(self):
        _, _, _, snapshot = fixture()
        variants = ["not JSON", "[]", json.dumps(dict(FEEDBACK, score=100)), json.dumps(dict(FEEDBACK, status="unknown")),
                    json.dumps(dict(FEEDBACK, hints=[])), json.dumps(dict(FEEDBACK, gaps=["a" * 601])),
                    json.dumps({"status": "understood", "strengths": [], "gaps": [], "hints": []})]
        provider = Mock()
        for raw in variants:
            provider.generate_json.return_value = raw
            with self.subTest(raw=raw[:40]), self.assertRaises(AppError):
                generate_feedback(provider, snapshot, snapshot["questions"][0], "answer", [])


class AnswerServiceTests(unittest.TestCase):
    def setUp(self):
        self.sample, self.analysis, self.questions, self.snapshot = fixture()
        self.source = {"id": 9, "body": f"/answer {self.snapshot['set_id']} Q1 タスク解放を必ず完了させます。",
                       "user": {"login": "owner", "type": "User"}, "issue_url": "https://api.github.com/repos/demo/example/issues/1"}
        self.event = {"action": "created", "repository": {"full_name": "demo/example"},
                      "issue": {"number": 1, "pull_request": {}}, "comment": copy.deepcopy(self.source)}
        self.question_comment = {"id": 8, "body": render_comment(self.analysis, self.questions, self.snapshot)}
        self.github = Mock(repository="demo/example", number=1)
        self.github.fetch_comment.return_value = self.source
        self.github.can_answer.return_value = True
        self.github.fetch_pull_request.return_value = self.sample["pull_request"]
        self.github.fetch_files.return_value = self.sample["files"]
        self.github.find_comment.side_effect = lambda marker=MARKER: self.question_comment if marker == MARKER else None
        self.provider = Mock()
        self.provider.generate_json.return_value = json.dumps(FEEDBACK)
        self.factory = Mock(return_value=self.provider)

    def test_valid_answer_posts_feedback_bound_to_source_question_and_commit(self):
        body = run_answer(self.github, self.factory, self.event, dry_run=False)
        self.assertIn("補足が必要な点", body)
        self.assertIn(self.snapshot["set_id"], body)
        self.assertIn(self.snapshot["head_sha"], body)
        self.assertIn("#issuecomment-9", body)
        self.github.publish_comment.assert_called_once_with(body, None)

    def test_preview_does_not_publish(self):
        self.assertIn("ヒント", run_answer(self.github, self.factory, self.event))
        self.github.publish_comment.assert_not_called()

    def test_stale_set_revision_and_legacy_questions_return_guidance_without_llm(self):
        cases = ("set", "head", "base", "legacy", "missing", "command")
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case == "set":
                    self.source["body"] = "/answer 0000000000000000 Q1 回答です。"
                elif case in {"head", "base"}:
                    self.sample["pull_request"][case]["sha"] = "a" * 40
                elif case == "legacy":
                    self.question_comment["body"] = MARKER + "\nold"
                elif case == "missing":
                    self.github.find_comment.side_effect = lambda marker=MARKER: None
                else:
                    self.source["body"] = "/answer Q1 回答です。"
                self.event["comment"] = copy.deepcopy(self.source)
                body = run_answer(self.github, self.factory, self.event, dry_run=False)
                self.assertIn("Code Understanding Feedback", body)
                self.factory.assert_not_called()
                self.github.publish_comment.assert_called_once()

    def test_unauthorized_bot_noncommand_or_nonpr_does_not_infer_or_post(self):
        for case in ("permission", "bot", "ordinary", "issue", "edited", "foreign_issue", "fork"):
            with self.subTest(case=case):
                self.setUp()
                if case == "permission":
                    self.github.can_answer.return_value = False
                elif case == "bot":
                    self.event["comment"]["user"]["type"] = "Bot"
                elif case == "ordinary":
                    self.event["comment"]["body"] = "ordinary comment"
                elif case == "issue":
                    self.event["issue"].pop("pull_request")
                elif case == "edited":
                    self.source["body"] += " edited"
                elif case == "foreign_issue":
                    self.source["issue_url"] = "https://api.github.com/repos/demo/example/issues/2"
                else:
                    self.sample["pull_request"]["head"]["repo"]["full_name"] = "other/fork"
                self.assertIsNone(run_answer(self.github, self.factory, self.event, dry_run=False))
                self.factory.assert_not_called()
                self.github.publish_comment.assert_not_called()

    def test_already_evaluated_answer_does_not_spend_tokens_or_duplicate_feedback(self):
        self.github.find_comment.side_effect = lambda marker=MARKER: {"id": 10}
        self.assertIsNone(run_answer(self.github, self.factory, self.event, dry_run=False))
        self.factory.assert_not_called()
        self.github.publish_comment.assert_not_called()

    def test_edited_source_during_inference_prevents_posting(self):
        edited = dict(self.source, body=self.source["body"] + "changed")
        self.github.fetch_comment.side_effect = [self.source, edited]
        self.assertIsNone(run_answer(self.github, self.factory, self.event, dry_run=False))
        self.github.publish_comment.assert_not_called()

    def test_revision_update_during_inference_posts_notice_instead_of_evaluation(self):
        changed = copy.deepcopy(self.sample["pull_request"])
        changed["head"]["sha"] = "a" * 40
        self.github.fetch_pull_request.side_effect = [self.sample["pull_request"], self.sample["pull_request"], changed]
        body = run_answer(self.github, self.factory, self.event, dry_run=False)
        self.assertIn("保留", body)
        self.assertNotIn(FEEDBACK["strengths"][0], body)

    def test_question_regeneration_during_inference_posts_notice(self):
        reads = 0
        def find(marker=MARKER):
            nonlocal reads
            if marker != MARKER:
                return None
            reads += 1
            return self.question_comment if reads == 1 else dict(self.question_comment, body="updated")
        self.github.find_comment.side_effect = find
        body = run_answer(self.github, self.factory, self.event, dry_run=False)
        self.assertIn("設問が更新", body)

    def test_new_exclusion_blocks_transmitting_previously_included_code(self):
        run_answer(self.github, self.factory, self.event, dry_run=False, excludes=("src/*",))
        self.factory.assert_not_called()

    def test_invalid_feedback_does_not_publish_a_reply(self):
        self.provider.generate_json.return_value = '{}'
        with self.assertRaises(AppError):
            run_answer(self.github, self.factory, self.event, dry_run=False)
        self.github.publish_comment.assert_not_called()


class AnswerIntegrationTests(unittest.TestCase):
    def test_http_boundary_posts_once_and_reanswer_creates_separate_feedback(self):
        sample, analysis, questions, snapshot = fixture()
        source = {"id": 9, "body": f"/answer {snapshot['set_id']} Q1 サーバーで処理を必ず完了します。",
                  "user": {"login": "owner", "type": "User"}, "issue_url": "https://api.github.com/repos/demo/example/issues/1"}
        comments = [{"id": 8, "body": render_comment(analysis, questions, snapshot), "user": {"login": "github-actions[bot]"}}]
        inference_calls = []
        def request(method, path, payload=None):
            if path.endswith("/permission"):
                return {"permission": "admin"}
            if path == "/repos/demo/example/issues/comments/9":
                return source
            if path == "/repos/demo/example/pulls/1":
                return sample["pull_request"]
            if path.startswith("/repos/demo/example/pulls/1/files"):
                return sample["files"]
            if path.startswith("/repos/demo/example/issues/1/comments") and method == "GET":
                return comments
            if path == "/repos/demo/example/issues/1/comments" and method == "POST":
                comments.append({"id": 10, "body": payload["body"], "user": {"login": "github-actions[bot]"}})
                return comments[-1]
            if path == "/chat/completions":
                inference_calls.append(payload)
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(FEEDBACK)}}]}
            self.fail(f"Unexpected request {method} {path}")
        http = Mock()
        http.request.side_effect = request
        github = GitHub(http, "demo/example", 1, "github-actions[bot]")
        event = {"action": "created", "repository": {"full_name": "demo/example"},
                 "issue": {"number": 1, "pull_request": {}}, "comment": copy.deepcopy(source)}
        factory = lambda: OpenAIProvider(http, "test-model")
        body = run_answer(github, factory, event, dry_run=False)
        self.assertIn(feedback_marker(9), body)
        self.assertIsNone(run_answer(github, factory, event, dry_run=False))
        self.assertEqual(len(comments), 2)
        self.assertEqual(len(inference_calls), 1)
        source["id"] = 11
        event["comment"] = copy.deepcopy(source)
        original_request = http.request.side_effect
        http.request.side_effect = lambda method, path, payload=None: source if path.endswith("/comments/11") else original_request(method, path, payload)
        self.assertIn(feedback_marker(11), run_answer(github, factory, event, dry_run=False))
        self.assertEqual(len(comments), 3)
        self.assertEqual(len(inference_calls), 2)


class AnswerCliTests(unittest.TestCase):
    def test_event_file_is_read_and_preview_is_written_without_posting(self):
        service = AnswerServiceTests()
        service.setUp()
        with tempfile.TemporaryDirectory() as folder:
            event_path = Path(folder) / "event.json"
            event_path.write_text(json.dumps(service.event), encoding="utf-8")
            environment = {"GITHUB_REPOSITORY": "demo/example", "GITHUB_EVENT_PATH": str(event_path), "GITHUB_TOKEN": "test-token"}
            output = Mock()
            with patch.dict(os.environ, environment, clear=True), patch("pr_understanding.answer_cli.GitHub", return_value=service.github), \
                 patch("pr_understanding.answer_cli.build_provider", service.factory), \
                 patch("pr_understanding.answer_cli.Path", side_effect=lambda path: event_path if str(path) == str(event_path) else output), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(answer_main(["--dry-run"]), 0)
            service.github.publish_comment.assert_not_called()
            output.write_text.assert_called_once()
            self.assertIn("再回答のヒント", output.write_text.call_args.args[0])

    def test_mutually_exclusive_post_and_dry_run_do_not_start_evaluation(self):
        with patch("pr_understanding.answer_cli.run_answer") as evaluator, patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                answer_main(["--post", "--dry-run"])
            evaluator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
