import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from keep_alive import keep_alive

# ローカル環境（自分のPC）用のパスワードを読み込む（Render本番環境では自動で無視されます）
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

class MyBot(commands.Bot):
    def __init__(self):
        # Botの権限（Intents）設定
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Cog（機能ごとの別ファイル）を読み込む
        await self.load_extension("cogs.greetings")
        await self.load_extension("cogs.start")
        await self.load_extension("cogs.song_recruit")
        await self.load_extension("cogs.song_recruit_slash")
        await self.load_extension("cogs.entry_sheet")

        # 環境変数 'RENDER' の有無でローカルか本番かを判定
        if not os.getenv('RENDER'):
            # ローカル環境（PC）の場合のみ実行される
            print("💻 ローカル環境を検知しました。スラッシュコマンドを同期します...")
            await self.tree.sync()
        else:
            # Render環境の場合
            print("☁️ Render環境を検知しました。スラッシュコマンドの同期をスキップします。")


# ここから下がプログラムの「本当のスタート地点」です
if __name__ == '__main__':
    # 1. ダミーのWebサーバー（受付窓口）を起動し、Renderのスリープを回避する
    keep_alive() 

    # 2. Botの本体を作成して起動する（※これより下に書いたコードは実行されません）
    bot = MyBot()
    bot.run(TOKEN)