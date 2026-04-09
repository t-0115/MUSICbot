import gspread
import datetime
from google.oauth2.service_account import Credentials

# ▼コピーしたあなたのスプレッドシートIDをここに貼り付けます
MASTER_SHEET_ID = "1CtR3snvb7zcWQYfYj1pXQv-05bTVZmEXgTpMOkP9rhM"

def get_sheet(role_name):
    """指定されたロール名（タブ名）のシートを取得する"""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file('credentials.json', scopes=scopes)
    gc = gspread.authorize(credentials)
    
    sh = gc.open_by_key(MASTER_SHEET_ID)
    return sh.worksheet(role_name)

def create_new_worksheet_in_master(role_name):
    """マスター用スプレッドシートの中に、新しいタブ（シート）を作成する"""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file('credentials.json', scopes=scopes)
    gc = gspread.authorize(credentials)
    
    try:
        sh = gc.open_by_key(MASTER_SHEET_ID)
        # ロール名と同じ名前の新しいタブを作成
        worksheet = sh.add_worksheet(title=role_name, rows=1000, cols=10)
        
        # 1行目にヘッダーを書き込む
        headers = ["日時", "募集名", "期", "氏名", "Discordユーザー名", "Discord ID"]
        worksheet.append_row(headers)
        return True
    except Exception as e:
        # 既に同じ名前のタブがある場合などはここに来ます
        print(f"タブ作成エラー: {e}")
        return False

def append_to_sheet(role_name, term, user_name, discord_tag, discord_id):
    """データを1行追加する処理"""
    sheet = get_sheet(role_name) # 該当するタブを取得
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    row = [now, role_name, term, user_name, discord_tag, str(discord_id)]
    sheet.append_row(row)

def delete_worksheet(role_name):
    """募集が削除されたとき、該当するタブを削除する"""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file('credentials.json', scopes=scopes)
    gc = gspread.authorize(credentials)
    
    try:
        sh = gc.open_by_key(MASTER_SHEET_ID)
        worksheet = sh.worksheet(role_name)
        sh.del_worksheet(worksheet)
    except Exception as e:
        print(f"タブ削除エラー: {e}")