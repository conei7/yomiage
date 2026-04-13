# yomiage

VOICEVOX を使った Discord 読み上げボットです。

## ディレクトリ構成

```
├── bot.py                  エントリーポイント
├── config/
│   ├── settings.json       ボット設定（公開可）
│   └── settings.private.json  トークン等の秘密情報（git管理外）
├── data/
│   ├── dictionary.json     読み辞書
│   ├── user_data.json      ユーザーごとの話者・速度設定
│   └── server_config.json  サーバー設定（自動参加・チャンネルバインド）
├── cogs/
│   └── maincog.py          スラッシュコマンド・イベント処理
├── core/
│   ├── config.py           設定読み込み
│   ├── json_io.py          JSON読み書き（アトミック書き込み）
│   ├── ffmpeg.py           ffmpeg自動ダウンロード
│   ├── text_normalizer.py  テキスト正規化
│   ├── text_processor.py   読み上げ用テキスト加工
│   ├── voice.py            VOICEVOX連携・VC接続管理
│   └── views.py            Discord UIコンポーネント
└── tools/
    └── check_speakers.py   話者一覧確認スクリプト
```

## セットアップ

### 1. 前提条件

- Python 3.10+
- [VOICEVOX エンジン](https://voicevox.hiroshiba.jp/)（ポート 50021 で起動）
- ffmpeg（PATH に通っているか、初回起動時に自動ダウンロード可）

### 2. インストール

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### 3. 秘密情報の設定

`config/settings.private.json` を編集し、Discord Bot トークンとギルド ID を設定します。

```json
{
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "target_guild_id": 123456789012345678
}
```

> このファイルは `.gitignore` で管理外になっています。

### 4. 起動

```bash
python bot.py
```

## スラッシュコマンド

| コマンド | 説明 |
|---|---|
| `/vc` | ボイスチャンネルに接続・切断 |
| `/setvoice` | 読み上げ話者を選択 |
| `/setspeed` | 読み上げ速度を設定（初期値: 1.0） |
| `/show_all_speakers` | 利用可能な話者一覧を表示 |
| `/skip` | 現在の読み上げをスキップ |
| `/add_dict` | 辞書に単語と読みを追加 |
| `/del_dict` | 辞書から単語を削除 |
| `/import_dict` | 辞書データを JSON ファイルからインポート |
| `/export_dict` | 辞書データを JSON ファイルとしてエクスポート |
| `/bind` | VC とテキストチャンネルをバインド（自動参加用） |
| `/unbind` | VC のバインドを解除 |
| `/toggle_auto_join` | VC 自動参加の ON/OFF |
| `/help` | コマンド一覧を表示 |
| `/zunda` | ボット情報を表示 |

## 設定ファイル

### config/settings.json

ボットの基本設定です。直接編集して変更できます。

| キー | 説明 |
|---|---|
| `max_message_length` | 読み上げ最大文字数 |
| `default_bot_speed` | ボット自身の読み上げ速度 |
| `default_bot_speaker` | ボット自身の話者 ID |
| `default_user_speed` | ユーザーの初期読み上げ速度 |
| `default_user_speaker` | ユーザーの初期話者 ID |
| `exceptional_bots` | 読み上げ対象にする Bot の ID リスト |
| `default_embed_color` | Embed のカラーコード |

### data/server_config.json

サーバーごとの動作設定です。コマンドからも変更できます。

| キー | 説明 |
|---|---|
| `auto_join_vc` | VC 自動参加の有効/無効 |
| `channel_bindings` | VC ID → テキストチャンネル ID のマッピング |
