from __future__ import annotations

from typing import Protocol

from .http import AppError


class JsonGenerator(Protocol):
    def generate_json(self, instruction: str, context: str, schema: dict) -> str:
        ...


class OpenAIProvider:
    def __init__(self, http, model: str, max_tokens=2000):
        self.http, self.model, self.max_tokens = http, model, max_tokens

    def generate_json(self, instruction, context, schema):
        response = self.http.request("POST", "/chat/completions", {
            "model": self.model, "store": False, "max_completion_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": context}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "understanding_check", "strict": True, "schema": schema,
            }},
        })
        try:
            choice = response["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("refusal"):
                raise AppError("OpenAI refused or did not finish generating questions.")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise AppError("OpenAI returned no question text.")
            return content
        except (KeyError, IndexError, TypeError):
            raise AppError("Unexpected OpenAI response structure.") from None


class OllamaProvider:
    def __init__(self, http, model: str, max_tokens=2000, context_size=32768):
        self.http, self.model = http, model
        self.max_tokens, self.context_size = max_tokens, context_size

    def generate_json(self, instruction, context, schema):
        response = self.http.request("POST", "/api/chat", {
            "model": self.model, "stream": False, "format": schema,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": context}],
            "options": {"temperature": 0.2, "num_predict": self.max_tokens, "num_ctx": self.context_size},
        })
        try:
            if response.get("done") is not True or response.get("done_reason") == "length":
                raise AppError("Ollama did not finish generating questions.")
            content = response["message"]["content"]
            if not isinstance(content, str):
                raise AppError("Ollama returned no question text.")
            return content
        except (KeyError, TypeError):
            raise AppError("Unexpected Ollama response structure.") from None


class FixtureProvider:
    def __init__(self, path):
        self.path = path

    def generate_json(self, instruction, context, schema):
        return self.path.read_text(encoding="utf-8")
