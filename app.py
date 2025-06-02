import os
import re
import json
from datetime import datetime
from flask import Flask, request, jsonify
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ───────────────────────────────────────────────────────────────────────────────
# 環境変数から Google サービスアカウントの認証情報を読み込む
# ───────────────────────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]
SERVICE_ACCOUNT_INFO = json.loads(os.environ["GOOGLE_CREDENTIALS"])


def extract_id(maybe_url_or_id: str) -> str:
    """
    スプレッドシートの URL または ID を受け取り、
    URL の場合は正規表現で ID 部分だけを抜き出す。ID だけならそのまま返す。
    """
    match = re.search(r"/d/([a-zA-Z0-9\-_]+)", maybe_url_or_id)
    if match:
        return match.group(1)
    return maybe_url_or_id


def authorize_gspread():
    """
    サービスアカウント情報を元に gspread クライアントを返す。
    """
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
    return gspread.authorize(creds)


def get_latest_from_health_tab(spreadsheet_id: str, health_tab: str = "体調管理") -> dict:
    """
    スプレッドシート ID とタブ名を指定して「体調管理」タブから最新の行を辞書形式で返す。
    - 1 行目・2 行目をヘッダーと想定し、3 行目以降をデータ行とする。
    - A 列（タイムスタンプ列）の値が空でない行のみ対象とし、
      日付（YYYY/MM/DD）を比較して一番新しい行を探す。
    """
    gc = authorize_gspread()
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(health_tab)

    # 全行を取得。1・2 行目はヘッダー、3 行目以降をデータとして扱う
    all_rows = ws.get_all_values()
    data_rows = [row for row in all_rows[2:] if len(row) >= 1 and row[0].strip()]
    if not data_rows:
        raise ValueError("体調管理タブにデータ行が見つかりませんでした。")

    # タイムスタンプ文字列（例: "2025/06/02 09:17:00" の先頭 "2025/06/02" 部分）を日付に変換
    def parse_date(cell_value: str):
        date_part = cell_value.split()[0]  # "YYYY/MM/DD" 部分を取り出す
        return datetime.strptime(date_part, "%Y/%m/%d")

    valid_date_rows = []
    for row in data_rows:
        try:
            dt_val = parse_date(row[0])
            valid_date_rows.append((row, dt_val))
        except ValueError:
            # フォーマットと異なる行はスキップ
            continue

    if not valid_date_rows:
        raise ValueError("体調管理タブ内に有効な日付フォーマット（YYYY/MM/DD）が見つかりませんでした。")

    # 日付が最新の行を選択
    latest_row, _ = max(valid_date_rows, key=lambda x: x[1])

    # 2 行目をヘッダーと見做し、列名と値を辞書化
    headers = ws.row_values(2)
    result = {}
    for idx, col_name in enumerate(headers):
        result[col_name] = latest_row[idx] if idx < len(latest_row) else ""

    return result


def get_latest_from_work_tab(spreadsheet_id: str, work_tab: str = "業務記録") -> dict:
    """
    スプレッドシート ID とタブ名を指定して「業務記録」タブから最新の行を辞書形式で返す。
    - 1 行目・2 行目をヘッダーと想定し、3 行目以降をデータ行とする。
    - A 列（タイムスタンプ列）が空でない行を対象とし、
      リストの最後の行を「最新」とみなす。
    """
    gc = authorize_gspread()
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(work_tab)

    all_rows = ws.get_all_values()
    data_rows = [row for row in all_rows[2:] if len(row) >= 1 and row[0].strip()]
    if not data_rows:
        raise ValueError("業務記録タブにデータ行が見つかりませんでした。")

    # 最終行を最新とみなす
    latest_row = data_rows[-1]

    headers = ws.row_values(2)
    result = {}
    for idx, col_name in enumerate(headers):
        result[col_name] = latest_row[idx] if idx < len(latest_row) else ""

    return result


@app.route("/healthdata/latest", methods=["GET"])
def healthdata_latest():
    """
    クエリパラメータ:
      - sheet_url   : スプレッドシートのフル URL (省略可、ID を自動抽出)
      - sheet_id    : スプレッドシートのファイル ID (省略可、URL の代わりに指定)
      - health_tab  : 体調データがあるタブ名 (省略時は "体調管理")

    成功時(200) レスポンス例:
      {
        "タイムスタンプ": "2025/06/02 09:17:00",
        "何時間寝た？": "7h",
        "よく眠れた？": "あんまり良くない",
        ...
      }
    エラー時(400) :
      { "error": "sheet_url または sheet_id が必要です" }
    エラー時(500) :
      { "error_type": "...", "error_msg": "詳細メッセージ" }
    """
    sheet_url = request.args.get("sheet_url", "").strip()
    sheet_id = request.args.get("sheet_id", "").strip()
    health_tab = request.args.get("health_tab", "").strip() or "体調管理"

    if not (sheet_url or sheet_id):
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    spreadsheet_id = extract_id(sheet_url) if sheet_url else sheet_id

    try:
        latest_data = get_latest_from_health_tab(spreadsheet_id, health_tab=health_tab)
        return jsonify(latest_data), 200
    except Exception as e:
        return jsonify({
            "error_type": type(e).__name__,
            "error_msg": str(e)
        }), 500


@app.route("/healthdata/compare", methods=["GET"])
def healthdata_compare():
    """
    クエリパラメータ:
      - sheet_url   : スプレッドシートのフル URL
      - sheet_id    : スプレッドシートのファイル ID
      - health_tab  : 体調データがあるタブ名 (省略時は "体調管理")

    成功時(200) レスポンス例:
      {
        "today": { ... },     # 当日の体調データ (辞書)
        "yesterday": { ... }, # 前日の体調データ (辞書)
        "advice": "..."       # 固定サンプルのアドバイス文
      }
    エラー時(400) :
      { "error": "最新行と比較対象行が見つかりません" }
    エラー時(500) :
      { "error_type": "...", "error_msg": "詳細メッセージ" }
    """
    sheet_url = request.args.get("sheet_url", "").strip()
    sheet_id = request.args.get("sheet_id", "").strip()
    health_tab = request.args.get("health_tab", "").strip() or "体調管理"

    if not (sheet_url or sheet_id):
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    spreadsheet_id = extract_id(sheet_url) if sheet_url else sheet_id

    try:
        gc = authorize_gspread()
        sh = gc.open_by_key(spreadsheet_id)
        ws = sh.worksheet(health_tab)
        all_rows = ws.get_all_values()
        data_rows = [row for row in all_rows[2:] if len(row) >= 1 and row[0].strip()]
        if len(data_rows) < 2:
            return jsonify({"error": "最新行と比較対象行が見つかりません"}), 400

        # 最新行とその直前の行を辞書化
        headers = ws.row_values(2)
        today_row = data_rows[-1]
        yesterday_row = data_rows[-2]

        today_dict = {headers[i]: today_row[i] if i < len(today_row) else "" for i in range(len(headers))}
        yesterday_dict = {headers[i]: yesterday_row[i] if i < len(yesterday_row) else "" for i in range(len(headers))}

        advice = "前日と比べて大きな変化はありません。体調維持を心がけてください。"

        return jsonify({
            "today": today_dict,
            "yesterday": yesterday_dict,
            "advice": advice
        }), 200

    except Exception as e:
        return jsonify({
            "error_type": type(e).__name__,
            "error_msg": str(e)
        }), 500


@app.route("/daily/summary", methods=["GET"])
def daily_summary():
    """
    クエリパラメータ:
      - sheet_url   : スプレッドシートのフル URL
      - sheet_id    : スプレッドシートのファイル ID
      - date        : 対象日 (YYYY-MM-DD)
      - health_tab  : 体調データタブ名 (省略可、デフォルト "体調管理")
      - work_tab    : 業務記録タブ名 (省略可、デフォルト "業務記録")

    成功時(200) レスポンス例:
      {
        "date": "2025-06-02",
        "health": { ... },   # 当日の体調データ
        "work": { ... },     # 当日の業務記録
        "comment": "..."     # まとめコメント
      }
    エラー時(400) :
      { "error": "sheet_url または sheet_id が必要です" }
      または { "error": "date が必要です (YYYY-MM-DD)" }
      または { "error": "date は YYYY-MM-DD 形式で指定してください" }
    エラー時(404) :
      { "error": "2025-06-02 の体調データが見つかりません" }
    エラー時(500) :
      { "error_type": "...", "error_msg": "詳細メッセージ" }
    """
    sheet_url = request.args.get("sheet_url", "").strip()
    sheet_id = request.args.get("sheet_id", "").strip()
    date_str = request.args.get("date", "").strip()
    health_tab = request.args.get("health_tab", "").strip() or "体調管理"
    work_tab = request.args.get("work_tab", "").strip() or "業務記録"

    if not date_str:
        return jsonify({"error": "date が必要です (YYYY-MM-DD)"}), 400
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "date は YYYY-MM-DD 形式で指定してください"}), 400

    if not (sheet_url or sheet_id):
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    spreadsheet_id = extract_id(sheet_url) if sheet_url else sheet_id

    try:
        gc = authorize_gspread()
        sh = gc.open_by_key(spreadsheet_id)

        # ―― 体調タブから該当日の行を探す
        ws_health = sh.worksheet(health_tab)
        all_health_rows = ws_health.get_all_values()
        health_rows = [row for row in all_health_rows[2:] if row[0].startswith(date_str)]
        if not health_rows:
            return jsonify({"error": f"{date_str} の体調データが見つかりません"}), 404

        health_headers = ws_health.row_values(2)
        latest_health_row = health_rows[-1]
        health_dict = {health_headers[i]: latest_health_row[i] if i < len(latest_health_row) else ""
                       for i in range(len(health_headers))}

        # ―― 業務記録タブから該当日の行を探す
        ws_work = sh.worksheet(work_tab)
        all_work_rows = ws_work.get_all_values()
        work_rows = [row for row in all_work_rows[2:] if row[0].startswith(date_str)]
        if work_rows:
            work_headers = ws_work.row_values(2)
            latest_work_row = work_rows[-1]
            work_dict = {work_headers[i]: latest_work_row[i] if i < len(latest_work_row) else ""
                         for i in range(len(work_headers))}
        else:
            work_dict = {}

        # ―― 簡易コメント生成
        comment = (
            f"{date_str} のまとめ: "
            f"睡眠 {health_dict.get('何時間寝た？','-')}、"
            f"気分 {health_dict.get('今日の気分は？','-')}。"
        )
        if work_dict:
            comment += f" 午前: {work_dict.get('10時以降、何した？','-')}、午後: {work_dict.get('午後何した？','-')}。"

        return jsonify({
            "date": date_str,
            "health": health_dict,
            "work": work_dict,
            "comment": comment
        }), 200

    except Exception as e:
        return jsonify({
            "error_type": type(e).__name__,
            "error_msg": str(e)
        }), 500


if __name__ == "__main__":
    # ローカル検証時は debug=True にするとエラー詳細が返る
    app.run(host="0.0.0.0", port=5000, debug=True)
