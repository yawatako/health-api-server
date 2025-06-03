"""Health-Work Data API

This Flask application exposes endpoints defined in *体調管理用スキーマ.yml*
(OpenAPI 3.1).  
Google Sheets is used as the storage backend.

Endpoints
---------

GET /healthdata/latest
    今日（シート最終行）の体調データを返します。

GET /healthdata/compare
    今日と昨日の体調データを比較して返します。

GET /healthdata/history
    指定日付範囲（start_date〜end_date）の体調データを配列で返します。

GET /daily/summary
    1 日分の体調＋業務データと簡易コメントを返します。

Environment variables
---------------------

GOOGLE_CREDENTIALS : Google サービスアカウント JSON 文字列

"""

from __future__ import annotations

import os
import re
import json
from datetime import datetime, date
from typing import List, Dict, Any

from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

###############################################################################
# Flask
###############################################################################

app = Flask(__name__)
CORS(app)  # CORS を許可（必要に応じて設定を絞る）

###############################################################################
# Google Sheets 認証
###############################################################################

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
SERVICE_ACCOUNT_INFO = json.loads(os.environ["GOOGLE_CREDENTIALS"])


def authorize_gspread() -> gspread.Client:
    """Return an authorised :class:`gspread.Client`."""
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
    return gspread.authorize(creds)


###############################################################################
# Utility
###############################################################################


def extract_id(maybe_url_or_id: str) -> str:
    """SpreadSheet の URL または ID を受け取り ID を返す。"""
    match = re.search(r"/d/(\w[\w\-]+)", maybe_url_or_id)
    return match.group(1) if match else maybe_url_or_id


def parse_ts_to_date(ts_cell: str) -> date:
    """タイムスタンプ文字列 YYYY/MM/DD hh:mm:ss → date 型へ。

    先頭の日付部分だけを使う。フォーマットが不正なら :class:`ValueError`。
    """
    date_part = ts_cell.split()[0]  # "YYYY/MM/DD"
    return datetime.strptime(date_part, "%Y/%m/%d").date()


def _sheet_rows(
    worksheet: gspread.Worksheet,
    header_rows: int = 2,
    require_a_not_empty: bool = True,
) -> List[List[str]]:
    """Return data rows (after headers)."""
    rows = worksheet.get_all_values()
    data = rows[header_rows:]
    if require_a_not_empty:
        data = [r for r in data if r and r[0].strip()]
    return data


###############################################################################
# Core query helpers
###############################################################################


def get_all_health_records(
    spreadsheet_id: str, tab_name: str = "体調管理"
) -> List[Dict[str, Any]]:
    """Return **all** health records in the sheet (as list of dicts)."""
    gc = authorize_gspread()
    ws = gc.open_by_key(spreadsheet_id).worksheet(tab_name)

    headers = ws.row_values(2)
    data_rows = _sheet_rows(ws)

    records: List[Dict[str, Any]] = []
    for row in data_rows:
        record = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
        records.append(record)
    return records


def get_all_work_records(
    spreadsheet_id: str, tab_name: str = "業務記録"
) -> List[Dict[str, Any]]:
    gc = authorize_gspread()
    ws = gc.open_by_key(spreadsheet_id).worksheet(tab_name)

    headers = ws.row_values(2)
    data_rows = _sheet_rows(ws)

    records: List[Dict[str, Any]] = []
    for row in data_rows:
        record = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
        records.append(record)
    return records


def latest_record(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        raise ValueError("No records found")
    return records[-1]


###############################################################################
# API Endpoints
###############################################################################


def _get_spreadsheet_id() -> str | None:
    """Helper to extract sheet_id from query params.

    Returns
    -------
    str | None
    """
    sheet_url = request.args.get("sheet_url", "").strip()
    sheet_id = request.args.get("sheet_id", "").strip()
    if not (sheet_url or sheet_id):
        return None
    return extract_id(sheet_url) if sheet_url else sheet_id


@app.route("/healthdata/latest", methods=["GET"])
def healthdata_latest() -> tuple[Any, int]:
    spreadsheet_id = _get_spreadsheet_id()
    if spreadsheet_id is None:
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    health_tab = request.args.get("health_tab", "体調管理").strip() or "体調管理"

    try:
        records = get_all_health_records(spreadsheet_id, health_tab)
        latest = latest_record(records)
        return jsonify(latest), 200
    except Exception as e:  # noqa: BLE001
        return (
            jsonify(
                {
                    "error_type": type(e).__name__,
                    "error_msg": str(e),
                }
            ),
            500,
        )


@app.route("/healthdata/compare", methods=["GET"])
def healthdata_compare() -> tuple[Any, int]:
    spreadsheet_id = _get_spreadsheet_id()
    if spreadsheet_id is None:
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    health_tab = request.args.get("health_tab", "体調管理").strip() or "体調管理"

    try:
        records = get_all_health_records(spreadsheet_id, health_tab)
        if len(records) < 2:
            return jsonify({"error": "最新行と比較対象行が見つかりません"}), 400

        today_dict = records[-1]
        yesterday_dict = records[-2]

        advice = "前日と比べて大きな変化はありません。体調維持を心がけてください。"

        return jsonify(
            {
                "today": today_dict,
                "yesterday": yesterday_dict,
                "advice": advice,
            }
        ), 200

    except Exception as e:  # noqa: BLE001
        return (
            jsonify(
                {
                    "error_type": type(e).__name__,
                    "error_msg": str(e),
                }
            ),
            500,
        )


@app.route("/healthdata/history", methods=["GET"])
def healthdata_history() -> tuple[Any, int]:
    """Return records in [start_date, end_date] inclusive."""
    spreadsheet_id = _get_spreadsheet_id()
    if spreadsheet_id is None:
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    health_tab = request.args.get("health_tab", "体調管理").strip() or "体調管理"
    start = request.args.get("start_date", "").strip()
    end = request.args.get("end_date", "").strip()

    if not (start and end):
        return jsonify({"error": "start_date と end_date が必要です (YYYY-MM-DD)"}), 400

    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "日付は YYYY-MM-DD 形式で指定してください"}), 400

    if start_date > end_date:
        return jsonify({"error": "start_date は end_date より前である必要があります"}), 400

    try:
        records = get_all_health_records(spreadsheet_id, health_tab)

        # filter by date range
        filtered = []
        for rec in records:
            try:
                rec_date = parse_ts_to_date(rec["タイムスタンプ"])
            except Exception:
                # skip malformed row
                continue
            if start_date <= rec_date <= end_date:
                filtered.append(rec)

        # OpenAPI の項では配列で返却
        return jsonify(filtered), 200

    except Exception as e:  # noqa: BLE001
        return (
            jsonify(
                {
                    "error_type": type(e).__name__,
                    "error_msg": str(e),
                }
            ),
            500,
        )


@app.route("/daily/summary", methods=["GET"])
def daily_summary() -> tuple[Any, int]:
    spreadsheet_id = _get_spreadsheet_id()
    if spreadsheet_id is None:
        return jsonify({"error": "sheet_url または sheet_id が必要です"}), 400

    date_str = request.args.get("date", "").strip()
    if not date_str:
        return jsonify({"error": "date が必要です (YYYY-MM-DD)"}), 400
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "date は YYYY-MM-DD 形式で指定してください"}), 400

    health_tab = request.args.get("health_tab", "体調管理").strip() or "体調管理"
    work_tab = request.args.get("work_tab", "業務記録").strip() or "業務記録"

    try:
        health_records = get_all_health_records(spreadsheet_id, health_tab)
        health_target = next(
            (
                r
                for r in reversed(health_records)
                if parse_ts_to_date(r["タイムスタンプ"]) == target_date
            ),
            None,
        )
        if not health_target:
            return (
                jsonify({"error": f"{date_str} の体調データが見つかりません"}),
                404,
            )

        work_records = get_all_work_records(spreadsheet_id, work_tab)
        work_target = next(
            (
                r
                for r in reversed(work_records)
                if parse_ts_to_date(r["タイムスタンプ"]) == target_date
            ),
            None,
        ) or {}

        comment_parts = [
            f"{date_str} のまとめ:",
            f"睡眠 {health_target.get('何時間寝た？', '-')},",
