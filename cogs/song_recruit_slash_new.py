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
    """テキストから募集者のDiscord IDを読み取る（全角コロンにも対応）"""
    match = re.search(r'募集者[:：]\s*<@!?(\d+)>', content)
    return int(match.group(1)) if match else 0

def get_body_content(content: str) -> str:
    """メッセージから自動生成されたヘッダーを除いた本文を抽出する"""
    lines = content.split('\n')
    if len(lines) >= 2 and ("募集" in lines[0] or "終了" in lines[0] or "締め切り" in lines[0]) and "募集者" in lines[1]:
        return '\n'.join(lines[2:]).strip()
    return content

def is_player_registered(content: str, name: str) -> bool:
    """【演奏者】セクション内に、指定された名前が既に登録されているかチェックする"""
    match = re.search(r'【演奏者】(.*?)(?=\n【|$)', content, re.DOTALL)
    if not match:
        return False
        
    players_section = match.group(1).strip()
    
    for line in players_section.splitlines():
        line = line.strip()
        m = re.search(r'・\s*\d+\s*期\s+(.*?)\s*(?:[(（]|\Z)', line)
        if m:
            registered_name = m.group(1).strip()
            if registered_name == name.strip():
                return True
                
    return False

def update_recruit_text(content: str, new_remaining: int, new_player_line: str = None, status: str = None) -> str:
    """テキスト内の人数やステータスを更新し、必要なら参加者を追記する"""
    if status == "temp_closed":
        new_header = "**⏸️ 【仮締め切り】**"
    elif status == "closed":
        new_header = "**🔒 【募集終了】**"
    elif new_remaining <= 0:
        new_header = "**🔒 【募集終了】**"
    else:
        new_header = f"**🎵 【あと {new_remaining} 人募集】**"

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
        # custom_idを修正した新しいボタン名で判定
        if getattr(child, 'custom_id', None) == 'song_recruit_join':
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
        self.user_message = discord.ui.TextInput(
            label='募集者へのメッセージ（任意）', 
            style=discord.TextStyle.paragraph,
            placeholder='例: よろしくお願いします！', 
            required=False,
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

        if is_player_registered(latest_content, self.name.value):
            return await interaction.followup.send('❌ 既に登録されています。', ephemeral=True)
            
        current_remaining = get_remaining_capacity(latest_content)
        if is_recruit_closed(latest_content) or current_remaining <= 0:
            return await interaction.followup.send('❌ 申し訳ありません、タッチの差で定員が埋まり募集が締め切られました。', ephemeral=True)

        new_remaining = current_remaining - 1
        player_line = f"・{self.generation.value.replace('期', '').strip()}期 {self.name.value} ({self.faculty.value})"
        
        status = "closed" if new_remaining <= 0 else None
        new_content = update_recruit_text(latest_content, new_remaining, player_line, status=status)
        new_view = build_recruit_view(new_remaining)

        await latest_message.edit(content=new_content, view=new_view)

        # DM送信とエラーハンドリング
        recruiter_id = get_recruiter_id(latest_content)
        print(f"DEBUG: 抽出された募集者ID -> {recruiter_id}")
        
        if recruiter_id:
            try:
                recruiter = await interaction.client.fetch_user(recruiter_id)
                msg = f"🔔 あなたの募集にエントリーがありました！\n参加者: {player_line}\n"
                
                if self.user_message.value.strip():
                    msg += f"\n💬 **メッセージ:**\n{self.user_message.value.strip()}\n\n"
                
                msg += f"{latest_message.jump_url}"
                
                if new_remaining <= 0:
                    msg += "\n🎉 **定員に達したため、募集を締め切りました。**"
                    
                await recruiter.send(msg)
                print("DEBUG: DM送信成功！")
            except discord.Forbidden:
                print("DEBUG: ユーザー側の設定（Forbidden）でDMが弾かれました")
            except Exception as e:
                print(f"DEBUG: DM送信中にその他のエラーが発生 -> {e}")
        else:
            print("DEBUG: 募集者IDが見つからなかったため、DM処理をスキップしました")
        
        await interaction.followup.send("✅ 参加が完了しました！", ephemeral=True)


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
        
        if len(lines) >= 2 and ("募集" in lines[0] or "終了" in lines[0] or "締め切り" in lines[0]) and "募集者" in lines[1]:
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

    @discord.ui.button(label='🟢 参加する', style=discord.ButtonStyle.success, custom_id='song_recruit_join')
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = interaction.message.content
        remaining = get_remaining_capacity(content)
        
        if is_recruit_closed(content) or remaining <= 0:
            return await interaction.response.send_message("❌ この募集は既に締め切られています。", ephemeral=True)
            
        await interaction.response.send_modal(JoinSongModal(interaction.message))

    @discord.ui.button(label='📝 本文編集', style=discord.ButtonStyle.secondary, custom_id='song_recruit_edit_msg')
    async def edit_msg_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = interaction.message.content
        recruiter_id = get_recruiter_id(content)
        
        if recruiter_id and interaction.user.id != recruiter_id:
            return await interaction.response.send_message('❌ 募集を立ち上げた本人のみ操作可能です。', ephemeral=True)
            
        await interaction.response.send_modal(EditMessageModal(interaction.message))

    @discord.ui.button(label='👥 人数変更', style=discord.ButtonStyle.secondary, custom_id='song_recruit_edit_cap')
    async def edit_cap_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = interaction.message.content
        recruiter_id = get_recruiter_id(content)
        
        if recruiter_id and interaction.user.id != recruiter_id:
            return await interaction.response.send_message('❌ 募集を立ち上げた本人のみ操作可能です。', ephemeral=True)

        remaining = get_remaining_capacity(content)
        await interaction.response.send_modal(EditCapacityModal(interaction.message, remaining))

    @discord.ui.button(label='⏸️ 停止/再開', style=discord.ButtonStyle.primary, custom_id='song_recruit_toggle')
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = interaction.message.content
        recruiter_id = get_recruiter_id(content)
        
        if recruiter_id and interaction.user.id != recruiter_id:
            return await interaction.response.send_message('❌ 募集を立ち上げた本人のみ操作可能です。', ephemeral=True)

        is_closed = is_recruit_closed(content)
        if is_closed:
            new_content = update_recruit_text(content, 1)
            await interaction.message.edit(content=new_content, view=build_recruit_view(1))
            await interaction.response.send_message("✅ 募集を再開しました！(必要に応じて「👥 人数変更」から調整してください)", ephemeral=True)
        else:
            new_content = update_recruit_text(content, 0, status="temp_closed")
            await interaction.message.edit(content=new_content, view=build_recruit_view(0))
            await interaction.response.send_message("✅ 募集を仮締め切りにしました！", ephemeral=True)

    @discord.ui.button(label='🔒 終了', style=discord.ButtonStyle.danger, custom_id='song_recruit_close')
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        content = interaction.message.content
        recruiter_id = get_recruiter_id(content)
        
        if recruiter_id and interaction.user.id != recruiter_id:
            return await interaction.response.send_message('❌ 募集を立ち上げた本人のみ操作可能です。', ephemeral=True)

        new_content = update_recruit_text(content, 0, status="closed")
        await interaction.message.edit(content=new_content, view=build_recruit_view(0))
        await interaction.response.send_message("✅ 募集を完全に締め切りました！", ephemeral=True)


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