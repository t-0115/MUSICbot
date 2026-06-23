# cogs/help.py
import discord
from discord import app_commands
from discord.ext import commands

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="MusicaBotの使い方とコマンド一覧を表示します")
    async def help_command(self, interaction: discord.Interaction):
        # Embedパネルの作成
        embed = discord.Embed(
            title="🎸 MusicaBot ヘルプセンター",
            description="イベントの企画から曲の募集、集計までをサポートするBotです！\n以下のコマンドを使用して操作します。",
            color=discord.Color.green()
        )

        # 使い方ステップ（ガイド）
        embed.add_field(
            name="🔰 基本的な使い方（ステップ）",
            value=(
                "**1.** `/start` でイベントを作成し、メンバーに参加登録してもらう\n"
                "**2.** `/曲募集` で演奏したい曲のメンバーを募集する\n"
                "**3.** `/エントリー集計` で募集結果をスプレッドシートに一括転記する"
            ),
            inline=False
        )

        # コマンド一覧
        embed.add_field(
            name="🛠️ コマンド一覧",
            value=(
                "**`/start`**\n"
                "イベントの参加募集パネルを作成し、専用チャンネルとロールを自動生成します。\n\n"
                
                "**`/曲募集`**\n"
                "曲のメンバー募集パネルを作成します。参加者の追加や締め切りをボタンで管理できます。\n\n"
                
                "**`/エントリー集計`**\n"
                "チャンネル内の「曲募集」メッセージをすべて読み取り、自動でスプレッドシートに書き出します。\n\n"
                
                "**`/曲数カウント`**\n"
                "集計済みのスプレッドシートから、全体の曲数や指定した人の演奏曲数をカウントします。\n" 
                "※start機能との連携が必要です。\n\n" 
                
                "**`/ロール作成`**\n"
                "指定されたロールを自動で作成します。\n\n"

                "**`/ロール削除`**\n"
                "指定されたロールを自動で削除します。"
               
            ),
            inline=False
        )

        # フッター（ちょっとした装飾）
        embed.set_footer(text="何か不明な点があれば、管理者にお問い合わせください。")

        # ephemeral=True にすることで、コマンドを打った本人にしか見えないようにする（チャンネルを汚さない）
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(HelpCog(bot))