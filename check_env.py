import os
from dotenv import load_dotenv

# カレントディレクトリ（現在地）を表示
print(f"現在の場所: {os.getcwd()}")

# ファイル一覧を表示
print(f"フォルダの中身: {os.listdir('.')}")

# .envを読み込んでみる
load_dotenv()
token = os.getenv('DISCORD_TOKEN')

if token is None:
    print("❌ 失敗: トークンが None です。読み込めていません。")
else:
    # 最初の5文字だけ表示して確認（全部表示するのは危険なため）
    print(f"✅ 成功: トークンを読み込めました！ ({token[:5]}...)")