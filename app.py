import os
import re
import json
from flask import Flask, request, jsonify
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ─────────────────────────────────────────────────────────
#  Google Sheets の認証情報を環境変数から読み込む
#  (GOOGLE_CREDENTIALS にサービスアカウントキーの JSON 全体が文字列で入っている想定)
# ─────────────────────────────────────────────────────────
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]
SERVICE_ACCOUNT_INFO = json.loads(os.environ["GOOGLE_CREDENTIALS"])

def extract_id(maybe_url_or_id: str) -> str:
    """
    引数にスプレッドシートの URL もしくは ID を渡すと、
    URL を正規表現で検出して ID 部分だけを返す。ID 文字列だけが来た場合はそのまま返す。
    """
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", maybe_url_or_id)
    if match:
        return match.group(1)
    return maybe_url_or_id

def get_latest_from_health_tab(spreadsheet_id: str, health_tab: str = "体調管理"):
    """
    引数にスプレッドシート ID とタブ名を渡すと、そのタブ（体調管理）の
    「ヘッダを飛ばして、タイムスタンプが入っている列をキーに最新行を返す」メソッド。
    ヘッダは「2行目」を想定し、3行目以降がデータ行。タイムスタンプ列（A列）が空白でない行のみを対象にする。
    """
    # まず認証してシートを開く
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)

    # 体調管理タブを開く（タブ名が間違っていると WorksheetNotFound が出る）
    ws = sh.worksheet(health_tab)

    # A列（タイムスタンプ含むので必ず空白でない）を想定して get_all_values() する
    all_rows = ws.get_all_values()      # 2次元配列：全行・全列
    # 「２行目がヘッダ」と想定 → 3行目以降が実データ
    data_rows = [row for row in all_rows[2:] if len(row) >= 1 and row[0].strip()]

    if not data_rows:
        # データ行が見つからない場合は 400 エラーにする
        raise ValueError("体調管理タブにデータ行が見つかりません")

    # 日付文字列だけを切り出して "%Y/%m/%d" としてパースし、最新日を探す
    def parse_date(s: str):
        # 例: "2025/06/02 09:17:00" → "2025/06/02"
        date_part = s.split()[0]
        # もし「2025/6/2」のように月や日がゼロパディングされていないケースがある場合は
        # ここで文字列を整形するか try/except で別フォーマットを試す方法も検討
        return (None
                if not re.match(r"^\d{4}/\d{1,2}/\d{1,2}$", date_part)
                else
                __import__("datetime").datetime.strptime(date_part, "%Y/%m/%d")
               )

    # parse_date が None になった行は除外して最新日を探す（ValueError を避けるための工夫）
    valid_date_rows = [(row, parse_date(row[0])) for row in data_rows]
    valid_date_rows = [ (row, dt) for (row, dt) in valid_date_rows if dt ]
    if not valid_date_rows:
        raise ValueError("日付フォーマットが %Y/%m/%d としてパースできる行がありません")

    # 最新行をキーで取得
    latest_row, _ = max(valid_date_rows, key=lambda x: x[1])

    # ヘッダは 2行目（index=1）とし、最新行と組み合わせて辞書を作成
    headers = ws.row_values(2)
    # カラム数が不揃いの場合にも安全に扱うため、zip せずに組み立てる例：
    result = {}
    for idx, col_name in enumerate(headers):
        if idx < len(latest_row):
            result[col_name] = latest_row[idx]
        else:
            result[col_name] = ""  # データがない列は空文字にする

    return result

@app.route("/healthdata/latest", methods=["GET"])
def healthdata_latest():
    """
    GET パラメータ
      - sheet_url: スプレッドシートのフル URL (例: https://docs.google.com/spreadsheets/d/xxx/edit#gid=0)
      - sheet_id:  スプレッドシートのファイル ID (URL ではなく ID 文字列 例: 1AcA8Y…)
      - health_tab: 「体調管理」以外のタブ名を使いたいときに指定（省略可。省略時は "体調管理"）
    """
    # クエリを受け取る
    sheet_url  = request.args.get("sheet_url", "").strip()
    sheet_id   = request.args.get("sheet_id", "").strip()
    health_tab = request.args.get("health_tab", "").strip() or "体調管理"

    # sheet_url または sheet_id のどちらか必須
    if not (sheet_url or sheet_id):
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    # URL → ID 抽出
    identifier = extract_id(sheet_url) if sheet_url else sheet_id

    try:
        # 最新データを取得し、JSON で返す
        latest = get_latest_from_health_tab(identifier, health_tab=health_tab)
        return jsonify(latest), 200

    except Exception as e:
        # ここで例外の種類とメッセージをそのままクライアントに返す (開発時のみ推奨)
        return jsonify({
            "error_type": type(e).__name__,
            "error_msg":  str(e)
        }), 500

if __name__ == "__main__":
    # debug=True にするとローカルで詳細な例外画面になる
    app.run(host="0.0.0.0", port=5000, debug=True)
