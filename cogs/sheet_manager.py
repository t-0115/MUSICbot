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
GAS_WEBAPP_URL = "https://script.google.com/macros/s/AKfycbw9iToFFmGfT2gC44FTXuhqV_AdBDgEeuu0oJnGs6ttvImBqc-Slz_rRfczUpFMKaL_/exec"
FOLDER_URL = "https://drive.google.com/drive/folders/1GrLUGXNaJnEu5yok3FEh1vddXrK-JQGh?usp=drive_link"
BOT_EMAIL = "musicabot@musicabot-492718.iam.gserviceaccount.com"

# ★ 今後項目を増やす時は、ここのリストを変更するだけでOKになります！
DEFAULT_HEADERS = ["日時", "募集名", "期", "名字", "名前", "Discordユーザー名", "Discord ID"]

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

def append_to_sheet(role_name: str, term: str, last_name: str, first_name: str, discord_tag: str, discord_id: int):
    """作成されたスプレッドシートを開いてデータを1行追加する（重複チェック付き）"""
    gc = get_gspread_client()
    
    try:
        sheet = gc.open(role_name).sheet1
        
        # ★ 「Discord ID」という見出しが何列目にあるか自動で探す
        id_col = get_column_index(sheet, "Discord ID", fallback_index=7)
        
        # 見つからなかった場合はエラーにならず None が返ってきます
        existing_cell = sheet.find(str(discord_id), in_column=id_col)
        if existing_cell:
            print(f"重複を検知しました。ID: {discord_id} の追加をスキップします。")
            return  # 既に存在する場合は、ここで処理を強制終了して書き込まない

        # 重複がなければ書き込む
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # ※ もし DEFAULT_HEADERS の順番を変えたら、ここの row の順番も合わせる
        row = [now, role_name, term, last_name, first_name, discord_tag, str(discord_id)]
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
        id_col = get_column_index(sheet, "Discord ID", fallback_index=7)
        cell = sheet.find(str(discord_id), in_column=id_col)
        if cell:
            sheet.delete_rows(cell.row)
    except Exception as e:
        print(f"スプレッドシート行削除エラー: {e}")

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