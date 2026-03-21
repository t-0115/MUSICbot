import discord
from discord import app_commands
from discord.ext import commands

# --------------------------------------------------------
# 1. ボタンをのせる「台紙（View）」の定義
# --------------------------------------------------------
class MyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60) # 60秒後に無効になる設定

    # ボタン1：緑色 (Success)
    @discord.ui.button(label="挨拶する", style=discord.ButtonStyle.green, emoji="👋")
    async def hello_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ボタンを押した人への返信
        await interaction.response.send_message("こんにちは！ボタンを押してくれてありがとう！", ephemeral=True)

    # ボタン2：赤色 (Danger)
    @discord.ui.button(label="消去", style=discord.ButtonStyle.red, emoji="🗑️")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # メッセージ自体を削除する処理
        await interaction.message.delete()

# --------------------------------------------------------
# 2. Cogの定義（コマンド部分）
# --------------------------------------------------------
class ButtonCog(commands.Cog):

    
    def __init__(self, bot):
        self.bot = bot
        
    @commands.Cog.listener()
    async def on_ready(self):
        print('buttons Cog が読み込まれました！')

    @app_commands.command(name="show_button", description="ボタン付きのメッセージを出します")
    async def show_button(self, interaction: discord.Interaction):
        # 上で作ったView（台紙）を作成
        view = MyButtonView()
        
        # view=view でメッセージと一緒に送信
        await interaction.response.send_message("下のボタンを押してみてね！", view=view)

# --------------------------------------------------------
# 3. セットアップ関数
# --------------------------------------------------------
async def setup(bot):
    await bot.add_cog(ButtonCog(bot))