# yomiage

VOICEVOX を使った Discord 読み上げボットです。

AutoMonitorなどから管理する場合、`SBC_MANAGED=1`で起動するとVOICEVOXのDocker起動をbot自身から行いません。エンジンの起動は運用側で行い、停止中は接続回復後の読み上げを待ちます。`DISCORD_BOT_TOKEN`または秘密設定ファイルでトークンを設定できます。

## ディレクトリ構成

```
├── bot.py                  エントリーポイント
├── config/
│   ├── settings.json       ボット設定（公開可）
│   └── settings.private.json  トークン等の秘密情報（git管理外）
├── data/
│   ├── dictionary.json     読み辞書の初期テンプレート
│   ├── dictionary.private.json  サーバー別の実行時辞書（git管理外）
│   ├── user_data.json      ユーザー設定の初期テンプレート
│   ├── user_data.private.json  実行時ユーザー設定（git管理外）
│   ├── server_config.json  サーバー設定の初期テンプレート
│   └── server_config.private.json  実行時サーバー設定（git管理外）
├── cogs/
│   └── maincog.py          スラッシュコマンド・イベント処理
├── core/
│   ├── config.py           設定読み込み
│   ├── guild_data.py       サーバー別データ分離・旧形式移行
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
- ffmpeg（PATH に通っているか、初回起動時に固定リリースをSHA-256検証して自動取得）

#### 2. インストール

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### 3. 秘密情報の設定

`config/settings.private.json` を作成し、管理者ユーザー ID を設定します。シングルアカウントでは、Botトークンは平文ファイルに残さず `DISCORD_BOT_TOKEN` 環境変数で渡すことを推奨します。

```powershell
$secureToken = Read-Host -AsSecureString "Bot token"
$env:DISCORD_BOT_TOKEN = [System.Net.NetworkCredential]::new('', $secureToken).Password
```

**シングルアカウント（通常）:**
```json
{
    "admin_users": [YOUR_DISCORD_USER_ID]
}
```

環境変数を使えない場合のみ、互換用の `bot_token` をこのJSONへ追加してください。その場合はファイルの読取権限をBot実行ユーザーだけに制限してください。

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

> このファイルは `.gitignore` で管理外ですが、それだけではローカルの読取を防げません。アクセス権も必ず制限してください。

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

Botトークンはリポジトリ内へ保存せず、起動プロセスの環境変数へ設定します。

```bash
cat > config/settings.private.json << 'EOF'
{
    "admin_users": [YOUR_DISCORD_USER_ID]
}
EOF
chmod 600 config/settings.private.json

# 手動起動時
read -rsp 'Bot token: ' DISCORD_BOT_TOKEN && echo
export DISCORD_BOT_TOKEN
```

#### 5. 起動

```bash
python bot.py
```

#### 6. systemd でサービス化（任意）

```bash
# systemd専用のトークンファイル（rootだけが読み書き可能）
sudo install -m 600 /dev/null /etc/yomiage.env
sudoedit /etc/yomiage.env
# 次の1行を入力: DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN_HERE

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
EnvironmentFile=/etc/yomiage.env

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
| `/toggle_announce` | 入退室読み上げの ON/OFF |
| `/add_dict` | 辞書に単語と読みを追加 |
| `/del_dict` | 辞書から単語を削除 |
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

### data/server_config.private.json

`data/server_config.json` を初期値として作られる、サーバーごとの実行時設定です。コマンドからも変更できます。

実行時は `guilds.<guild_id>` 配下へ分離して保存します。旧形式のトップレベル設定も既定値として自動的に読み継ぎます。

| キー | 説明 |
|---|---|
| `auto_join_vc` | VC 自動参加の有効/無効 |
| `announce_join_leave` | 入退室読み上げの有効/無効 |
| `channel_bindings` | VC ID → テキストチャンネル ID のマッピング |
