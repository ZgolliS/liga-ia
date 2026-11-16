"""Le site web : pages HTML + API du chatbot.

Lancer en local :  uv run uvicorn foot.web.app:app --reload
Puis ouvrir http://localhost:8000
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from foot import queries
from foot.chatbot import Chatbot
from foot.db import connect, connect_readonly
from foot.llm import LLMClient
from foot.web import seo

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


class AppState:
    """Ce qui est partagé entre toutes les visites (chargé une seule fois)."""

    llm: LLMClient | None = None
    embedder = None
    news_store = None
    collector = None
    mirror = None  # mode Cloud : copie locale de la base déposée par le job
    lock = threading.Lock()


state = AppState()


def reload_news_index() -> None:
    """(Re)charge l'index de recherche des actus. Appelé après chaque collecte.
    En mode Cloud, l'index préparé par le job est simplement lu."""
    if state.embedder is None:
        return
    from foot.news_index import build_news_index
    from rag.store import VectorStore

    if state.mirror and (state.mirror.workdir / "index_news" / "chunks.json").exists():
        store = VectorStore.load(state.mirror.workdir / "index_news")
        with state.lock:
            state.news_store = store
        log.info("Index des actus chargé : %d articles", len(store.chunks))
        return
    conn = connect(DB_PATH)
    try:
        store = build_news_index(conn, state.embedder, index_dir=None if state.mirror else
                                 Path("data/index_news"))
        with state.lock:
            state.news_store = store
        log.info("Index des actus : %d articles", len(store.chunks))
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global DB_PATH
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if os.environ.get("STORAGE_URL"):  # mode Cloud : la base vient du stockage (bucket)
        from foot.storage import StorageMirror, open_storage

        state.mirror = StorageMirror(open_storage(os.environ["STORAGE_URL"]),
                                     Path(os.environ.get("WORK_DIR", "/tmp/liga")),
                                     on_update=reload_news_index)
        DB_PATH = state.mirror.db_path
        try:
            state.mirror.refresh()
        except Exception:
            log.exception("Stockage injoignable au démarrage")
    if not DB_PATH.exists():
        connect(DB_PATH).close()  # base vide avec le schéma, en attendant les données
    if os.environ.get("LLM_API_KEY"):
        # Un visiteur n'attend pas 2 minutes : délai court, pas de réessai sur le même
        # modèle, on passe directement au modèle de secours.
        state.llm = LLMClient(retries_per_model=0, timeout=30)
    if os.environ.get("ENABLE_SEMANTIC_SEARCH", "1") == "1":
        from rag.embeddings import FastEmbedEmbedder

        state.embedder = FastEmbedEmbedder()
        threading.Thread(target=reload_news_index, daemon=True).start()
    if os.environ.get("ENABLE_COLLECTOR", "0") == "1":
        from foot.scheduler import Collector

        state.collector = Collector(DB_PATH, llm=state.llm, on_news_updated=reload_news_index)
        state.collector.start()
    if state.mirror:
        state.mirror.start(every_s=float(os.environ.get("REFRESH_SECONDS", "60")))
    yield
    if state.collector:
        state.collector.stop()
    if state.mirror:
        state.mirror.stop()
        state.mirror = None


app = FastAPI(title="Liga IA", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=800)  # pages et JSON compressés


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Les fichiers statiques (polices, CSS, JS) se gardent une semaine dans le navigateur."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


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


# ------------------------------------------------------------ accès à la base

def get_db():
    if state.mirror:
        state.mirror.refresh_if_stale(float(os.environ.get("REFRESH_SECONDS", "60")))
    conn = connect_readonly(DB_PATH) if state.mirror else connect(DB_PATH, apply_schema=False)
    try:
        yield conn
    finally:
        conn.close()


def base_url(request: Request) -> str:
    return seo.site_url(str(request.base_url))


def page(request: Request, name: str, meta: dict | None = None, **context) -> HTMLResponse:
    base = base_url(request)
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return templates.TemplateResponse(request, name, {
        "chat_enabled": state.llm is not None, "base": base, "canonical": base + path,
        "meta": meta or {}, **context})


# ------------------------------------------------------------ pages

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db=Depends(get_db)):
    table = queries.standings(db)
    played = queries.matches(db, when="played", limit=12)
    stats = queries.site_stats(db)
    season = queries.current_season(db)
    return page(request, "home.html", seo.home(base_url(request), table, stats, season),
                standings=table[:6], podium=table[:3], leader=table[0] if table else None,
                live=queries.matches(db, when="live", limit=10),
                last=played[:6], ticker=played, season=seo.season_label(season),
                upcoming=queries.matches(db, when="upcoming", limit=6),
                rumors=queries.rumors(db, limit=6), news=queries.news(db, limit=6),
                stats=stats, empty=not table)


@app.get("/classement", response_class=HTMLResponse)
def standings_page(request: Request, db=Depends(get_db)):
    rows = queries.standings(db)
    season = queries.current_season(db)
    return page(request, "standings.html", seo.standings(base_url(request), rows, season),
                standings=rows, season=seo.season_label(season))


@app.get("/matchs", response_class=HTMLResponse)
def matches_page(request: Request, journee: int | None = None, db=Depends(get_db)):
    days = [r[0] for r in db.execute(
        "SELECT DISTINCT matchday FROM matches WHERE matchday IS NOT NULL ORDER BY matchday")]
    if journee is None:  # par défaut : la prochaine journée (ou la dernière jouée)
        nxt = queries.matches(db, when="upcoming", limit=1)
        journee = nxt[0]["matchday"] if nxt else (days[-1] if days else 1)
    rows = [queries._match(r) for r in db.execute(
        queries._MATCH_SQL + " WHERE m.matchday = ? ORDER BY m.utc_date", (journee,))]
    return page(request, "matches.html", seo.matches(base_url(request), journee, rows),
                matches=rows, journee=journee, days=days)


@app.get("/mercato", response_class=HTMLResponse)
def mercato_page(request: Request, fiabilite: str | None = None, db=Depends(get_db)):
    all_rumors = queries.rumors(db, limit=500)
    shown = [r for r in all_rumors if not fiabilite or r["kind"] == fiabilite]
    counts = defaultdict(int)
    for r in all_rumors:
        counts[r["kind"]] += 1
    return page(request, "mercato.html", seo.mercato(base_url(request), len(all_rumors), counts),
                rumors=shown, counts=counts, filtre=fiabilite,
                rumors_total=len(all_rumors), news=queries.news(db, limit=12))


@app.get("/equipe/{team_id}", response_class=HTMLResponse)
def team_page(request: Request, team_id: int, db=Depends(get_db)):
    t = queries.team(db, team_id)
    if t is None:
        raise HTTPException(404)
    return page(request, "team.html", seo.team(base_url(request), t), team=t)


@app.get("/joueur/{player_id}", response_class=HTMLResponse)
def player_page(request: Request, player_id: int, db=Depends(get_db)):
    p = queries.player(db, player_id)
    if p is None:
        raise HTTPException(404)
    return page(request, "player.html", seo.player(base_url(request), p), player=p)


def error_context(request: Request) -> dict:
    """Contexte minimal des pages d'erreur (pas d'adresse canonique : non indexées)."""
    return {"chat_enabled": False, "meta": {}, "base": base_url(request), "canonical": None}


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    """Erreur imprévue : on la note dans les journaux et on montre une page propre."""
    log.exception("Erreur sur %s", request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Erreur interne"}, status_code=500)
    return templates.TemplateResponse(request, "500.html", error_context(request), status_code=500)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """Page « introuvable » pour les visiteurs, JSON pour l'API."""
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return templates.TemplateResponse(request, "404.html", error_context(request),
                                          status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# ------------------------------------------------------------ robots et moteurs IA

AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot",
           "PerplexityBot", "Google-Extended", "Applebot-Extended", "CCBot"]


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    """Tout le monde peut lire le site (moteurs classiques ET robots des IA), sauf l'API."""
    lines = ["User-agent: *", "Allow: /", "Disallow: /api/", ""]
    for bot in AI_BOTS:  # explicite : on VEUT être cité par les moteurs de réponse IA
        lines += [f"User-agent: {bot}", "Allow: /", "Disallow: /api/", ""]
    lines.append(f"Sitemap: {base_url(request)}/sitemap.xml")
    return "\n".join(lines) + "\n"


@app.get("/sitemap.xml")
def sitemap(request: Request, db=Depends(get_db)):
    base = base_url(request)
    today = datetime.now(UTC).date().isoformat()
    urls = [(f"{base}{path}", freq, prio) for path, freq, prio in [
        ("/", "hourly", "1.0"), ("/classement", "daily", "0.9"),
        ("/matchs", "hourly", "0.9"), ("/mercato", "hourly", "0.9")]]
    urls += [(f"{base}/equipe/{r[0]}", "daily", "0.8") for r in db.execute("SELECT id FROM teams")]
    urls += [(f"{base}/joueur/{r[0]}", "weekly", "0.6") for r in db.execute("SELECT id FROM players")]
    body = "".join(f"<url><loc>{loc}</loc><lastmod>{today}</lastmod><changefreq>{freq}"
                   f"</changefreq><priority>{prio}</priority></url>" for loc, freq, prio in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>')
    return Response(xml, media_type="application/xml")


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt(request: Request, db=Depends(get_db)):
    """Présentation du site pour les IA (convention llms.txt) : quoi, où, à quel point c'est frais."""
    base = base_url(request)
    table = queries.standings(db)
    stats = queries.site_stats(db)
    meta = seo.home(base, table, stats, queries.current_season(db))
    teams = "\n".join(f"- [{r['team']}]({base}/equipe/{r['team_id']}) : {seo.ordinal(r['position'])}, "
                      f"{r['points']} pts" for r in table)
    return f"""# Liga IA

> Site francophone sur la Liga espagnole : classement, résultats, calendrier, effectifs et
> mercato. Chaque rumeur de transfert reçoit une note de fiabilité (officiel, très probable,
> rumeur, intox probable) calculée à partir de la fiabilité et du nombre des sources.

{meta['summary'] or ''}

Données mises à jour toutes les 15 minutes (matchs : football-data.org ; actus : médias
sportifs, avec lien vers l'article d'origine ; photos : Wikimedia Commons, licences libres).

## Pages principales
- [Classement de la Liga]({base}/classement)
- [Matchs et résultats]({base}/matchs)
- [Mercato vérifié]({base}/mercato) : {stats['rumors']} rumeurs notées

## Équipes
{teams}
"""


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
    # Les joueurs cités avec un lien vers leur fiche : on renvoie leur photo pour la bulle.
    ids = [int(x) for x in re.findall(r"\(/joueur/(\d+)\)", answer.text)][:10]
    faces = {f"/joueur/{pid}": url for pid, url in queries.player_photos(db, ids).items()}
    return {"answer": answer.text, "tools": answer.tools_used, "faces": faces}


@app.get("/api/status")
def status(db=Depends(get_db)):
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("teams", "players", "matches", "news", "claims")}
    return {"donnees": counts, "chatbot": state.llm is not None,
            "recherche_semantique": state.news_store is not None,
            "collecte": state.collector.status() if state.collector else "désactivée",
            "version_base": state.mirror.current if state.mirror else None}
