import discord
from discord import app_commands
from discord.ext import commands
from .recruit_ui import ChannelNamingModal, RecruitView 

class RecruitCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="recruit", description="募集システムを開始します")
    @app_commands.describe(
        role_name="作成するロールの名前",
        channel_count="作成するチャンネル数 (0~5)"
    )
    async def create_recruit(self, interaction: discord.Interaction, role_name: str, channel_count: app_commands.Range[int, 0, 5]):
        if channel_count == 0:
            await interaction.response.defer()
            try:
                new_role = await interaction.guild.create_role(name=role_name)
                view = RecruitView(role=new_role, channels=[], role_name=role_name)
                await interaction.followup.send(embed=view.create_embed(), view=view)
            except discord.Forbidden:
                await interaction.followup.send("❌ 権限が足りないため、ロールを作成できませんでした。", ephemeral=True)
        else:
            modal = ChannelNamingModal(role_name=role_name, count=channel_count)
            await interaction.response.send_modal(modal)

async def setup(bot):
    await bot.add_cog(RecruitCog(bot))