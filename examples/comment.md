<!-- pr-understanding:v1 -->
## 🤖 Code Understanding Check

AIの解説を見る前に、自分の言葉で答えてみてください。

対象コミット: `1234567890abcdef1234567890abcdef12345678`

解析範囲: 1/1 ファイル。部分差分: 0 件。
省略: 除外設定 0、差分なし 0、入力上限 0、API上限 0 件。

### Q1
releaseTask の fetch に keepalive: true を追加した意図は何だと考えますか。PRタイトルと差分を根拠に説明してください。

対象: src/releaseTask\.ts

### Q2
releaseTask のリクエスト送信直後にページを離れた場合、この変更はリクエストの扱いをどのように変えますか。送信の継続とサーバー処理の完了保証を分けて説明してください。

対象: src/releaseTask\.ts

### Q3
keepalive: true を指定してもタスク解放が完了しない可能性がある条件を1つ挙げ、その条件を確認するテストを提案してください。

対象: src/releaseTask\.ts

回答は通常のPRコメントに Q1 / Q2 / Q3 を付けて記入できます。

この初期版では回答の自動評価は行いません。AIの設問には誤りが含まれる場合があります。
