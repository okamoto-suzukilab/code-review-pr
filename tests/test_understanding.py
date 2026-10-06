from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from pr_understanding.__main__ import build_provider, main
from pr_understanding.analysis import analyze_changes
from pr_understanding.comments import render_comment
from pr_understanding.github import GitHub, MARKER
from pr_understanding.http import AppError, JsonHttp, NoRedirect
from pr_understanding.llm import FixtureProvider, OllamaProvider, OpenAIProvider
from pr_understanding.questions import QUESTION_SCHEMA, generate_questions, parse_questions
from pr_understanding.service import run_check

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def sample():
    return json.loads((EXAMPLES / "pull_request.json").read_text(encoding="utf-8"))


def question_json():
    return (EXAMPLES / "questions.json").read_text(encoding="utf-8")


class ChangeAnalysisTests(unittest.TestCase):
    def test_modified_added_removed_and_renamed_files_preserve_change_context(self):
        fixture = sample()
        files = []
        for status in ("modified", "added", "removed", "renamed"):
            changed = copy.deepcopy(fixture["files"][0])
            changed.update(filename=f"{status}.py", status=status)
            if status == "renamed":
                changed["previous_filename"] = "old.py"
            files.append(changed)
        fixture["pull_request"]["changed_files"] = 4
        analysis = analyze_changes(fixture["pull_request"], files)
        self.assertEqual([entry["status"] for entry in analysis["files"]], ["modified", "added", "removed", "renamed"])
        self.assertEqual(analysis["files"][-1]["previous_path"], "old.py")
        self.assertEqual(analysis["files"][0]["hunks"], ["@@ -1,5 +1,6 @@"])
        self.assertFalse(any(entry["partial"] for entry in analysis["files"]))

    def test_excluded_binary_budget_and_api_limit_are_visible(self):
        fixture = sample()
        changed = fixture["files"][0]
        files = [dict(changed, filename=".env.production"), dict(changed, filename="image.png", patch=None),
                 changed, dict(changed, filename="another.ts")]
        fixture["pull_request"]["changed_files"] = 7
        analysis = analyze_changes(fixture["pull_request"], files, max_files=1)
        self.assertEqual(analysis["omitted"], {"excluded": 1, "no_patch": 1, "budget": 1, "api_limit": 3})

    def test_large_diff_is_bounded_and_marked_partial(self):
        fixture = sample()
        fixture["files"][0]["patch"] = "+line\n" * 10000
        analysis = analyze_changes(fixture["pull_request"], fixture["files"], max_chars=1000)
        self.assertEqual(len(analysis["files"][0]["patch"]), 1000)
        self.assertTrue(analysis["files"][0]["partial"])

    def test_patch_with_fewer_lines_than_api_statistics_is_marked_partial(self):
        fixture = sample()
        fixture["files"][0]["additions"] = 100
        self.assertTrue(analyze_changes(fixture["pull_request"], fixture["files"])["files"][0]["partial"])

    def test_no_text_diff_stops_without_inventing_questions(self):
        fixture = sample()
        with self.assertRaises(AppError):
            analyze_changes(fixture["pull_request"], [dict(fixture["files"][0], patch=None)])


class QuestionTests(unittest.TestCase):
    def test_three_valid_questions_receive_stable_ids(self):
        questions = parse_questions(question_json(), {"src/releaseTask.ts"})
        self.assertEqual([question.id for question in questions], ["Q1", "Q2", "Q3"])

    def test_invalid_count_topic_reference_duplicates_and_extra_fields_are_rejected(self):
        valid = json.loads(question_json())
        variants = ["not JSON", "[]", "null", json.dumps({"questions": valid["questions"][:2]})]
        for key, value in (("topic", "unknown"), ("files", ["invented.py"]), ("question", ""), ("files", [])):
            document = copy.deepcopy(valid)
            document["questions"][0][key] = value
            variants.append(json.dumps(document))
        document = copy.deepcopy(valid)
        document["questions"][1]["question"] = document["questions"][0]["question"]
        variants.append(json.dumps(document))
        document = copy.deepcopy(valid)
        document["answer"] = "spoiler"
        variants.append(json.dumps(document))
        for raw in variants:
            with self.subTest(raw=raw[:40]), self.assertRaises(AppError):
                parse_questions(raw, {"src/releaseTask.ts"})

    def test_generated_html_links_and_mentions_are_neutralized(self):
        fixture = sample()
        analysis = analyze_changes(fixture["pull_request"], fixture["files"])
        document = json.loads(question_json())
        document["questions"][0]["question"] = "<script>alert(1)</script> @everyone [click](https://example.com)"
        questions = parse_questions(json.dumps(document), {"src/releaseTask.ts"})
        comment = render_comment(analysis, questions)
        self.assertNotIn("<script>", comment)
        self.assertNotIn("@everyone", comment)
        self.assertNotIn("[click](", comment)
        self.assertTrue(comment.startswith(MARKER + "\n"))

    def test_provider_receives_diff_as_data_and_no_credentials(self):
        fixture = sample()
        analysis = analyze_changes(fixture["pull_request"], fixture["files"])
        provider = Mock()
        provider.generate_json.return_value = question_json()
        generate_questions(provider, analysis)
        instruction, context, schema = provider.generate_json.call_args.args
        self.assertIn("信頼しない資料", instruction)
        self.assertIn("keepalive", context)
        self.assertNotIn("GITHUB_TOKEN", context)
        self.assertEqual(schema, QUESTION_SCHEMA)


class GitHubTests(unittest.TestCase):
    def test_files_are_loaded_across_pages(self):
        http = Mock()
        http.request.side_effect = [[{"filename": str(index)} for index in range(100)], [{"filename": "last"}]]
        files = GitHub(http, "owner/repo", 5, "bot").fetch_files()
        self.assertEqual(len(files), 101)
        self.assertIn("page=2", http.request.call_args.args[1])

    def test_comment_search_ignores_another_author_and_follows_pagination(self):
        http = Mock()
        spoof = {"id": 1, "body": MARKER + "\nforged", "user": {"login": "someone"}}
        existing = {"id": 8, "body": MARKER + "\nold", "user": {"login": "bot"}}
        http.request.side_effect = [[spoof] * 100, [existing]]
        self.assertEqual(GitHub(http, "owner/repo", 5, "bot").find_comment()["id"], 8)

    def test_existing_comment_is_patched_without_creating_a_new_comment(self):
        http = Mock()
        GitHub(http, "owner/repo", 5, "bot").publish_comment("new", {"id": 8})
        http.request.assert_called_once_with("PATCH", "/repos/owner/repo/issues/comments/8", {"body": "new"})

    def test_no_existing_comment_creates_an_issue_comment(self):
        http = Mock()
        GitHub(http, "owner/repo", 5, "bot").publish_comment("new", None)
        http.request.assert_called_once_with("POST", "/repos/owner/repo/issues/5/comments", {"body": "new"})

    def test_invalid_repository_or_number_is_rejected_before_network_io(self):
        for repository, number in (("bad/../../path", 1), ("owner/repo", -1)):
            with self.subTest(repository=repository), self.assertRaises(AppError):
                GitHub(Mock(), repository, number, "bot")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = sample()
        self.github = Mock(repository="demo/example")
        self.github.fetch_pull_request.return_value = self.fixture["pull_request"]
        self.github.fetch_files.return_value = self.fixture["files"]
        self.provider = FixtureProvider(EXAMPLES / "questions.json")

    def test_preview_generates_three_questions_without_comment_access(self):
        analysis, questions, body = run_check(self.github, self.provider)
        self.assertEqual(len(questions), 3)
        self.assertIn("Q3", body)
        self.github.find_comment.assert_not_called()
        self.github.publish_comment.assert_not_called()

    def test_post_uses_existing_comment(self):
        self.github.find_comment.return_value = {"id": 99}
        run_check(self.github, self.provider, dry_run=False)
        self.assertEqual(self.github.publish_comment.call_args.args[1], {"id": 99})

    def test_head_or_base_update_during_generation_prevents_posting(self):
        for side in ("head", "base"):
            with self.subTest(side=side):
                changed = copy.deepcopy(self.fixture["pull_request"])
                changed[side]["sha"] = "updated"
                self.github.fetch_pull_request.side_effect = [self.fixture["pull_request"], self.fixture["pull_request"], changed]
                with self.assertRaises(AppError):
                    run_check(self.github, self.provider, dry_run=False)
                self.github.publish_comment.assert_not_called()

    def test_update_while_fetching_files_prevents_llm_call(self):
        changed = copy.deepcopy(self.fixture["pull_request"])
        changed["head"]["sha"] = "updated"
        self.github.fetch_pull_request.side_effect = [self.fixture["pull_request"], changed]
        provider = Mock()
        with self.assertRaises(AppError):
            run_check(self.github, provider, dry_run=False)
        provider.generate_json.assert_not_called()

    def test_closed_or_draft_pr_is_never_posted(self):
        for key, value in (("state", "closed"), ("draft", True)):
            changed = copy.deepcopy(self.fixture["pull_request"])
            changed[key] = value
            self.github.fetch_pull_request.side_effect = [self.fixture["pull_request"], self.fixture["pull_request"], changed]
            with self.subTest(key=key), self.assertRaises(AppError):
                run_check(self.github, self.provider, dry_run=False)
            self.github.publish_comment.assert_not_called()

    def test_invalid_llm_output_preserves_existing_comment(self):
        provider = Mock()
        provider.generate_json.return_value = '{"questions": []}'
        with self.assertRaises(AppError):
            run_check(self.github, provider, dry_run=False)
        self.github.publish_comment.assert_not_called()


class ProviderTests(unittest.TestCase):
    def test_openai_sends_schema_and_parses_completed_content(self):
        http = Mock()
        http.request.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": question_json()}}]}
        content = OpenAIProvider(http, "example-model").generate_json("instruction", "context", QUESTION_SCHEMA)
        self.assertEqual(content, question_json())
        method, path, payload = http.request.call_args.args
        self.assertEqual((method, path), ("POST", "/chat/completions"))
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertFalse(payload["store"])

    def test_openai_refusal_truncation_and_missing_content_fail(self):
        for response in (
            {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"refusal": "no", "content": None}}]},
            {"choices": []},
        ):
            http = Mock()
            http.request.return_value = response
            with self.subTest(response=response), self.assertRaises(AppError):
                OpenAIProvider(http, "model").generate_json("", "", QUESTION_SCHEMA)

    def test_ollama_sends_nonstreaming_schema_and_context_limit(self):
        http = Mock()
        http.request.return_value = {"done": True, "message": {"content": question_json()}}
        self.assertEqual(OllamaProvider(http, "local-model").generate_json("i", "c", QUESTION_SCHEMA), question_json())
        payload = http.request.call_args.args[2]
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"], QUESTION_SCHEMA)
        self.assertEqual(payload["options"]["num_ctx"], 32768)

    def test_ollama_truncated_generation_fails(self):
        http = Mock()
        http.request.return_value = {"done": True, "done_reason": "length", "message": {"content": "partial"}}
        with self.assertRaises(AppError):
            OllamaProvider(http, "model").generate_json("", "", QUESTION_SCHEMA)

    @patch.dict(os.environ, {"LLM_PROVIDER": "ollama", "LLM_MODEL": "local-model"}, clear=True)
    def test_ollama_does_not_require_an_openai_key(self):
        self.assertIsInstance(build_provider(), OllamaProvider)


class HttpTests(unittest.TestCase):
    def test_json_request_serializes_utf8_and_sets_auth_and_timeout(self):
        http = JsonHttp("https://example.com/v1", {"Authorization": "Bearer test"}, timeout=9)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok": true}'
        http.opener = Mock()
        http.opener.open.return_value = response
        self.assertEqual(http.request("POST", "/chat", {"question": "日本語"}), {"ok": True})
        request = http.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.com/v1/chat")
        self.assertEqual(json.loads(request.data), {"question": "日本語"})
        self.assertEqual(request.get_header("Authorization"), "Bearer test")
        self.assertEqual(http.opener.open.call_args.kwargs["timeout"], 9)

    def test_http_errors_do_not_leak_response_or_retry(self):
        http = JsonHttp("https://example.com")
        http.opener = Mock()
        http.opener.open.side_effect = HTTPError("https://example.com", 401, "secret", {}, io.BytesIO(b"private source"))
        with self.assertRaises(AppError) as caught:
            http.request("POST", "/")
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(http.opener.open.call_count, 1)

    def test_connection_errors_are_sanitized(self):
        http = JsonHttp("https://example.com")
        http.opener = Mock()
        http.opener.open.side_effect = URLError("secret endpoint")
        with self.assertRaises(AppError) as caught:
            http.request("GET", "/")
        self.assertNotIn("secret", str(caught.exception))

    def test_remote_cleartext_credentials_and_redirects_are_rejected(self):
        for url in ("http://example.com", "https://token@example.com", "https://example.com?key=secret"):
            with self.subTest(url=url), self.assertRaises(AppError):
                JsonHttp(url)
        JsonHttp("http://127.0.0.1:11434")
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))


class CliTests(unittest.TestCase):
    def test_offline_demo_writes_three_questions_without_network(self):
        with tempfile.TemporaryDirectory() as folder, patch("pr_understanding.http.build_opener") as opener:
            output, snapshot = Path(folder) / "comment.md", Path(folder) / "questions.json"
            with redirect_stdout(io.StringIO()):
                code = main(["--demo", "--output", str(output), "--json-output", str(snapshot)])
            self.assertEqual(code, 0)
            self.assertIn("### Q3", output.read_text(encoding="utf-8"))
            self.assertEqual(len(json.loads(snapshot.read_text(encoding="utf-8"))["questions"]), 3)
            opener.assert_not_called()

    def test_demo_cannot_post(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--demo", "--post"]), 1)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_configuration_fails_without_creating_output(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--pr", "1"]), 1)


class PipelineIntegrationTests(unittest.TestCase):
    def exercise_provider(self, provider_name):
        fixture = sample()
        comments = []
        writes = []
        inference_payloads = []

        def respond(request, timeout):
            path = urlsplit(request.full_url).path
            method = request.get_method()
            payload = json.loads(request.data) if request.data else None
            if path.endswith("/chat/completions"):
                inference_payloads.append(payload)
                response = {"choices": [{"finish_reason": "stop", "message": {"content": question_json()}}]}
            elif path == "/api/chat":
                inference_payloads.append(payload)
                response = {"done": True, "message": {"content": question_json()}}
            elif path == "/repos/demo/example/pulls/1":
                response = fixture["pull_request"]
            elif path == "/repos/demo/example/pulls/1/files":
                response = fixture["files"]
            elif method == "GET" and path == "/repos/demo/example/issues/1/comments":
                response = comments
            elif method == "POST" and path == "/repos/demo/example/issues/1/comments":
                writes.append(method)
                comments.append({"id": 42, "body": payload["body"], "user": {"login": "github-actions[bot]"}})
                response = comments[0]
            elif method == "PATCH" and path == "/repos/demo/example/issues/comments/42":
                writes.append(method)
                comments[0]["body"] = payload["body"]
                response = comments[0]
            else:
                self.fail(f"Unexpected request: {method} {path}")
            return io.BytesIO(json.dumps(response).encode("utf-8"))

        environment = {
            "GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "demo/example",
            "LLM_PROVIDER": provider_name, "LLM_MODEL": "test-model", "OPENAI_API_KEY": "test-key",
        }
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, environment, clear=True):
            output, snapshot = Path(folder) / "comment.md", Path(folder) / "questions.json"
            with patch("pr_understanding.http.build_opener") as opener, redirect_stdout(io.StringIO()):
                opener.return_value.open.side_effect = respond
                for _ in range(2):
                    code = main(["--pr", "1", "--post", "--output", str(output), "--json-output", str(snapshot)])
                    self.assertEqual(code, 0)
            self.assertEqual(writes, ["POST", "PATCH"])
            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["body"], output.read_text(encoding="utf-8"))
            self.assertEqual(len(inference_payloads), 2)
            self.assertIn("keepalive", inference_payloads[0]["messages"][1]["content"])
            self.assertEqual(len(json.loads(snapshot.read_text(encoding="utf-8"))["questions"]), 3)

    def test_openai_cli_fetches_diff_generates_posts_and_updates_through_http_boundary(self):
        self.exercise_provider("openai")

    def test_ollama_cli_fetches_diff_generates_posts_and_updates_through_http_boundary(self):
        self.exercise_provider("ollama")


if __name__ == "__main__":
    unittest.main()
