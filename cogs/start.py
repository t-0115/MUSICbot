import discord
from discord import app_commands
from discord.ext import commands
from .start_ui import ChannelNamingModal, StartView 
from .sheet_manager import create_sheet_via_gas

class StartCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="start", description="イベントを開始します")
    @app_commands.describe(
        role_name="作成するロールの名前",
        channel_count="作成するチャンネル数 (0~5)"
    )
    async def create_recruit(self, interaction: discord.Interaction, role_name: str, channel_count: app_commands.Range[int, 0, 5]):
        """募集コマンドのエントリーポイント"""
        
        # チャンネル作成が不要（0）の場合は、Modalを使わずに直接作成処理へ進む
        if channel_count == 0:
            await interaction.response.defer()  # 処理に時間がかかるため、応答を遅延させる
            
            try:
                # 1. ロールの作成
                new_role = await interaction.guild.create_role(name=role_name)
                
                # 2. GAS経由でスプレッドシートを作成し、URLを取得
                sheet_url = create_sheet_via_gas(role_name)
                
                # 3. UI（埋め込みとボタン）の生成と送信
                view = StartView(role=new_role, channels=[], role_name=role_name, sheet_url=sheet_url)
                await interaction.followup.send(embed=view.create_embed(), view=view)
                
            except discord.Forbidden:
                await interaction.followup.send("❌ 権限が足りないため、ロールを作成できませんでした。", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 予期せぬエラーが発生しました: {e}", ephemeral=True)
        
        # チャンネル作成が必要（1~5）の場合は、チャンネル名入力用のModalを表示
        else:
            modal = ChannelNamingModal(role_name=role_name, count=channel_count)
            await interaction.response.send_modal(modal)

async def setup(bot):
    await bot.add_cog(StartCog(bot))