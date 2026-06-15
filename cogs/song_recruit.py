# cogs/song_recruit.py
import re
import discord
from discord import app_commands
from discord.ext import commands

# ==========================================
# 文字列解析ヘルパー
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
        player_info = self.view.format_player_info(self.generation.value, self.name.value, self.faculty.value)
        
        # 💡ボタンから参加した人の情報をマージ用に保持
        self.view.button_joined_players.append(player_info)
        
        await self.view.add_player_info(interaction.message, player_info)
        await self.view.update_source_message(interaction)
        await self.view.update_board_display(interaction.message, interaction)

        if self.view.is_full():
            dm_embed = discord.Embed(
                title='🎉 募集定員に達しました！',
                description=(
                    f'曲（**{self.view.get_song_title(interaction.message.embeds[0])}**）が定員に達しました。\n'
                    '掲示板は「確認中」としてボタンを無効化しています。'
                ),
                color=discord.Color.orange(),
            )
            await self.view.send_recruiter_dm(dm_embed, view=CapacityReachedView(interaction.message, self.view))
        else:
            dm_embed = self.view.build_join_notify_embed(interaction.message, player_info)
            await self.view.send_recruiter_dm(dm_embed)


class SongBoardView(discord.ui.View):
    def __init__(self, recruiter: discord.Member | discord.User, capacity: int, channel_id: int, message_id: int):
        super().__init__(timeout=None)
        self.recruiter = recruiter
        self.capacity = capacity
        self.original_channel_id = channel_id
        self.original_message_id = message_id
        self.joined_users: set[int] = set()
        self.button_joined_players: list[str] = [] # 💡ボタン参加者の保持用

    @staticmethod
    def get_embed(message: discord.Message) -> discord.Embed | None:
        return message.embeds[0] if message.embeds else None

    @staticmethod
    def get_field(embed: discord.Embed, field_name: str, default: str = '') -> str:
        return next((field.value for field in embed.fields if field.name == field_name), default)

    @staticmethod
    def set_field(embed: discord.Embed, field_name: str, value: str, inline: bool = False) -> None:
        for index, field in enumerate(embed.fields):
            if field.name == field_name:
                embed.set_field_at(index, name=field_name, value=value, inline=inline)
                return
        embed.add_field(name=field_name, value=value, inline=inline)

    @staticmethod
    def format_player_info(generation: str, name: str, faculty: str) -> str:
        return format_player_line(f'{generation} {name}({faculty})')

    @staticmethod
    def get_song_title(embed: discord.Embed) -> str:
        return embed.title.replace('🎵 曲募集: ', '') if embed.title else '不明'

    def is_full(self) -> bool:
        return len(self.joined_users) >= self.capacity

    def get_button(self, custom_id: str) -> discord.ui.Button | None:
        return next((child for child in self.children if getattr(child, 'custom_id', None) == custom_id), None)

    async def update_source_message(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        try:
            channel = interaction.guild.get_channel(self.original_channel_id)
            if channel is None:
                return

            original_msg = await channel.fetch_message(self.original_message_id)
            if original_msg is None or original_msg.content is None:
                return

            remaining = max(0, self.capacity - len(self.joined_users))

            def replace_num(match: re.Match[str]) -> str:
                matched = match.group(1)
                if re.fullmatch(r'\d+', matched):
                    return str(remaining)
                return int_to_kanji(remaining) if len(matched) == 1 else str(remaining)

            new_content = re.sub(
                r'((?:\d+|[' + KANJI_NUMBERS + r']))(?=[^\n]*?募集)',
                replace_num,
                original_msg.content,
                count=1,
            )
            if new_content != original_msg.content:
                await original_msg.edit(content=new_content)
        except discord.Forbidden:
            pass
        except Exception:
            pass

    async def update_board_display(self, message: discord.Message, interaction: discord.Interaction = None):
        embed = self.get_embed(message)
        if embed is None:
            return

        remaining = self.capacity - len(self.joined_users)
        join_button = self.get_button('song_join')

        if remaining <= 0:
            self.set_field(embed, '募集人数', '⚠️ 定員到達（確認中）', inline=True)
            embed.color = discord.Color.orange()
            if join_button:
                join_button.disabled = True
        else:
            self.set_field(embed, '募集人数', f'あと {remaining} 人', inline=True)
            embed.color = discord.Color.green()
            if join_button:
                join_button.disabled = False

        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await message.edit(embed=embed, view=self)

    async def add_player_info(self, message: discord.Message, player_info: str) -> None:
        embed = self.get_embed(message)
        if embed is None:
            return

        current = self.get_field(embed, '現在の演奏者', default='不明')
        value = player_info if current in ['不明', ''] else f'{current}\n{player_info}'
        self.set_field(embed, '現在の演奏者', value, inline=False)

    def build_join_notify_embed(self, message: discord.Message, player_info: str) -> discord.Embed:
        embed = discord.Embed(title='🔔 エントリーがありました！', color=discord.Color.blue())
        embed.add_field(name='曲名', value=self.get_song_title(message.embeds[0]), inline=False)
        embed.add_field(name='参加者', value=player_info, inline=False)
        return embed

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

    async def execute_close(self, interaction: discord.Interaction, board_message: discord.Message | None = None):
        target_msg = board_message or interaction.message
        embed = self.get_embed(target_msg)
        if embed is None:
            return

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

        latest_content = '（元のメッセージが取得できませんでした。手動でコピーしてください）'
        try:
            channel = interaction.guild.get_channel(self.original_channel_id)
            if channel is not None:
                original_msg = await channel.fetch_message(self.original_message_id)
                latest_content = re.sub(r'[^\n]*?\d+[^\n]*?募集\s*\n?', '', original_msg.content, count=1)
        except Exception:
            pass

        players_field = self.get_field(embed, '現在の演奏者', default='')
        if '【演奏者】' in latest_content:
            final_text = re.sub(r'【演奏者】.*', f'【演奏者】\n{players_field}', latest_content, flags=re.DOTALL)
        else:
            final_text = f'{latest_content}\n\n【演奏者】\n{players_field}'

        dm_embed = discord.Embed(
            title='📝 募集締め切り完了',
            description='大本のメッセージから最新の内容を取得し、参加者を追記しました！\n以下のテキストをコピーしてエントリーチャンネルへ投稿してください。',
            color=discord.Color.gold(),
        )
        dm_embed.add_field(name='コピー用テキスト', value=f'```text\n{final_text}\n```', inline=False)

        await self.send_recruiter_dm(dm_embed)


# ==========================================
# 共通処理 ＆ Cog
# ==========================================
async def post_to_board(interaction: discord.Interaction, message: discord.Message, capacity: int, title: str, time_str: str, players: str, cog: 'SongRecruitCog'):
    board_channel = discord.utils.get(interaction.guild.text_channels, name='掲示板')
    if not board_channel:
        await interaction.response.send_message('❌ 「掲示板」チャンネルが見つかりません。', ephemeral=True)
        return

    embed = discord.Embed(
        title=f'🎵 曲募集: {title}',
        description=f'[#{message.channel.name}]({message.jump_url})',
        color=discord.Color.green(),
    )
    embed.set_author(
        name=f'募集者: {message.author.display_name}',
        icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
    )
    embed.add_field(name='募集人数', value=f'あと {capacity} 人', inline=True)
    embed.add_field(name='曲時間', value=time_str, inline=True)
    embed.add_field(name='現在の演奏者', value=players, inline=False)

    view = SongBoardView(
        recruiter=message.author,
        capacity=capacity,
        channel_id=message.channel.id,
        message_id=message.id,
    )
    board_msg = await board_channel.send(embed=embed, view=view)
    
    # 💡更新検知のために紐付けを記録
    cog.active_boards[message.id] = {
        "board_channel_id": board_channel.id,
        "board_message_id": board_msg.id,
        "view": view
    }
    
    await interaction.response.send_message('✅ 掲示板に作成しました！', ephemeral=True)


class CapacityConfirmModal(discord.ui.Modal):
    def __init__(self, message: discord.Message, title: str, time_str: str, players: str, cog: 'SongRecruitCog'):
        super().__init__(title='募集人数の確認')
        self.target_message = message
        self.song_title = title
        self.time_str = time_str
        self.players = players
        self.cog = cog # 💡追加
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
        await post_to_board(interaction, self.target_message, int(self.exact_capacity.value), self.song_title, self.time_str, self.players, self.cog)


class SongRecruitCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_boards = {} # 💡大元のメッセージと掲示板の紐付けを管理
        self.ctx_menu = app_commands.ContextMenu(name='曲募集ボードに登録', callback=self.register_song)
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    async def register_song(self, interaction: discord.Interaction, message: discord.Message):
        text = message.content
        capacity_raw = parse_capacity_raw(text)
        title = extract_first_group(r'【曲名】(.*)', text)
        time_str = extract_first_group(r'【曲時間】(.*)', text)
        players = parse_players(text)

        capacity = normalize_capacity(capacity_raw)
        if capacity is None:
            await interaction.response.send_modal(CapacityConfirmModal(message, title, time_str, players, self))
        else:
            await post_to_board(interaction, message, capacity, title, time_str, players, self)

    # 💡大元のメッセージが編集されたことを検知するリスナー
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.id not in self.active_boards:
            return
            
        board_info = self.active_boards[after.id]
        board_channel = self.bot.get_channel(board_info["board_channel_id"])
        if not board_channel:
            return
            
        try:
            board_msg = await board_channel.fetch_message(board_info["board_message_id"])
        except discord.NotFound:
            del self.active_boards[after.id]
            return

        view = board_info["view"]
        text = after.content
        
        # ヘルパー関数を利用してスッキリと再パース
        capacity_raw = parse_capacity_raw(text)
        title = extract_first_group(r'【曲名】(.*)', text)
        time_str = extract_first_group(r'【曲時間】(.*)', text)
        players_base = parse_players(text)
        
        if players_base == '不明':
            players_base = ""

        # 大元のメンバーリストと、掲示板のボタンから参加したメンバーをマージ
        if view.button_joined_players:
            if players_base:
                players = players_base + "\n" + "\n".join(view.button_joined_players)
            else:
                players = "\n".join(view.button_joined_players)
        else:
            players = players_base if players_base else "不明"

        # 定員が明確な表現に更新された場合は反映
        new_capacity = normalize_capacity(capacity_raw)
        if new_capacity is not None:
            view.capacity = new_capacity

        embed = board_msg.embeds[0]
        
        # 締め切り状態（鍵マーク）を維持
        if '🔒 [募集終了]' in embed.title:
            embed.title = f"🔒 [募集終了] {title}"
        else:
            embed.title = f"🎵 曲募集: {title}"
            
        for index, field in enumerate(embed.fields):
            if field.name == "曲時間":
                embed.set_field_at(index, name="曲時間", value=time_str, inline=True)
            elif field.name == "現在の演奏者":
                embed.set_field_at(index, name="現在の演奏者", value=players, inline=False)
                
        # 残り人数の再計算やボタンの制御を自動で適用
        await view.update_board_display(board_msg)

async def setup(bot):
    await bot.add_cog(SongRecruitCog(bot))