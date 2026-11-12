"""Le site web : pages HTML + API du chatbot.

Lancer en local :  uv run uvicorn foot.web.app:app --reload
Puis ouvrir http://localhost:8000
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from foot import queries
from foot.chatbot import Chatbot
from foot.db import connect
from foot.llm import LLMClient

log = logging.getLogger("liga")
HERE = Path(__file__).parent
DB_PATH = Path(os.environ.get("DB_PATH", "data/foot.db"))
MADRID = ZoneInfo("Europe/Madrid")

POSITIONS = {"Goalkeeper": "Gardien", "Defence": "Défenseur", "Midfield": "Milieu",
             "Offence": "Attaquant", "Centre-Back": "Défenseur central",
             "Left-Back": "Latéral gauche", "Right-Back": "Latéral droit",
             "Defensive Midfield": "Milieu défensif", "Central Midfield": "Milieu central",
             "Attacking Midfield": "Milieu offensif", "Left Winger": "Ailier gauche",
             "Right Winger": "Ailier droit", "Centre-Forward": "Avant-centre"}
STATUSES = {"FINISHED": "Terminé", "AWARDED": "Terminé", "IN_PLAY": "En direct",
            "PAUSED": "Mi-temps", "TIMED": "À venir", "SCHEDULED": "À venir",
            "POSTPONED": "Reporté", "SUSPENDED": "Suspendu", "CANCELLED": "Annulé"}
DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.",
          "nov.", "déc."]
LABEL_CLASS = {"✅": "officiel", "🟢": "probable", "🟡": "rumeur", "🔴": "intox"}


class AppState:
    """Ce qui est partagé entre toutes les visites (chargé une seule fois)."""

    llm: LLMClient | None = None
    embedder = None
    news_store = None
    collector = None
    lock = threading.Lock()


state = AppState()


def reload_news_index() -> None:
    """(Re)construit l'index de recherche des actus. Appelé après chaque collecte."""
    if state.embedder is None:
        return
    from foot.news_index import build_news_index

    conn = connect(DB_PATH)
    try:
        store = build_news_index(conn, state.embedder)
        with state.lock:
            state.news_store = store
        log.info("Index des actus : %d articles", len(store.chunks))
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    connect(DB_PATH).close()  # crée la base et le schéma si besoin
    if os.environ.get("LLM_API_KEY"):
        state.llm = LLMClient()
    if os.environ.get("ENABLE_SEMANTIC_SEARCH", "1") == "1":
        from rag.embeddings import FastEmbedEmbedder

        state.embedder = FastEmbedEmbedder()
        threading.Thread(target=reload_news_index, daemon=True).start()
    if os.environ.get("ENABLE_COLLECTOR", "0") == "1":
        from foot.scheduler import Collector

        state.collector = Collector(DB_PATH, llm=state.llm, on_news_updated=reload_news_index)
        state.collector.start()
    yield
    if state.collector:
        state.collector.stop()


app = FastAPI(title="Liga IA", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


# ------------------------------------------------------------ filtres d'affichage

def fmt_date(iso: str | None, with_time: bool = True) -> str:
    """'2026-11-21T20:00:00Z' -> 'sam. 21 nov. · 21:00' (heure de Madrid)."""
    if not iso:
        return ""
    d = datetime.fromisoformat(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    d = d.astimezone(MADRID)
    text = f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"
    return f"{text} · {d:%H:%M}" if with_time else text


templates.env.filters["date"] = fmt_date
templates.env.filters["day"] = lambda iso: fmt_date(iso, with_time=False)
templates.env.filters["position"] = lambda p: POSITIONS.get(p or "", p or "—")
templates.env.filters["status"] = lambda s: STATUSES.get(s, s)
templates.env.filters["label_class"] = lambda label: LABEL_CLASS.get((label or " ")[0], "")


# ------------------------------------------------------------ accès à la base

def get_db():
    conn = connect(DB_PATH, apply_schema=False)
    try:
        yield conn
    finally:
        conn.close()


def page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"chat_enabled": state.llm is not None,
                                                      **context})


# ------------------------------------------------------------ pages

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db=Depends(get_db)):
    table = queries.standings(db)
    return page(request, "home.html", standings=table[:6],
                live=queries.matches(db, when="live", limit=10),
                last=queries.matches(db, when="played", limit=6),
                upcoming=queries.matches(db, when="upcoming", limit=6),
                rumors=queries.rumors(db, limit=6), news=queries.news(db, limit=6),
                empty=not table)


@app.get("/classement", response_class=HTMLResponse)
def standings_page(request: Request, db=Depends(get_db)):
    return page(request, "standings.html", standings=queries.standings(db))


@app.get("/matchs", response_class=HTMLResponse)
def matches_page(request: Request, journee: int | None = None, db=Depends(get_db)):
    days = [r[0] for r in db.execute(
        "SELECT DISTINCT matchday FROM matches WHERE matchday IS NOT NULL ORDER BY matchday")]
    if journee is None:  # par défaut : la prochaine journée (ou la dernière jouée)
        nxt = queries.matches(db, when="upcoming", limit=1)
        journee = nxt[0]["matchday"] if nxt else (days[-1] if days else 1)
    rows = [queries._match(r) for r in db.execute(
        queries._MATCH_SQL + " WHERE m.matchday = ? ORDER BY m.utc_date", (journee,))]
    return page(request, "matches.html", matches=rows, journee=journee, days=days)


@app.get("/mercato", response_class=HTMLResponse)
def mercato_page(request: Request, fiabilite: str | None = None, db=Depends(get_db)):
    all_rumors = queries.rumors(db, limit=500)
    shown = [r for r in all_rumors
             if not fiabilite or templates.env.filters["label_class"](r["label"]) == fiabilite]
    counts = defaultdict(int)
    for r in all_rumors:
        counts[templates.env.filters["label_class"](r["label"])] += 1
    return page(request, "mercato.html", rumors=shown, counts=counts, filtre=fiabilite,
                news=queries.news(db, limit=12))


@app.get("/equipe/{team_id}", response_class=HTMLResponse)
def team_page(request: Request, team_id: int, db=Depends(get_db)):
    t = queries.team(db, team_id)
    if t is None:
        raise HTTPException(404)
    return page(request, "team.html", team=t)


@app.get("/joueur/{player_id}", response_class=HTMLResponse)
def player_page(request: Request, player_id: int, db=Depends(get_db)):
    p = queries.player(db, player_id)
    if p is None:
        raise HTTPException(404)
    return page(request, "player.html", player=p)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """Page « introuvable » pour les visiteurs, JSON pour l'API."""
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return templates.TemplateResponse(
            request, "404.html", {"chat_enabled": state.llm is not None}, status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# ------------------------------------------------------------ API du chatbot

class RateLimiter:
    """Protège le quota gratuit de l'IA : N questions par minute et par visiteur,
    et un plafond global par jour."""

    def __init__(self, per_minute: int, per_day: int):
        self.per_minute, self.per_day = per_minute, per_day
        self.visits: dict[str, deque] = defaultdict(deque)
        self.day, self.day_count = "", 0
        self.lock = threading.Lock()

    def allow(self, visitor: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        with self.lock:
            if today != self.day:
                self.day, self.day_count = today, 0
            q = self.visits[visitor]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute or self.day_count >= self.per_day:
                return False
            q.append(now)
            self.day_count += 1
            return True


limiter = RateLimiter(int(os.environ.get("CHAT_PER_MINUTE", "6")),
                      int(os.environ.get("CHAT_PER_DAY", "400")))


class ChatTurn(BaseModel):
    role: str
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


def visitor_id(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request, db=Depends(get_db)):
    if state.llm is None:
        raise HTTPException(503, "Le chatbot n'est pas configuré (clé LLM_API_KEY absente).")
    if not limiter.allow(visitor_id(request)):
        raise HTTPException(429, "Trop de questions d'un coup : réessaie dans une minute.")
    with state.lock:
        store = state.news_store
    bot = Chatbot(db, state.llm, news_store=store, embedder=state.embedder)
    answer = bot.ask(body.message, [t.model_dump() for t in body.history])
    return {"answer": answer.text, "tools": answer.tools_used}


@app.get("/api/status")
def status(db=Depends(get_db)):
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("teams", "players", "matches", "news", "claims")}
    return {"donnees": counts, "chatbot": state.llm is not None,
            "recherche_semantique": state.news_store is not None,
            "collecte": state.collector.status() if state.collector else "désactivée"}
