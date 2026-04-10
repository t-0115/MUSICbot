import gspread
import datetime
from google.oauth2.service_account import Credentials
import urllib.request
import json
import re

# ==========================================
# 設定部分
# ==========================================
GAS_WEBAPP_URL = "https://script.google.com/macros/s/AKfycbwWqlovqrr5AkSfVAD8CVXU1tzDEAfoYhhCjvbghRrh9lURU40P6oQyqmC-qi8LNz_X/exec"
FOLDER_URL = "https://drive.google.com/drive/folders/1GrLUGXNaJnEu5yok3FEh1vddXrK-JQGh?usp=drive_link"
BOT_EMAIL = "musicabot@musicabot-492718.iam.gserviceaccount.com"

# ==========================================
# 共通関数
# ==========================================
def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file('credentials.json', scopes=scopes)
    return gspread.authorize(credentials)


# ==========================================
# スプレッドシート操作のメインロジック
# ==========================================
def create_sheet_via_gas(role_name: str) -> str | None:
    """GASを経由して新しいスプレッドシートを作成する"""
    match = re.search(r'folders/([a-zA-Z0-9-_]+)', FOLDER_URL)
    folder_id = match.group(1) if match else ""

    payload = {
        "action": "create",  # ★ 追加: 作成アクションであることをGASに伝える
        "roleName": role_name,
        "folderId": folder_id,
        "botEmail": BOT_EMAIL
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

def append_to_sheet(role_name: str, term: str, user_name: str, discord_tag: str, discord_id: int):
    """作成済みのスプレッドシートを開き、参加者情報を1行追加する"""
    gc = get_gspread_client()
    try:
        sheet = gc.open(role_name).sheet1
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row = [now, role_name, term, user_name, discord_tag, str(discord_id)]
        sheet.append_row(row)
    except Exception as e:
        print(f"スプレッドシート行追加エラー: {e}")
        raise e

def remove_from_sheet(role_name: str, discord_id: int):
    """Discord IDを元に、スプレッドシートから該当ユーザーの行を削除する"""
    gc = get_gspread_client()
    try:
        sheet = gc.open(role_name).sheet1
        cell = sheet.find(str(discord_id), in_column=6)
        if cell:
            sheet.delete_rows(cell.row)
    except gspread.exceptions.CellNotFound:
        pass
    except Exception as e:
        print(f"スプレッドシート行削除エラー: {e}")

def delete_spreadsheet(role_name: str):
    """★ 修正: 削除処理もGASに依頼する"""
    payload = {
        "action": "delete", # 削除アクション
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