# PR Understanding Check — MVP

対象リポジトリ: [okamoto-suzukilab/code-review-pr](https://github.com/okamoto-suzukilab/code-review-pr)

GitHub PRの差分から、人間がコードを理解するための3問を日本語で生成し、PRコメントに投稿します。
目的・動作の変化・境界条件／テストを1問ずつ出題します。
PRコメントで回答すると、理解できている点・補足が必要な点・再回答のヒントを日本語で返します。

Python 3.9以上、標準ライブラリのみ。DB・Webサーバー・pip installは不要です。

```text
GitHub Actions → GitHub REST API → ファイル別diff解析
                                      ↓
                         JsonGenerator（LLM共通インターフェース）
                           ├─ OpenAI / Chat Completions互換API
                           └─ Ollama /api/chat
                                      ↓
                         JSON検証 → 3問＋設問セット保存 → PRコメント作成・更新
                                      ↓
                         /answer コメント → 回答評価 → フィードバック
```

## まずはキーなしで試す

このREADMEと同じディレクトリで実行します。

```sh
python3 -m unittest discover -s tests -v
python3 -m pr_understanding --demo
cat work/comment.md
```

デモは同梱のPRと固定の設問JSONを使う完全オフラインの動作例です。
実際のLLM推論やGitHub投稿はしません。出力は `work/comment.md` と `work/questions.json`。
生成される見た目は [コメント例](examples/comment.md) でも確認できます。

## GitHub Actionsで自動投稿する（OpenAI）

1. この実装のPull Requestを `main` にマージします。
   別リポジトリへ導入する場合は `pr_understanding/`、`.github/`、`tests/`、`examples/` をルートへコピーします。
   既存workflowと名前が重なる場合は統合します。
2. **先にデフォルトブランチ、および自動実行したいPRのベースブランチへ導入をマージ**します。
   出題ジョブはベースコミットのコードを実行するため、導入PR自身ではまだ出題できません。
3. Settings → Secrets and variables → Actions に以下を登録します。

   | 種類 | 名前 | 値 |
   | --- | --- | --- |
   | Secret | `OPENAI_API_KEY` | APIキー |
   | Variable | `LLM_MODEL` | 利用可能なStructured Outputs対応モデル名（例 `gpt-4o-mini`） |
   | Variable・任意 | `MAX_DIFF_CHARS` | 既定 `24000` |
   | Variable・任意 | `EXCLUDE_GLOBS` | `private/*,secrets/*` など |

4. Actionsが許可され、組織ポリシーがworkflowの `pull-requests: write` を許可していることを確認します。
   `GITHUB_TOKEN` は自動発行されるためSecretへの追加は不要です。
5. 同じリポジトリ内のブランチから、通常のPRを作成／更新します。
   Actions → **PR understanding check** を確認し、PRのConversationに3問が投稿されれば完了です。
   再実行または追加pushでは、専用マーカーと投稿者が一致する既存コメントを更新します。

自動実行の対象は `opened`、`synchronize`、`reopened`、`ready_for_review`。
draft、forkからのPR、DependabotのPRは対象外です。PRタイトルだけの編集では再生成しません。
LLM APIの呼び出しには利用先の課金が適用されます。再実行も再生成するためAPIを呼びます。

### workflowの実行境界

コメント書き込み用に `pull_request_target` を使用します。
**checkout対象は `base.sha` 固定で、PR head・merge refのコード、スクリプト、依存関係は実行しません。**
ベースブランチに導入したコードとworkflowを信頼境界にします。PR差分はAPIで文字列として取得するだけです。
今後このworkflowへPRコードを実行するテストやinstall手順を追加しないでください。
通常のテストは別の `tests.yml` が読み取り権限で実行します。

## ローカルから本物のPRを試す

```sh
cp .env.example .env
# .envを編集し、トークン、リポジトリ、モデルを設定
set -a
source .env
set +a

# 既定はプレビュー。GitHubの読み取りとLLM呼び出しは実行する
python3 -m pr_understanding --pr 123 --dry-run
cat work/comment.md

# 投稿を明示すると、作成または既存コメントの更新を行う
python3 -m pr_understanding --pr 123 --post
```

`.env` は自動読込しません。自分で編集したファイルをsourceします。
`--repo owner/repository` は `GITHUB_REPOSITORY` を上書きできます。
fine-grained PATを使う場合は対象リポジトリへの **Pull requests: Read and write** を付与し、
`COMMENT_AUTHOR` をPAT所有者のGitHubログイン名へ設定します。プレビューだけならreadで足ります。
Actionsの既定投稿者は `github-actions[bot]`。別の投稿者のコメントは更新しません。
ローカルとActionsで投稿者が異なると、それぞれにコメントができます。

## 回答とフィードバック（OpenAI / GitHub Actions）

新しい出題コメントには「設問セットID」とコピーできる回答形式が表示されます。
PRのConversationに、次の形式で**新しいコメント**を投稿してください。

```text
/answer 0123456789abcdef Q1 ページ離脱時にもタスク解放リクエストの送信を継続するためだと考えます。
```

`0123456789abcdef` は実際の設問セットIDに置き換えます。Q1〜Q3のいずれか1問、
回答は1〜4000文字。コマンドの後に改行して回答を書くこともできます。
`/answer Q1 回答` だけでは、PR更新前の同じQ番号と区別できないため受け付けません。

Actionsの **Answer understanding feedback** が実行され、通常のPRコメントとして以下を返します。

- 理解できている点
- 補足が必要な点
- 再回答のヒント

判定は「理解できています」「補足して再回答してみましょう」「差分だけでは判断できません」の3種類です。
点数と模範解答は生成しないよう指示しています。LLMの内容は機械的に完全保証できないため確認してください。
改善して再回答する場合は、同じ設問セットIDとQ番号で新しいコメントを投稿します。
回答コメントを編集しても評価は再実行されません。

### 初回利用と条件

1. 回答評価機能のPRをデフォルトブランチ `main` にマージします。
2. 既存のPRでは **PR understanding check → Run workflow** を使うか、追加pushで出題し直します。
   以前の出題には設問セットの保存情報がないため、再出題が必要です。
3. 出題された回答形式をコピーし、PRへコメントします。

既存PRを最新の出題コードで実行する場合は、Actions → **PR understanding check** → **Run workflow** を開き、
ブランチに `main`、入力にPR番号を指定してください。手動実行はデフォルトブランチ限定です。
過去の実行の「Re-run jobs」は当時のイベントとベースコミットを再利用するため、機能導入前の実行では
新しい設問形式にならない場合があります。機能導入後はRun workflowか追加pushで新しい実行を作ってください。

既存の `OPENAI_API_KEY` と `LLM_MODEL` をそのまま使用します。新しいSecretは不要です。
評価対象は同じリポジトリ内のopen・非draftのPRです。回答者はリポジトリへのwrite・maintain・admin権限を持つ
人間ユーザーに限定し、GitHub APIで権限を確認します。bot、通常の会話、fork PRは評価しません。
そのため、一般の外部閲覧者が回答してもLLM API呼び出しは発生しません。

### 出題と回答の取り違え防止

設問コメント内に、repository・PR番号・base/head SHA・設問本文・参照ファイル・差分の指紋を
HTMLコメントとして保存します。これは暗号化された情報ではありません。コード本文は保存せず、
必要な差分をGitHub APIから再取得し、出題時の指紋と照合します。DBや別のストレージは不要です。

- 設問セットIDはコミット・設問内容・差分の指紋から決定します。
- 最新の出題コメントをbotの投稿者名とマーカーで確認し、その設問セットにだけ回答できます。
- PR・設問が更新されていた場合は評価を止め、再出題／最新の回答形式を案内します。
- 出題時に省略した差分を全体として評価しません。対象が現在の除外設定に入った場合もLLMへ送りません。
- 評価済み回答には回答コメントIDのマーカーが付き、同じイベントの再実行ではLLMも投稿もスキップします。
- 評価中に回答が編集された場合は投稿を止めます。新しいコメントとして再回答してください。
- フィードバックには元の回答へのリンク、設問IDとコミットを表示します。

回答ごとにLLM APIを1回呼びます。形式違反や古い設問への回答には、LLMを呼ばず案内を返します。
既存の返信がある再実行もLLMを呼びません。API失敗・不正な評価JSONの場合はActionsが失敗し、
不完全な評価を投稿しません。管理者はActionsから再実行できます。
入力の回答本文と該当設問の差分は外部APIへ送信されます。

### ローカルからのプレビュー

GitHubのissue_commentイベントJSONをファイルへ保存し、通常の環境変数に加えて設定します。

```sh
export GITHUB_EVENT_PATH=/absolute/path/to/answer-event.json
python3 -m pr_understanding.answer_cli --dry-run
# 内容を確認後に投稿する場合
python3 -m pr_understanding.answer_cli --post
```

出力は `work/feedback.md`。イベントの元コメントがGitHub上に存在し、投稿者・本文・PRが一致する必要があります。
Actionsの出題者が `github-actions[bot]` なら、ローカル評価でも `COMMENT_AUTHOR=github-actions[bot]` にします。
PATで評価を投稿すると投稿者が変わり、再実行で既存のフィードバックを検出できない場合があります。
同じ投稿者のトークンを使用するのが推奨です。

### workflowの実行境界

`issue_comment: created` はデフォルトブランチ上のworkflowだけを実行します。
checkoutはイベントの `github.sha` 固定です。回答本文はイベントファイルから読み込み、
シェルやworkflowへ埋め込みません。PRのheadコードを実行する処理はありません。
botの返信と編集イベントでは評価しないため、返信による無限ループを防ぎます。
同じ回答の実行を直列化し、別の回答は並行処理できます。評価と出題の競合は投稿直前にも確認しますが、
GitHub APIに原子的な条件付きコメント投稿がないため、最終確認直後の更新との競合余地は残ります。

## Ollamaへ切り替える

Ollamaをインストールし、使用するモデルをダウンロードしてサーバーを起動します。
以下はモデル名の一例です。使用モデルとマシンに応じて容量・速度・出題品質を確認してください。

```sh
ollama pull qwen2.5-coder:7b
ollama serve
```

既にOllamaアプリが稼働中なら `ollama serve` は不要です。別のターミナルで実行します。

```sh
export LLM_PROVIDER=ollama
export LLM_MODEL=qwen2.5-coder:7b
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export MAX_DIFF_CHARS=12000
export HTTP_TIMEOUT_SECONDS=300
python3 -m pr_understanding --repo okamoto-suzukilab/code-review-pr --pr 123 --dry-run
```

GitHub用の環境変数は引き続き必要ですが、`OPENAI_API_KEY` は不要です。
JSON Schema対応のローカル `/api/chat` を使用します。モデルによって形式違反で停止する場合があります。

### Mac上のOllamaをActionsから使う

GitHub-hosted runnerのlocalhostはあなたのMacではありません。
MacにPython 3.9以上とOllamaを用意し、対象リポジトリの Settings → Actions → Runners の手順で
self-hosted runnerを登録して、カスタムラベル `ollama` を付けます。

1. `examples/ollama-workflow.yml` を `.github/workflows/ollama.yml` へコピーしてデフォルトブランチへマージ。
2. Repository Variable `OLLAMA_MODEL` にダウンロード済みモデル名を設定。
3. MacでrunnerとOllamaを稼働させる。
4. Actions → **PR understanding with local Ollama** → Run workflow → デフォルトブランチとPR番号を指定。

この例は信頼するリポジトリでの**手動実行専用**です。デフォルトブランチ以外からは実行しません。
個人Macを外部PRの自動実行マシンにしないでください。Ollamaポートのインターネット公開も不要です。
OpenAIの自動workflowが不要なら `understanding.yml` を無効化／削除してください。
両workflowはPR番号単位で同じconcurrency groupを使用し、重複投稿を抑えます。

## 環境変数

| 名前 | 既定／用途 |
| --- | --- |
| `GITHUB_TOKEN` | 必須。PRを読む／コメントを投稿するトークン |
| `GITHUB_REPOSITORY` | `owner/repo`。`--repo`でも指定可 |
| `COMMENT_AUTHOR` | `github-actions[bot]`。PATの場合は所有者名 |
| `GITHUB_API_URL` | `https://api.github.com` |
| `LLM_PROVIDER` | `openai` または `ollama` |
| `LLM_MODEL` | 必須。利用先に存在するモデル名 |
| `OPENAI_API_KEY` | OpenAI利用時のみ必須 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1`。互換APIはJSON Schemaと `max_completion_tokens` 対応が必要 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` |
| `OLLAMA_CONTEXT_SIZE` | `32768`。Ollamaへ要求するコンテキスト長 |
| `HTTP_TIMEOUT_SECONDS` | LLM待ち時間 `120` 秒。GitHubは30秒 |
| `MAX_OUTPUT_TOKENS` | `2000`。出力トークン上限 |
| `MAX_DIFF_CHARS` | 差分本文合計 `24000` 文字。1000〜100000 |
| `MAX_FILES` | 解析対象の最大 `40` ファイル。1〜100 |
| `EXCLUDE_GLOBS` | 追加除外パターンをカンマ区切りで指定 |

workflowへ追加設定を反映するには該当ステップの `env` にも追加します。
HTTPはlocalhostのみ許可し、それ以外のエンドポイントはHTTPS必須です。

## 解析とエラー時の動作

- GitHubのファイル一覧を100件ずつ、最大3000件取得します。各ファイルのunified diff、変更種別、
  拡張子、追加／削除数、hunk見出しをまとめてLLMへ渡します。AST解析やTree-sitterは初期版では使いません。
- API返却順で最大40ファイル、1ファイル最大6000文字、差分合計最大24000文字を採用します。
  これらは文字数であり厳密なトークン数ではありません。長いPRでは上限を下げるかPRを分割してください。
- `.env` 系、`.pem`、`.key`、lockファイルは既定で除外します。バイナリやpatchがないファイルも省略します。
  この除外は秘密情報の検出を保証しません。OpenAI利用時は採用されたファイル名・差分・PRタイトル等が外部へ送信されます。
- 省略数と部分差分数をコメントに表示します。解析対象がゼロの場合はLLM呼び出しも投稿もせず終了コード1。
- JSONの構造、3問、観点の順序、文字数、重複、参照ファイルの存在を検証します。
  拒否・途中終了・不正JSON・不正な参照は投稿せず終了コード1です。既存コメントは維持されます。
- PRのbase/head SHAを取得前後と投稿直前に比較し、途中更新・close・draft化を検出した場合は投稿しません。
  GitHubには「SHAが同じ場合だけコメントする」原子的APIがないため、最終確認直後の更新との小さな競合余地は残ります。
- 同じPRのActionsジョブは直列化します。ローカルからの同時実行までは排他しません。
- 通信失敗時は停止し、本文・APIキーはログへ出しません。課金や二重投稿を避けるため自動リトライはしません。
  タイムアウトでは投稿済みの可能性があるためPRを確認後に再実行してください。
- LLMにはツールや認証情報を渡しません。入力内の命令を無視するプロンプトを使い、投稿文のHTML／Markdownとメンションを無害化します。
  構造検証は設問の意味や正確性を保証しないため、人間による確認は必要です。

## 構成と回答評価の拡張

| ファイル | 責務 |
| --- | --- |
| `github.py` | PR・差分取得、投稿者を照合したコメント更新 |
| `analysis.py` | 差分の構造化、入力上限と除外 |
| `llm.py` | `JsonGenerator` ProtocolとOpenAI／Ollamaアダプター |
| `questions.py` | 出題プロンプト、JSON Schema、設問の検証 |
| `comments.py` | コメント整形 |
| `service.py` | 取得→解析→生成→投稿の処理順序と更新検知 |
| `snapshots.py` | 設問セットの保存・照合と評価用差分の復元 |
| `evaluation.py` | 回答形式、回答者確認、LLM評価、重複防止、返信の整形 |
| `answer_cli.py` | issue_commentイベントからの回答評価 |
| `__main__.py` | 環境変数、CLI、ファイル出力 |
| `http.py` | タイムアウト付きHTTP、秘密を含まないエラー |

新しいLLMは `generate_json(instruction, context, schema) -> str` を実装し、
`build_provider()` に分岐を追加します。GitHubや出題ロジックを変更する必要はありません。

`questions.json` はschema_version=2、repository、PR番号、base/head SHA、set_id、
設問ID（Q1〜Q3）・観点・本文・参照ファイル・差分の指紋を保存します。
同じスナップショットをPRコメントにも保存し、Actions実行後も回答評価に利用できます。
`evaluation.py` は出題と同じ `JsonGenerator` を使用し、評価専用のJSON Schemaとプロンプトを渡します。
将来は回答履歴のダッシュボード、学習者ごとの記録、評価基準の調整をこの境界から追加できます。

## よくある問題

| 症状 | 確認すること |
| --- | --- |
| HTTP 401 / 403 | トークン権限、Secret、組織ポリシー、利用先のアクセス権 |
| HTTP 400 / 404 | モデル名、Structured Outputs対応、API URL、Ollamaのモデルpull |
| HTTP 429 | 利用先の上限・課金設定。時間を置いて手動再実行 |
| connection failed | Ollama稼働、localhostの実行マシン、タイムアウト |
| invalid JSON / did not finish | 対応モデル、MAX_OUTPUT_TOKENS、差分サイズ・コンテキスト長 |
| workflowがskip | fork／draft／Dependabot／手動Ollamaのブランチ選択 |
| No module named pr_understanding | 実行位置、ベースブランチへのコード導入を確認 |

## 参照した公式仕様

- [GitHub: PRファイル一覧](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests-files)
- [GitHub: PRへのissueコメント](https://docs.github.com/en/rest/issues/comments)
- [GitHub: pull_request_target](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request_target)
- [OpenAI: Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [OpenAI: Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Ollama: chat API](https://docs.ollama.com/api/chat)
- [Ollama: Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)
