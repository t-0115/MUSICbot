import discord

class ChannelNamingModal(discord.ui.Modal):
    def __init__(self, role_name, count):
        super().__init__(title=f"{role_name} のチャンネル名設定")
        self.role_name = role_name
        self.count = count
        self.inputs = []

        for i in range(count):
            text_input = discord.ui.TextInput(
                label=f"{i+1}個目のチャンネル名（後ろに付く単語）",
                placeholder="例：全体、募集、エントリーなど",
                required=True,
                max_length=20
            )
            self.add_item(text_input)
            self.inputs.append(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("チャンネルとロールを作成中...", ephemeral=True)
        
        try:
            new_role = await interaction.guild.create_role(name=self.role_name)
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                new_role: discord.PermissionOverwrite(view_channel=True),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True)
            }
            
            target_category = interaction.channel.category
            created_channels = []
            
            for text_input in self.inputs:
                suffix = text_input.value
                ch = await interaction.guild.create_text_channel(
                    name=f"{self.role_name}_{suffix}",
                    category=target_category,
                    overwrites=overwrites
                )
                created_channels.append(ch)

            view = RecruitView(role=new_role, channels=created_channels, role_name=self.role_name)
            await interaction.channel.send(embed=view.create_embed(), view=view)
            
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)

class JoinModal(discord.ui.Modal, title='参加者情報の入力'):
    term_input = discord.ui.TextInput(label='期 (2桁)', placeholder='例：24', min_length=1, max_length=2)
    name_input = discord.ui.TextInput(label='氏名', placeholder='例：山田 太郎')

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        if not self.term_input.value.isdigit():
            await interaction.response.send_message("❌ 期は数字で入力してください。", ephemeral=True)
            return
        term = self.term_input.value.zfill(2)
        self.view.participants[interaction.user.id] = {"name": self.name_input.value, "term": term}
        await interaction.user.add_roles(self.view.role)
        await self.view.update_message(interaction)
        await interaction.response.send_message(f"【{term}期】{self.name_input.value}として参加登録しました！", ephemeral=True)

class DeleteConfirmModal(discord.ui.Modal, title='⚠️ 削除の最終確認'):
    dummy = discord.ui.TextInput(label='そのまま送信で削除', required=False)

    def __init__(self, role, channels, original_message):
        super().__init__()
        self.role = role
        self.channels = channels
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("削除中...", ephemeral=True)
        for ch in self.channels:
            await ch.delete()
        if self.role: await self.role.delete()
        if self.original_message: await self.original_message.delete()

class RecruitView(discord.ui.View):
    def __init__(self, role: discord.Role, channels: list[discord.TextChannel], role_name: str):
        super().__init__(timeout=None)
        self.role = role
        self.channels = channels 
        self.role_name = role_name
        self.participants = {}  

    def create_embed(self):
        grouped = {}
        for data in self.participants.values():
            term = data["term"]
            grouped.setdefault(term, []).append(data["name"])
        
        list_str = ""
        for term in sorted(grouped.keys()):
            list_str += f"**{term}期**: {' / '.join(grouped[term])}\n"
        
        if self.channels:
            mentions = " ".join([ch.mention for ch in self.channels])
        else:
            mentions = "なし"

        embed = discord.Embed(title=f"🎉 「{self.role_name}」の募集", color=discord.Color.blue())
        embed.description = f"専用チャンネル: {mentions}\n下のボタンで参加してください。"
        embed.add_field(name=f"👥 参加者 ({len(self.participants)}名)", value=list_str or "なし", inline=False)
        return embed

    async def update_message(self, interaction: discord.Interaction):
        await interaction.message.edit(embed=self.create_embed(), view=self)

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, b: discord.ui.Button):
        await interaction.response.send_modal(JoinModal(self))

    @discord.ui.button(label="参加取り消し", style=discord.ButtonStyle.primary)
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button):
        if interaction.user.id in self.participants:
            del self.participants[interaction.user.id]
            await interaction.user.remove_roles(self.role)
            await self.update_message(interaction)
            await interaction.response.send_message("取り消しました。", ephemeral=True)
        else:
            await interaction.response.send_message("参加していません。", ephemeral=True)

    @discord.ui.button(label="削除", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, b: discord.ui.Button):
        await interaction.response.send_modal(DeleteConfirmModal(self.role, self.channels, interaction.message))