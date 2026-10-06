import html
import re

from .github import MARKER


def escape_text(text):
    text = " ".join(text.split())
    text = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text)
    return html.escape(text, quote=False).replace("@", "＠")


def render_comment(analysis, questions):
    partial = sum(changed["partial"] for changed in analysis["files"])
    omitted = analysis["omitted"]
    lines = [
        MARKER, "## 🤖 Code Understanding Check", "",
        "AIの解説を見る前に、自分の言葉で答えてみてください。", "",
        f"対象コミット: `{analysis['head_sha']}`", "",
        f"解析範囲: {len(analysis['files'])}/{analysis['total_files']} ファイル。部分差分: {partial} 件。",
        f"省略: 除外設定 {omitted['excluded']}、差分なし {omitted['no_patch']}、入力上限 {omitted['budget']}、API上限 {omitted['api_limit']} 件。", "",
    ]
    for question in questions:
        lines.extend([f"### {question.id}", escape_text(question.question), "",
                      "対象: " + " / ".join(escape_text(path) for path in question.files), ""])
    lines.extend(["回答は通常のPRコメントに Q1 / Q2 / Q3 を付けて記入できます。", "",
                  "この初期版では回答の自動評価は行いません。AIの設問には誤りが含まれる場合があります。"])
    return "\n".join(lines) + "\n"
