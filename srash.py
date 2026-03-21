import discord
import os
from discord import app_commands 
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --------------------------------------------------
# 起動時の処理（コマンドの同期）
# --------------------------------------------------
@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user.name}')
    
    try:
        # スラッシュコマンドをDiscordサーバーに同期させる
        synced = await bot.tree.sync()
        print(f'{len(synced)} 個のコマンドを同期しました')
    except Exception as e:
        print(f'同期エラー: {e}')

# --------------------------------------------------
# スラッシュコマンド1: シンプルな挨拶
# --------------------------------------------------
@bot.tree.command(name="hello", description="Botが挨拶を返します")
async def hello(interaction: discord.Interaction):
    # interaction.response.send_message で返信します（ctx.sendとは違います）
    await interaction.response.send_message("こんにちは！スラッシュコマンドへようこそ！")

# --------------------------------------------------
# スラッシュコマンド2: 引数（オプション）を使う
# --------------------------------------------------
@bot.tree.command(name="omikuji", description="今日のおみくじを引きます")
@app_commands.describe(name="占いたい人の名前") # 引数の説明
async def omikuji(interaction: discord.Interaction, name: str):
    import random
    results = ["大吉", "中吉", "小吉", "末吉", "凶"]
    result = random.choice(results)
    
    await interaction.response.send_message(f'{name} さんの運勢は... **{result}** です！')

bot.run(TOKEN)