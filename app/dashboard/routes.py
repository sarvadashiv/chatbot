import sqlite3
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/dashboard/templates")

DB_PATH = "query_logs.db"

@router.get("/admin/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT query, intent, status, created_at
        FROM query_logs
        ORDER BY created_at DESC
        LIMIT 100
    """)

    rows = c.fetchall()
    conn.close()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "rows": rows
        }
    )