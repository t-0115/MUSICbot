# cogs/anonymous.py
import discord
from discord import app_commands
from discord.ext import commands

class AnonymousCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="匿名投稿", description="送信者を隠して、このチャンネルに匿名で質問や意見を投稿します")
    @app_commands.describe(message="投稿するメッセージを入力してください")
    # 🔥 1分間（60秒）の間に「5回」まで投稿可能にする設定（6回目でエラーが発動）
    @app_commands.checks.cooldown(5, 60.0, key=lambda i: i.user.id)
    @app_commands.guild_only()
    async def anonymous_question(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)
        try:
            content = f"{message}"
            await interaction.channel.send(content=content)

            await interaction.followup.send("🤫 匿名でメッセージを投稿しました。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)

    # 🔥 1分間に5回の制限を超えて6回目を打ったときに発動する処理
    @anonymous_question.error
    async def anonymous_question_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            # ① 本人には「制限がかかった」ことをこっそり伝える
            await interaction.response.send_message(
                f"⏳ 連投制限がかかりました。あと **{error.retry_after:.1f}秒** 待ってください。", 
                ephemeral=True
            )
            
            # ② チャンネル全体に向けて、連投した犯人をメンションで晒し上げる
            await interaction.channel.send(
                f"⚠️ **連投ペナルティ（匿名解除）**\n"
                f"{interaction.user.mention} さんが、1分間に5回を超えるペースで匿名投稿を連投しようとしました。\n"
                f"※荒らし対策のため、短時間の連投はシステムにより名前が公開されます。"
            )
        else:
            await interaction.response.send_message(f"❌ エラーが発生しました: {error}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AnonymousCog(bot))