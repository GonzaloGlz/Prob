"""Modelo matemático y de IA para múltiples deportes."""
import math
import json
import re
from datetime import datetime, timezone
from collections import defaultdict
import ollama
from mcp_client import get_livescore_h2h

def _avg(xs): return sum(xs) / len(xs) if xs else 0.0

def _weighted_avg(xs, decay=0.92):
    if not xs: return 0.0
    weights = [decay ** i for i in range(len(xs))]
    return sum(x * w for x, w in zip(xs, weights)) / sum(weights)

def pmf(k, lam):
    lam = max(lam, 0.05)
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))

def p_over_poisson(lam, line):
    return 1 - sum(pmf(i, lam) for i in range(int(math.floor(line)) + 1))

def get_logo(db, team):
    row = db.execute("SELECT logo_url FROM team_logos WHERE team_name=?", (team,)).fetchone()
    return row["logo_url"] if row else None

# ===== MODELO PARA FÚTBOL =====
def _team_goals(db, league, team, n=15):
    rows = db.execute("""SELECT home, away, home_score, away_score FROM matches 
        WHERE league=? AND state='post' AND home_score IS NOT NULL 
        ORDER BY date DESC LIMIT 300""", (league,)).fetchall()
    gf, ga = [], []
    for r in rows:
        if r["home"] == team: gf.append(r["home_score"]); ga.append(r["away_score"])
        elif r["away"] == team: gf.append(r["away_score"]); ga.append(r["home_score"])
        if len(gf) >= n: break
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
    for line in (1.5, 2.5, 3.5): mk[f"Más de {line} goles"] = p_over_poisson(tot, line)
    
    h_stats = get_team_stats(db, league, home)
    a_stats = get_team_stats(db, league, away)
    if "corners" in h_stats and "corners" in a_stats:
        total_c = h_stats["corners"] + a_stats["corners"]
        for line in (8.5, 9.5, 10.5): mk[f"Más de {line} córners"] = p_over_poisson(total_c, line)
        
    return {"markets": mk, "expected": {"Goles esperados": f"{lh:.2f} - {la:.2f}"}, "h_stats": h_stats, "a_stats": a_stats, "home_logo": get_logo(db, home), "away_logo": get_logo(db, away)}

# ===== MODELO PARA OTROS DEPORTES =====
def predict_generic(db, league, sport, home, away):
    def get_scores(team):
        rows = db.execute("""SELECT home, away, home_score, away_score FROM matches 
            WHERE league=? AND state='post' AND home_score IS NOT NULL AND (home=? OR away=?)
            ORDER BY date DESC LIMIT 15""", (league, team, team)).fetchall()
        for_pts, against_pts = [], []
        for r in rows:
            if r["home"] == team: for_pts.append(r["home_score"]); against_pts.append(r["away_score"])
            else: for_pts.append(r["away_score"]); against_pts.append(r["home_score"])
        return _weighted_avg(for_pts), _weighted_avg(against_pts)

    h_for, h_against = get_scores(home)
    a_for, a_against = get_scores(away)
    lg_avg = db.execute("SELECT AVG(home_score), AVG(away_score) FROM matches WHERE league=? AND state='post'", (league,)).fetchone()
    default_score = (lg_avg[0] + lg_avg[1]) / 2 if lg_avg[0] and lg_avg[1] else 20.0
    if h_for == 0: h_for = default_score
    if a_for == 0: a_for = default_score

    exp_h = (h_for + a_against) / 2
    exp_a = (a_for + h_against) / 2
    diff = exp_h - exp_a

    sd = 13.5 if sport == "football" else 10.0 if sport == "basketball" else 1.5 if sport == "baseball" else 2.0
    p_h = 0.5 * (1 + math.erf(diff / (sd * math.sqrt(2))))
    p_a = 1 - p_h
    p_d = 0.0 if sport in ["basketball", "football"] else 0.05

    mk = {"Gana local": p_h, "Empate": p_d, "Gana visita": p_a}
    tot = exp_h + exp_a
    
    if sport == "football": lines = [35.5, 40.5, 45.5, 50.5, 55.5]
    elif sport == "basketball": lines = [200.5, 210.5, 220.5, 230.5, 240.5]
    elif sport == "baseball": lines = [6.5, 7.5, 8.5, 9.5, 10.5]
    else: lines = [5.5, 6.5, 7.5, 8.5]

    for line in lines:
        p_over = 1 - 0.5 * (1 + math.erf((line - tot) / (sd * math.sqrt(2))))
        mk[f"Más de {line} puntos"] = p_over

    return {"markets": mk, "expected": {"Puntos esperados": f"{exp_h:.1f} - {exp_a:.1f}"}, "h_stats": {}, "a_stats": {}, "home_logo": get_logo(db, home), "away_logo": get_logo(db, away)}

def predict_individual(db, league, sport, home, away):
    h2h = db.execute("""SELECT home, away, home_score, away_score FROM matches 
        WHERE league=? AND state='post' AND ((home=? AND away=?) OR (home=? AND away=?))""",
        (league, home, away, away, home)).fetchall()
    h_wins = sum(1 for r in h2h if (r["home"] == home and r["home_score"] > r["away_score"]) or (r["away"] == home and r["away_score"] > r["home_score"]))
    a_wins = len(h2h) - h_wins
    p_h = (h_wins + 1) / (len(h2h) + 2) if len(h2h) > 0 else 0.50
    p_a = 1 - p_h
    mk = {"Gana local": p_h, "Empate": 0.0, "Gana visita": p_a}
    return {"markets": mk, "expected": {"Ganador": "Basado en H2H"}, "h_stats": {}, "a_stats": {}, "home_logo": get_logo(db, home), "away_logo": get_logo(db, away)}

def predict(db, league, sport, home, away):
    if sport == "soccer": return predict_soccer(db, league, home, away)
    elif sport in ["tennis", "mma"]: return predict_individual(db, league, sport, home, away)
    else: return predict_generic(db, league, sport, home, away)

# ===== SIMULADOR DE MERCADOS DE APUESTAS =====
def simulate_full_markets(db, league, sport, home, away):
    if sport != "soccer":
        return {} 
        
    p = predict_soccer(db, league, home, away)
    lh = p.get("lambda_home", 1.2)
    la = p.get("lambda_away", 1.0)
    mk = p["markets"]
    h_stats, a_stats = p["h_stats"], p["a_stats"]
    
    markets = {}
    markets["Ganador (1X2)"] = [(f"Gana {home}", mk.get("Gana local", 0)), ("Empate", mk.get("Empate", 0)), (f"Gana {away}", mk.get("Gana visita", 0))]
    
    p_h, p_d, p_a = mk.get("Gana local", 0), mk.get("Empate", 0), mk.get("Gana visita", 0)
    markets["Doble Oportunidad"] = [(f"{home} o Empate", p_h + p_d), (f"{away} o Empate", p_a + p_d), (f"{home} o {away}", p_h + p_a)]
    
    tot = lh + la
    markets["Total de Goles"] = [("Más de 1.5", p_over_poisson(tot, 1.5)), ("Más de 2.5", p_over_poisson(tot, 2.5)), ("Más de 3.5", p_over_poisson(tot, 3.5)), ("Menos de 1.5", 1 - p_over_poisson(tot, 1.5)), ("Menos de 2.5", 1 - p_over_poisson(tot, 2.5))]
    
    p_ambos = (1 - pmf(0, lh)) * (1 - pmf(0, la))
    markets["Multigoles (Ambos Marcan)"] = [("Sí", p_ambos), ("No", 1 - p_ambos)]
    
    h_c, a_c = h_stats.get("corners", 5.0), a_stats.get("corners", 4.5)
    tot_c = h_c + a_c
    markets["Total de Córners"] = [("Más de 8.5", p_over_poisson(tot_c, 8.5)), ("Más de 9.5", p_over_poisson(tot_c, 9.5)), ("Más de 10.5", p_over_poisson(tot_c, 10.5)), ("Menos de 10.5", 1 - p_over_poisson(tot_c, 10.5))]
    
    p_par = sum(pmf(i, tot_c) for i in range(0, 30, 2))
    markets["Córners Par/Impar"] = [("Par", p_par), ("Impar", 1 - p_par)]

    return markets

# ===== IA PARA JUSTIFICAR MERCADOS =====
def analyze_markets_with_ai(home, away, markets_dict, h_stats, a_stats):
    """La IA lee los números calculados por Poisson y da su veredicto."""
    
    # Formatear los mercados a texto para pasárselos a la IA
    texto_mercados = ""
    for titulo, opciones in markets_dict.items():
        texto_mercados += f"- {titulo}: "
        texto_mercados += ", ".join([f"{nom} ({prob*100:.1f}%)" for nom, prob in opciones])
        texto_mercados += "\n"

    prompt = f"""
    Eres un analista experto en apuestas deportivas. Analiza el partido {home} vs {away}.
    
    Estos son los datos estadísticos y las probabilidades calculadas por un modelo matemático:
    - Córners promedio de {home}: {h_stats.get('corners', 'N/A')}
    - Córners promedio de {away}: {a_stats.get('corners', 'N/A')}
    - Goles esperados (xG): {h_stats.get('xg', 'N/A')} vs {a_stats.get('xg', 'N/A')}
    
    MERCADOS DISPONIBLES Y SUS PROBABILIDADES:
    {texto_mercados}
    
    Instrucciones:
    1. Elige los 2 o 3 mercados que TÚ consideres con mejor valor (los más seguros o con mejor relación riesgo/beneficio).
    2. Justifica tu elección en base a los datos de córners, goles o tendencias que ves.
    3. Escribe tu respuesta en un párrafo corto, directo y en español (máximo 100 palabras).
    """
    try:
        response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}], options={"temperature": 0.3})
        return response['message']['content']
    except Exception as e: 
        return f"⚠️ Error IA: {e}"

# =========================================================
# GENERADORES DE PARLEYS (MATEMÁTICO Y IA)
# =========================================================
def generate_parlays(db, leagues, sport, min_prob=0.65, max_prob=0.92, min_legs=2, max_legs=8, mode="mix", top_n=3):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    ph = ",".join("?" * len(leagues))
    matches = db.execute(f"""SELECT * FROM matches WHERE league IN ({ph}) 
        AND state='pre' AND date>=? ORDER BY date LIMIT 200""", (*leagues, now)).fetchall()

    all_picks = []
    for m in matches:
        p = predict(db, m["league"], sport, m["home"], m["away"])
        if not p: continue
        
        prob_h = p["markets"].get("Gana local", 0)
        prob_a = p["markets"].get("Gana visita", 0)
        
        if prob_h > prob_a: pick_team, pick_prob, pick_market = m["home"], prob_h, "Gana local"
        else: pick_team, pick_prob, pick_market = m["away"], prob_a, "Gana visita"

        h_corners = p["h_stats"].get("corners", "-") if p["h_stats"] else "-"
        a_corners = p["a_stats"].get("corners", "-") if p["a_stats"] else "-"
        xg_text = p["expected"].get("Goles esperados", p["expected"].get("Puntos esperados", "-"))

        all_picks.append({
            "match": f"{m['home']} vs {m['away']}", "league": m["league"], "date": m["date"][:16],
            "market": pick_market, "prob": pick_prob, "match_id": m["id"],
            "ganador": pick_team, "prob_ganador": f"{pick_prob*100:.1f}%",
            "xg": xg_text, "corners": f"{h_corners} - {a_corners}"
        })

    if not all_picks: return []
    filtered_picks = [p for p in all_picks if min_prob <= p["prob"] <= max_prob]
    if len(filtered_picks) < min_legs: filtered_picks = sorted(all_picks, key=lambda x: -x["prob"])
    filtered_picks.sort(key=lambda x: -x["prob"])

    by_match = defaultdict(list)
    for pk in filtered_picks: by_match[pk["match_id"]].append(pk)
    diversified = [picks[0] for picks in by_match.values()]
    diversified.sort(key=lambda x: -x["prob"])

    parlays = []
    if mode == "same":
        by_league = defaultdict(list)
        for pk in diversified: by_league[pk["league"]].append(pk)
        for lg, picks in by_league.items():
            if len(picks) >= min_legs:
                legs = picks[:max_legs]
                combo = 1.0
                for l in legs: combo *= l["prob"]
                parlays.append({"legs": legs, "combined_prob": combo, "n_legs": len(legs)})
    else:
        for target_legs in range(max_legs, min_legs - 1, -1):
            legs, combo_prob, local_used = [], 1.0, set()
            for pk in diversified:
                if pk["match_id"] in local_used: continue
                legs.append(pk); local_used.add(pk["match_id"]); combo_prob *= pk["prob"]
                if len(legs) >= target_legs: break
            if len(legs) >= min_legs: parlays.append({"legs": legs, "combined_prob": combo_prob, "n_legs": len(legs)})
            if len(parlays) >= top_n: break

    parlays.sort(key=lambda x: -x["combined_prob"])
    return parlays[:top_n]

def generate_parlays_ai(db, leagues, sport, min_prob=0.60, max_prob=0.95, min_legs=2, max_legs=6, mode="mix"):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    ph = ",".join("?" * len(leagues))
    matches = db.execute(f"""SELECT * FROM matches WHERE league IN ({ph}) 
        AND state='pre' AND date>=? ORDER BY date LIMIT 80""", (*leagues, now)).fetchall()
    if not matches: return []

    candidatos = []
    for m in matches:
        p = predict(db, m["league"], sport, m["home"], m["away"])
        if not p: continue
        prob_h, prob_a = p["markets"].get("Gana local", 0), p["markets"].get("Gana visita", 0)
        if prob_h > prob_a: pick_team, pick_prob, pick_market = m["home"], prob_h, "Gana local"
        else: pick_team, pick_prob, pick_market = m["away"], prob_a, "Gana visita"

        h_c = p["h_stats"].get("corners", "-") if p["h_stats"] else "-"
        a_c = p["a_stats"].get("corners", "-") if p["a_stats"] else "-"
        candidatos.append({
            "match": f"{m['home']} vs {m['away']}", "league": m["league"], "date": m["date"][:16],
            "equipo_favorito": pick_team, "mercado": pick_market, "prob": round(pick_prob * 100, 1),
            "marcador_esperado": p["expected"], "corners_promedio": f"Local: {h_c}, Visita: {a_c}"
        })
    if len(candidatos) < min_legs: return []

    contexto = json.dumps(candidatos[:40], ensure_ascii=False, indent=2)
    instruccion_liga = "NO repitas el mismo partido más de una vez." if mode == "mix" else "TODOS los picks DEBEN SER DE LA MISMA LIGA."

    prompt = f"""Eres un analista deportivo experto. Te doy una lista de equipos favoritos.
PARTIDOS DISPONIBLES:
{contexto}

TAREA:
1. Elige {min_legs} a {max_legs} equipos que GANARÁN.
2. {instruccion_liga}
3. Justifica cada pick en 1-2 líneas mencionando datos concretos.

RESPONDE EXACTAMENTE EN ESTE FORMATO JSON:
{{
  "picks": [ {{"partido": "Local vs Visita", "equipo_favorito": "Nombre", "mercado": "Gana local", "prob": 78.2, "razon": "..."}} ],
  "prob_combinada": 32.5,
  "veredicto": "..."
}}
"""
    try:
        response = ollama.chat(model="llama3", messages=[{"role": "user", "content": prompt}], options={"temperature": 0.1})
        raw = response["message"]["content"].strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match: return [{"error": "La IA no devolvió JSON."}]
        
        data = json.loads(match.group(0))
        picks = data.get("picks", [])
        if len(picks) < min_legs: return [{"error": "Pocos picks."}]

        probs = [p["prob"] / 100 for p in picks if isinstance(p.get("prob"), (int, float))]
        combinada = 1.0
        for pr in probs: combinada *= pr
        for pk in picks: pk["ganador"] = pk.get("equipo_favorito", "—")

        return [{"legs": picks, "combined_prob": combinada, "n_legs": len(picks), "veredicto": data.get("veredicto", ""), "fuente": "IA"}]
    except Exception as e: return [{"error": f"Error de IA: {e}"}]

def analyze_with_ai(db, league, home, away, h_stats, a_stats, mk):
    prompt = f"""
    Eres un analista deportivo experto. Analiza el siguiente partido: {home} vs {away}.
    Estadísticas: {home}: {h_stats} | {away}: {a_stats}
    Probabilidades: Gana {home}: {mk.get('Gana local', 0)*100:.1f}%, Empate: {mk.get('Empate', 0)*100:.1f}%, Gana {away}: {mk.get('Gana visita', 0)*100:.1f}%
    
    Escribe un análisis breve (máximo 150 palabras) en español. Concluye con un veredicto.
    """
    try:
        response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
        return response['message']['content']
    except Exception as e: return f"⚠️ Asegúrate de tener Ollama corriendo. Error: {e}"