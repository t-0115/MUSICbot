# cogs/start.py
import re
import discord
from discord import app_commands
from discord.ext import commands
from .sheet_manager import append_to_sheet, delete_spreadsheet, create_sheet_via_gas, remove_from_sheet

# ==========================================
# ヘルパー関数 (メッセージから状態を復元・生成)
# ==========================================
def parse_participants(embed: discord.Embed) -> dict:
    """Embedのフィールドから参加者情報をパースして辞書で返す"""
    participants = {}
    for field in embed.fields:
        if field.name.startswith("👥 参加者"):
            for line in field.value.split("\n"):
                match = re.match(r"\*\*(\d+)期\*\*: (.*)", line.strip())
                if match:
                    term = match.group(1)
                    members_str = match.group(2)
                    for member_str in members_str.split(" / "):
                        member_str = member_str.strip()
                        if not member_str:
                            continue
                        # "山田 太郎(<@12345>)" のような形式から名前とIDを抽出
                        m_match = re.match(r"(.*?)\s*\(<@!?(\d+)>\)", member_str)
                        if m_match:
                            name = m_match.group(1).strip()
                            uid = int(m_match.group(2))
                            participants[uid] = {"name": name, "term": term}
    return participants

def extract_info_from_message(message: discord.Message):
    """メッセージのEmbedからロール名、チャンネルメンション、シートURL、参加者リストを復元する"""
    if not message.embeds:
        return "", "なし", "", {}
    
    embed = message.embeds[0]
    
    # 1. role_name
    title_match = re.search(r"🎉 「(.*?)」の募集", embed.title or "")
    role_name = title_match.group(1) if title_match else "不明"
    
    # 2. channels_mentions
    desc = embed.description or ""
    ch_match = re.search(r"専用チャンネル: ([^\n]+)", desc)
    channels_mentions = ch_match.group(1).strip() if ch_match else "なし"
    
    # 3. sheet_url
    url_match = re.search(r"\[参加者一覧\]\((.*?)\)", desc)
    sheet_url = url_match.group(1) if url_match else ""
    
    # 4. participants
    participants = parse_participants(embed)
    
    return role_name, channels_mentions, sheet_url, participants

def create_embed(role_name: str, channels_mentions: str, sheet_url: str, participants: dict) -> discord.Embed:
    """最新の参加者情報をもとにEmbedを生成する"""
    grouped = {}
    for uid, data in participants.items():
        term = data["term"]
        # IDを隠し味として付与（再起動後にパースするため）
        grouped.setdefault(term, []).append(f"{data['name']}(<@{uid}>)")
    
    list_str = ""
    for term in sorted(grouped.keys()):
        list_str += f"**{term}期**: {' / '.join(grouped[term])}\n"
    
    embed = discord.Embed(title=f"🎉 「{role_name}」の募集", color=discord.Color.blue())
    desc = f"専用チャンネル: {channels_mentions}\n"
    if sheet_url:
        desc += f"📄 **[参加者一覧]({sheet_url})**\n\n"
    desc += "下のボタンで参加してください。"
    embed.description = desc
    
    embed.add_field(name=f"👥 参加者 ({len(participants)}名)", value=list_str or "なし", inline=False)
    return embed


# ==========================================
# Modals (入力フォーム関連)
# ==========================================
class ChannelNamingModal(discord.ui.Modal):
    def __init__(self, role_name: str, count: int):
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
        await interaction.response.send_message("チャンネルとロールを作成中...しばらくお待ちください。", ephemeral=True)
        
        try:
            sheet_url = create_sheet_via_gas(self.role_name)
            new_role = await interaction.guild.create_role(name=self.role_name)
            
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                new_role: discord.PermissionOverwrite(view_channel=True),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True)
            }
            
            target_category = getattr(interaction.channel, 'category', None)
            created_channels = []
            
            for text_input in self.inputs:
                suffix = text_input.value
                ch = await interaction.guild.create_text_channel(
                    name=f"{self.role_name}_{suffix}",
                    category=target_category,
                    overwrites=overwrites
                )
                created_channels.append(ch)

            channels_mentions = " ".join([ch.mention for ch in created_channels]) if created_channels else "なし"
            embed = create_embed(self.role_name, channels_mentions, sheet_url, {})
            view = StartView()
            
            await interaction.channel.send(embed=embed, view=view)
            
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)

class JoinModal(discord.ui.Modal, title='参加者情報の入力'):
    # 【変更点】名字と名前を別々の入力欄に分割
    term_input = discord.ui.TextInput(label='期 (2桁)', placeholder='例：24', min_length=1, max_length=2)
    last_name_input = discord.ui.TextInput(label='名字', placeholder='例：山田', max_length=15)
    first_name_input = discord.ui.TextInput(label='名前', placeholder='例：太郎', max_length=15)

    def __init__(self, role: discord.Role, role_name: str, channels_mentions: str, sheet_url: str, participants: dict):
        super().__init__()
        self.role = role
        self.role_name = role_name
        self.channels_mentions = channels_mentions
        self.sheet_url = sheet_url
        self.participants = participants

    async def on_submit(self, interaction: discord.Interaction):
        if not self.term_input.value.isdigit():
            await interaction.response.send_message("❌ 期は数字で入力してください。", ephemeral=True)
            return
            
        term = self.term_input.value.zfill(2)
        last_name = self.last_name_input.value
        first_name = self.first_name_input.value
        
        # Discordのメッセージ表示用には結合する
        user_name_combined = f"{last_name} {first_name}"
        discord_id = interaction.user.id
        
        # 参加情報を追加（DiscordのEmbed表示用）
        self.participants[discord_id] = {"name": user_name_combined, "term": term}
        await interaction.user.add_roles(self.role)
        
        # メッセージを新しいEmbedで更新
        new_embed = create_embed(self.role_name, self.channels_mentions, self.sheet_url, self.participants)
        await interaction.message.edit(embed=new_embed)
        
        await interaction.response.send_message(f"【{term}期】{user_name_combined}として参加登録しました！", ephemeral=True)

        try:
            # 【変更点】スプレッドシートへは名字と名前を分けて送信 (discord_tagを削除)
            append_to_sheet(
                role_name=self.role_name,
                term=term,
                last_name=last_name,
                first_name=first_name,
                discord_id=discord_id
            )
        except Exception as e:
            print(f"Spreadsheet Error: {e}")
            await interaction.followup.send("⚠️ （システム通知）スプレッドシートへの記録に失敗しました。", ephemeral=True)

class DeleteConfirmModal(discord.ui.Modal, title='⚠️ 削除の最終確認'):
    dummy = discord.ui.TextInput(label='そのまま送信で削除', placeholder='何も入力せず送信を押してください', required=False)

    def __init__(self, role: discord.Role, role_name: str, channels: list[discord.TextChannel], original_message: discord.Message):
        super().__init__()
        self.role = role
        self.role_name = role_name
        self.channels = channels
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("募集データ（チャンネル・ロール・スプレッドシート）を削除しています...", ephemeral=True)
    
        for ch in self.channels:
            await ch.delete()
        if self.role:
            await self.role.delete()
        if self.original_message:
            await self.original_message.delete()
    
        delete_spreadsheet(self.role_name)


# ==========================================
# Views (ボタンUI関連)
# ==========================================
class StartView(discord.ui.View):
    """永続的(Persistent)なView。固有の状態は持たず、毎回メッセージから情報を復元する。"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success, custom_id="start_join")#バッティング回避のため名前変更
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_name, channels_mentions, sheet_url, participants = extract_info_from_message(interaction.message)
        
        if interaction.user.id in participants:
            await interaction.response.send_message("❌ 既に登録されています！", ephemeral=True)
            return
            
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            await interaction.response.send_message("❌ 対象のロールが見つかりません。削除された可能性があります。", ephemeral=True)
            return

        await interaction.response.send_modal(JoinModal(role, role_name, channels_mentions, sheet_url, participants))

    @discord.ui.button(label="参加取り消し", style=discord.ButtonStyle.primary, custom_id="start_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_name, channels_mentions, sheet_url, participants = extract_info_from_message(interaction.message)
        
        if interaction.user.id in participants:
            del participants[interaction.user.id]
            
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if role:
                await interaction.user.remove_roles(role)
                
            new_embed = create_embed(role_name, channels_mentions, sheet_url, participants)
            await interaction.message.edit(embed=new_embed)
            await interaction.response.send_message("参加を取り消しました。", ephemeral=True)
            
            try:
                remove_from_sheet(role_name, interaction.user.id)
            except Exception as e:
                print(f"行削除時のエラー: {e}")
        else:
            await interaction.response.send_message("まだ参加していません。", ephemeral=True)

    @discord.ui.button(label="削除", style=discord.ButtonStyle.danger, custom_id="start_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_name, channels_mentions, _, _ = extract_info_from_message(interaction.message)
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        
        # メンション文字列からチャンネルIDを抽出
        channel_ids = [int(cid) for cid in re.findall(r"<#(\d+)>", channels_mentions)]
        channels = [interaction.guild.get_channel(cid) for cid in channel_ids if interaction.guild.get_channel(cid)]
        
        await interaction.response.send_modal(DeleteConfirmModal(role, role_name, channels, interaction.message))


# ==========================================
# Cog (コマンド関連)
# ==========================================
class StartCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Cogが読み込まれたとき（Bot起動時）にViewを永続化登録する"""
        self.bot.add_view(StartView())

    @app_commands.command(name="start", description="イベントを開始します")
    @app_commands.describe(
        role_name="作成するロールの名前",
        channel_count="作成するチャンネル数 (0~5)"
    )
    async def create_recruit(self, interaction: discord.Interaction, role_name: str, channel_count: app_commands.Range[int, 0, 5]):
        if channel_count == 0:
            await interaction.response.defer() 
            
            try:
                new_role = await interaction.guild.create_role(name=role_name)
                sheet_url = create_sheet_via_gas(role_name)
                
                embed = create_embed(role_name, "なし", sheet_url, {})
                view = StartView()
                await interaction.followup.send(embed=embed, view=view)
                
            except discord.Forbidden:
                await interaction.followup.send("❌ 権限が足りないため、ロールを作成できませんでした。", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 予期せぬエラーが発生しました: {e}", ephemeral=True)
        else:
            modal = ChannelNamingModal(role_name=role_name, count=channel_count)
            await interaction.response.send_modal(modal)

async def setup(bot):
    await bot.add_cog(StartCog(bot))