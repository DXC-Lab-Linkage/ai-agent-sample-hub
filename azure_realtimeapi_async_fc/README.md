# Azure OpenAI Realtime Voice Chat with Function Calling

Azure OpenAI Realtime APIを使用したリアルタイム音声対話アプリケーションです。Chainlitフレームワークを使用してWebベースのUIを提供し、音声とテキストの双方向コミュニケーションに加えて、非同期Function Calling機能を搭載しています。

## 🎯 機能

- **リアルタイム音声対話**: Azure OpenAI Realtime APIを使用した低遅延の音声会話
- **音声認識**: ユーザーの音声を自動的にテキストに変換
- **音声合成**: AIの回答を音声で再生
- **テキスト表示**: 音声のやり取りをリアルタイムでテキスト表示
- **日本語対応**: 日本語での音声認識・合成に最適化
- **Webベースの直感的なUI**: ブラウザから簡単にアクセス可能
- **🚀 非同期Function Calling**: 長時間実行される処理でも会話を継続
  - **天気予報取得**: 指定地域の天気情報を即座に取得
  - **データベース検索**: 時間のかかる検索処理を非同期で実行
  - **並行処理**: Function Call実行中も他の質問に応答可能

## 🚀 クイックスタート

### 前提条件・稼働確認環境

- Python 3.11.9(稼働確認済みの環境)
- Azure OpenAI サービスのアカウント
- Realtime APIが有効なAzure OpenAIデプロイメント

### インストール

依存関係をインストール：
```bash
pip install -r requirements.txt
```


### 設定

1. `.env.example`を`.env`にコピー
```bash
cp .env.example .env
```

2. `.env`ファイルを編集してAzure OpenAIの設定を入力
```env
AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
AZURE_OPENAI_API_KEY="your-api-key"
AZURE_OPENAI_API_VERSION="2025-04-01-preview"
AZURE_OPENAI_DEPLOYMENT="your-realtime-deployment-name"
```

3. Chainlitの音声機能を有効化
`.chainlit/config.toml`ファイルの`[features.audio]`セクションで音声機能がtrue(デフォルトはfalse)になっていることを確認
```toml
[features.audio]
    enabled = true
    sample_rate = 24000
```

### 実行

```bash
chainlit run app.py
```

ブラウザで `http://localhost:8000` にアクセスしてアプリケーションを使用できます。

### ブラウザの設定

音声機能を使用するために、ブラウザでマイクのアクセス許可が必要です。

**Chrome/Edge（動作確認済み）**:
- アドレスバーの ℹ️ アイコン（HTTP接続時）をクリック → 「マイク」を「許可」に設定

**その他のブラウザ**:
- 初回アクセス時にマイクの使用許可を求められた場合は「許可」を選択
- ブラウザの設定からサイトごとのマイク許可を設定

**注意**: 
- ローカル開発環境（http://localhost:8000）では、HTTPSではなくHTTPでアクセスするため、アドレスバーには鍵マーク（🔒）ではなく情報マーク（ℹ️）が表示されます
- ブラウザによって設定方法が異なる場合があります

## 📋 使用方法

### 基本的な使い方
1. **音声モード開始**: 「録音開始」ボタンをクリック
2. **音声で質問**: マイクに向かって話しかける
3. **AIの回答**: 音声とテキストで回答が表示される
4. **テキスト入力**: チャット欄からテキストでも質問可能

### Function Calling機能
#### 🌤️ 天気予報（即座に応答）
- 「東京の天気は？」
- 「明日の大阪の天気を教えて」
- 「札幌の今日の天気はどう？」

#### 🔍 データベース検索（非同期処理）
- 「AIについて調べて」
- 「機械学習の情報を検索して」
- 「データサイエンスについて教えて」

**💡 非同期処理の特徴**:
- データベース索には約20秒かかりますが、実行中も他の質問ができます
- 「検索を開始しました。他に何かお手伝いできることはありますか？」のように応答します
- 複数のFunction Callを同時実行可能です

## 🛠️ 技術スタック

- **チャットフレームワーク**: [Chainlit](https://chainlit.io/) - 対話型AIアプリケーション構築
- **Realtime API**: Azure OpenAI Realtime API - リアルタイム音声処理
- **音声処理**: PCM16フォーマット、ユーザ音声の文字起こし部分はWhisper音声認識
- **Function Calling**: 非同期実行によるツール呼び出し
- **非同期処理**: asyncio/awaitによる並行処理
- **言語**: Python

## 📁 プロジェクト構造

```
azure_realtimeapi_async_fc/
├── app.py                    # メインアプリケーション
├── requirements.txt          # Python依存関係
├── .env.example             # 環境変数テンプレート
├── chainlit.md              # Chainlit設定
├── .chainlit/
│   └── config.toml          # Chainlit設定ファイル（音声機能など）
└── README.md                # このファイル
```

## ⚙️ 設定オプション

### 環境変数

| 変数名 | 説明 | 必須 |
|--------|------|------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAIエンドポイントURL | ✅ |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI APIキー | ✅ |
| `AZURE_OPENAI_DEPLOYMENT` | Realtimeモデルのデプロイメント名 | ✅ |
| `AZURE_OPENAI_API_VERSION` | APIバージョン | 🔧 (デフォルト: 2025-04-01-preview) |


### 音声設定

- **入力フォーマット**: PCM16
- **出力フォーマット**: PCM16
- **音声認識**: Whisper-1 (日本語最適化)
- **VAD設定**: サーバーサイド音声検出
- **声質（voice）**: デフォルト設定を使用

## 🔧 カスタマイズ

### Function Callツールの追加

新しいツールを追加するには、`app.py`の`tools`配列に定義を追加し、対応する実行関数を作成してください。

```python
# ツール定義例
{
    "type": "function",
    "name": "your_new_tool",
    "description": "ツールの説明",
    "parameters": {
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "パラメータの説明"
            }
        },
        "required": ["param1"]
    }
}

# 実行関数例
async def your_new_tool(param1: str) -> dict:
    # ツールの処理
    return {"result": "処理結果"}
```

### システムプロンプトの変更

`app.py`の`instructions`パラメータを編集してください。

```python
"instructions": (
    "Always respond in Japanese. "
    "ユーザーとは日本語で会話してください。"
    "天気に関する質問があれば、get_weatherツールを使用して正確な情報を提供してください。"
    "データベース検索の依頼があれば、search_databaseツールを使用してください。"
    "重要: search_databaseツールは時間がかかる処理です。"
    "ツールの実行中でも、ユーザーとの会話は継続してください。"
),
```

### 音声検出の調整

`turn_detection`設定を変更：

```python
"turn_detection": {
    "type": "server_vad",
    "threshold": 0.6,           # 音声検出の閾値
    "silence_duration_ms": 700, # 無音判定時間
    # ...
}
```

### 声質（voice）の変更

AIの音声の声質を変更するには、`connection.session.update`部分に`voice`パラメータを追加してください。

```python
await connection.session.update(
    session={
        "modalities": ["text", "audio"],
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        # セッションの既定の声を設定
        "voice": "<voice_id>",  # 例: "alloy" / "marin" など、デプロイで有効な声
        "input_audio_transcription": {
            "model": "whisper-1",
            "language": "ja"
        },
        # ... その他の設定
    }
)
```

## 🐛 トラブルシューティング

### よくある問題

1. **接続エラー**: Azure OpenAIの認証情報を確認
2. **音声が認識されない**: 
   - ブラウザのマイク許可を確認
   - `.chainlit/config.toml`で`[features.audio] enabled = true`を確認
   - マイクが正常に動作しているか確認
3. **音声が再生されない**: ブラウザの音声設定を確認
4. **「録音開始」ボタンが表示されない**: Chainlitの音声機能が無効になっている可能性があります
5. **Function Callが実行されない**: 
   - instructionがFunction Callingに対応しているか確認
   - ツール定義のJSONスキーマが正しいか確認
6. **長時間処理が途中で止まる**: 
   - 非同期処理が正しく実装されているか確認
   - ログでエラーメッセージを確認

### 動作詳細の確認

デバッグモードで動作詳細を確認できます。

```bash
chainlit run app.py --debug
```

## 📄 注意点

- このアプリケーションはAzure OpenAI Realtime APIを使用するため、API使用料金が発生します。使用前に料金体系をご確認ください。

