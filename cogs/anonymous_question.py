# cogs/anonymous_question.py
import discord
from discord import app_commands
from discord.ext import commands

class AnonymousQuestionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="匿名質問", description="送信者を隠して、このチャンネルに匿名で質問や意見を投稿します")
    @app_commands.describe(question="質問や意見の内容を入力してください（最大2000文字）")
    async def anonymous_question(self, interaction: discord.Interaction, question: str):
        # ephemeral=True にすることで、誰がコマンドを実行したかを周囲に完全に隠します
        await interaction.response.defer(ephemeral=True)

        try:
            # サーバーのオーナー（管理者）を取得
            guild_owner = interaction.guild.owner
            owner_mention = guild_owner.mention if guild_owner else "管理者"

            # チャンネルに投稿する匿名のEmbedパネルを作成
            embed = discord.Embed(
                title="📢 匿名からの質問・意見",
                description=question,
                color=discord.Color.orange()
            )
            embed.set_footer(text=f"幹部への連絡や直接の相談は {owner_mention} まで")

            # コマンドが打たれたチャンネルにBotが代わりに送信
            await interaction.channel.send(embed=embed)

            # 実行した本人にだけ、こっそり成功メッセージを表示
            await interaction.followup.send(
                "🤫 **匿名で質問を投稿しました！**\n周りのメンバーには、あなたが送信したことは一切分かりません。", 
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.followup.send("❌ このチャンネルにメッセージを送信する権限がBotにありません。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 送信中にエラーが発生しました: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AnonymousQuestionCog(bot))