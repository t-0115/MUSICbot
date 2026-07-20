# cogs/start.py
import re
import asyncio
import gspread
import discord
from discord import app_commands
from discord.ext import commands
from . import sheet_manager
from .sheet_manager import append_to_sheet, delete_spreadsheet, create_sheet_via_gas, remove_from_sheet

# ★ シート作成直後、Google Drive側の検索インデックスへの反映に
#   数秒のタイムラグが生じることがある（gc.open(role_name) が見つけられない）。
#   entry_sheet.py 側は既にこの対策で待機しているので、こちらも合わせる。
SHEET_INDEX_WAIT_SECONDS = 4

# ==========================================
# 排他制御用ロックマネージャー
# ==========================================
class MessageLockManager:
    """メッセージIDごとにasyncio.Lockを管理するクラス。
    同一メッセージへの並行操作（参加・取消の同時押し等）を防ぐ。"""

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()  # _locks辞書自体への同時アクセスを防ぐ

    async def acquire(self, message_id: int) -> asyncio.Lock:
        """指定メッセージIDのロックを取得して返す"""
        async with self._meta_lock:
            if message_id not in self._locks:
                self._locks[message_id] = asyncio.Lock()
        lock = self._locks[message_id]
        await lock.acquire()
        return lock

    def release(self, message_id: int):
        """指定メッセージIDのロックを解放する"""
        if message_id in self._locks:
            self._locks[message_id].release()

    def get_context(self, message_id: int):
        """with文で使えるコンテキストマネージャーを返す"""
        return _LockContext(self, message_id)


class _LockContext:
    """MessageLockManagerをasync withで使うためのコンテキストマネージャー"""

    def __init__(self, manager: MessageLockManager, message_id: int):
        self._manager = manager
        self._message_id = message_id

    async def __aenter__(self):
        await self._manager.acquire(self._message_id)

    async def __aexit__(self, *args):
        self._manager.release(self._message_id)


# モジュールレベルのシングルトン
_lock_manager = MessageLockManager()


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

    lines = []
    for term in sorted(grouped.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        lines.append(f"**{term}期**: {' / '.join(grouped[term])}")

    embed = discord.Embed(title=f"🎉 「{role_name}」の募集", color=discord.Color.blue())
    desc = f"専用チャンネル: {channels_mentions}\n"
    if sheet_url:
        desc += f"📄 **[参加者一覧]({sheet_url})**\n\n"
    desc += "下のボタンで参加してください。"
    embed.description = desc

    # ★ Discordのフィールドvalueは1024文字までという制限があるため、
    #   超える場合は複数フィールドに分割して追加する。
    #   parse_participants側は embed.fields を全件ループしているので
    #   フィールド名が "👥 参加者" で始まっていれば自動的に拾われる。
    FIELD_VALUE_LIMIT = 1024
    chunks = []
    current_chunk = ""
    for line in lines:
        candidate = f"{current_chunk}{line}\n"
        if len(candidate) > FIELD_VALUE_LIMIT:
            if current_chunk:
                chunks.append(current_chunk)
            # 1行だけでも1024文字を超える極端なケースへの保険
            if len(line) + 1 > FIELD_VALUE_LIMIT:
                line_with_nl = line + "\n"
                for i in range(0, len(line_with_nl), FIELD_VALUE_LIMIT):
                    chunks.append(line_with_nl[i:i + FIELD_VALUE_LIMIT])
                current_chunk = ""
            else:
                current_chunk = f"{line}\n"
        else:
            current_chunk = candidate
    if current_chunk:
        chunks.append(current_chunk)

    if not chunks:
        chunks = ["なし"]

    # Embed全体のフィールド数上限（25個）にも収まるよう安全マージンを確保
    MAX_FIELDS = 24
    if len(chunks) > MAX_FIELDS:
        chunks = chunks[:MAX_FIELDS]
        chunks[-1] += "\n...(表示しきれない参加者がいます。スプレッドシートをご確認ください)"

    for i, chunk in enumerate(chunks):
        field_name = f"👥 参加者 ({len(participants)}名)" if i == 0 else "👥 参加者（続き）"
        embed.add_field(name=field_name, value=chunk, inline=False)

    return embed


def parse_roster_sheet(sheet) -> tuple[dict, int]:
    """参加者名簿シート（sheet_manager.DEFAULT_HEADERS形式）の全行を読み、
    「募集名」ごとに participants 辞書へグループ化する。
    通常は1シート=1募集名だが、手動編集等で複数の募集名が混在していても
    それぞれ別の募集として扱えるようにグループ化している。

    戻り値: ({role_name: {discord_id: {"name": "姓 名", "term": term}}}, スキップした行数)
    """
    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        return {}, 0

    role_idx = sheet_manager.get_column_index(sheet, "募集名", fallback_index=2) - 1
    term_idx = sheet_manager.get_column_index(sheet, "期", fallback_index=3) - 1
    last_idx = sheet_manager.get_column_index(sheet, "名字", fallback_index=4) - 1
    first_idx = sheet_manager.get_column_index(sheet, "名前", fallback_index=5) - 1
    id_idx = sheet_manager.get_column_index(sheet, "Discord ID", fallback_index=6) - 1
    max_idx = max(role_idx, term_idx, last_idx, first_idx, id_idx)

    grouped: dict = {}
    skipped = 0
    for row in all_values[1:]:
        if len(row) <= max_idx:
            skipped += 1
            continue

        role_name = row[role_idx].strip()
        discord_id_str = row[id_idx].strip()
        if not role_name or not discord_id_str.isdigit():
            skipped += 1
            continue

        term = row[term_idx].strip()
        name = f"{row[last_idx].strip()} {row[first_idx].strip()}".strip()
        grouped.setdefault(role_name, {})[int(discord_id_str)] = {"name": name, "term": term}

    return grouped, skipped


def _split_name(combined_name: str) -> tuple[str, str]:
    """Embedに保存されている「姓 名」形式の文字列を、シート書き込み用に分割する。
    スペースが見つからない場合は名前欄を空にする。"""
    parts = combined_name.split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], ""


def sync_sheet_with_discord(role_name: str, participants: dict) -> dict:
    """スプレッドシートの内容をDiscordの参加者一覧に合わせて自己修復する（ベストエフォート処理）。
    Discord側を正として、シートに無い人を追加し、シートにだけ残っている行を削除する。

    「参加」「参加取り消し」ボタンが押されるたびに呼ばれる想定。専用の同期コマンドを
    別途用意するのではなく、普段の操作のついでに少しずつ過去の書き込み漏れ・削除漏れを
    解消していく方針。

    戻り値:
        {
            "added": [name, ...],                        追加できた人の名前
            "add_failed": {discord_id_str: error_msg},    追加に失敗した人
            "removed": [discord_id_str, ...],              削除できたDiscord ID
            "remove_failed": {discord_id_str: error_msg},  削除に失敗した人
        }
    シート自体が開けない・読み込めない場合は、今回の参加/取消操作自体を巻き込んで
    失敗させないよう、処理をスキップして空の結果を返す。
    """
    result = {"added": [], "add_failed": {}, "removed": [], "remove_failed": {}}

    try:
        gc = sheet_manager.get_gspread_client()
        sheet = gc.open(role_name).sheet1
        id_col = sheet_manager.get_column_index(sheet, "Discord ID", fallback_index=6)
        all_values = sheet.get_all_values()
    except Exception as e:
        print(f"[自動同期] シートの読み込みに失敗したためスキップします: {e}")
        return result

    sheet_ids = set()
    if len(all_values) > 1:
        for row in all_values[1:]:
            if len(row) >= id_col:
                cell_value = row[id_col - 1].strip()
                if cell_value:
                    sheet_ids.add(cell_value)

    discord_ids = {str(uid) for uid in participants.keys()}
    only_in_discord = discord_ids - sheet_ids
    only_in_sheet = sheet_ids - discord_ids

    # ① Discordにはいるがシートに無い人 → シートに追加
    for uid_str in only_in_discord:
        uid = int(uid_str)
        info = participants.get(uid)
        if not info:
            continue
        last_name, first_name = _split_name(info["name"])
        try:
            append_to_sheet(
                role_name=role_name,
                term=info["term"],
                last_name=last_name,
                first_name=first_name,
                discord_id=uid
            )
            result["added"].append(info["name"])
        except Exception as e:
            result["add_failed"][uid_str] = str(e)
            print(f"[自動同期] 追加失敗 ({info['name']}): {e}")

    # ② シートにはあるがDiscordにいない人 → シートから削除
    for uid_str in only_in_sheet:
        try:
            remove_from_sheet(role_name, int(uid_str))
            result["removed"].append(uid_str)
        except Exception as e:
            result["remove_failed"][uid_str] = str(e)
            print(f"[自動同期] 削除失敗 (Discord ID: {uid_str}): {e}")

    if result["added"] or result["removed"]:
        print(f"[自動同期] role={role_name} added={result['added']} removed={result['removed']}")

    return result


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

            # ★ チャンネル削除後にも復元できるよう、作成したチャンネルをシートに記録しておく（ベストエフォート）。
            try:
                sheet_manager.record_channels(self.role_name, created_channels)
            except Exception as e:
                print(f"[チャンネル記録] スプレッドシートへの記録に失敗しました: {e}")

            # ★ シート作成直後はDrive側の検索インデックス反映が間に合わないことがあるため、
            #   参加ボタン付きのメッセージを投稿する前に少し待つ。
            #   （役職・チャンネル作成の間にある程度時間は経過しているが、念のため保証する）
            await asyncio.sleep(SHEET_INDEX_WAIT_SECONDS)

            embed = create_embed(self.role_name, channels_mentions, sheet_url, {})
            view = StartView()
            
            await interaction.channel.send(embed=embed, view=view)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ 権限が足りないため、チャンネルまたはロールを作成できませんでした。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 予期しないエラーが発生しました: {e}", ephemeral=True)

class JoinModal(discord.ui.Modal, title='参加者情報の入力'):
    term_input = discord.ui.TextInput(label='期 (2桁)', placeholder='例：24', min_length=1, max_length=2)
    last_name_input = discord.ui.TextInput(label='名字', placeholder='例：山田', max_length=15)
    first_name_input = discord.ui.TextInput(label='名前', placeholder='例：太郎', max_length=15)

    def __init__(self, role: discord.Role, role_name: str, channels_mentions: str, sheet_url: str, participants: dict, message_id: int):
        super().__init__()
        self.role = role
        self.role_name = role_name
        self.channels_mentions = channels_mentions
        self.sheet_url = sheet_url
        self.participants = participants
        self.message_id = message_id  # ★ ロック解放に使う

    async def on_submit(self, interaction: discord.Interaction):
        # バリデーションはロック取得前に行う（ロックを無駄に保持しないため）
        if not self.term_input.value.isdigit():
            await interaction.response.send_message("❌ 期は数字で入力してください。", ephemeral=True)
            return

        try:
            async with _lock_manager.get_context(self.message_id):
                # ★ ロック内でメッセージを再フェッチして最新の参加者リストを取得
                fresh_message = await interaction.channel.fetch_message(self.message_id)
                _, _, _, fresh_participants = extract_info_from_message(fresh_message)

                if interaction.user.id in fresh_participants:
                    await interaction.response.send_message("❌ 既に登録されています！（送信中に別の操作が完了しました）", ephemeral=True)
                    return

                term = self.term_input.value.zfill(2)
                last_name = self.last_name_input.value
                first_name = self.first_name_input.value
                user_name_combined = f"{last_name} {first_name}"
                discord_id = interaction.user.id

                fresh_participants[discord_id] = {"name": user_name_combined, "term": term}
                await interaction.user.add_roles(self.role)

                new_embed = create_embed(self.role_name, self.channels_mentions, self.sheet_url, fresh_participants)
                # ★ ロック内でメッセージを更新（他の操作と順序が保証される）
                await fresh_message.edit(embed=new_embed)

            # ロック解放後にユーザーへ応答（editが完了してから通知）
            await interaction.response.send_message(f"【{term}期】{user_name_combined}として参加登録しました！", ephemeral=True)

            # ★ 自分の登録だけでなく、ついでにシート全体をDiscordの状態に自己修復する。
            #   （過去の書き込み漏れ・削除漏れがあれば、この操作を機に解消される）
            sync_result = sync_sheet_with_discord(self.role_name, fresh_participants)

            if str(discord_id) in sync_result["add_failed"]:
                error_msg = sync_result["add_failed"][str(discord_id)]
                print(f"Spreadsheet Error: {error_msg}")
                await interaction.followup.send(
                    "⚠️ （システム通知）スプレッドシートへの記録に失敗しました。"
                    "Discord上は参加登録済みですが、シートには反映されていない可能性があります。"
                    "別の人が参加/参加取り消しボタンを押すと自動的に再試行されます。",
                    ephemeral=True
                )

        except discord.NotFound:
            await interaction.response.send_message("❌ 募集メッセージが見つかりません。削除された可能性があります。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 予期せぬエラーが発生しました: {e}", ephemeral=True)


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
            try:
                await ch.delete()
            except discord.Forbidden:
                await interaction.followup.send(f"⚠️ チャンネル {ch.name} の削除に失敗しました（権限不足）。", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"⚠️ チャンネル {ch.name} の削除中にエラーが発生しました: {e}", ephemeral=True)

        if self.role:
            try:
                await self.role.delete()
            except discord.Forbidden:
                await interaction.followup.send(f"⚠️ ロール {self.role.name} の削除に失敗しました（権限不足）。", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"⚠️ ロール {self.role.name} の削除中にエラーが発生しました: {e}", ephemeral=True)

        if self.original_message:
            try:
                await self.original_message.delete()
            except discord.HTTPException as e:
                await interaction.followup.send(f"⚠️ 募集メッセージの削除中にエラーが発生しました: {e}", ephemeral=True)

        try:
            delete_spreadsheet(self.role_name)
        except Exception as e:
            await interaction.followup.send(f"⚠️ スプレッドシートの削除中にエラーが発生しました: {e}", ephemeral=True)


# ==========================================
# Views (ボタンUI関連)
# ==========================================
class StartView(discord.ui.View):
    """永続的(Persistent)なView。固有の状態は持たず、毎回メッセージから情報を復元する。"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success, custom_id="start_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        message_id = interaction.message.id

        # ★ ボタン押下時点でロックを取得して重複登録チェック
        async with _lock_manager.get_context(message_id):
            role_name, channels_mentions, sheet_url, participants = extract_info_from_message(interaction.message)

            if interaction.user.id in participants:
                await interaction.response.send_message("❌ 既に登録されています！", ephemeral=True)
                return

            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if not role:
                await interaction.response.send_message("❌ 対象のロールが見つかりません。削除された可能性があります。", ephemeral=True)
                return

            # ★ モーダルを開く（ロックはここで解放される。on_submit内で再取得する）
            await interaction.response.send_modal(
                JoinModal(role, role_name, channels_mentions, sheet_url, participants, message_id)
            )

    @discord.ui.button(label="参加取り消し", style=discord.ButtonStyle.primary, custom_id="start_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        message_id = interaction.message.id

        # ★ ロック内で参加者リストを読み取り・更新・メッセージ編集を一括実行
        async with _lock_manager.get_context(message_id):
            # 最新のメッセージを再フェッチして状態を取得
            fresh_message = await interaction.channel.fetch_message(message_id)
            role_name, channels_mentions, sheet_url, participants = extract_info_from_message(fresh_message)

            if interaction.user.id not in participants:
                await interaction.response.send_message("まだ参加していません。", ephemeral=True)
                return

            del participants[interaction.user.id]

            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if role:
                await interaction.user.remove_roles(role)

            new_embed = create_embed(role_name, channels_mentions, sheet_url, participants)
            await fresh_message.edit(embed=new_embed)

        await interaction.response.send_message("参加を取り消しました。", ephemeral=True)

        # ★ 自分の取消だけでなく、ついでにシート全体をDiscordの状態に自己修復する。
        sync_result = sync_sheet_with_discord(role_name, participants)

        if str(interaction.user.id) in sync_result["remove_failed"]:
            error_msg = sync_result["remove_failed"][str(interaction.user.id)]
            print(f"行削除時のエラー: {error_msg}")
            # ★ Discord側は取消済みなのに、シート側の削除に失敗したことをサイレントにしない。
            try:
                await interaction.followup.send(
                    "⚠️ （システム通知）スプレッドシートからの削除に失敗しました。"
                    "Discord上は取消済みですが、シート上にはまだ行が残っている可能性があります。"
                    "別の人が参加/参加取り消しボタンを押すと自動的に再試行されます。",
                    ephemeral=True
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="削除", style=discord.ButtonStyle.danger, custom_id="start_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_name, channels_mentions, _, _ = extract_info_from_message(interaction.message)
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        
        # メンション文字列からチャンネルIDを抽出
        channel_ids = [int(cid) for cid in re.findall(r"<#(\d+)>", channels_mentions)]
        channels = [interaction.guild.get_channel(cid) for cid in channel_ids if interaction.guild.get_channel(cid)]
        
        await interaction.response.send_modal(DeleteConfirmModal(role, role_name, channels, interaction.message))


# ==========================================
# 参加者チェック（Discord ⇔ スプレッドシート 突き合わせ・確認専用）
# ==========================================
@app_commands.context_menu(name="参加者チェック")
async def check_consistency(interaction: discord.Interaction, message: discord.Message):
    """募集メッセージを右クリック（長押し）して実行。
    Embed上の参加者一覧とスプレッドシートの内容を突き合わせ、不一致を報告する（確認のみ）。
    実際の同期（追加・削除）はこのコマンドでは行わない。
    誰かが「参加」または「参加取り消し」ボタンを押すたびに sync_sheet_with_discord() が
    自動で走り、その時点の不一致を少しずつ解消していく仕組みになっている。"""
    await interaction.response.defer(ephemeral=True)

    role_name, _, _, participants = extract_info_from_message(message)
    if not role_name or role_name == "不明":
        await interaction.followup.send("❌ このメッセージは募集メッセージとして認識できませんでした。", ephemeral=True)
        return

    try:
        gc = sheet_manager.get_gspread_client()
        sheet = gc.open(role_name).sheet1
    except Exception as e:
        await interaction.followup.send(f"❌ スプレッドシート「{role_name}」を開けませんでした: {e}", ephemeral=True)
        return

    id_col = sheet_manager.get_column_index(sheet, "Discord ID", fallback_index=6)
    all_values = sheet.get_all_values()

    sheet_ids = set()
    if len(all_values) > 1:
        for row in all_values[1:]:
            if len(row) >= id_col:
                cell_value = row[id_col - 1].strip()
                if cell_value:
                    sheet_ids.add(cell_value)

    discord_ids = {str(uid) for uid in participants.keys()}

    only_in_discord = discord_ids - sheet_ids
    only_in_sheet = sheet_ids - discord_ids

    if not only_in_discord and not only_in_sheet:
        await interaction.followup.send("✅ Discordの参加者一覧とスプレッドシートは一致しています！", ephemeral=True)
        return

    lines = ["⚠️ 不一致が見つかりました。\n"]

    if only_in_discord:
        lines.append("**【Discordにはいるが、シートに無い】** → シートへの書き込み漏れの可能性")
        for uid in sorted(only_in_discord):
            info = participants.get(int(uid), {})
            lines.append(f"・{info.get('name', '?')} (<@{uid}>)")
        lines.append("")

    if only_in_sheet:
        lines.append("**【シートにはあるが、Discordにいない】** → 取消時の削除漏れの可能性")
        for uid in sorted(only_in_sheet):
            lines.append(f"・Discord ID: {uid}")

    lines.append("\nℹ️ 誰かが「参加」または「参加取り消し」ボタンを押すと、その操作のついでに自動で同期されます。")

    summary = "\n".join(lines)
    if len(summary) > 1900:
        summary = summary[:1900] + "\n...(文字数制限のため省略されました)"

    await interaction.followup.send(summary, ephemeral=True)


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

                # ★ シート作成直後はDrive側の検索インデックス反映が間に合わないことがあるため、
                #   参加ボタン付きのメッセージを投稿する前に少し待つ。
                await asyncio.sleep(SHEET_INDEX_WAIT_SECONDS)
                
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

    @app_commands.command(name="start復元", description="スプレッドシートの参加者名簿から募集メッセージ・チャンネル・ロールを復元します")
    @app_commands.describe(sheet_url="復元元の参加者名簿スプレッドシートのURL")
    async def restore_recruit(self, interaction: discord.Interaction, sheet_url: str):
        await interaction.response.defer(ephemeral=True)

        try:
            gc = sheet_manager.get_gspread_client()
            spreadsheet = gc.open_by_url(sheet_url)
            sheet = spreadsheet.sheet1
        except gspread.exceptions.SpreadsheetNotFound:
            await interaction.followup.send("❌ 指定されたスプレッドシートが見つかりませんでした。URLを確認してください。", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ スプレッドシートを開けませんでした: {e}", ephemeral=True)
            return

        grouped, skipped = parse_roster_sheet(sheet)
        if not grouped:
            await interaction.followup.send("❌ 復元できる参加者データが見つかりませんでした。", ephemeral=True)
            return

        try:
            channel_records = sheet_manager.get_recorded_channels(spreadsheet)
        except Exception as e:
            print(f"[復元] チャンネル情報の読み込みに失敗しました: {e}")
            channel_records = {}

        posted = []
        created_roles = []
        created_channels_report = []
        channel_failed = []
        channel_perm_failed = []
        role_missing_channels = []
        role_assigned_count = 0
        role_assign_failed = []
        member_not_found = 0

        for role_name, participants in grouped.items():
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if not role:
                try:
                    role = await interaction.guild.create_role(name=role_name)
                    created_roles.append(role_name)
                except discord.Forbidden:
                    role = None

            recreated_channels = []
            if role:
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    role: discord.PermissionOverwrite(view_channel=True),
                    interaction.guild.me: discord.PermissionOverwrite(view_channel=True)
                }

                for record in channel_records.get(role_name, []):
                    ch = interaction.guild.get_channel(record["id"]) if record["id"] else None
                    if not ch:
                        ch = discord.utils.get(interaction.guild.text_channels, name=record["name"])

                    if not ch:
                        try:
                            ch = await interaction.guild.create_text_channel(
                                name=record["name"],
                                category=getattr(interaction.channel, 'category', None),
                                overwrites=overwrites
                            )
                            created_channels_report.append(record["name"])
                        except discord.Forbidden:
                            channel_failed.append(record["name"])
                            continue
                    else:
                        # ★ 既存チャンネルでも、非公開設定（対象ロールのみ閲覧可）を復元のたびに再適用し、
                        #   手動変更等によるドリフトを是正する（他の overwrite は巻き込まないよう個別に設定）。
                        try:
                            await ch.set_permissions(interaction.guild.default_role, view_channel=False)
                            await ch.set_permissions(role, view_channel=True)
                            await ch.set_permissions(interaction.guild.me, view_channel=True)
                        except discord.Forbidden:
                            channel_perm_failed.append(record["name"])

                    # ★ 再作成やID不一致でDiscord側のIDが変わった場合、シート側も最新IDに更新しておく
                    if record["id"] != ch.id:
                        sheet_manager.update_channel_id(spreadsheet, record["name"], ch.id)

                    recreated_channels.append(ch)
            elif channel_records.get(role_name):
                # ★ ロールが無い状態でチャンネルを作ると非公開設定にできないため、
                #   /start本来の挙動（ロールが無ければチャンネルも作らない）に合わせてスキップする。
                role_missing_channels.append(role_name)

            channels_mentions = " ".join(ch.mention for ch in recreated_channels) if recreated_channels else "なし"

            # ★ 参加者全員にロールを付け直す（復元直後はDiscordロールが未付与のため）
            if role:
                for uid, info in participants.items():
                    member = interaction.guild.get_member(uid)
                    if not member:
                        try:
                            member = await interaction.guild.fetch_member(uid)
                        except discord.NotFound:
                            member_not_found += 1
                            continue
                        except discord.HTTPException:
                            member_not_found += 1
                            continue

                    if role in member.roles:
                        continue

                    try:
                        await member.add_roles(role)
                        role_assigned_count += 1
                    except discord.Forbidden:
                        role_assign_failed.append(info["name"])

            embed = create_embed(role_name, channels_mentions, spreadsheet.url, participants)
            # ★ Embed内の参加者メンションは元々通知対象外だが、念のため一切メンション通知が飛ばないようにする
            await interaction.channel.send(embed=embed, view=StartView(), allowed_mentions=discord.AllowedMentions.none())
            posted.append(f"「{role_name}」({len(participants)}名)")

        lines = [f"✅ {len(posted)}件の募集メッセージを復元しました: " + "、".join(posted)]
        if created_roles:
            lines.append("🔧 ロールを再作成しました: " + "、".join(created_roles))
        if created_channels_report:
            lines.append("🔧 チャンネルを再作成しました: " + "、".join(created_channels_report))
        if channel_failed:
            lines.append("⚠️ 権限不足のため再作成できなかったチャンネルがあります: " + "、".join(channel_failed))
        if channel_perm_failed:
            lines.append("⚠️ 権限不足のため非公開設定を再適用できなかったチャンネルがあります: " + "、".join(channel_perm_failed))
        if role_missing_channels:
            lines.append("⚠️ ロールを作成できなかったため、以下の募集ではチャンネルの復元をスキップしました: " + "、".join(role_missing_channels))
        if role_assigned_count:
            lines.append(f"🎭 {role_assigned_count}人にロールを再付与しました。")
        if member_not_found:
            lines.append(f"⚠️ {member_not_found}人はサーバーに見つからなかったためロール付与をスキップしました（退出済みの可能性）。")
        if role_assign_failed:
            lines.append("⚠️ 権限不足のためロールを付与できなかった人がいます: " + "、".join(role_assign_failed))
        if skipped:
            lines.append(f"⚠️ {skipped}行はDiscord IDが不正/欠落のためスキップしました。")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

async def setup(bot):
    await bot.add_cog(StartCog(bot))
    bot.tree.add_command(check_consistency)