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
    players_raw = extract_first_group(r'【演奏者】(.*?)(\n【|$|\n\n🔗)', text, default='', flags=re.DOTALL)
    lines = [format_player_line(line)
             for line in players_raw.splitlines()
             if re.search(r'\d+期', line)]
    return '\n'.join(lines) if lines else '不明'


# ==========================================
# 定員到達時のDM用View ＆ 追加募集Modal
# ==========================================
class AddCapacityModal(discord.ui.Modal):
    def __init__(self, trigger_message: discord.Message, board_view: 'SongBoardView', dm_view: 'CapacityReachedView'):
        super().__init__(title='追加募集人数の設定')
        self.trigger_message = trigger_message
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
        
        await self.board_view.update_board_display(self.trigger_message)
        await self.board_view.update_source_message(self.trigger_message)

        for child in self.dm_view.children:
            child.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self.dm_view)

        await interaction.response.send_message('✅ 募集人数を増やし、募集を継続しました！', ephemeral=True)

class CapacityReachedView(discord.ui.View):
    def __init__(self, trigger_message: discord.Message, board_view: 'SongBoardView'):
        super().__init__(timeout=None)
        self.trigger_message = trigger_message
        self.board_view = board_view

    @discord.ui.button(label='募集を締め切る', style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await self.board_view.execute_close(interaction, self.trigger_message)

    @discord.ui.button(label='追加で募集する', style=discord.ButtonStyle.primary)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCapacityModal(self.trigger_message, self.board_view, self))


# ==========================================
# 掲示板のメインUI
# ==========================================
class JoinSongModal(discord.ui.Modal):
    def __init__(self, view: 'SongBoardView'):
        super().__init__(title='エントリー情報の入力')
        self.view = view
        self.generation = discord.ui.TextInput(label='期（半角数字のみ）', placeholder='例: 10', max_length=5, required=True)
        self.name = discord.ui.TextInput(label='氏名', placeholder='例: 山田太郎', max_length=20, required=True)
        self.faculty = discord.ui.TextInput(label='学部・学科・学年', placeholder='例: 工学部情報学科2年', max_length=30, required=True)
        self.user_message = discord.ui.TextInput(label='メッセージ (任意)', style=discord.TextStyle.paragraph, required=False, max_length=200)

        self.add_item(self.generation)
        self.add_item(self.name)
        self.add_item(self.faculty)
        self.add_item(self.user_message)

    async def on_submit(self, interaction: discord.Interaction):
        gen_input = self.generation.value.replace('期', '').strip()
        
        if not gen_input.isascii() or not gen_input.isdigit():
            await interaction.response.send_message('❌ 「期」は半角数字で入力してください。（例: 10）', ephemeral=True)
            return

        if interaction.user.id in self.view.joined_users:
            await interaction.response.send_message('❌ 登録済みです。', ephemeral=True)
            return

        self.view.joined_users.add(interaction.user.id)
        player_info = format_player_line(f'{gen_input}期 {self.name.value}({self.faculty.value})')
        
        self.view.button_joined_players.append(player_info)
        
        # 掲示板Embedと元メッセージの両方を更新（どちらのボタンから押されても対応可能）
        await self.view.update_board_display(interaction.message, interaction)
        await self.view.update_source_message(interaction.message, interaction)

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

    async def get_both_messages(self, target_message: discord.Message) -> tuple[discord.Message | None, discord.Message | None]:
        """引数のメッセージが元テキストか掲示板かを自動判定し、URLを辿って両方のメッセージオブジェクトを取得する"""
        source_msg = None
        board_msg = None

        if target_message.embeds and ('🎵' in target_message.embeds[0].title or '🔒' in target_message.embeds[0].title):
            board_msg = target_message
            match = re.search(r'https://discord\.com/channels/\d+/(\d+)/(\d+)', board_msg.embeds[0].description or '')
            if match:
                ch = target_message.guild.get_channel(int(match.group(1)))
                if ch:
                    try:
                        source_msg = await ch.fetch_message(int(match.group(2)))
                    except discord.NotFound:
                        pass
        else:
            source_msg = target_message
            match = re.search(r'https://discord\.com/channels/\d+/(\d+)/(\d+)', source_msg.content)
            if match:
                ch = target_message.guild.get_channel(int(match.group(1)))
                if ch:
                    try:
                        board_msg = await ch.fetch_message(int(match.group(2)))
                    except discord.NotFound:
                        pass

        return source_msg, board_msg

    async def update_source_message(self, message: discord.Message, interaction: discord.Interaction = None) -> None:
        """元のテキストメッセージを更新する（ボタンの再添付も行う）"""
        source_msg, board_msg = await self.get_both_messages(message)
        if not source_msg:
            return

        try:
            remaining = max(0, self.capacity - len(self.joined_users))
            base_content = source_msg.content.replace('~~', '')

            # 1. 募集人数の更新
            def replace_num(m: re.Match[str]) -> str:
                matched = m.group(1)
                if re.fullmatch(r'\d+', matched):
                    return str(remaining)
                return int_to_kanji(remaining) if len(matched) == 1 else str(remaining)

            new_content = re.sub(
                r'((?:\d+|[' + KANJI_NUMBERS + r']))(?=[^\n]*?募集)',
                replace_num,
                base_content,
                count=1,
            )

            # 2. 演奏者リストの更新 (🔗リンクの手前に挿入する)
            merged_players = self.get_merged_players()
            if '【演奏者】' in new_content:
                new_content = re.sub(r'【演奏者】(.*?)(?=\n【|$|\n\n🔗)', f'【演奏者】\n{merged_players}', new_content, flags=re.DOTALL)
            else:
                if '🔗 [掲示板を見る]' in new_content:
                    new_content = new_content.replace('🔗 [掲示板を見る]', f'\n【演奏者】\n{merged_players}\n\n🔗 [掲示板を見る]')
                else:
                    new_content += f'\n\n【演奏者】\n{merged_players}'

            # 3. 締め切り状態であれば全体に横線を引く
            is_closed = False
            if board_msg and board_msg.embeds and '🔒' in board_msg.embeds[0].title:
                is_closed = True

            if is_closed:
                lines = new_content.split('\n')
                new_lines = []
                for line in lines:
                    if line.strip() and not line.startswith('🔗'):
                        new_lines.append(f"~~{line}~~")
                    else:
                        new_lines.append(line)
                new_content = '\n'.join(new_lines)

            if interaction and not interaction.response.is_done() and interaction.message.id == source_msg.id:
                await interaction.response.edit_message(content=new_content, view=self)
            else:
                await source_msg.edit(content=new_content, view=self)
        except Exception:
            pass

    async def update_board_display(self, message: discord.Message, interaction: discord.Interaction = None):
        """掲示板のEmbedメッセージを更新する"""
        _, board_msg = await self.get_both_messages(message)
        if not board_msg or not board_msg.embeds:
            return

        embed = board_msg.embeds[0]
        remaining = self.capacity - len(self.joined_users)
        
        # ボタンの状態からクローズ状態かを推測する
        join_button = next((child for child in self.children if getattr(child, 'custom_id', None) == 'song_join'), None)
        is_closed_manually = (join_button and join_button.disabled and remaining > 0)

        for index, field in enumerate(embed.fields):
            if field.name == '現在の演奏者':
                embed.set_field_at(index, name='現在の演奏者', value=self.get_merged_players(), inline=False)
            elif field.name == '募集人数':
                if is_closed_manually:
                    embed.set_field_at(index, name='募集人数', value='終了', inline=True)
                elif remaining <= 0:
                    embed.set_field_at(index, name='募集人数', value='⚠️ 定員到達（確認中）', inline=True)
                else:
                    embed.set_field_at(index, name='募集人数', value=f'あと {remaining} 人', inline=True)

        if is_closed_manually:
            embed.color = discord.Color.red()
            if '🎵 曲募集:' in embed.title:
                embed.title = embed.title.replace('🎵 曲募集:', '🔒 [募集終了]')
        elif remaining <= 0:
            embed.color = discord.Color.orange()
        else:
            embed.color = discord.Color.green()
            if join_button:
                join_button.disabled = False
            if '🔒 [募集終了]' in embed.title:
                embed.title = embed.title.replace('🔒 [募集終了]', '🎵 曲募集:')

        if interaction and not interaction.response.is_done() and interaction.message.id == board_msg.id:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await board_msg.edit(embed=embed, view=self)

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

        for child in self.children:
            child.disabled = False

        await self.update_board_display(interaction.message, interaction)
        await self.update_source_message(interaction.message, interaction)

    async def execute_close(self, interaction: discord.Interaction, target_message: discord.Message | None = None):
        msg = target_message or interaction.message
        source_msg, board_msg = await self.get_both_messages(msg)

        # 1. Viewのコンポーネント（ボタン）を無効化
        for child in self.children:
            if getattr(child, 'custom_id', None) in ['song_join', 'song_close']:
                child.disabled = True

        # 2. 掲示板側の更新
        if board_msg and board_msg.embeds:
            embed = board_msg.embeds[0]
            if '🎵 曲募集:' in embed.title:
                embed.title = embed.title.replace('🎵 曲募集:', '🔒 [募集終了]')
            embed.color = discord.Color.red()
            
            for index, field in enumerate(embed.fields):
                if field.name == '募集人数':
                    embed.set_field_at(index, name='募集人数', value='終了', inline=True)

            if interaction.message.id == board_msg.id and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await board_msg.edit(embed=embed, view=self)

        # 3. 元テキスト側の更新
        final_text = f"【曲名】{self.song_title}\n【演奏者】\n{self.get_merged_players()}"
        if source_msg:
            clean_content = source_msg.content.replace('~~', '')
            merged_players = self.get_merged_players()
            if '【演奏者】' in clean_content:
                final_text = re.sub(r'【演奏者】(.*?)(?=\n【|$|\n\n🔗)', f'【演奏者】\n{merged_players}', clean_content, flags=re.DOTALL)
            else:
                if '🔗 [掲示板を見る]' in clean_content:
                    final_text = clean_content.replace('🔗 [掲示板を見る]', f'\n【演奏者】\n{merged_players}\n\n🔗 [掲示板を見る]')
                else:
                    final_text = clean_content + f'\n\n【演奏者】\n{merged_players}'

            lines = final_text.split('\n')
            new_lines = [f"~~{line}~~" if line.strip() and not line.startswith('🔗') else line for line in lines]
            new_content = '\n'.join(new_lines)
            
            if interaction.message.id == source_msg.id and not interaction.response.is_done():
                await interaction.response.edit_message(content=new_content, view=self)
            else:
                await source_msg.edit(content=new_content, view=self)

        # 4. DM送信（コピペ用テキストからは🔗リンクを除外する）
        final_text_no_link = re.sub(r'\n\n🔗.*', '', final_text)
        dm_embed = discord.Embed(
            title='📝 募集締め切り完了',
            description='募集を締め切りました！\n以下のテキストをコピーしてエントリーチャンネルへ投稿してください。',
            color=discord.Color.gold(),
        )
        dm_embed.add_field(name='コピー用テキスト', value=f'```text\n{final_text_no_link}\n```', inline=False)
        await self.send_recruiter_dm(dm_embed)


# ==========================================
# 共通の掲示板投稿処理
# ==========================================
async def post_to_board_slash(interaction: discord.Interaction, raw_text: str, capacity: int, title: str, time_str: str, players: str):
    board_channel = discord.utils.get(interaction.guild.text_channels, name='掲示板')
    if not board_channel:
        await interaction.followup.send('❌ 「掲示板」という名前のチャンネルが見つかりません。', ephemeral=True)
        return

    view = SongBoardView(
        recruiter=interaction.user,
        capacity=capacity,
        song_title=title,
        initial_players=players,
    )

    # まず元テキストをView付きで送信する
    source_msg = await interaction.channel.send(content=raw_text, view=view)

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

    # 掲示板にEmbedを送信する
    board_msg = await board_channel.send(embed=embed, view=view)

    # 元テキストの下部に掲示板へのリンクを追記して更新する（これで双方向のリンクが完了！）
    updated_raw_text = raw_text + f"\n\n🔗 [掲示板を見る]({board_msg.jump_url})"
    await source_msg.edit(content=updated_raw_text, view=view)

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