"""Descarga masiva multi-fuente: ESPN, API-Football, Sofascore y TheSportsDB."""
import os, sqlite3, time
from datetime import datetime, timezone
import requests
from sofascore_api import SofaScoreClient

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.db")

# ===== CONFIGURACIÓN DE API-FOOTBALL =====
API_FOOTBALL_KEY = "bee4cbe52adce1abb2947c78b26df2f6"
API_HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}
API_BASE = "https://v3.football.api-sports.io"

# Formato: "Nombre en App": (slug_ESPN, ID_Sofascore, ID_API-Football)
LEAGUES = {
    "Liga MX": ("mex.1", 116, 262),
    "MLS": ("usa.1", 242, 253),
    "Premier League": ("eng.1", 17, 39),
    "La Liga": ("esp.1", 8, 140),
    "Serie A": ("ita.1", 23, 135),
    "Bundesliga": ("ger.1", 35, 78),
    "Ligue 1": ("fra.1", 34, 61),
    "Champions League": ("uefa.champions", 7, 2),
    "Europa League": ("uefa.europa", 14, 3),
    "UEFA Nations League": ("uefa.nations", 2014, 5),
    "Brasileirão": ("bra.1", 325, 71),
    "Liga Profesional (Argentina)": ("arg.1", 406, 128),
}

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS matches(
        id TEXT PRIMARY KEY, league TEXT, sport TEXT, date TEXT, state TEXT,
        home TEXT, away TEXT, home_score REAL, away_score REAL,
        source TEXT, stats_done INTEGER DEFAULT 0)""")
    
    # Auto-reparación de columnas
    cols = [col[1] for col in c.execute("PRAGMA table_info(matches)").fetchall()]
    if "source" not in cols: c.execute("ALTER TABLE matches ADD COLUMN source TEXT")
    if "stats_done" not in cols: c.execute("ALTER TABLE matches ADD COLUMN stats_done INTEGER DEFAULT 0")

    c.execute("""CREATE TABLE IF NOT EXISTS match_stats(
        match_id TEXT, team TEXT,
        corners REAL, shots REAL, shots_on_target REAL,
        passes REAL, pass_accuracy REAL,
        yellow_cards REAL, red_cards REAL,
        fouls REAL, possession REAL, xg REAL,
        PRIMARY KEY (match_id, team))""")
    c.execute("""CREATE TABLE IF NOT EXISTS team_logos(
        team_name TEXT PRIMARY KEY, logo_url TEXT)""")
    return c

def _to_num(x):
    if x is None: return None
    try: return float(str(x).replace("%", "").strip())
    except: return None

# ===== 1. FUENTE: ESPN =====
def fetch_espn(db, league_name, espn_slug, log):
    log(f"  [ESPN] Buscando {league_name}...")
    current_year = datetime.now().year
    n = 0
    for year in [current_year - 1, current_year]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{espn_slug}/scoreboard"
        try:
            r = requests.get(url, params={"dates": str(year), "limit": 1000}, timeout=15)
            if r.status_code != 200: continue
            data = r.json()
        except: continue

        for ev in data.get("events", []):
            try:
                comp = ev["competitions"][0]
                home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
                away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
                state = ev["status"]["type"]["state"]
                
                db.execute("""INSERT OR IGNORE INTO matches
                    (id, league, sport, date, state, home, away, home_score, away_score, source, stats_done)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"espn:{ev['id']}", league_name, "football", ev["date"], state,
                     home["team"]["displayName"], away["team"]["displayName"],
                     _to_num(home.get("score")), _to_num(away.get("score")), "espn", 0))
                n += 1
            except: continue
        db.commit()
    log(f"    -> {n} partidos encontrados.")

def fetch_espn_stats(db, log):
    rows = db.execute("SELECT id, league FROM matches WHERE source='espn' AND state='post' AND stats_done=0 LIMIT 100").fetchall()
    if not rows: return
    log(f"  [ESPN] Descargando stats de {len(rows)} partidos...")
    for r in rows:
        ev_id = r["id"].split(":")[1]
        league_slug = next((slug for lg, (slug, _, _) in LEAGUES.items() if lg == r["league"]), None)
        if not league_slug: continue

        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_slug}/summary"
        try:
            res = requests.get(url, params={"event": ev_id}, timeout=10).json()
            for team_data in res.get("boxscore", {}).get("teams", []):
                team_name = team_data.get("team", {}).get("displayName")
                if not team_name: continue
                d = {}
                for stat in team_data.get("statistics", []):
                    name = stat.get("name", "").lower()
                    val = _to_num(stat.get("displayValue"))
                    if "corner" in name: d["corners"] = val
                    elif "shot" in name and "on" in name: d["sot"] = val
                    elif "total" in name and "shot" in name: d["shots"] = val
                    elif "possession" in name: d["possession"] = val
                    elif "yellow" in name: d["yellow"] = val
                    elif "red" in name: d["red"] = val
                    elif "foul" in name: d["fouls"] = val
                if d:
                    db.execute("""INSERT OR REPLACE INTO match_stats
                        (match_id, team, corners, shots, shots_on_target, possession, yellow_cards, red_cards, fouls)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                        (r["id"], team_name, d.get("corners"), d.get("shots"), d.get("sot"), d.get("possession"), d.get("yellow"), d.get("red"), d.get("fouls")))
            db.execute("UPDATE matches SET stats_done=1 WHERE id=?", (r["id"],))
            db.commit()
        except: 
            db.execute("UPDATE matches SET stats_done=1 WHERE id=?", (r["id"],))
            db.commit()
        time.sleep(0.3)

# ===== 2. FUENTE: API-FOOTBALL =====
def fetch_apif(db, league_name, apif_id, log):
    log(f"  [API-Football] Buscando {league_name}...")
    n = 0
    # Buscar temporadas recientes
    for season in [2023, 2024, 2025]:
        url = f"{API_BASE}/fixtures"
        try:
            r = requests.get(url, headers=API_HEADERS, params={"league": apif_id, "season": season}, timeout=15)
            if r.status_code == 200:
                data = r.json().get("response", [])
                for f in data:
                    try:
                        fid = f["fixture"]["id"]
                        db.execute("""INSERT OR IGNORE INTO matches
                            (id, league, sport, date, state, home, away, home_score, away_score, source, stats_done)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (f"apif:{fid}", league_name, "football", f["fixture"]["date"],
                             "post", f["teams"]["home"]["name"], f["teams"]["away"]["name"],
                             f["goals"]["home"], f["goals"]["away"], "apif", 0))
                        n += 1
                    except: continue
                db.commit()
        except: pass
        time.sleep(0.5)
    log(f"    -> {n} partidos encontrados.")

def fetch_apif_stats(db, log):
    rows = db.execute("SELECT id FROM matches WHERE source='apif' AND state='post' AND stats_done=0 LIMIT 100").fetchall()
    if not rows: return
    log(f"  [API-Football] Descargando stats de {len(rows)} partidos...")
    for r in rows:
        fid = r["id"].split(":")[1]
        url = f"{API_BASE}/fixtures/statistics"
        try:
            res = requests.get(url, headers=API_HEADERS, params={"fixture": fid}, timeout=15)
            if res.status_code == 200:
                data = res.json().get("response", [])
                for team_data in data:
                    team = team_data["team"]["name"]
                    d = {}
                    for s in team_data.get("statistics", []):
                        name = s.get("type", "").lower()
                        val = _to_num(s.get("value"))
                        if "corner" in name: d["corners"] = val
                        elif "total shot" in name or "shots" == name: d["shots"] = val
                        elif "shots on goal" in name: d["sot"] = val
                        elif "passes" in name and "%" not in name: d["passes"] = val
                        elif "possession" in name: d["possession"] = val
                        elif "yellow" in name: d["yellow"] = val
                        elif "red" in name: d["red"] = val
                        elif "fouls" in name: d["fouls"] = val
                        elif "expected goals" in name or "xg" in name: d["xg"] = val
                    if d:
                        db.execute("""INSERT OR REPLACE INTO match_stats
                            (match_id, team, corners, shots, shots_on_target, passes, possession, yellow_cards, red_cards, fouls, xg)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (r["id"], team, d.get("corners"), d.get("shots"), d.get("sot"), d.get("passes"), d.get("possession"), d.get("yellow"), d.get("red"), d.get("fouls"), d.get("xg")))
                db.execute("UPDATE matches SET stats_done=1 WHERE id=?", (r["id"],))
                db.commit()
        except: pass
        time.sleep(0.6) # Respetar rate limit

# ===== 3. FUENTE: SOFASCORE =====
def fetch_sofa(db, league_name, sofa_id, log):
    log(f"  [Sofascore] Buscando {league_name}...")
    client = SofaScoreClient()
    try:
        events = client.get_events_range("football", start="2023-01-01", end=datetime.now().strftime("%Y-%m-%d"))
    except Exception as e:
        log(f"    Error: {e}"); client.close(); return

    n = 0
    for ev in events:
        if ev.get("tournament", {}).get("uniqueTournament", {}).get("id") != sofa_id: continue
        try:
            ev_id = ev["id"]
            state = {"notstarted": "pre", "finished": "post", "inprogress": "in"}.get(ev["status"]["type"], "pre")
            db.execute("""INSERT OR IGNORE INTO matches
                (id, league, sport, date, state, home, away, home_score, away_score, source, stats_done)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (f"sofa:{ev_id}", league_name, "football", datetime.fromtimestamp(ev["startTimestamp"], tz=timezone.utc).isoformat(), state,
                 ev["homeTeam"]["name"], ev["awayTeam"]["name"],
                 ev.get("homeScore", {}).get("current"), ev.get("awayScore", {}).get("current"), "sofa", 0))
            n += 1
        except: continue
    db.commit()
    log(f"    -> {n} partidos encontrados.")
    client.close()

def fetch_sofa_stats(db, log):
    rows = db.execute("SELECT id FROM matches WHERE source='sofa' AND state='post' AND stats_done=0 LIMIT 200").fetchall()
    if not rows: return
    log(f"  [Sofascore] Descargando stats de {len(rows)} partidos...")
    client = SofaScoreClient()
    for r in rows:
        ev_id = int(r["id"].split(":")[1])
        try:
            stats = client.get_event_statistics(ev_id)
            for block in stats.get("statistics", []):
                team = block.get("team", {}).get("name")
                if not team: continue
                d = {}
                for group in block.get("groups", []):
                    for item in group.get("statisticsItems", []):
                        name = item.get("name", "").lower()
                        val = item.get("home") if block.get("homeAway")=="home" else item.get("away")
                        if "corner" in name: d["corners"] = _to_num(val)
                        elif "shot on target" in name: d["sot"] = _to_num(val)
                        elif "total shot" in name: d["shots"] = _to_num(val)
                        elif "pass" in name and "%" not in name: d["passes"] = _to_num(val)
                        elif "possession" in name: d["possession"] = _to_num(val)
                        elif "yellow" in name: d["yellow"] = _to_num(val)
                        elif "red" in name: d["red"] = _to_num(val)
                        elif "expected goals" in name or name == "xg": d["xg"] = _to_num(val)
                if d:
                    db.execute("""INSERT OR REPLACE INTO match_stats
                        (match_id, team, corners, shots, shots_on_target, passes, possession, yellow_cards, red_cards, xg)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (r["id"], team, d.get("corners"), d.get("shots"), d.get("sot"), d.get("passes"), d.get("possession"), d.get("yellow"), d.get("red"), d.get("xg")))
            db.execute("UPDATE matches SET stats_done=1 WHERE id=?", (r["id"],))
            db.commit()
        except: 
            db.execute("UPDATE matches SET stats_done=1 WHERE id=?", (r["id"],))
            db.commit()
        time.sleep(0.2)
    client.close()

# ===== 4. FUENTE: THESPORTSDB (Logos) =====
def enrich_logos(db, log):
    log("  [TheSportsDB] Buscando logos faltantes...")
    equipos = db.execute("""SELECT DISTINCT home as team FROM matches 
        WHERE home NOT IN (SELECT team_name FROM team_logos)
        UNION 
        SELECT DISTINCT away as team FROM matches 
        WHERE away NOT IN (SELECT team_name FROM team_logos)""").fetchall()
    
    if not equipos: return
    for row in equipos:
        team = row["team"]
        url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={team}"
        try:
            r = requests.get(url, timeout=10)
            data = r.json()
            if data.get("teams"):
                logo = data["teams"][0].get("strTeamBadge")
                if logo:
                    db.execute("INSERT OR REPLACE INTO team_logos (team_name, logo_url) VALUES (?,?)", (team, logo))
                    db.commit()
        except: pass
        time.sleep(0.5)

def run_all(log=print):
    db = conn()
    for league, (espn_slug, sofa_id, apif_id) in LEAGUES.items():
        fetch_espn(db, league, espn_slug, log)
        fetch_apif(db, league, apif_id, log) # <-- Nueva fuente
        fetch_sofa(db, league, sofa_id, log)
    
    fetch_espn_stats(db, log)
    fetch_apif_stats(db, log)
    fetch_sofa_stats(db, log)
    enrich_logos(db, log)
    db.close()
    log("✅ Proceso completado. Datos de 3 APIs y logos actualizados.")

if __name__ == "__main__":
    run_all()