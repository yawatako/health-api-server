import gspread
from google.oauth2.service_account import Credentials

# スプレッドシートのID（URLの「/d/」の後ろの部分！）
SPREADSHEET_ID = "1AcA8YIpMg1D_Sj5FfyWUR2ZxRbUIDLDpxkPZ2Ifjtbc"

# サービスアカウントJSONのパス
SERVICE_ACCOUNT_FILE = "service_account.json"

# スコープの指定（Sheets API & Drive API）
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

# 認証
creds = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

# gspreadクライアント生成
gc = gspread.authorize(creds)

# スプレッドシートを開く
sh = gc.open_by_key(SPREADSHEET_ID)

# シート名を指定（例：「フォームの回答 1」）
worksheet = sh.worksheet("フォームの回答 1")

# 全データ取得
all_data = worksheet.get_all_values()

# 最新のデータ（2行目以降）を取得（タイトル行はall_data[0]）
latest = all_data[-1]
columns = all_data[0]

# 辞書型で表示
latest_dict = dict(zip(columns, latest))
print(latest_dict)
