-- Schéma de la base de données foot (SQLite, compatible PostgreSQL à quelques détails près).
-- Organisation en couches : BRONZE (brut) -> ARGENT (tableaux propres) -> OR (vues).

PRAGMA foreign_keys = ON;

-- ============================ 🥉 BRONZE ============================
-- Copie exacte de chaque réponse d'API : permet de tout recalculer sans rappeler l'API.
CREATE TABLE IF NOT EXISTS raw_payloads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,              -- ex : 'football-data', 'rss:marca'
    endpoint    TEXT NOT NULL,              -- ex : '/competitions/PD/matches'
    fetched_at  TEXT NOT NULL,              -- date ISO 8601 en UTC
    payload     TEXT NOT NULL               -- la réponse JSON brute
);

-- ============================ 🥈 ARGENT ============================
-- Chaque source a ses propres identifiants : on garde notre id interne + ceux des sources.
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fd_id       INTEGER UNIQUE,             -- identifiant chez football-data.org
    af_id       INTEGER UNIQUE,             -- identifiant chez API-Football
    name        TEXT NOT NULL,
    short_name  TEXT,
    tla         TEXT,                       -- code à 3 lettres, ex : 'FCB'
    coach_name  TEXT,                       -- entraîneur actuel
    crest_url   TEXT,                       -- logo
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fd_id         INTEGER UNIQUE,
    af_id         INTEGER UNIQUE,
    name          TEXT NOT NULL,
    position      TEXT,
    nationality   TEXT,
    date_of_birth TEXT,
    team_id       INTEGER REFERENCES teams(id),
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fd_id         INTEGER UNIQUE,
    season        TEXT NOT NULL,            -- ex : '2026' pour la saison 2026/27
    matchday      INTEGER,                  -- journée
    utc_date      TEXT NOT NULL,
    status        TEXT NOT NULL,            -- SCHEDULED, IN_PLAY, FINISHED…
    home_team_id  INTEGER NOT NULL REFERENCES teams(id),
    away_team_id  INTEGER NOT NULL REFERENCES teams(id),
    home_score    INTEGER,
    away_score    INTEGER,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standings (
    season        TEXT NOT NULL,
    team_id       INTEGER NOT NULL REFERENCES teams(id),
    position      INTEGER NOT NULL,
    played        INTEGER NOT NULL,
    won           INTEGER NOT NULL,
    draw          INTEGER NOT NULL,
    lost          INTEGER NOT NULL,
    goals_for     INTEGER NOT NULL,
    goals_against INTEGER NOT NULL,
    points        INTEGER NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER NOT NULL REFERENCES matches(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    team_id     INTEGER NOT NULL REFERENCES teams(id),
    minute      INTEGER,
    card_type   TEXT NOT NULL CHECK (card_type IN ('YELLOW', 'RED', 'YELLOW_RED')),
    UNIQUE (match_id, player_id, minute, card_type)   -- empêche les doublons
);

CREATE TABLE IF NOT EXISTS injuries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL REFERENCES players(id),
    reason          TEXT,                   -- tel que communiqué officiellement
    start_date      TEXT,
    expected_return TEXT,
    source          TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (player_id, start_date)
);

CREATE TABLE IF NOT EXISTS transfers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER REFERENCES players(id),
    player_name TEXT NOT NULL,
    from_team   TEXT,
    to_team     TEXT,
    date        TEXT,
    fee         TEXT,                       -- texte libre : '80 M€', 'prêt', 'libre'…
    source      TEXT NOT NULL,
    UNIQUE (player_name, to_team, date)
);

-- Médias et leur niveau de fiabilité (voir reliability.py pour la signification des niveaux).
CREATE TABLE IF NOT EXISTS sources (
    id      TEXT PRIMARY KEY,               -- ex : 'marca'
    name    TEXT NOT NULL,
    domain  TEXT UNIQUE,                    -- ex : 'marca.com'
    tier    INTEGER NOT NULL CHECK (tier BETWEEN 0 AND 4)
);

-- Articles récupérés via RSS : on garde titre, résumé court et lien (pas l'article entier).
CREATE TABLE IF NOT EXISTS news (
    url           TEXT PRIMARY KEY,         -- l'URL sert d'identifiant unique
    source_id     TEXT REFERENCES sources(id),
    title         TEXT NOT NULL,
    summary       TEXT,
    published_at  TEXT,
    updated_at    TEXT NOT NULL
);

-- Qui est cité dans quel article (joueur ou équipe de Liga).
CREATE TABLE IF NOT EXISTS news_mentions (
    news_url     TEXT NOT NULL REFERENCES news(url),
    entity_type  TEXT NOT NULL CHECK (entity_type IN ('player', 'team')),
    entity_id    INTEGER NOT NULL,
    PRIMARY KEY (news_url, entity_type, entity_id)
);

-- Rumeurs extraites des articles par l'IA : "tel article dit que tel joueur va dans tel club".
CREATE TABLE IF NOT EXISTS claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    news_url        TEXT NOT NULL REFERENCES news(url),
    player_name     TEXT NOT NULL,
    player_id       INTEGER REFERENCES players(id),  -- si c'est un joueur de Liga reconnu
    from_team       TEXT NOT NULL DEFAULT '',
    to_team         TEXT NOT NULL DEFAULT '',
    stance          TEXT NOT NULL CHECK (stance IN ('affirme', 'dément', 'neutre')),
    original_source TEXT,                -- à qui l'article attribue l'info (journaliste…)
    source_type     TEXT,                -- officiel, agent, journaliste, opinion
    UNIQUE (news_url, player_name, to_team)
);

-- Articles déjà lus par l'IA : on ne les renvoie jamais (quota gratuit limité).
CREATE TABLE IF NOT EXISTS news_processing (
    news_url      TEXT PRIMARY KEY REFERENCES news(url),
    processed_at  TEXT NOT NULL,
    model         TEXT
);

-- ============================ 🥇 OR ============================
-- Vues prêtes pour le site et le chatbot.
CREATE VIEW IF NOT EXISTS v_player_cards AS
SELECT p.name AS player, t.name AS team,
       SUM(c.card_type = 'YELLOW') AS yellow_cards,
       SUM(c.card_type IN ('RED', 'YELLOW_RED')) AS red_cards
FROM cards c
JOIN players p ON p.id = c.player_id
JOIN teams t ON t.id = c.team_id
GROUP BY p.id, t.id;

CREATE VIEW IF NOT EXISTS v_standings AS
SELECT s.season, s.position, t.name AS team, s.played, s.won, s.draw, s.lost,
       s.goals_for, s.goals_against, s.goals_for - s.goals_against AS goal_diff, s.points
FROM standings s JOIN teams t ON t.id = s.team_id
ORDER BY s.season, s.position;
