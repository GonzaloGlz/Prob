from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from updater import conn, LEAGUES, run_all
from model import predict, analyze_with_ai, generate_parlays, generate_parlays_ai, simulate_full_markets

st.set_page_config(page_title="PROB", page_icon="🎯", layout="wide")
st.title("🎯 Parleysitos")
st.caption("Predicciones y análisis multi-deporte")

DEPORTES_NOMBRES = {
    "soccer": "⚽ Fútbol",
    "football": "🏈 Fútbol Americano",
    "basketball": "🏀 Básquetbol",
    "baseball": "⚾ Béisbol",
    "hockey": "🏒 Hockey",
    "tennis": "🎾 Tenis",
    "mma": "🥊 UFC / MMA"
}

def get_sport(league_data):
    try: return league_data[3]
    except IndexError: return "soccer"

deportes = sorted(list(set(get_sport(v) for v in LEAGUES.values())))
sel_deporte = st.sidebar.selectbox("Deporte", deportes, format_func=lambda x: DEPORTES_NOMBRES.get(x, x.capitalize()))

ligas_disponibles = [k for k, v in LEAGUES.items() if get_sport(v) == sel_deporte]
ALL = "🌎 Todas las ligas"
sel_liga = st.sidebar.selectbox("Liga", [ALL] + sorted(ligas_disponibles))

if st.sidebar.button("🔄 Actualizar datos"):
    with st.spinner("Descargando datos..."):
        run_all(log=lambda m: None)
    st.sidebar.success("¡Listo!")

db = conn()
ligas_sel = ligas_disponibles if sel_liga == ALL else [sel_liga]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
ph = ",".join("?" * len(ligas_sel))
up = db.execute(f"""SELECT * FROM matches WHERE league IN ({ph}) 
    AND state='pre' AND date>=? ORDER BY date LIMIT 100""", (*ligas_sel, now)).fetchall()

if not up:
    st.warning("No hay partidos próximos para este deporte.")
    st.stop()

tab_parley, tab_partidos, tab_analisis = st.tabs(["🎲 Generador de Parlays", "📋 Próximos Partidos", "🔍 Análisis Detallado"])

with tab_parley:
    st.subheader(f"🎲 Estrategias de Parley ({DEPORTES_NOMBRES.get(sel_deporte, sel_deporte)})")
    
    col1, col2, col3, col4 = st.columns(4)
    tipo_parley = col1.radio("Modalidad:", ["Mix (Diferentes ligas)", "Misma liga"], horizontal=True)
    min_prob = col2.slider("Prob. mínima por pick", 0.50, 0.85, 0.55, 0.01)
    min_legs = col3.slider("Mín. selecciones", 2, 5, 2)
    max_legs = col4.slider("Máx. selecciones", 3, 8, 5)
    
    if min_legs > max_legs:
        st.error("El mínimo de selecciones no puede ser mayor al máximo.")
        st.stop()

    mode = "mix" if "Mix" in tipo_parley else "same"

    sub_mate, sub_ai = st.tabs(["🎯 Parley Matemático", "🤖 Parley IA"])

    with sub_mate:
        parlays = generate_parlays(db, ligas_sel, sel_deporte, min_prob, 0.92, min_legs, max_legs, mode, 3)
        if not parlays: st.warning("No se encontraron suficientes picks.")
        else:
            for i, pl in enumerate(parlays, 1):
                prob_pct = pl["combined_prob"] * 100
                color = "🟢" if prob_pct >= 35 else "🟡" if prob_pct >= 20 else "🔴"
                with st.expander(f"{color} Parley #{i} · {pl['n_legs']} picks · Prob: {prob_pct:.1f}%", expanded=(i == 1)):
                    
                    # Mostramos la tabla base
                    rows = [{
                        "Partido": leg["match"], 
                        "Liga": leg["league"], 
                        "Mercado": leg["market"], 
                        "Prob. Pick": f"{leg['prob']*100:.1f}%", 
                        "Favorito": f"{leg['ganador']} ({leg['prob_ganador']})",
                        "Córners Prom.": leg["corners"],
                        "xG / Pts.": leg["xg"]
                    } for leg in pl["legs"]]
                    st.table(pd.DataFrame(rows))

                    # --- SIMULACIÓN DE MERCADOS ESTILO CASA DE APUESTAS ---
                    st.markdown("### 🔗 Detalle de Mercados por Partido")
                    
                    for leg in pl["legs"]:
                        local, visita = leg["match"].split(" vs ")
                        detalle_mercados = simulate_full_markets(db, leg["league"], sel_deporte, local, visita)
                        
                        if detalle_mercados:
                            with st.expander(f"📊 Mercados: {leg['match']}", expanded=False):
                                for titulo, opciones in detalle_mercados.items():
                                    st.markdown(f"**{titulo}**")
                                    cols = st.columns(len(opciones))
                                    for idx, (nombre, prob) in enumerate(opciones):
                                        # Creamos "botones" visuales rojos (simulados)
                                        cols[idx].markdown(
                                            f"<div style='background-color:#e31b23; color:white; padding:10px; border-radius:5px; text-align:center; margin-bottom:5px;'>"
                                            f"<b>{nombre}</b><br>"
                                            f"<span style='font-size:12px;'>{prob*100:.1f}%</span>"
                                            f"</div>", unsafe_allow_html=True
                                        )
                                    st.markdown("<br>", unsafe_allow_html=True)

    with sub_ai:
        if st.button("🧠 Generar Parley con IA"):
            with st.spinner("La IA está analizando..."):
                ai_parlays = generate_parlays_ai(db, ligas_sel, sel_deporte, min_prob, 0.95, min_legs, max_legs, mode)
            for res in ai_parlays:
                if "error" in res: st.error(res["error"])
                else:
                    st.markdown(f"### 🤖 Parley IA · {res['n_legs']} picks · Prob: **{res['combined_prob']*100:.1f}%**")
                    if res.get("veredicto"): st.info(res["veredicto"])
                    rows = [{
                        "Partido": pk["partido"], 
                        "Mercado": pk["mercado"], 
                        "Prob.": f"{pk['prob']}%", 
                        "Favorito": pk.get("ganador", "—"), 
                        "Razón": pk.get("razon", "")
                    } for pk in res["legs"]]
                    st.table(pd.DataFrame(rows))

with tab_partidos:
    st.subheader(f"Próximos Partidos · {sel_liga}")
    rows = []
    for m in up:
        p = predict(db, m["league"], sel_deporte, m["home"], m["away"])
        base = {"Partido": f"{m['date'][:16]} · {m['home']} vs {m['away']}", "Liga": m["league"]}
        if p:
            if sel_deporte == "soccer":
                base["Local"] = f"{p['markets'].get('Gana local', 0)*100:.0f}%"
                base["Empate"] = f"{p['markets'].get('Empate', 0)*100:.0f}%"
                base["Visita"] = f"{p['markets'].get('Gana visita', 0)*100:.0f}%"
            else:
                base["Gana Local"] = f"{p['markets'].get('Gana local', 0)*100:.0f}%"
                base["Gana Visita"] = f"{p['markets'].get('Gana visita', 0)*100:.0f}%"
        else: base["Nota"] = "Faltan datos"
        rows.append(base)
    st.dataframe(pd.DataFrame(rows).fillna("—"), use_container_width=True, hide_index=True)

with tab_analisis:
    labels = [f"{m['date'][:16]} · {m['home']} vs {m['away']}" for m in up]
    sel = st.selectbox("Selecciona un partido", labels)
    if sel:
        m = next(mm for mm in up if f"{mm['date'][:16]} · {mm['home']} vs {mm['away']}" == sel)
        p = predict(db, m["league"], sel_deporte, m["home"], m["away"])
        if not p: st.warning("Faltan datos.")
        else:
            st.markdown(f"## {m['home']} vs {m['away']}")
            t1, t2 = st.tabs(["📊 Probabilidades", "🤖 Análisis IA"])
            
            with t1:
                c1, c2, c3 = st.columns(3)
                c1.metric("Gana Local", f"{p['markets'].get('Gana local', 0)*100:.1f}%")
                if sel_deporte == "soccer":
                    c2.metric("Empate", f"{p['markets'].get('Empate', 0)*100:.1f}%")
                c3.metric("Gana Visita", f"{p['markets'].get('Gana visita', 0)*100:.1f}%")
                
                chart_data = {k: v for k, v in p["markets"].items() if k.startswith("Más de")}
                if chart_data: st.bar_chart(pd.Series(chart_data))
                
                if p["h_stats"]:
                    st.write("### Comparativa de Estadísticas")
                    dict_stats = {"corners": "Córners", "shots": "Tiros", "possession": "Posesión (%)", "xg": "xG"}
                    comp = [{"Estadística": label, m["home"]: p["h_stats"].get(k, "-"), m["away"]: p["a_stats"].get(k, "-")} for k, label in dict_stats.items()]
                    st.table(pd.DataFrame(comp).set_index("Estadística"))
            
            with t2:
                if st.button("🧠 Generar análisis IA", key=f"ai_{m['id']}"):
                    with st.spinner("La IA está analizando..."):
                        txt = analyze_with_ai(db, m["league"], m["home"], m["away"], p["h_stats"], p["a_stats"], p["markets"])
                    st.info(txt)