"""Modelo con tolerancia a datos faltantes + Análisis de IA + Generador de Parlays."""
import math
import json
import re
from datetime import datetime, timezone
from collections import defaultdict
import ollama
from mcp_client import get_livescore_h2h


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _weighted_avg(xs, decay=0.92):
    if not xs:
        return 0.0
    weights = [decay ** i for i in range(len(xs))]
    return sum(x * w for x, w in zip(xs, weights)) / sum(weights)


def pmf(k, lam):
    lam = max(lam, 0.05)
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def p_over_poisson(lam, line):
    return 1 - sum(pmf(i, lam) for i in range(int(math.floor(line)) + 1))


def _team_goals(db, league, team, n=15):
    rows = db.execute("""SELECT home, away, home_score, away_score FROM matches 
        WHERE league=? AND state='post' AND home_score IS NOT NULL 
        ORDER BY date DESC LIMIT 300""", (league,)).fetchall()
    gf, ga = [], []
    for r in rows:
        if r["home"] == team:
            gf.append(r["home_score"])
            ga.append(r["away_score"])
        elif r["away"] == team:
            gf.append(r["away_score"])
            ga.append(r["home_score"])
        if len(gf) >= n:
            break
    return _weighted_avg(gf), _weighted_avg(ga)


def get_team_stats(db, league, team, n=15):
    stats = {"corners": [], "shots": [], "shots_on_target": [], "passes": [],
             "possession": [], "yellow_cards": [], "red_cards": [], "xg": []}
    rows = db.execute("""SELECT s.* FROM match_stats s
        JOIN matches m ON s.match_id = m.id
        WHERE m.league=? AND (m.home=? OR m.away=?) AND m.state='post' AND s.team=?
        ORDER BY m.date DESC LIMIT ?""", (league, team, team, team, n)).fetchall()

    for r in rows:
        row_dict = dict(r)
        for k in stats.keys():
            if k in row_dict and row_dict[k] is not None:
                stats[k].append(row_dict[k])

    return {k: round(_weighted_avg(v), 2) for k, v in stats.items() if v}


def get_logo(db, team):
    row = db.execute("SELECT logo_url FROM team_logos WHERE team_name=?", (team,)).fetchone()
    return row["logo_url"] if row else None


def predict_soccer(db, league, home, away):
    h_gf, h_ga = _team_goals(db, league, home)
    a_gf, a_ga = _team_goals(db, league, away)
    lg_h = db.execute("SELECT AVG(home_score) FROM matches WHERE league=? AND state='post'", (league,)).fetchone()[0] or 1.2
    lg_a = db.execute("SELECT AVG(away_score) FROM matches WHERE league=? AND state='post'", (league,)).fetchone()[0] or 1.0

    lh = (h_gf + a_ga) / 2 if h_gf and a_ga else lg_h
    la = (a_gf + h_ga) / 2 if a_gf and h_ga else lg_a

    M = 11
    mat = [[pmf(i, lh) * pmf(j, la) for j in range(M)] for i in range(M)]
    p_h = sum(mat[i][j] for i in range(M) for j in range(M) if i > j)
    p_d = sum(mat[i][i] for i in range(M))
    p_a = sum(mat[i][j] for i in range(M) for j in range(M) if i < j)
    s = p_h + p_d + p_a
    mk = {"Gana local": p_h / s, "Empate": p_d / s, "Gana visita": p_a / s}

    tot = lh + la
    for line in (1.5, 2.5, 3.5):
        mk[f"Más de {line} goles"] = p_over_poisson(tot, line)

    h_stats = get_team_stats(db, league, home)
    a_stats = get_team_stats(db, league, away)
    if "corners" in h_stats and "corners" in a_stats:
        total_c = h_stats["corners"] + a_stats["corners"]
        for line in (8.5, 9.5, 10.5):
            mk[f"Más de {line} córners"] = p_over_poisson(total_c, line)

    exp = {"Goles esperados": f"{lh:.2f} - {la:.2f}"}

    return {"markets": mk, "expected": exp, "h_stats": h_stats, "a_stats": a_stats,
            "home_logo": get_logo(db, home), "away_logo": get_logo(db, away),
            "lambda_home": lh, "lambda_away": la}


# =========================================================
# OPCIÓN 1: PARLEY MATEMÁTICO
# =========================================================
def generate_parlays(db, leagues, min_prob=0.65, max_prob=0.92, min_legs=3, max_legs=8, mode="mix", top_n=3):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    ph = ",".join("?" * len(leagues))
    matches = db.execute(f"""SELECT * FROM matches WHERE league IN ({ph}) 
        AND state='pre' AND date>=? ORDER BY date LIMIT 200""", (*leagues, now)).fetchall()

    all_picks = []
    for m in matches:
        p = predict_soccer(db, m["league"], m["home"], m["away"])
        if not p: continue
        
        prob_h = p["markets"].get("Gana local", 0)
        prob_a = p["markets"].get("Gana visita", 0)
        
        # Elegimos al equipo con mayor probabilidad (ignorando empate)
        if prob_h > prob_a:
            pick_team = m["home"]
            pick_prob = prob_h
            pick_market = "Gana local"
        else:
            pick_team = m["away"]
            pick_prob = prob_a
            pick_market = "Gana visita"

        if min_prob <= pick_prob <= max_prob:
            all_picks.append({
                "match": f"{m['home']} vs {m['away']}",
                "league": m["league"],
                "date": m["date"][:16],
                "market": pick_market,
                "prob": pick_prob,
                "match_id": m["id"],
                "ganador": pick_team,                      # <-- AQUI ESTA LA CLAVE
                "prob_ganador": f"{pick_prob*100:.1f}%"    # <-- AQUI ESTA LA CLAVE
            })

    if not all_picks: return []

    all_picks.sort(key=lambda x: -x["prob"])

    by_match = defaultdict(list)
    for pk in all_picks:
        by_match[pk["match_id"]].append(pk)

    diversified = []
    for mid, picks in by_match.items():
        diversified.append(picks[0])

    diversified.sort(key=lambda x: -x["prob"])

    parlays = []
    if mode == "same":
        by_league = defaultdict(list)
        for pk in diversified:
            by_league[pk["league"]].append(pk)
        
        for lg, picks in by_league.items():
            if len(picks) >= min_legs:
                legs = picks[:max_legs]
                combo = 1.0
                for l in legs: combo *= l["prob"]
                parlays.append({"legs": legs, "combined_prob": combo, "n_legs": len(legs)})
    else:
        for target_legs in range(max_legs, min_legs - 1, -1):
            legs = []
            combo_prob = 1.0
            local_used = set()
            for pk in diversified:
                if pk["match_id"] in local_used: continue
                legs.append(pk)
                local_used.add(pk["match_id"])
                combo_prob *= pk["prob"]
                if len(legs) >= target_legs: break
            if len(legs) >= min_legs:
                parlays.append({"legs": legs, "combined_prob": combo_prob, "n_legs": len(legs)})
            if len(parlays) >= top_n: break

    parlays.sort(key=lambda x: -x["combined_prob"])
    return parlays[:top_n]


# =========================================================
# OPCIÓN 2: PARLEY IA
# =========================================================
def generate_parlays_ai(db, leagues, min_prob=0.60, max_prob=0.95, min_legs=3, max_legs=6, mode="mix"):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    ph = ",".join("?" * len(leagues))
    matches = db.execute(f"""SELECT * FROM matches WHERE league IN ({ph}) 
        AND state='pre' AND date>=? ORDER BY date LIMIT 80""", (*leagues, now)).fetchall()

    if not matches: return []

    candidatos = []
    for m in matches:
        p = predict_soccer(db, m["league"], m["home"], m["away"])
        if not p: continue
        
        prob_h = p["markets"].get("Gana local", 0)
        prob_a = p["markets"].get("Gana visita", 0)
        
        if prob_h > prob_a:
            pick_team = m["home"]
            pick_prob = prob_h
            pick_market = "Gana local"
        else:
            pick_team = m["away"]
            pick_prob = prob_a
            pick_market = "Gana visita"

        if min_prob <= pick_prob <= max_prob:
            candidatos.append({
                "match": f"{m['home']} vs {m['away']}",
                "league": m["league"],
                "date": m["date"][:16],
                "equipo_favorito": pick_team,
                "mercado": pick_market,
                "prob": round(pick_prob * 100, 1),
                "xg": p["expected"].get("Goles esperados", ""),
            })

    if len(candidatos) < min_legs: return []

    contexto = json.dumps(candidatos[:40], ensure_ascii=False, indent=2)
    instruccion_liga = "NO repitas el mismo partido más de una vez." if mode == "mix" else "TODOS los picks DEBEN SER DE LA MISMA LIGA."

    prompt = f"""Eres un analista deportivo experto. Te doy una lista de equipos favoritos para ganar.
PARTIDOS DISPONIBLES:
{contexto}

TAREA:
1. Elige {min_legs} a {max_legs} equipos que TÚ consideres que GANARÁN su partido (no elijas empates).
2. {instruccion_liga}
3. Justifica cada pick en 1-2 líneas mencionando datos concretos.
4. Da una probabilidad combinada estimada.

RESPONDE EXACTAMENTE EN ESTE FORMATO JSON (sin markdown, sin texto extra):
{{
  "picks": [
    {{"partido": "Local vs Visita", "equipo_favorito": "Nombre del equipo", "mercado": "Gana local", "prob": 78.2, "razon": "..."}}
  ],
  "prob_combinada": 32.5,
  "veredicto": "Explicación breve en 2-3 líneas"
}}
"""

    try:
        response = ollama.chat(
            model="llama3",
            messages=[{"role": "user", "content": prompt}],
            options={"timeout": 120, "temperature": 0.1},
        )
        raw = response["message"]["content"].strip()

        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match: return [{"error": f"La IA no devolvió JSON. Respuesta: {raw[:100]}..."}]
        
        data = json.loads(match.group(0))
        picks = data.get("picks", [])
        if len(picks) < min_legs: return [{"error": f"La IA solo devolvió {len(picks)} picks."}]

        probs = [p["prob"] / 100 for p in picks if isinstance(p.get("prob"), (int, float))]
        combinada = 1.0
        for pr in probs: combinada *= pr

        # Agregamos el ganador a los picks devueltos por la IA para que app.py lo pueda leer
        for pk in picks:
            pk["ganador"] = pk.get("equipo_favorito", "—")

        return [{
            "legs": picks,
            "combined_prob": combinada,
            "n_legs": len(picks),
            "veredicto": data.get("veredicto", ""),
            "fuente": "IA",
        }]
    except Exception as e:
        return [{"error": f"Error de IA: {e}"}]


# =========================================================
# ANÁLISIS INDIVIDUAL CON IA
# =========================================================
def analyze_with_ai(db, league, home, away, h_stats, a_stats, mk):
    h2h = db.execute("""SELECT date, home, away, home_score, away_score FROM matches 
        WHERE league=? AND state='post' AND 
        ((home=? AND away=?) OR (home=? AND away=?))
        ORDER BY date DESC LIMIT 5""", (league, home, away, away, home)).fetchall()

    h2h_text = ""
    for r in h2h:
        h2h_text += f"- {r['date'][:10]}: {r['home']} {r['home_score']} - {r['away_score']} {r['away']}\n"
    if not h2h_text:
        h2h_text = "No hay enfrentamientos directos recientes en la base de datos."

    try:
        livescore_h2h = get_livescore_h2h(home, away)
        if "no disponible" in livescore_h2h.lower():
            livescore_h2h = "No se pudo obtener información de enfrentamientos en línea."
    except Exception:
        livescore_h2h = "No se pudo obtener información de enfrentamientos en línea."

    prompt = f"""
    Eres un analista deportivo experto. Analiza el siguiente partido de fútbol entre {home} (Local) y {away} (Visita).
    Historial reciente en BD local: {h2h_text}
    Historial LiveScore MCP: {livescore_h2h}
    Estadísticas de {home}: {h_stats}
    Estadísticas de {away}: {a_stats}
    Probabilidades: Gana {home}: {mk.get('Gana local', 0)*100:.1f}%, Empate: {mk.get('Empate', 0)*100:.1f}%, Gana {away}: {mk.get('Gana visita', 0)*100:.1f}%
    
    Escribe un análisis breve (máximo 150 palabras) en español. Menciona cómo el historial y las estadísticas afectan el partido. Concluye con un veredicto.
    """

    try:
        response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
        return response['message']['content']
    except Exception as e:
        return f"⚠️ Asegúrate de tener Ollama corriendo. Error: {e}"


def predict(db, league, sport, home, away):
    return predict_soccer(db, league, home, away)