import secrets
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.config import DASHBOARD_PASSWORD, DASHBOARD_USERNAME

router = APIRouter()
templates = Jinja2Templates(directory="app/dashboard/templates")
security = HTTPBasic()

DB_PATH = "query_logs.db"


def _require_dashboard_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    if not DASHBOARD_USERNAME or not DASHBOARD_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard authentication is not configured.",
        )

    username_ok = secrets.compare_digest(credentials.username, DASHBOARD_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/admin/dashboard", response_class=HTMLResponse, dependencies=[Depends(_require_dashboard_auth)])
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
