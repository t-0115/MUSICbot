# cogs/sheet_manager.py
import gspread
import datetime
from google.oauth2.service_account import Credentials
import urllib.request
import json
import re

# ==========================================
# 設定部分
# ==========================================
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

GAS_WEBAPP_URL = config["GAS_WEBAPP_URL"]
FOLDER_URL = config["FOLDER_URL"]
BOT_EMAIL = config["BOT_EMAIL"]

# ★ 今後項目を増やす時は、ここのリストを変更するだけでOKになります！
DEFAULT_HEADERS = ["日時", "募集名", "期", "名字", "名前", "Discord ID"]

# ★ 作成済みチャンネルを記録する専用タブ（募集メッセージ削除後の復元用）
CHANNEL_SHEET_TITLE = "チャンネル情報"
CHANNEL_HEADERS = ["募集名", "チャンネル名", "チャンネルID"]

# ★ entry_sheet.py が転記に使うタブ名。
#   タブの並び順（インデックス）は他のタブ追加で変わりうるため、名前で参照する。
ENTRY_SHEET_TITLE = "エントリー"

# ==========================================
# 共通関数
# ==========================================
def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file('credentials.json', scopes=scopes)
    return gspread.authorize(credentials)

def get_column_index(sheet, header_name: str, fallback_index: int) -> int:
    """1行目の見出しから、対象の列が左から何番目かを探す（1始まり）"""
    try:
        headers = sheet.row_values(1)
        return headers.index(header_name) + 1
    except Exception:
        # エラーが起きた場合や見つからなかった場合は、デフォルトの列番号を返す
        return fallback_index

# ==========================================
# スプレッドシート操作のメインロジック
# ==========================================
def create_sheet_via_gas(role_name: str) -> str | None:
    """GASを経由して新しいスプレッドシートを作成する"""
    match = re.search(r'folders/([a-zA-Z0-9-_]+)', FOLDER_URL)
    folder_id = match.group(1) if match else ""

    payload = {
        "action": "create",
        "roleName": role_name,
        "folderId": folder_id,
        "botEmail": BOT_EMAIL,
        "headers": DEFAULT_HEADERS # ★ GASにヘッダー構成を指示する
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(GAS_WEBAPP_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get("status") == "success":
                url = result.get('url')
                print(f"GASでのスプレッドシート作成成功: {url}")
                return url
            else:
                print(f"GAS側でエラーが発生しました: {result.get('message')}")
                return None
    except Exception as e:
        print(f"GASへの通信エラー: {e}")
        return None

def append_to_sheet(role_name: str, term: str, last_name: str, first_name: str, discord_id: int):
    """作成されたスプレッドシートを開いてデータを1行追加する（重複チェック付き）"""
    gc = get_gspread_client()
    
    try:
        sheet = gc.open(role_name).sheet1
        
        # ★ 「Discord ID」という見出しが何列目にあるか自動で探す
        id_col = get_column_index(sheet, "Discord ID", fallback_index=6)
        
        # 見つからなかった場合はエラーにならず None が返ってきます
        existing_cell = sheet.find(str(discord_id), in_column=id_col)
        if existing_cell:
            print(f"重複を検知しました。ID: {discord_id} の追加をスキップします。")
            return  # 既に存在する場合は、ここで処理を強制終了して書き込まない

        # 重複がなければ書き込む
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # ※ もし DEFAULT_HEADERS の順番を変えたら、ここの row の順番も合わせる
        row = [now, role_name, term, last_name, first_name, str(discord_id)]
        sheet.append_row(row)
        
    except Exception as e:
        print(f"スプレッドシート行追加エラー: {e}")
        raise e

def remove_from_sheet(role_name: str, discord_id: int):
    """Discord IDを元に、スプレッドシートから該当ユーザーの行を削除する"""
    gc = get_gspread_client()
    try:
        sheet = gc.open(role_name).sheet1
        # ★ 「Discord ID」という見出しが何列目にあるか自動で探す
        id_col = get_column_index(sheet, "Discord ID", fallback_index=6)
        cell = sheet.find(str(discord_id), in_column=id_col)
        if cell:
            sheet.delete_rows(cell.row)
    except Exception as e:
        print(f"スプレッドシート行削除エラー: {e}")

def record_channels(role_name: str, channels: list) -> None:
    """作成したチャンネル一覧を、スプレッドシート内の専用タブに記録する
    （募集メッセージ・チャンネルが削除された場合の復元用）。
    channels: discord.TextChannel のリスト。空リストなら何もしない。"""
    if not channels:
        return

    gc = get_gspread_client()
    spreadsheet = gc.open(role_name)

    try:
        ws = spreadsheet.worksheet(CHANNEL_SHEET_TITLE)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=CHANNEL_SHEET_TITLE, rows=len(channels) + 10, cols=len(CHANNEL_HEADERS))
        ws.append_row(CHANNEL_HEADERS)

    rows = [[role_name, ch.name, str(ch.id)] for ch in channels]
    ws.append_rows(rows)


def get_recorded_channels(spreadsheet) -> dict:
    """スプレッドシートの「チャンネル情報」タブから、募集名ごとのチャンネル一覧を読み込む。
    タブが無い場合（この機能の追加前に作られた古いシート）は空辞書を返す。

    戻り値: {role_name: [{"name": チャンネル名, "id": チャンネルID(int|None)}, ...]}
    """
    try:
        ws = spreadsheet.worksheet(CHANNEL_SHEET_TITLE)
    except gspread.exceptions.WorksheetNotFound:
        return {}

    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return {}

    header = all_values[0]

    def idx(name, fallback):
        try:
            return header.index(name)
        except ValueError:
            return fallback

    role_i = idx("募集名", 0)
    name_i = idx("チャンネル名", 1)
    id_i = idx("チャンネルID", 2)
    max_i = max(role_i, name_i, id_i)

    grouped: dict = {}
    for row in all_values[1:]:
        if len(row) <= max_i:
            continue
        role_name = row[role_i].strip()
        ch_name = row[name_i].strip()
        ch_id_str = row[id_i].strip()
        if not role_name or not ch_name:
            continue
        grouped.setdefault(role_name, []).append({
            "name": ch_name,
            "id": int(ch_id_str) if ch_id_str.isdigit() else None
        })
    return grouped


def update_channel_id(spreadsheet, channel_name: str, new_id: int) -> None:
    """再作成等でチャンネルIDが変わった際に、「チャンネル情報」タブの該当行のIDを更新する
    （ベストエフォート。タブが無い・該当行が見つからない場合は何もしない）。
    同名チャンネルが複数行ある場合は最初に見つかった行のみ更新する。"""
    try:
        ws = spreadsheet.worksheet(CHANNEL_SHEET_TITLE)
        name_col = get_column_index(ws, "チャンネル名", fallback_index=2)
        id_col = get_column_index(ws, "チャンネルID", fallback_index=3)

        cell = ws.find(channel_name, in_column=name_col)
        if cell:
            ws.update_cell(cell.row, id_col, str(new_id))
    except Exception as e:
        print(f"[チャンネルID更新] スプレッドシートの更新に失敗しました: {e}")


def delete_spreadsheet(role_name: str):
    """削除処理もGASに依頼する"""
    payload = {
        "action": "delete",
        "roleName": role_name
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(GAS_WEBAPP_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get("status") == "success":
                print(f"GAS経由でスプレッドシートを削除しました: {role_name}")
            else:
                print(f"GAS側で削除エラーが発生しました: {result.get('message')}")
    except Exception as e:
        print(f"GASへの通信エラー(削除): {e}")