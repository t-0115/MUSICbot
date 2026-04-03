import discord
from discord import app_commands
from discord.ext import commands

# クラス名は分かりやすく（ファイル名と違ってもOK）
class Greetings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # スラッシュコマンドは通常通りだが、引数の最初に 'self' が必要
    @app_commands.command(name="hello", description="Cogから挨拶します")
    async def hello(self, interaction: discord.Interaction):
        await interaction.response.send_message("こんにちは！")

# この setup 関数が、main.py から呼び出される入り口
async def setup(bot):
    await bot.add_cog(Greetings(bot))