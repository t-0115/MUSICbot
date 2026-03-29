import discord
import os
import asyncio
import subprocess
import sys
from discord.ext import commands
from keep_alive import keep_alive  # Webサーバー化ツール
from dotenv import load_dotenv

# --- 自動アップデート処理 ---
print("🔄 yt-dlpの最新版をチェック・更新しています...")
try:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--quiet"])
    print("✅ yt-dlpの準備完了！Botを起動します...")
except Exception as e:
    print(f"⚠️ アップデートに失敗しましたが、起動を続行します: {e}")
# ----------------------------

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.load_extension("cogs.greetings")
        await self.load_extension("cogs.buttons")
        await self.load_extension("cogs.music")
        await self.load_extension("cogs.recruit")
        
        # 【修正箇所】毎回同期すると429エラーになるので、普段はコメントアウト(#)しておきます！
        # コマンドを新しく追加・変更した時だけ、# を外して1回だけ実行してください。
        # await self.tree.sync()

bot = MyBot()

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user}')


# ===== 修正箇所：起動の順番 =====

# 1. まず最初にダミーのWebサーバー（ポート）を開いて、Renderを安心させる
keep_alive() 

# 2. 最後にBotを起動する（※これより下にコードを書いても絶対に実行されません）
bot.run(TOKEN)