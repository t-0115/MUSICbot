import discord
from .sheet_manager import append_to_sheet, delete_spreadsheet, create_sheet_via_gas, remove_from_sheet

# ==========================================
# Modals (入力フォーム関連)
# ==========================================

class ChannelNamingModal(discord.ui.Modal):
    """チャンネル名を入力させるためのモーダル"""
    def __init__(self, role_name: str, count: int):
        super().__init__(title=f"{role_name} のチャンネル名設定")
        self.role_name = role_name
        self.count = count
        self.inputs = []

        # 指定された数だけ入力フィールドを動的に生成
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
        """チャンネル名が送信されたときの処理"""
        await interaction.response.send_message("チャンネルとロールを作成中...しばらくお待ちください。", ephemeral=True)
        
        try:
            # 1. スプレッドシートの作成
            sheet_url = create_sheet_via_gas(self.role_name)
            
            # 2. ロールの作成
            new_role = await interaction.guild.create_role(name=self.role_name)
            
            # 3. チャンネルの権限設定（新しいロールとBot自身のみ閲覧可能）
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                new_role: discord.PermissionOverwrite(view_channel=True),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True)
            }
            
            # 4. 指定された名前でテキストチャンネルを作成
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

            # 5. 募集用の埋め込みメッセージとボタンを送信
            view = RecruitView(role=new_role, channels=created_channels, role_name=self.role_name, sheet_url=sheet_url)
            await interaction.channel.send(embed=view.create_embed(), view=view)
            
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)


class JoinModal(discord.ui.Modal, title='参加者情報の入力'):
    """参加ボタンを押した際に入力するモーダル"""
    term_input = discord.ui.TextInput(label='期 (2桁)', placeholder='例：24', min_length=1, max_length=2)
    name_input = discord.ui.TextInput(label='氏名', placeholder='例：山田 太郎')

    def __init__(self, view: discord.ui.View):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        """参加情報が送信されたときの処理"""
        if not self.term_input.value.isdigit():
            await interaction.response.send_message("❌ 期は数字で入力してください。", ephemeral=True)
            return
            
        term = self.term_input.value.zfill(2) # 1桁の場合は先頭に0を埋める (例: 1 -> 01)
        user_name = self.name_input.value
        discord_tag = str(interaction.user)
        discord_id = interaction.user.id
        
        # 1. Viewの参加者リストに情報を保存
        self.view.participants[discord_id] = {"name": user_name, "term": term}
        
        # 2. ロールの付与とメッセージの更新
        await interaction.user.add_roles(self.view.role)
        await self.view.update_message(interaction)
        await interaction.response.send_message(f"【{term}期】{user_name}として参加登録しました！", ephemeral=True)

        # 3. スプレッドシートへ情報を記録
        try:
            append_to_sheet(
                role_name=self.view.role_name,
                term=term,
                user_name=user_name,
                discord_tag=discord_tag,
                discord_id=discord_id
            )
        except Exception as e:
            # シートへの記録が失敗してもDiscord上の登録は完了しているため、システム通知のみ行う
            print(f"Spreadsheet Error: {e}")
            await interaction.followup.send("⚠️ （システム通知）スプレッドシートへの記録に失敗しました。", ephemeral=True)


class DeleteConfirmModal(discord.ui.Modal, title='⚠️ 削除の最終確認'):
    """削除ボタンを押した際の最終確認モーダル"""
    dummy = discord.ui.TextInput(label='そのまま送信で削除', placeholder='何も入力せず送信を押してください', required=False)

    def __init__(self, role: discord.Role, channels: list[discord.TextChannel], original_message: discord.Message):
        super().__init__()
        self.role = role
        self.channels = channels
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction):
        """削除が承認されたときの処理"""
        await interaction.response.send_message("募集データ（チャンネル・ロール・スプレッドシート）を削除しています...", ephemeral=True)
    
        role_name = self.role.name
    
        # 関連リソースの削除
        for ch in self.channels:
            await ch.delete()
        if self.role:
            await self.role.delete()
        if self.original_message:
            await self.original_message.delete()
    
        # スプレッドシートの削除
        delete_spreadsheet(role_name)

# ==========================================
# Views (ボタンUI関連)
# ==========================================

class RecruitView(discord.ui.View):
    """募集メッセージに付随するボタンとロジックを管理するView"""
    def __init__(self, role: discord.Role, channels: list[discord.TextChannel], role_name: str, sheet_url: str = ""):
        super().__init__(timeout=None) # timeout=Noneで一定時間経過後もボタンを機能させる
        self.role = role
        self.channels = channels 
        self.role_name = role_name
        self.sheet_url = sheet_url
        self.participants = {} # 参加者を保持する辞書 {discord_id: {"name": str, "term": str}}

    def create_embed(self) -> discord.Embed:
        """現在の参加者状況を元に、メッセージの埋め込み（Embed）を生成する"""
        # 参加者を「期」ごとにグループ化
        grouped = {}
        for data in self.participants.values():
            term = data["term"]
            grouped.setdefault(term, []).append(data["name"])
        
        # グループ化された参加者を文字列として整形
        list_str = ""
        for term in sorted(grouped.keys()):
            list_str += f"**{term}期**: {' / '.join(grouped[term])}\n"
        
        # チャンネルのメンション文字列を生成
        mentions = " ".join([ch.mention for ch in self.channels]) if self.channels else "なし"

        # 埋め込みの構築
        embed = discord.Embed(title=f"🎉 「{self.role_name}」の募集", color=discord.Color.blue())
        
        desc = f"専用チャンネル: {mentions}\n"
        if self.sheet_url:
            desc += f"📄 **[参加者一覧]({self.sheet_url})**\n\n"
        desc += "下のボタンで参加してください。"
        embed.description = desc
        
        embed.add_field(name=f"👥 参加者 ({len(self.participants)}名)", value=list_str or "なし", inline=False)
        return embed

    async def update_message(self, interaction: discord.Interaction):
        """埋め込みメッセージの内容を最新の状態に更新する"""
        await interaction.message.edit(embed=self.create_embed(), view=self)

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success, custom_id="recruit_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        """参加ボタンの処理"""
        if interaction.user.id in self.participants:
            await interaction.response.send_message("❌ 既に登録されています！", ephemeral=True)
            return
        await interaction.response.send_modal(JoinModal(self))

    @discord.ui.button(label="参加取り消し", style=discord.ButtonStyle.primary, custom_id="recruit_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """参加取り消しボタンの処理"""
        if interaction.user.id in self.participants:
            # 1. データの削除とロールの剥奪
            del self.participants[interaction.user.id]
            await interaction.user.remove_roles(self.role)
            await self.update_message(interaction)
            
            # 2. 先に応答を返し、Discord APIの3秒タイムアウトを回避
            await interaction.response.send_message("参加を取り消しました。", ephemeral=True)
            
            # 3. スプレッドシート側の行も削除
            try:
                remove_from_sheet(self.role_name, interaction.user.id)
            except Exception as e:
                print(f"行削除時のエラー: {e}")
        else:
            await interaction.response.send_message("まだ参加していません。", ephemeral=True)

    @discord.ui.button(label="削除", style=discord.ButtonStyle.danger, custom_id="recruit_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        """募集削除ボタンの処理"""
        await interaction.response.send_modal(DeleteConfirmModal(self.role, self.channels, interaction.message))