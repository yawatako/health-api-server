import os
import json
import logging
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException, Query
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI(
    title="Health-Work Data API",
    version="0.1.0",
    description="API wrapper over Google Sheets defined by OpenAPI schema."
)

logger = logging.getLogger("uvicorn.error")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def get_service():
    service_account_info = os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO")
    if not service_account_info:
        logger.error("Environment variable GOOGLE_SERVICE_ACCOUNT_INFO not set.")
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_INFO env var not set")

    try:
        info = json.loads(service_account_info)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in GOOGLE_SERVICE_ACCOUNT_INFO: %s", e)
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_INFO must be JSON")

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)

def fetch_values(sheet_id: str, range_name: str) -> List[List[str]]:
    service = get_service()
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=range_name)
            .execute()
        )
    except Exception as exc:
        logger.exception("Google Sheets API error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result.get("values", [])

def records_to_dicts(values: List[List[str]]) -> List[Dict[str, str]]:
    if not values:
        return []
    header = values[0]
    records: List[Dict[str, str]] = []
    for row in values[1:]:
        # right‑pad short rows
        padded = row + [None] * (len(header) - len(row))
        records.append(dict(zip(header, padded)))
    return records

def extract_sheet_id(url_or_id: Optional[str]) -> Optional[str]:
    if not url_or_id:
        return None
    if url_or_id.startswith("http"):
        import re

        match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
        if match:
            return match.group(1)
    return url_or_id

def resolve_sheet_id(sheet_url: Optional[str], sheet_id: Optional[str]) -> str:
    sid = extract_sheet_id(sheet_url) or extract_sheet_id(sheet_id) or os.getenv("DEFAULT_SHEET_ID")
    if not sid:
        raise HTTPException(status_code=400, detail="sheet_id or sheet_url must be specified")
    return sid


# -------- API endpoints --------
@app.get("/healthdata/latest", summary="最新の体調データを取得する")
def get_healthdata_latest(
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(os.getenv("HEALTH_TAB_DEFAULT", "体調")),
):
    sid = resolve_sheet_id(sheet_url, sheet_id)
    range_name = f"{health_tab}!A1:ZZ"
    records = records_to_dicts(fetch_values(sid, range_name))
    if not records:
        raise HTTPException(status_code=400, detail="No data")
    return records[-1]

@app.get("/healthdata/compare", summary="最新と前日の体調を比較する")
def get_healthdata_compare(
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(os.getenv("HEALTH_TAB_DEFAULT", "体調")),
):
    sid = resolve_sheet_id(sheet_url, sheet_id)
    range_name = f"{health_tab}!A1:ZZ"
    records = records_to_dicts(fetch_values(sid, range_name))
    if len(records) < 2:
        raise HTTPException(status_code=400, detail="Not enough rows")
    latest, prev = records[-1], records[-2]

    diff: Dict[str, Optional[float]] = {}
    for k in latest.keys():
        try:
            diff[k] = float(latest[k]) - float(prev.get(k, 0))
        except (TypeError, ValueError):
            diff[k] = None

    return {"latest": latest, "previous": prev, "diff": diff}

@app.get("/healthdata/history", summary="指定日範囲の体調データを取得する")
def get_healthdata_history(
    start_date: str = Query(..., description="開始日 (YYYY-MM-DD)"),
    end_date: str = Query(..., description="終了日 (YYYY-MM-DD)"),
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(os.getenv("HEALTH_TAB_DEFAULT", "体調")),
):
    import datetime as dt

    sid = resolve_sheet_id(sheet_url, sheet_id)
    range_name = f"{health_tab}!A1:ZZ"
    records = records_to_dicts(fetch_values(sid, range_name))

    try:
        s = dt.date.fromisoformat(start_date)
        e = dt.date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format; use YYYY-MM-DD")

    result = [
        r for r in records
        if "日付" in r and r["日付"] and s <= dt.date.fromisoformat(r["日付"]) <= e
    ]
    return result

@app.get("/daily/summary", summary="指定日の体調・業務のまとめを返す")
def get_daily_summary(
    date: str = Query(..., description="対象日 (YYYY-MM-DD)"),
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(os.getenv("HEALTH_TAB_DEFAULT", "体調")),
    work_tab: str = Query(os.getenv("WORK_TAB_DEFAULT", "業務")),
):
    import datetime as dt

    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format; use YYYY-MM-DD")

    sid = resolve_sheet_id(sheet_url, sheet_id)

    # Health part
    health_records = records_to_dicts(fetch_values(sid, f"{health_tab}!A1:ZZ"))
    health = next((r for r in health_records if r.get("日付") == date), None)

    # Work part
    work_records = records_to_dicts(fetch_values(sid, f"{work_tab}!A1:ZZ"))
    work = [r for r in work_records if r.get("日付") == date]

    return {"date": date, "health": health, "work": work}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
