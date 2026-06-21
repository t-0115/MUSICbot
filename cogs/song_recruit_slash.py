# cogs/song_recruit_slash.py
import re
import discord
from discord import app_commands
from discord.ext import commands

# ==========================================
# テキスト解析・更新ヘルパー
# ==========================================
def is_recruit_closed(content: str) -> bool:
    """テキストから「募集終了」または「仮締め切り」状態かどうかを読み取る"""
    return "🔒" in content or "【募集終了】" in content or "⏸️" in content or "【仮締め切り】" in content

def get_remaining_capacity(content: str) -> int:
    """テキストから「あと◯人」の数字を読み取る（★募集終了・仮締め切り時は確実に0として扱う）"""
    if is_recruit_closed(content):
        return 0
    match = re.search(r'あと\s*(\d+)\s*人', content)
    return int(match.group(1)) if match else 0

def get_recruiter_id(content: str) -> int:
    """テキストから募集者のDiscord IDを読み取る"""
    match = re.search(r'募集者:\s*<@!?(\d+)>', content)
    return int(match.group(1)) if match else 0

def get_body_content(content: str) -> str:
    """メッセージから自動生成されたヘッダーを除いた本文を抽出する"""
    lines = content.split('\n')
    # ヘッダー判定に「締め切り」も含めることで仮締め切り時も正しく本文を抽出
    if len(lines) >= 2 and ("募集" in lines[0] or "終了" in lines[0] or "締め切り" in lines[0]) and "募集者:" in lines[1]:
        return '\n'.join(lines[2:]).strip()
    return content

def update_recruit_text(content: str, new_remaining: int, new_player_line: str = None, status: str = None) -> str:
    """テキスト内の人数やステータスを更新し、必要なら参加者を追記する"""
    # ステータス指定に合わせてヘッダーを出し分ける
    if status == "temp_closed":
        new_header = "**⏸️ 【仮締め切り】**"
    elif status == "closed":
        new_header = "**🔒 【募集終了】**"
    elif new_remaining <= 0:
        new_header = "**🔒 【募集終了】**"
    else:
        new_header = f"**🎵 【あと {new_remaining} 人募集】**"

    # 古いヘッダー（🎵、🔒、⏸️）を見つけて差し替える
    if re.search(r'\*\*(?:🎵|🔒|⏸️) 【.*】\*\*', content):
        content = re.sub(r'\*\*(?:🎵|🔒|⏸️) 【.*】\*\*', new_header, content, count=1)
    else:
        content = f"{new_header}\n{content}"

    if new_player_line:
        if '【演奏者】' in content:
            content = content.replace('【演奏者】', f'【演奏者】\n{new_player_line}')
        else:
            content += f'\n\n【演奏者】\n{new_player_line}'

    return content

def build_recruit_view(remaining: int) -> 'PersistentRecruitView':
    view = PersistentRecruitView()
    is_closed = (remaining <= 0)
    for child in view.children:
        if getattr(child, 'custom_id', None) == 'recruit_join':
            child.disabled = is_closed
    return view


# ==========================================
# モーダル群（参加 / 編集）
# ==========================================
class JoinSongModal(discord.ui.Modal):
    def __init__(self, message: discord.Message):
        super().__init__(title='エントリー情報の入力')
        self.target_message = message

        self.generation = discord.ui.TextInput(label='期（半角数字のみ）', placeholder='例: 24', max_length=5, required=True)
        self.name = discord.ui.TextInput(label='氏名', placeholder='例: 山田太郎', max_length=20, required=True)
        self.faculty = discord.ui.TextInput(label='学部・学科・学年', placeholder='例: 工情物', max_length=30, required=True)
        
        # ★追加：募集者へのメッセージ入力欄（任意）
        self.user_message = discord.ui.TextInput(
            label='募集者へのメッセージ（任意）', 
            style=discord.TextStyle.paragraph,
            placeholder='例: よろしくお願いします！', 
            required=False, # 入力必須ではない
            max_length=500
        )

        self.add_item(self.generation)
        self.add_item(self.name)
        self.add_item(self.faculty)
        self.add_item(self.user_message)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            latest_message = await interaction.channel.fetch_message(self.target_message.id)
        except discord.NotFound:
            return await interaction.followup.send('❌ 元のメッセージが見つかりません。', ephemeral=True)

        latest_content = latest_message.content

        if self.name.value in latest_content:
            return await interaction.followup.send('❌ 既に登録されています。', ephemeral=True)
            
        current_remaining = get_remaining_capacity(latest_content)
        if is_recruit_closed(latest_content) or current_remaining <= 0:
            return await interaction.followup.send('❌ 申し訳ありません、タッチの差で定員が埋まり募集が締め切られました。', ephemeral=True)

        new_remaining = current_remaining - 1
        player_line = f"・{self.generation.value.replace('期', '').strip()}期 {self.name.value} ({self.faculty.value})"
        
        # 定員に達した場合は完全終了(closed)として処理する
        status = "closed" if new_remaining <= 0 else None
        new_content = update_recruit_text(latest_content, new_remaining, player_line, status=status)
        new_view = build_recruit_view(new_remaining)

        await latest_message.edit(content=new_content, view=new_view)

        recruiter_id = get_recruiter_id(latest_content)
        if recruiter_id:
            try:
                recruiter = await interaction.client.fetch_user(recruiter_id)
                msg = f"🔔 あなたの募集にエントリーがありました！\n参加者: {player_line}\n"
                
                # もしメッセージが入力されていればDMに追記する
                if self.user_message.value.strip():
                    msg += f"\n💬 **メッセージ:**\n{self.user_message.value.strip()}\n\n"
                
                msg += f"{latest_message.jump_url}"
                
                if new_remaining <= 0:
                    msg += "\n🎉 **定員に達したため、募集を締め切りました。**"
                await recruiter.send(msg)
            except:
                pass
        
        await interaction.followup.send("✅ エントリーが完了しました！", ephemeral=True)


class EditCapacityModal(discord.ui.Modal):
    def __init__(self, message: discord.Message, current_remaining: int):
        super().__init__(title='募集人数の編集')
        self.target_message = message

        self.new_capacity = discord.ui.TextInput(
            label='変更後の「あと◯人」を入力してください',
            placeholder='例: 3 （半角数字）',
            default=str(current_remaining), 
            required=True,
            max_length=2,
        )
        self.add_item(self.new_capacity)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.new_capacity.value.isdigit() or int(self.new_capacity.value) < 0:
            return await interaction.response.send_message('❌ 0以上の半角数字を入力してください。', ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        
        try:
            latest_message = await interaction.channel.fetch_message(self.target_message.id)
        except discord.NotFound:
            return await interaction.followup.send('❌ メッセージが見つかりません。', ephemeral=True)

        new_remaining = int(self.new_capacity.value)
        status = "closed" if new_remaining == 0 else None
        new_content = update_recruit_text(latest_message.content, new_remaining, status=status)
        new_view = build_recruit_view(new_remaining)

        await latest_message.edit(content=new_content, view=new_view)
        
        if new_remaining == 0:
            await interaction.followup.send('✅ 募集人数を0人に変更し、締め切りました！', ephemeral=True)
        else:
            await interaction.followup.send(f'✅ 募集人数を「あと {new_remaining} 人」に変更しました！', ephemeral=True)


class EditMessageModal(discord.ui.Modal):
    def __init__(self, message: discord.Message):
        super().__init__(title='募集メッセージの編集')
        self.target_message = message
        
        current_body = get_body_content(message.content)

        self.new_body = discord.ui.TextInput(
            label='本文（テンプレや演奏者）を編集できます',
            style=discord.TextStyle.paragraph,
            default=current_body[:2000],
            required=True,
            max_length=2000,
        )
        self.add_item(self.new_body)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            latest_message = await interaction.channel.fetch_message(self.target_message.id)
        except discord.NotFound:
            return await interaction.followup.send('❌ メッセージが見つかりません。', ephemeral=True)

        lines = latest_message.content.split('\n')
        
        if len(lines) >= 2 and ("募集" in lines[0] or "終了" in lines[0] or "締め切り" in lines[0]) and "募集者:" in lines[1]:
            header = lines[0]
            recruiter = lines[1]
            new_content = f"{header}\n{recruiter}\n\n{self.new_body.value.strip()}"
        else:
            new_content = self.new_body.value.strip()

        await latest_message.edit(content=new_content)
        await interaction.followup.send('✅ メッセージの本文を上書き更新しました！', ephemeral=True)


# ==========================================
# メインUI（ボタンView）
# ==========================================
class PersistentRecruitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='参加する', style=discord.ButtonStyle.success, custom_id='recruit_join')
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = interaction.message.content
        remaining = get_remaining_capacity(content)
        
        if is_recruit_closed(content) or remaining <= 0:
            return await interaction.response.send_message("❌ この募集は既に締め切られています。", ephemeral=True)
            
        await interaction.response.send_modal(JoinSongModal(interaction.message))

    @discord.ui.button(label='⚙️ 管理 / 編集', style=discord.ButtonStyle.primary, custom_id='recruit_edit')
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = interaction.message.content
        recruiter_id = get_recruiter_id(content)
        
        if recruiter_id and interaction.user.id != recruiter_id:
            return await interaction.response.send_message('❌ 募集を立ち上げた本人のみ操作可能です。', ephemeral=True)

        view = discord.ui.View()
        
        edit_msg_btn = discord.ui.Button(label="本文を編集する", style=discord.ButtonStyle.primary)
        async def edit_msg_callback(i: discord.Interaction):
            latest_msg = await i.channel.fetch_message(interaction.message.id)
            await i.response.send_modal(EditMessageModal(latest_msg))
        edit_msg_btn.callback = edit_msg_callback

        edit_cap_btn = discord.ui.Button(label="募集人数を変更する", style=discord.ButtonStyle.success)
        async def edit_cap_callback(i: discord.Interaction):
            latest_msg = await i.channel.fetch_message(interaction.message.id)
            remaining = get_remaining_capacity(latest_msg.content)
            await i.response.send_modal(EditCapacityModal(latest_msg, remaining))
        edit_cap_btn.callback = edit_cap_callback
        
        if is_recruit_closed(content):
            # 募集終了 or 仮締め切り 状態の場合
            toggle_btn = discord.ui.Button(label="募集を再開する", style=discord.ButtonStyle.success)
            async def toggle_callback(i: discord.Interaction):
                latest_msg = await i.channel.fetch_message(interaction.message.id)
                new_content = update_recruit_text(latest_msg.content, 1)
                await latest_msg.edit(content=new_content, view=build_recruit_view(1))
                await i.response.edit_message(content="✅ 募集を再開しました！(必要に応じて「人数を変更する」から調整してください)", view=None)
            toggle_btn.callback = toggle_callback
            
            view.add_item(edit_msg_btn)
            view.add_item(edit_cap_btn)
            view.add_item(toggle_btn)
        else:
            # 募集中 の場合
            temp_close_btn = discord.ui.Button(label="仮締め切りにする", style=discord.ButtonStyle.primary)
            async def temp_close_callback(i: discord.Interaction):
                latest_msg = await i.channel.fetch_message(interaction.message.id)
                new_content = update_recruit_text(latest_msg.content, 0, status="temp_closed")
                await latest_msg.edit(content=new_content, view=build_recruit_view(0))
                await i.response.edit_message(content="✅ 募集を仮締め切りにしました！", view=None)
            temp_close_btn.callback = temp_close_callback

            close_btn = discord.ui.Button(label="完全に締め切る", style=discord.ButtonStyle.danger)
            async def close_callback(i: discord.Interaction):
                latest_msg = await i.channel.fetch_message(interaction.message.id)
                new_content = update_recruit_text(latest_msg.content, 0, status="closed")
                await latest_msg.edit(content=new_content, view=build_recruit_view(0))
                await i.response.edit_message(content="✅ 募集を締め切りました！", view=None)
            close_btn.callback = close_callback

            view.add_item(edit_msg_btn)
            view.add_item(edit_cap_btn)
            view.add_item(temp_close_btn)
            view.add_item(close_btn)

        await interaction.response.send_message("🔧 **募集の管理**\n行いたい操作を選んでください。", view=view, ephemeral=True)


# ==========================================
# スラッシュコマンド（募集の作成）
# ==========================================
class RawTextRecruitModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title='曲募集の作成')
        
        self.capacity_input = discord.ui.TextInput(
            label='募集人数（半角数字のみ）', placeholder='例: 3', required=True, max_length=2
        )
        self.add_item(self.capacity_input)
        
        self.raw_text = discord.ui.TextInput(
            label='募集テンプレ（人数部分は不要です）', style=discord.TextStyle.paragraph,
            placeholder='【曲名】...\n【曲時間】...\n\n【演奏者】...', required=True, max_length=2000
        )
        self.add_item(self.raw_text)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.capacity_input.value.isdigit() or int(self.capacity_input.value) <= 0:
            return await interaction.response.send_message('❌ 募集人数は1以上の半角数字で入力してください。', ephemeral=True)
        
        capacity = int(self.capacity_input.value)
        raw_val = self.raw_text.value.strip()
        
        raw_val = re.sub(r'^\s*(?:\d+|[〇零一二三四五六七八九])[人名]?募集\s*\n?', '', raw_val).strip()
        
        base_content = (
            f"**🎵 【あと {capacity} 人募集】**\n"
            f"募集者: {interaction.user.mention}\n\n"
            f"{raw_val}"
        )

        view = build_recruit_view(capacity)
        await interaction.response.send_message(content=base_content, view=view)


# ==========================================
# Cog
# ==========================================
class SongRecruitSlashCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_view(PersistentRecruitView())

    @app_commands.command(name='曲募集', description='曲の募集を開始します（このチャンネルに投稿されます）')
    async def recruit_slash(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RawTextRecruitModal())

async def setup(bot):
    await bot.add_cog(SongRecruitSlashCog(bot))