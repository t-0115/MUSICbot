import discord
from discord import app_commands
from discord.ext import commands
from discord import ui

# --------------------------------------------------------
# 1. 入力フォーム（Modal）の定義
# --------------------------------------------------------
class TimeInputModal(ui.Modal, title="参加登録"):
    # 入力欄の設定
    time_input = ui.TextInput(
        label="参加できる時間帯を教えてください",
        style=discord.TextStyle.short,
        placeholder="例: 21:00～23:00、いつでもOK、など",
        required=True,
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        # ボタンが押された元のメッセージを取得
        message = interaction.message
        embed = message.embeds[0] # メッセージ内の埋め込みを取得

        # 埋め込みに新しいフィールド（参加者と時間）を追加
        # inline=False にすると、縦にリスト形式で並びます
        embed.add_field(
            name=f"👤 {interaction.user.display_name}",
            value=f"🕒 {self.time_input.value}",
            inline=False
        )

        # メッセージを編集して更新
        await message.edit(embed=embed)
        
        # 押した人への確認メッセージ（自分にしか見えない）
        await interaction.response.send_message(f"「{self.time_input.value}」で登録しました！", ephemeral=True)

# --------------------------------------------------------
# 2. ボタン（View）の定義
# --------------------------------------------------------
class RecruitView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) # ずっと押せるようにタイムアウトなし

    @ui.button(label="参加・時間を入力", style=discord.ButtonStyle.green, emoji="📝")
    async def join_button(self, interaction: discord.Interaction, button: ui.Button):
        # ボタンを押したら、上のフォーム(Modal)を表示させる
        await interaction.response.send_modal(TimeInputModal())

# --------------------------------------------------------
# 3. Cog（コマンド部分）
# --------------------------------------------------------
class RecruitCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="recruit", description="時間を指定できる募集パネルを作ります")
    @app_commands.describe(title="募集のタイトル", date="日付など")
    async def recruit(self, interaction: discord.Interaction, title: str, date: str):
        # Embed（枠）の作成
        embed = discord.Embed(
            title=f"📢 募集: {title}",
            description=f"**開催日: {date}**\n\n下のボタンを押して、参加できる時間を入力してください！",
            color=discord.Color.blue()
        )
        embed.set_footer(text="募集中...")

        # ボタン付きで送信
        await interaction.response.send_message(embed=embed, view=RecruitView())

async def setup(bot):
    await bot.add_cog(RecruitCog(bot))