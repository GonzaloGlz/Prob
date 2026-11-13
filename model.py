"""Modelo con tolerancia a datos faltantes + Análisis de IA."""
import math
import ollama
from mcp_client import get_livescore_h2h  # NUEVO


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


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
    return _avg(gf), _avg(ga)


def get_team_stats(db, league, team, n=15):
    stats = {"corners": [], "shots": [], "shots_on_target": [], "passes": [], 
             "possession": [], "yellow_cards": [], "red_cards": [], "xg": []}
    rows = db.execute("""SELECT s.* FROM match_stats s
        JOIN matches m ON s.match_id = m.id
        WHERE m.league=? AND (m.home=? OR m.away=?) AND m.state='post' AND s.team=?
        ORDER BY m.date DESC LIMIT ?""", (league, team, team, team, n)).fetchall()
    
    for r in rows:
        # Convertimos a diccionario para evitar IndexError si falta una columna
        row_dict = dict(r)
        for k in stats.keys():
            if k in row_dict and row_dict[k] is not None:
                stats[k].append(row_dict[k])
                
    return {k: round(_avg(v), 2) for k, v in stats.items() if v}


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
            "home_logo": get_logo(db, home), "away_logo": get_logo(db, away)}


def analyze_with_ai(db, league, home, away, h_stats, a_stats, mk):
    """Genera un análisis usando Ollama + datos H2H de la BD + LiveScore MCP."""
    # 1. H2H desde la base de datos local
    h2h = db.execute("""SELECT date, home, away, home_score, away_score FROM matches 
        WHERE league=? AND state='post' AND 
        ((home=? AND away=?) OR (home=? AND away=?))
        ORDER BY date DESC LIMIT 5""", (league, home, away, away, home)).fetchall()

    h2h_text = ""
    for r in h2h:
        h2h_text += f"- {r['date'][:10]}: {r['home']} {r['home_score']} - {r['away_score']} {r['away']}\n"
    if not h2h_text:
        h2h_text = "No hay enfrentamientos directos recientes en la base de datos."

    # 2. H2H en línea desde LiveScore MCP
    livescore_h2h = get_livescore_h2h(home, away)
    if "no disponible" in livescore_h2h.lower():
        livescore_h2h = "No se pudo obtener información de enfrentamientos en línea."

    # 3. Construir el prompt
    prompt = f"""
    Eres un analista deportivo experto. Analiza el siguiente partido de fútbol entre {home} (Local) y {away} (Visita).

    Historial reciente en BD local (últimos 5 enfrentamientos directos):
    {h2h_text}

    Historial de enfrentamientos según LiveScore MCP (referencia en línea):
    {livescore_h2h}

    Estadísticas promedio de {home}: {h_stats}
    Estadísticas promedio de {away}: {a_stats}

    Probabilidades calculadas por un modelo matemático Poisson:
    - Gana {home}: {mk.get('Gana local', 0)*100:.1f}%
    - Empate: {mk.get('Empate', 0)*100:.1f}%
    - Gana {away}: {mk.get('Gana visita', 0)*100:.1f}%

    Basándote en estos datos, escribe un análisis breve (máximo 150 palabras) en español.
    Menciona cómo el historial directo y las estadísticas (córners, posesión, xG) afectan el partido.
    Concluye con un veredicto final sobre quién tiene ventaja y por qué.
    """

    # 4. Llamar a Ollama
    try:
        response = ollama.chat(model='llama3', messages=[
            {'role': 'user', 'content': prompt}
        ])
        return response['message']['content']
    except Exception as e:
        return f"⚠️ Asegúrate de tener Ollama instalado y corriendo (`ollama run llama3`). Error: {e}"


def predict(db, league, sport, home, away):
    return predict_soccer(db, league, home, away)