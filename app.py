import re
from flask import Flask, request, jsonify
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

def extract_id(maybe_url_or_id: str) -> str:
    # URLなら /d/ の後ろを抜き出す、IDならそのまま返す
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", maybe_url_or_id)
    if match:
        return match.group(1)
    return maybe_url_or_id

def get_latest(spreadsheet_id: str):
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    wk = sh.worksheet("フォームの回答 1")
    data = wk.get_all_values()
    cols = data[0]
    latest = data[-1]
    return dict(zip(cols, latest))

@app.route("/healthdata/latest", methods=["GET"])
def healthdata_latest():
    url = request.args.get("sheet_url", "").strip()
    sid = request.args.get("sheet_id", "").strip()
    if not url and not sid:
        return jsonify({"error": "sheet_urlまたはsheet_idが必要です"}), 400
    # URLがあれば優先、なければID
    identifier = extract_id(url) if url else sid
    try:
        latest = get_latest(identifier)
        return jsonify(latest)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/healthdata/compare", methods=["GET"])
def healthdata_compare():
    url = request.args.get("sheet_url", "").strip()
    sid = request.args.get("sheet_id", "").strip()
    if not url and not sid:
        return jsonify({"error": "sheet_urlまたはsheet_idが必要です"}), 400
    identifier = extract_id(url) if url else sid
    try:
        data = get_latest(identifier)
        # ここで「前日分」も取りに行くロジックを追加すればOK
        # （例：全行取得して[-2]をyesterdayにセット）
        sh = gspread.authorize(Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )).open_by_key(identifier)
        wk = sh.worksheet("フォームの回答 1")
        all_values = wk.get_all_values()
        yesterday = dict(zip(all_values[0], all_values[-2]))
        advice = "前日と比べて異常なし！"  # ここはカスタムロジックで生成してね
        return jsonify({
            "today": data,
            "yesterday": yesterday,
            "advice": advice
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(port=5000)
