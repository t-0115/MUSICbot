# cogs/song_recruit_slash.py
import re
import discord
from discord import app_commands
from discord.ext import commands

# ==========================================
# 文字列解析ヘルパー（漢数字対応）
# ==========================================
def format_player_line(line: str) -> str:
    line = line.strip()
    if not line:
        return ''
    return line if line.startswith('・') else f'・{line}'

KANJI_NUMBERS = '〇零一二三四五六七八九'
KANJI_DIGITS = {'〇': 0, '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}

def extract_first_group(pattern: str, text: str, default: str = '不明', flags: int = 0) -> str:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else default

def kanji_to_int(text: str) -> int | None:
    return KANJI_DIGITS.get(text.strip()) if len(text.strip()) == 1 else None

def int_to_kanji(value: int) -> str:
    if 0 <= value <= 9:
        return {v: k for k, v in KANJI_DIGITS.items()}[value]
    return str(value)

def parse_capacity_raw(text: str) -> str:
    match = re.search(r'([^\n]*?(?:\d+|[' + KANJI_NUMBERS + r'])[^\n]*?)募集', text)
    return match.group(1).strip() if match else ''

def normalize_capacity(capacity_raw: str) -> int | None:
    if any(token in capacity_raw for token in ['~', '〜', '-', '約', '程度']):
        return None
    numbers = re.findall(r'\d+', capacity_raw)
    if len(numbers) == 1:
        return int(numbers[0])
    kanji_match = re.search(r'[' + KANJI_NUMBERS + r']', capacity_raw)
    if kanji_match:
        return kanji_to_int(kanji_match.group(0))
    return None

def parse_players(text: str) -> str:
    players_raw = extract_first_group(r'【演奏者】(.*?)(\n【|$)', text, default='', flags=re.DOTALL)
    lines = [format_player_line(line)
             for line in players_raw.splitlines()
             if re.search(r'\d+期', line)]
    return '\n'.join(lines) if lines else '不明'


# ==========================================
# 定員到達時のDM用View ＆ 追加募集Modal
# ==========================================
class AddCapacityModal(discord.ui.Modal):
    def __init__(self, board_message: discord.Message, board_view: 'SongBoardView', dm_view: 'CapacityReachedView'):
        super().__init__(title='追加募集人数の設定')
        self.board_message = board_message
        self.board_view = board_view
        self.dm_view = dm_view

        self.added_capacity = discord.ui.TextInput(
            label='追加で何人募集しますか？',
            placeholder='例: 1 （半角数字）',
            required=True,
            max_length=2,
        )
        self.add_item(self.added_capacity)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.added_capacity.value.isdigit():
            await interaction.response.send_message('❌ 半角数字で入力してください。', ephemeral=True)
            return

        added = int(self.added_capacity.value)
        self.board_view.capacity += added
        
        await self.board_view.update_board_display(self.board_message)
        await self.board_view.update_source_message(interaction.guild)

        for child in self.dm_view.children:
            child.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self.dm_view)

        await interaction.response.send_message('✅ 募集人数を増やし、募集を継続しました！', ephemeral=True)

class CapacityReachedView(discord.ui.View):
    def __init__(self, board_message: discord.Message, board_view: 'SongBoardView'):
        super().__init__(timeout=None)
        self.board_message = board_message
        self.board_view = board_view

    @discord.ui.button(label='募集を締め切る', style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await self.board_view.execute_close(interaction, self.board_message)

    @discord.ui.button(label='追加で募集する', style=discord.ButtonStyle.primary)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCapacityModal(self.board_message, self.board_view, self))


# ==========================================
# 掲示板のメインUI
# ==========================================
class JoinSongModal(discord.ui.Modal):
    def __init__(self, view: 'SongBoardView'):
        super().__init__(title='エントリー情報の入力')
        self.view = view
        self.generation = discord.ui.TextInput(label='期', placeholder='例: 10期', max_length=10, required=True)
        self.name = discord.ui.TextInput(label='氏名', placeholder='例: 山田太郎', max_length=20, required=True)
        self.faculty = discord.ui.TextInput(label='学部・学科・学年', placeholder='例: 工学部情報学科2年', max_length=30, required=True)
        self.user_message = discord.ui.TextInput(label='メッセージ (任意)', style=discord.TextStyle.paragraph, required=False, max_length=200)

        self.add_item(self.generation)
        self.add_item(self.name)
        self.add_item(self.faculty)
        self.add_item(self.user_message)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id in self.view.joined_users:
            await interaction.response.send_message('❌ 登録済みです。', ephemeral=True)
            return

        self.view.joined_users.add(interaction.user.id)
        player_info = format_player_line(f'{self.generation.value} {self.name.value}({self.faculty.value})')
        
        self.view.button_joined_players.append(player_info)
        
        await self.view.add_player_info_to_embed(interaction.message, player_info)
        await self.view.update_source_message(interaction.guild)
        await self.view.update_board_display(interaction.message, interaction)

        if self.view.is_full():
            dm_embed = discord.Embed(
                title='🎉 募集定員に達しました！',
                description=f'曲（**{self.view.song_title}**）が定員に達しました。\n掲示板は「確認中」としてボタンを無効化しています。',
                color=discord.Color.orange(),
            )
            await self.view.send_recruiter_dm(dm_embed, view=CapacityReachedView(interaction.message, self.view))
        else:
            dm_embed = discord.Embed(title='🔔 エントリーがありました！', color=discord.Color.blue())
            dm_embed.add_field(name='曲名', value=self.view.song_title, inline=False)
            dm_embed.add_field(name='参加者', value=player_info, inline=False)
            await self.view.send_recruiter_dm(dm_embed)


class SongBoardView(discord.ui.View):
    def __init__(self, recruiter: discord.Member | discord.User, capacity: int, song_title: str, initial_players: str):
        super().__init__(timeout=None)
        self.recruiter = recruiter
        self.capacity = capacity
        self.song_title = song_title
        self.initial_players = initial_players
        self.source_channel_id = 0
        self.source_message_id = 0
        self.joined_users: set[int] = set()
        self.button_joined_players: list[str] = []

    def is_full(self) -> bool:
        return len(self.joined_users) >= self.capacity

    def get_merged_players(self) -> str:
        all_players = []
        if self.initial_players and self.initial_players != '不明':
            all_players.append(self.initial_players)
        if self.button_joined_players:
            all_players.append('\n'.join(self.button_joined_players))
        return '\n'.join(all_players) if all_players else 'なし'

    async def update_source_message(self, guild: discord.Guild | None) -> None:
        """Botが投稿した大元のメッセージを正規表現で書き換える"""
        if not guild:
            return
        try:
            channel = guild.get_channel(self.source_channel_id)
            if not channel:
                return
            msg = await channel.fetch_message(self.source_message_id)
            
            remaining = max(0, self.capacity - len(self.joined_users))

            # 【追加】横線をリセットする（再開時に元に戻すため）
            base_content = msg.content.replace('~~', '')

            # 1. 募集人数の数字/漢数字部分を置換
            def replace_num(match: re.Match[str]) -> str:
                matched = match.group(1)
                if re.fullmatch(r'\d+', matched):
                    return str(remaining)
                return int_to_kanji(remaining) if len(matched) == 1 else str(remaining)

            new_content = re.sub(
                r'((?:\d+|[' + KANJI_NUMBERS + r']))(?=[^\n]*?募集)',
                replace_num,
                base_content,  # base_contentを使用するように変更
                count=1,
            )

            # 2. 演奏者リストをマージ済みの最新状態に置換
            merged_players = self.get_merged_players()
            if '【演奏者】' in new_content:
                new_content = re.sub(r'【演奏者】.*', f'【演奏者】\n{merged_players}', new_content, flags=re.DOTALL)
            else:
                new_content += f'\n\n【演奏者】\n{merged_players}'

            if new_content != msg.content:
                await msg.edit(content=new_content)
        except Exception:
            pass

    async def update_board_display(self, message: discord.Message, interaction: discord.Interaction = None):
        embed = message.embeds[0] if message.embeds else None
        if embed is None:
            return

        remaining = self.capacity - len(self.joined_users)
        join_button = next((child for child in self.children if getattr(child, 'custom_id', None) == 'song_join'), None)

        def set_field(name: str, value: str):
            for index, field in enumerate(embed.fields):
                if field.name == name:
                    embed.set_field_at(index, name=name, value=value, inline=True)
                    return

        if remaining <= 0:
            set_field('募集人数', '⚠️ 定員到達（確認中）')
            embed.color = discord.Color.orange()
            if join_button:
                join_button.disabled = True
        else:
            set_field('募集人数', f'あと {remaining} 人')
            embed.color = discord.Color.green()
            if join_button:
                join_button.disabled = False

        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await message.edit(embed=embed, view=self)

    async def add_player_info_to_embed(self, message: discord.Message, player_info: str) -> None:
        embed = message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == '現在の演奏者':
                embed.set_field_at(index, name='現在の演奏者', value=self.get_merged_players(), inline=False)
                break

    async def send_recruiter_dm(self, embed: discord.Embed, view: discord.ui.View | None = None) -> None:
        try:
            await self.recruiter.send(embed=embed, view=view)
        except discord.Forbidden:
            pass

    @discord.ui.button(label='参加する', style=discord.ButtonStyle.success, custom_id='song_join')
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.joined_users:
            await interaction.response.send_message('❌ 登録済みです。', ephemeral=True)
            return
        await interaction.response.send_modal(JoinSongModal(self))

    @discord.ui.button(label='締め切る', style=discord.ButtonStyle.danger, custom_id='song_close')
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.recruiter.id:
            await interaction.response.send_message('❌ 募集者のみ操作可能です。', ephemeral=True)
            return
        await self.execute_close(interaction)

    @discord.ui.button(label='再開する', style=discord.ButtonStyle.secondary, custom_id='song_resume')
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.recruiter.id:
            await interaction.response.send_message('❌ 募集者のみ操作可能です。', ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        if '🔒 [募集終了]' in embed.title:
            embed.title = embed.title.replace('🔒 [募集終了]', '🎵 曲募集:')

        for child in self.children:
            child.disabled = False

        await self.update_board_display(interaction.message, interaction)
        # 再開時は元のテキストにも「募集」の文字と人数を復元する必要がありますが、
        # 今の仕様だと「あと○人」のままで進める挙動になります。
        await self.update_source_message(interaction.guild)

    async def execute_close(self, interaction: discord.Interaction, board_message: discord.Message | None = None):
        target_msg = board_message or interaction.message
        embed = target_msg.embeds[0]

        if '🎵 曲募集:' in embed.title:
            embed.title = embed.title.replace('🎵 曲募集:', '🔒 [募集終了]')
        embed.color = discord.Color.red()

        for child in self.children:
            if getattr(child, 'custom_id', None) in ['song_join', 'song_close']:
                child.disabled = True

        if board_message is None:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await target_msg.edit(embed=embed, view=self)

        # 【変更】募集元メッセージを横線(取り消し線)で消す処理
        try:
            channel = interaction.guild.get_channel(self.source_channel_id)
            if channel:
                msg = await channel.fetch_message(self.source_message_id)
                # 全ての行に横線(~~)を引く（空行はそのままにする）
                lines = msg.content.replace('~~', '').split('\n')
                new_content = '\n'.join([f"~~{line}~~" if line.strip() else "" for line in lines])
                await msg.edit(content=new_content)
        except Exception:
            pass

        # コピペ用テキストのDM送信
        dm_embed = discord.Embed(
            title='📝 募集締め切り完了',
            description='募集を締め切りました！\n以下のテキストをコピーしてエントリーチャンネルへ投稿してください。',
            color=discord.Color.gold(),
        )
        
        final_text = f"【曲名】{self.song_title}\n【演奏者】\n{self.get_merged_players()}"
        dm_embed.add_field(name='コピー用テキスト', value=f'```text\n{final_text}\n```', inline=False)
        await self.send_recruiter_dm(dm_embed)


# ==========================================
# 共通の掲示板投稿処理
# ==========================================
async def post_to_board_slash(interaction: discord.Interaction, raw_text: str, capacity: int, title: str, time_str: str, players: str):
    board_channel = discord.utils.get(interaction.guild.text_channels, name='掲示板')
    if not board_channel:
        await interaction.followup.send('❌ 「掲示板」という名前のチャンネルが見つかりません。', ephemeral=True)
        return

    # 1. 募集元チャンネルにBotが入力された文章をそのまま投稿（これによりBotが編集権限を持つ）
    source_msg = await interaction.channel.send(content=raw_text)

    # 2. 掲示板用Viewの作成とEmbedの送信
    view = SongBoardView(
        recruiter=interaction.user,
        capacity=capacity,
        song_title=title,
        initial_players=players,
    )
    view.source_channel_id = source_msg.channel.id
    view.source_message_id = source_msg.id

    embed = discord.Embed(
        title=f'🎵 曲募集: {title}',
        description=f'[#{interaction.channel.name}]({source_msg.jump_url})',
        color=discord.Color.green(),
    )
    embed.set_author(
        name=f'募集者: {interaction.user.display_name}',
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    embed.add_field(name='募集人数', value=f'あと {capacity} 人', inline=True)
    embed.add_field(name='曲時間', value=time_str, inline=True)
    embed.add_field(name='現在の演奏者', value=players if players != '不明' else 'なし', inline=False)

    await board_channel.send(embed=embed, view=view)
    await interaction.followup.send(f'✅ {board_channel.mention} に掲示板を作成し、このチャンネルに募集文を投稿しました！', ephemeral=True)


# ==========================================
# スラッシュコマンド用のModal群
# ==========================================
class CapacityConfirmModal(discord.ui.Modal):
    def __init__(self, raw_text: str, title: str, time_str: str, players: str):
        super().__init__(title='募集人数の確認')
        self.raw_text = raw_text
        self.song_title = title
        self.time_str = time_str
        self.players = players
        self.exact_capacity = discord.ui.TextInput(
            label='上限人数（半角数字）',
            placeholder='例: 3',
            required=True,
            max_length=2,
        )
        self.add_item(self.exact_capacity)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.exact_capacity.value.isdigit():
            await interaction.response.send_message('❌ 半角数字で入力してください。', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await post_to_board_slash(interaction, self.raw_text, int(self.exact_capacity.value), self.song_title, self.time_str, self.players)


class RawTextRecruitModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title='曲募集の作成')
        self.raw_text = discord.ui.TextInput(
            label='募集文を貼り付けてください',
            style=discord.TextStyle.paragraph,
            placeholder='○人募集\n【曲名】...\n【曲時間】...\n\n【演奏者】...',
            required=True,
            max_length=2000,
        )
        self.add_item(self.raw_text)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.raw_text.value
        capacity_raw = parse_capacity_raw(text)
        title = extract_first_group(r'【曲名】(.*)', text)
        time_str = extract_first_group(r'【曲時間】(.*)', text)
        players = parse_players(text)

        capacity = normalize_capacity(capacity_raw)
        
        if capacity is None:
            # 解析不能な場合は人数入力モーダルへ
            await interaction.response.send_modal(CapacityConfirmModal(text, title, time_str, players))
        else:
            await interaction.response.defer(ephemeral=True)
            await post_to_board_slash(interaction, text, capacity, title, time_str, players)


# ==========================================
# Cog (スラッシュコマンド登録)
# ==========================================
class SongRecruitSlashCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='曲募集', description='既存のテンプレートを貼り付けて曲募集を開始します')
    async def recruit_slash(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RawTextRecruitModal())

async def setup(bot):
    await bot.add_cog(SongRecruitSlashCog(bot))