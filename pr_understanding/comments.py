import html
import re

from .github import MARKER
from .snapshots import encode_snapshot


def escape_text(text):
    text = " ".join(text.split())
    text = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text)
    return html.escape(text, quote=False).replace("@", "＠")


def render_comment(analysis, questions, snapshot=None):
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
    if snapshot:
        lines.extend([
            f"設問セットID: `{snapshot['set_id']}`", "",
            "回答するには、以下の形式で新しいPRコメントを投稿してください。1コメントで複数問に回答できます。", "",
            f"```text\n/answer {snapshot['set_id']}\nQ1：ここに自分の回答を書く\nQ2：ここに自分の回答を書く\nQ3：ここに自分の回答を書く\n```", "",
            "答える問だけ残せます。Q番号の後は空白・改行・コロン・セミコロン（全角も可）で区切ってください。再回答は新しいコメントで投稿してください。",
            "AIは理解できている点・補足が必要な点・ヒントを返します。", "",
            encode_snapshot(snapshot),
        ])
    else:
        lines.extend(["回答は通常のPRコメントに Q1 / Q2 / Q3 を付けて記入できます。", "",
                      "この設問には回答評価用の保存情報がありません。"])
    lines.extend(["", "AIの設問・フィードバックには誤りが含まれる場合があります。"])
    return "\n".join(lines) + "\n"
