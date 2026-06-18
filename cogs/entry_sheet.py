# cogs/entry_sheet.py
import discord
from discord import app_commands
from discord.ext import commands
import re
import gspread
import asyncio

# パターンAでのインポート
from . import sheet_manager

# ==========================================
# 抽出ヘルパー
# ==========================================
def extract_section(pattern: str, text: str, default: str = '') -> str:
    """指定した正規表現パターンで文字列を抽出する"""
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else default

def parse_entry_message(text: str) -> dict:
    """メッセージから必要なエントリー情報を抽出する"""
    # 1行で終わる項目
    title = extract_section(r'【曲名】([^\n]*)', text, '不明')
    time_str = extract_section(r'【曲時間】([^\n]*)', text, '')
    
    # 演奏者ブロック全体を抽出
    players_raw = extract_section(r'【演奏者】(.*?)(?=\n【|$)', text, '')
    
    # 演奏者を一人ずつに分割してリスト化
    players_list = []
    for line in players_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        
        # 1. 行頭の「〇」「・」や空白文字を削除
        line = re.sub(r'^[〇・\s]+', '', line)
        # 2. カッコ「(」や「（」以降の文字（学部や学年など）をすべて削除
        line = re.sub(r'[(（].*', '', line).strip()
        
        if line:
            players_list.append(line)
    
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

    @app_commands.command(name='エントリー集計', description='チャンネルの権限ロールと同じスプシを探し（無ければ作成して）転記します')
    async def export_entries(self, interaction: discord.Interaction):
        # 処理に時間がかかるため、先に「考え中」状態にする
        await interaction.response.defer(ephemeral=False)

        # ------------------------------------------
        # 1. チャンネルの権限から対象の「ロール名」を自動特定
        # ------------------------------------------
        target_role_name = None
        for target, overwrite in interaction.channel.overwrites.items():
            if isinstance(target, discord.Role):
                if target.is_default() or target.managed:
                    continue
                if overwrite.view_channel is True:
                    target_role_name = target.name
                    break

        if not target_role_name:
            await interaction.followup.send(
                "❌ このチャンネルの権限設定から、対象となるロール名を特定できませんでした。\n"
                "対象ロールに「チャンネルを見る」権限が明示的に与えられているか確認してください。"
            )
            return

        # sheet_manager から gspread クライアントを取得
        try:
            gc = sheet_manager.get_gspread_client()
        except Exception as e:
            await interaction.followup.send(f"❌ Google認証エラーが発生しました: {e}")
            return

        # ------------------------------------------
        # 2. スプレッドシートの取得（無ければ sheet_manager で作成）
        # ------------------------------------------
        try:
            # まずは既存のスプレッドシートを探す
            spreadsheet = gc.open(target_role_name)
        except gspread.exceptions.SpreadsheetNotFound:
            # 見つからなかった場合は、sheet_manager を使ってGAS経由で新規作成
            await interaction.followup.send(f"⏳ スプレッドシート「**{target_role_name}**」が見つからないため、指定フォルダに新規作成しています...")
            
            url = sheet_manager.create_sheet_via_gas(target_role_name)
            if not url:
                await interaction.followup.send("❌ GAS経由でのスプレッドシート作成に失敗しました。")
                return
            
            # Google Drive API側に反映されるまで少しラグがあるため待機
            await asyncio.sleep(4)
            try:
                spreadsheet = gc.open(target_role_name)
            except Exception as e:
                await interaction.followup.send(f"❌ スプレッドシートの作成は成功しましたが、読み込みに失敗しました: {e}")
                return
        except Exception as e:
            await interaction.followup.send(f"❌ スプレッドシートの検索中にエラーが発生しました: {e}")
            return

        # ------------------------------------------
        # 3. 2つ目のタブを取得（無ければ新規作成）
        # ------------------------------------------
        worksheets = spreadsheet.worksheets()
        
        if len(worksheets) >= 2:
            # すでにタブが2つ以上ある場合は、2つ目（インデックス1）を指定
            worksheet = worksheets[1]
            worksheet.clear() # 既存なら中身をリセット
            worksheet_name = worksheet.title
        else:
            # タブが1つしかない場合は、2つ目のタブとして新規作成
            worksheet_name = "エントリー" # 任意のシート名
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows="100", cols="30")

        # ------------------------------------------
        # 4. Discordのメッセージを解析して書き込み
        # ------------------------------------------
        extracted_data = []
        extracted_messages = []
        message_count = 0
        max_players = 0  # 最も参加者が多いバンドの人数を記録

        try:
            async for message in interaction.channel.history(limit=None, oldest_first=True):
                if '【曲名】' not in message.content:
                    continue

                parsed = parse_entry_message(message.content)
                players = parsed['players']
                
                # 最大人数の更新
                if len(players) > max_players:
                    max_players = len(players)

                # ベースの行（URL, 曲名, 曲時間）に、演奏者のリスト（D列以降）を合体させる
                row = [
                    message.jump_url,      # A列: URL
                    parsed['title'],       # B列: 曲名
                    parsed['time'],        # C列: 曲時間
                ] + players
                
                extracted_data.append(row)
                extracted_messages.append(message)
                message_count += 1

            if not extracted_data:
                await interaction.followup.send("⚠️ このチャンネルにはエントリー情報（【曲名】を含むメッセージ）が見つかりませんでした。")
                return

            # ヘッダーを動的に作成（演奏者1, 演奏者2... と最大人数分作る）
            headers = ["メッセージURL", "曲名", "曲時間"] + [f"演奏者{i+1}" for i in range(max_players)]
            
            # gspreadのエラーを防ぐため、全行の長さをヘッダーの長さに合わせる（足りない列は空白文字で埋める）
            for row in extracted_data:
                row.extend([''] * (len(headers) - len(row)))

            # 書き込み
            worksheet.append_row(headers)
            worksheet.append_rows(extracted_data)

            # 対象メッセージにリアクションをつける
            for msg in extracted_messages:
                try:
                    await msg.add_reaction('☑️')
                except discord.HTTPException:
                    pass

            # 完了メッセージ
            await interaction.followup.send(
                f"✅ 集計完了！\n"
                f"スプレッドシート「**{target_role_name}**」の、"
                f"2つ目のタブ（**{worksheet_name}**）に合計 **{message_count}件** のデータを転記し、対象メッセージに☑️をつけました！\n\n"
                f"📊 **スプレッドシートを開く:**\n{spreadsheet.url}"
            )

        except Exception as e:
            await interaction.followup.send(f"❌ 転記処理中にエラーが発生しました: {e}")

async def setup(bot):
    await bot.add_cog(EntrySheetCog(bot))