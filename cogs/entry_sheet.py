# cogs/entry_sheet.py
import discord
from discord import app_commands
from discord.ext import commands
import re
import gspread
import asyncio
import unicodedata

from . import sheet_manager

# ==========================================
# 抽出ヘルパー
# ==========================================
def extract_section(pattern: str, text: str, default: str = '') -> str:
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else default

def parse_entry_message(text: str, master_names: dict, last_name_map: dict) -> dict:
    title = extract_section(r'【曲名】([^\n]*)', text, '不明')
    time_str = extract_section(r'【曲時間】([^\n]*)', text, '')
    players_raw = extract_section(r'【演奏者】(.*?)(?=\n【|$)', text, '')
    
    players_list = []
    for line in players_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        
        line = unicodedata.normalize('NFKC', line)
        
        term_match = re.search(r'(\d+)期', line)
        fallback_term = term_match.group(1) if term_match else ""
        
        line = re.sub(r'[(（].*', '', line)
        line = re.sub(r'\d+期', '', line)
        
        line = re.sub(r'[^\w\s]', '', line)
        
        clean_input = re.sub(r'\s+', '', line)
        if not clean_input:
            continue
            
        # 名簿データが1件もない（新規作成時など）場合は照合をスキップし⚠️を付けない
        if not master_names and not last_name_map:
            final_name = f"{fallback_term} {clean_input}".strip()
        elif clean_input in master_names:
            final_name = master_names[clean_input]
        elif clean_input in last_name_map:
            candidates = last_name_map[clean_input]
            if len(candidates) == 1:
                final_name = candidates[0]
            else:
                final_name = f"⚠️ {fallback_term} {clean_input} (同姓複数)".strip()
        else:
            final_name = f"⚠️ {fallback_term} {clean_input}".strip()
            
        players_list.append(final_name)
    
    return {
        "title": title,
        "time": time_str,
        "players": players_list
    }


# ==========================================
# Cog (スラッシュコマンド登録)
# ==========================================
class EntrySheetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_spreadsheet(self, interaction: discord.Interaction, create_if_missing: bool):
        target_role_name = None
        for target, overwrite in interaction.channel.overwrites.items():
            if isinstance(target, discord.Role):
                if target.is_default() or target.managed:
                    continue
                if overwrite.view_channel is True:
                    target_role_name = target.name
                    break

        # チャンネル名から _ の前の文字を抽出
        fallback_name = interaction.channel.name.split('_')[0]

        try:
            gc = sheet_manager.get_gspread_client()
        except Exception as e:
            return None, f"❌ Google認証エラーが発生しました: {e}"

        spreadsheet = None
        used_sheet_name = fallback_name

        # ① まずロール名で検索を試みる
        if target_role_name:
            try:
                spreadsheet = gc.open(target_role_name)
                used_sheet_name = target_role_name
            except gspread.exceptions.SpreadsheetNotFound:
                pass

        # ② ロール名で見つからない、またはそもそもロール名が取得できなかった場合は、チャンネル名で検索
        if not spreadsheet:
            try:
                spreadsheet = gc.open(fallback_name)
                used_sheet_name = fallback_name
            except gspread.exceptions.SpreadsheetNotFound:
                pass

        # ③ どちらでも見つからなかった場合は新規作成
        if not spreadsheet:
            if not create_if_missing:
                return None, f"⚠️ スプレッドシートが見つかりません。先に `/エントリー集計` を行ってください。"
            
            used_sheet_name = fallback_name
            await interaction.followup.send(f"⏳ スプレッドシート「**{used_sheet_name}**」が見つからないため、指定フォルダに新規作成しています...")
            
            url = sheet_manager.create_sheet_via_gas(used_sheet_name)
            if not url:
                return None, "❌ GAS経由でのスプレッドシート作成に失敗しました。"
            
            await asyncio.sleep(4)
            try:
                spreadsheet = gc.open(used_sheet_name)
            except Exception as e:
                return None, f"❌ スプレッドシートの作成は成功しましたが、読み込みに失敗しました: {e}"
                    
        return spreadsheet, used_sheet_name

    @app_commands.command(name='曲数カウント', description='参加者の演奏曲数をカウントします（未指定で全体の曲数を出力）')
    @app_commands.describe(user="特定の人のみカウントしたい場合はメンション（未指定で全体の曲数を表示）")
    async def count_songs(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer(ephemeral=True)

        spreadsheet, result = await self._get_spreadsheet(interaction, create_if_missing=False)
        if not spreadsheet:
            await interaction.followup.send(result)
            return

        worksheets = spreadsheet.worksheets()
        if len(worksheets) < 2:
            await interaction.followup.send("⚠️ エントリー集計のタブ（2つ目のタブ）が見つかりません。先に `/エントリー集計` を実行してください。")
            return

        try:
            entry_sheet = worksheets[1]
            entry_data = entry_sheet.get_all_values()

            if not entry_data or len(entry_data) < 2:
                await interaction.followup.send("⚠️ エントリー集計データが空です。")
                return

            total_entries = len(entry_data) - 1 # 1行目はヘッダーなのでマイナス1

            if user is None:
                await interaction.followup.send(f"🎸 **現在の全体エントリー数: {total_entries}曲**")
                return

            participant_sheet = spreadsheet.get_worksheet(0)
            participant_data = participant_sheet.get_all_values()
            
            if not participant_data:
                await interaction.followup.send("⚠️ 参加者名簿（1つ目のタブ）が空です。")
                return

            headers = participant_data[0]
            term_idx = headers.index("期") if "期" in headers else 2
            last_name_idx = headers.index("名字") if "名字" in headers else 3
            first_name_idx = headers.index("名前") if "名前" in headers else 4
            id_idx = headers.index("Discord ID") if "Discord ID" in headers else 6

            target_formatted_name = None
            target_id_str = str(user.id)

            for row in participant_data[1:]:
                if len(row) > max(term_idx, last_name_idx, first_name_idx, id_idx):
                    if row[id_idx].strip() == target_id_str:
                        term = row[term_idx]
                        last_name = row[last_name_idx].strip()
                        first_name = row[first_name_idx].strip()
                        clean_name = re.sub(r'\s+', '', f"{last_name}{first_name}")
                        target_formatted_name = f"{term} {clean_name}"
                        break
            
            if not target_formatted_name:
                await interaction.followup.send(f"⚠️ {user.mention} さんは参加者名簿（1つ目のタブ）に登録されていません。")
                return

            song_count = 0
            for row in entry_data[1:]:
                if target_formatted_name in row:
                    song_count += 1
            
            await interaction.followup.send(f"🎵 **{target_formatted_name}** さんの現在の演奏曲数は **{song_count}曲** です！")

        except Exception as e:
            await interaction.followup.send(f"❌ データの読み込み中にエラーが発生しました: {e}")
            return


    @app_commands.command(name='エントリー集計', description='チャンネルの権限ロールと同じスプシを探し（無ければ作成して）転記します')
    async def export_entries(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        spreadsheet, result = await self._get_spreadsheet(interaction, create_if_missing=True)
        if not spreadsheet:
            await interaction.followup.send(result)
            return
        used_sheet_name = result

        master_names = {}
        last_name_map = {}
        
        try:
            participant_sheet = spreadsheet.get_worksheet(0)
            participant_data = participant_sheet.get_all_values()
            
            if participant_data:
                headers = participant_data[0]
                
                term_idx = headers.index("期") if "期" in headers else 2
                last_name_idx = headers.index("名字") if "名字" in headers else 3
                first_name_idx = headers.index("名前") if "名前" in headers else 4
                
                for row in participant_data[1:]:
                    if len(row) > max(term_idx, last_name_idx, first_name_idx):
                        term = row[term_idx]
                        last_name = row[last_name_idx].strip()
                        first_name = row[first_name_idx].strip()
                        
                        official_name = f"{last_name} {first_name}"
                        clean_name = re.sub(r'\s+', '', official_name)
                        formatted_name = f"{term} {clean_name}"
                        
                        master_names[clean_name] = formatted_name
                        
                        if last_name not in last_name_map:
                            last_name_map[last_name] = []
                        last_name_map[last_name].append(formatted_name)
        except Exception as e:
            print(f"参加者マスターデータの取得に失敗しました: {e}")

        worksheets = spreadsheet.worksheets()
        if len(worksheets) >= 2:
            worksheet = worksheets[1]
            worksheet.clear()
            worksheet_name = worksheet.title
        else:
            worksheet_name = "エントリー"
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows="100", cols="30")

        extracted_data = []
        extracted_messages = []
        warning_messages_info = [] 
        message_count = 0
        max_players = 0

        try:
            async for message in interaction.channel.history(limit=None, oldest_first=True):
                if '【演奏者】' not in message.content:
                    continue

                # テンプレメッセージ（案内用）を除外
                title_match = re.search(r'【曲名】([^\n]*)', message.content)
                temp_title = title_match.group(1).strip() if title_match else ''
                
                # 「曲名が空っぽ」かつ「期・氏名・学年 という文字が含まれる」場合は無視する
                if not temp_title and '期・氏名・学年' in message.content:
                    continue

                try:
                    for reaction in message.reactions:
                        if str(reaction.emoji) in ['☑️', '⚠️'] and reaction.me:
                            await message.remove_reaction(reaction.emoji, self.bot.user)
                except discord.HTTPException:
                    pass

                parsed = parse_entry_message(message.content, master_names, last_name_map)
                title = parsed['title']
                players = parsed['players']
                
                if len(players) == 0:
                    continue
                
                if len(players) > max_players:
                    max_players = len(players)

                row = [
                    message.jump_url,
                    title,
                    parsed['time'],
                ] + players
                
                extracted_data.append(row)
                
                has_warning = any('⚠️' in p for p in players)
                extracted_messages.append((message, has_warning))
                
                if has_warning:
                    warning_messages_info.append((title, message.jump_url))

                message_count += 1

            if not extracted_data:
                await interaction.followup.send("⚠️ このチャンネルには有効なエントリー情報が見つかりませんでした。")
                return

            headers = ["メッセージURL", "曲名", "曲時間"] + [f"演奏者{i+1}" for i in range(max_players)]
            for row in extracted_data:
                row.extend([''] * (len(headers) - len(row)))

            worksheet.append_row(headers)
            worksheet.append_rows(extracted_data)

            # for msg, has_warning in extracted_messages:
            #     try:
            #         if has_warning:
            #             await msg.add_reaction('⚠️')
            #         else:
            #             await msg.add_reaction('☑️')
            #     except discord.HTTPException:
            #         pass

            summary = (
                f"✅ 集計完了！\n"
                f"スプレッドシート「**{used_sheet_name}**」の、"
                f"2つ目のタブ（**{worksheet_name}**）に合計 **{message_count}件** のデータを転記しました！\n\n"
                f"📊 **スプレッドシートを開く:**\n{spreadsheet.url}"
            )

            if warning_messages_info:
                summary += "\n\n⚠️ **以下のエントリーは名簿との照合ができなかった名前が含まれています（手動で確認してください）：**\n"
                for title, url in warning_messages_info:
                    summary += f"・[{title}]({url})\n"
            
            if len(summary) > 1950:
                summary = summary[:1900] + "\n...（文字数制限のため省略されました）"

            await interaction.followup.send(summary, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 転記処理中にエラーが発生しました: {e}")

async def setup(bot):
    await bot.add_cog(EntrySheetCog(bot))