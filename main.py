import subprocess
import sys

# --- 自動アップデート処理 ---
print("🔄 yt-dlpの最新版をチェック・更新しています...")
try:
    # 裏側で pip install -U yt-dlp を実行する
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--quiet"])
    print("✅ yt-dlpの準備完了！Botを起動します...")
except Exception as e:
    print(f"⚠️ アップデートに失敗しましたが、起動を続行します: {e}")
# ----------------------------

import discord
import os
import asyncio
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Botクラスを継承してカスタマイズする（最近の推奨方法）
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    # setup_hook は起動時のログイン直前に実行される特別な関数
    async def setup_hook(self):
        # 'cogs.greetings' を読み込む（フォルダ名.ファイル名）
        # ※ファイルが増えたらここに追加していく
        await self.load_extension("cogs.greetings")

        await self.load_extension("cogs.buttons")

        await self.load_extension("cogs.music")

        await self.load_extension("cogs.recruit")
        
        # コマンド同期（テスト用サーバーIDを指定する場合）
        # MY_GUILD = discord.Object(id=あなたのサーバーID)
        # self.tree.copy_global_to(guild=MY_GUILD)
        # await self.tree.sync(guild=MY_GUILD)
        
        # グローバル同期（本番用：反映に時間がかかる）の場合はこちら
        await self.tree.sync()

bot = MyBot()

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user}')

bot.run(TOKEN)