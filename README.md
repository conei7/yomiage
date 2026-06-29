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
│   ├── ffmpeg.py           ffmpeg自動ダウンロード（Windows）
│   ├── text_normalizer.py  テキスト正規化
│   ├── text_processor.py   読み上げ用テキスト加工
│   ├── voice.py            VOICEVOX連携・VC接続管理
│   └── views.py            Discord UIコンポーネント
└── tools/
    └── check_speakers.py   話者一覧確認スクリプト
```

## セットアップ

### Windows

#### 1. 前提条件

- Python 3.10+
- [VOICEVOX エンジン](https://voicevox.hiroshiba.jp/)（ポート 50021 で起動）
- ffmpeg（PATH に通っているか、初回起動時に自動ダウンロード可）

#### 2. インストール

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### 3. 秘密情報の設定

`config/settings.private.json` を作成し、Discord Bot トークンと管理者ユーザー ID を設定します。

**シングルアカウント（通常）:**
```json
{
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "admin_users": [YOUR_DISCORD_USER_ID]
}
```

**マルチアカウント（複数ボットを並列起動）:**
```json
{
    "admin_users": [YOUR_DISCORD_USER_ID],
    "accounts": [
        { "bot_token": "TOKEN_FOR_BOT_1", "label": "bot1" },
        { "bot_token": "TOKEN_FOR_BOT_2", "label": "bot2" }
    ]
}
```

- `accounts` リストを書くと、各エントリを別スレッドで並列起動します。
- `label` はログ出力の識別子です（省略すると `bot1`, `bot2`, ... が自動付与）。
- `data/` や `config/` ファイルはすべてのアカウントで共有されます。
- 各エントリはトップレベルの設定を継承し、必要なキーだけ上書きできます。

> このファイルは `.gitignore` で管理外になっています。

#### 4. 起動

```bash
python bot.py
```

### Ubuntu / Linux

#### 1. 前提パッケージのインストール

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg libffi-dev libnacl-dev libopus0
```

#### 2. VOICEVOX エンジンの準備

Docker を使う方法（推奨）:

```bash
# CPU版
docker run -d --name voicevox -p 50021:50021 voicevox/voicevox_engine:cpu-latest

# GPU版 (NVIDIA GPU がある場合)
docker run -d --name voicevox --gpus all -p 50021:50021 voicevox/voicevox_engine:nvidia-latest
```

動作確認:

```bash
curl http://127.0.0.1:50021/version
# バージョン文字列が返れば OK
```

> Linux では、ボット起動時に VOICEVOX に接続できない場合、`docker start voicevox` で既存コンテナの自動起動を試みます。

#### 3. インストール

```bash
git clone https://github.com/conei7/yomiage.git
cd yomiage
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 4. 秘密情報の設定

```bash
cat > config/settings.private.json << 'EOF'
{
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "admin_users": [YOUR_DISCORD_USER_ID]
}
EOF
```

#### 5. 起動

```bash
python bot.py
```

#### 6. systemd でサービス化（任意）

```bash
sudo nano /etc/systemd/system/yomiage.service
```

```ini
[Unit]
Description=Yomiage Discord Bot
After=network.target docker.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/yomiage
ExecStart=/home/YOUR_USERNAME/yomiage/.venv/bin/python bot.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable yomiage
sudo systemctl start yomiage

# ログ確認
sudo journalctl -u yomiage -f

# VOICEVOX コンテナも自動起動にする場合
docker update --restart unless-stopped voicevox
```

## スラッシュコマンド

### 一般コマンド

| コマンド | 説明 | 応答 |
|---|---|---|
| `/vc` | ボイスチャンネルに接続・切断 | 全体 |
| `/setvoice` | 読み上げ話者を選択 | 自分のみ |
| `/setspeed` | 読み上げ速度を設定（0.5〜2.0、初期値: 1.0） | 自分のみ |
| `/mute` | 自分のメッセージの読み上げを ON/OFF | 自分のみ |
| `/mysettings` | 現在の読み上げ設定を確認 | 自分のみ |
| `/skip` | 現在の読み上げをスキップ | 全体 |
| `/show_all_speakers` | 利用可能な話者一覧を表示 | 自分のみ |
| `/add_dict` | 辞書に単語と読みを追加 | 全体 |
| `/del_dict` | 辞書から単語を削除 | 全体 |
| `/help` | コマンド一覧を表示 | 自分のみ |
| `/zunda` | ボット情報を表示 | 自分のみ |

### 管理者コマンド（manage_guild 権限 または admin_users）

| コマンド | 説明 |
|---|---|
| `/set_auto_channel` | 自動接続する VC とテキストチャンネルを設定 |
| `/bind` | VC とテキストチャンネルをバインド |
| `/unbind` | VC のバインドを解除 |
| `/show_bindings` | バインド一覧を表示 |
| `/toggle_auto_join` | VC 自動参加の ON/OFF |
| `/import_dict` | 辞書データを JSON ファイルからインポート |
| `/export_dict` | 辞書データを JSON ファイルとしてエクスポート |

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
