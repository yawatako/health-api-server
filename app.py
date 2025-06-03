"""Health-Work Data API server

Deployable on Render. Implements a subset of the OpenAPI spec in
`体調管理用スキーマ.yml` and fetches data from Google Sheets.

Environment variables (set in Render dashboard):
------------------------------------------------
GOOGLE_SA_JSON        – JSON string of your Google Cloud service-account key
DEFAULT_HEALTH_TAB    – (optional) default sheet tab for health data, default "Health"
DEFAULT_WORK_TAB      – (optional) default sheet tab for work data, default "Work"

Start command on Render:
    uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from google.oauth2 import service_account
from googleapiclient.discovery import build
from pydantic import BaseModel, Field, RootModel, ConfigDict

# --------------------------------------------------------------------
# Google Sheets helpers
# --------------------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def _get_service():
    """Return an authorized Sheets API service object."""
    creds_json = os.getenv("GOOGLE_SA_JSON")
    if not creds_json:
        raise RuntimeError(
            "GOOGLE_SA_JSON environment variable not set; add service-account JSON in Render Secrets"
        )
    creds_info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _sheet_id_from_url(sheet_url: str) -> Optional[str]:
    """Extract spreadsheetId from a full Google Sheets URL."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9\-_]+)", sheet_url)
    return m.group(1) if m else None


def _resolve_sheet_id(sheet_url: Optional[str], sheet_id: Optional[str]) -> str:
    if sheet_id:
        return sheet_id
    if sheet_url:
        maybe_id = _sheet_id_from_url(sheet_url)
        if maybe_id:
            return maybe_id
    raise HTTPException(status_code=400, detail="sheet_id or sheet_url is required")


def fetch_rows(sheet_id: str, tab_name: str) -> List[Dict[str, str]]:
    """Fetch all rows from the given tab as a list of dicts (keys = header row)."""
    service = _get_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{tab_name}!A1:ZZ")
        .execute()
    )
    values = result.get("values", [])
    if not values:
        return []
    headers = values[0]
    records = [dict(zip(headers, row)) for row in values[1:]]
    return records


# --------------------------------------------------------------------
# Pydantic models (v2 style) -------------------------------------------------
# --------------------------------------------------------------------
class HealthRecord(RootModel[Dict[str, str]]):
    """1 row from the Health sheet tab (header → cell value)."""

    root: Dict[str, str]
    model_config = ConfigDict(extra="allow")


class WorkRecord(RootModel[Dict[str, str]]):
    """1 row from the Work sheet tab (header → cell value)."""

    root: Dict[str, str]
    model_config = ConfigDict(extra="allow")


class CompareResponse(BaseModel):
    today: Dict[str, str]
    yesterday: Dict[str, str]
    advice: str


class DailySummary(BaseModel):
    date: str = Field(..., regex=r"\d{4}-\d{2}-\d{2}")
    health: Dict[str, str]
    work: Optional[Dict[str, str]] = None
    comment: Optional[str] = ""


# --------------------------------------------------------------------
# FastAPI application --------------------------------------------------------
# --------------------------------------------------------------------
app = FastAPI(
    title="Health-Work Data API",
    version="2.2.0",
    description="API endpoints backed by Google Sheets as defined in 体調管理用スキーマ.yml",
)


def _default(env_key: str, fallback: str) -> str:
    return os.getenv(env_key, fallback)


# -------------------------  /healthdata/latest  ----------------------
@app.get("/healthdata/latest", response_model=HealthRecord, tags=["healthdata"])
def get_healthdata_latest(
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(_default("DEFAULT_HEALTH_TAB", "Health")),
):
    sid = _resolve_sheet_id(sheet_url, sheet_id)
    rows = fetch_rows(sid, health_tab)
    if not rows:
        raise HTTPException(status_code=400, detail="No data in sheet")
    return rows[-1]


# -------------------------  /healthdata/compare  ---------------------
@app.get("/healthdata/compare", response_model=CompareResponse, tags=["healthdata"])
def get_healthdata_compare(
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(_default("DEFAULT_HEALTH_TAB", "Health")),
):
    sid = _resolve_sheet_id(sheet_url, sheet_id)
    rows = fetch_rows(sid, health_tab)
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 rows to compare")
    today, yesterday = rows[-1], rows[-2]
    advice = _simple_advice(today, yesterday)
    return CompareResponse(today=today, yesterday=yesterday, advice=advice)


def _simple_advice(today: Dict[str, str], yest: Dict[str, str]) -> str:
    """Very simple comparison of the '今日の気分は？' column (if present)."""
    key = "今日の気分は？"
    if key in today and key in yest:
        try:
            t_val, y_val = float(today[key]), float(yest[key])
        except ValueError:
            t_val, y_val = today[key], yest[key]
        if t_val > y_val:
            return "昨日より気分が良さそうです！引き続き休息を確保してください。"
        elif t_val < y_val:
            return "昨日より落ち込んでいるようです。早めに休憩を取りましょう。"
    return "大きな変化はないようです。バランスを維持してください。"


# -------------------------  /healthdata/period  ----------------------
@app.get("/healthdata/period", response_model=List[HealthRecord], tags=["healthdata"])
def get_healthdata_period(
    start_date: str = Query(..., regex=r"\d{4}-\d{2}-\d{2}"),
    end_date: str = Query(..., regex=r"\d{4}-\d{2}-\d{2}"),
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(_default("DEFAULT_HEALTH_TAB", "Health")),
):
    sid = _resolve_sheet_id(sheet_url, sheet_id)
    rows = fetch_rows(sid, health_tab)
    if not rows:
        raise HTTPException(status_code=400, detail="No data in sheet")

    def _parse(d: str) -> datetime.date:
        return datetime.datetime.strptime(d[:10], "%Y-%m-%d").date()

    s, e = _parse(start_date), _parse(end_date)
    filtered = [
        r for r in rows if (d := _get_date_value(r)) and s <= _parse(d) <= e
    ]
    return filtered


def _get_date_value(row: Dict[str, str]) -> Optional[str]:
    for k in ("date", "日付", "タイムスタンプ", "Timestamp"):
        if k in row:
            return row[k]
    return None


# -----------------------  /healthdata/dailySummary  ------------------
@app.get("/healthdata/dailySummary", response_model=DailySummary, tags=["healthdata"])
def get_daily_summary(
    date: str = Query(..., regex=r"\d{4}-\d{2}-\d{2}"),
    sheet_url: Optional[str] = Query(None),
    sheet_id: Optional[str] = Query(None),
    health_tab: str = Query(_default("DEFAULT_HEALTH_TAB", "Health")),
    work_tab: str = Query(_default("DEFAULT_WORK_TAB", "Work")),
):
    sid = _resolve_sheet_id(sheet_url, sheet_id)
    h_rows = fetch_rows(sid, health_tab)
    w_rows = fetch_rows(sid, work_tab)

    health_row = _find_row_by_date(h_rows, date)
    work_row = _find_row_by_date(w_rows, date)

    if not health_row:
        raise HTTPException(status_code=400, detail="Health data not found for specified date")

    comment = health_row.get("一言メモ", "")
    return DailySummary(date=date, health=health_row, work=work_row, comment=comment)


def _find_row_by_date(rows: List[Dict[str, str]], date_str: str) -> Optional[Dict[str, str]]:
    return next((r for r in rows if _get_date_value(r) == date_str), None)


# --------------------------------------------------------------------
# Local dev entry-point -------------------------------------------------------
# --------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
