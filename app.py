from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from updater import conn, LEAGUES, run_all
from model import predict, analyze_with_ai, generate_parlays, generate_parlays_ai

st.set_page_config(page_title="PROB", page_icon="🎯", layout="wide")
st.title("🎯 Parleysitos")
st.caption("Predicciones según el Ing. Gonzalo y la IA")

ALL = "🌎 Todas las ligas"
sel_liga = st.sidebar.selectbox("Liga", [ALL] + sorted(list(LEAGUES.keys())))

if st.sidebar.button("🔄 Actualizar datos (3 APIs)"):
    with st.spinner("Descargando datos..."):
        run_all(log=lambda m: None)
    st.sidebar.success("¡Listo!")

db = conn()
ligas_sel = list(LEAGUES.keys()) if sel_liga == ALL else [sel_liga]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
ph = ",".join("?" * len(ligas_sel))
up = db.execute(f"""SELECT * FROM matches WHERE league IN ({ph}) 
    AND state='pre' AND date>=? ORDER BY date LIMIT 100""", (*ligas_sel, now)).fetchall()

if not up:
    st.warning("No hay partidos próximos.")
    st.stop()

# ===== PESTAÑAS PRINCIPALES =====
tab_parley, tab_partidos, tab_analisis = st.tabs([
    "🎲 Generador de Parlays",
    "📋 Próximos Partidos",
    "🔍 Análisis Detallado"
])

# ---------- PESTAÑA 1: GENERADOR DE PARLAYS ----------
with tab_parley:
    st.subheader("🎲 Dos Estrategias de Parley")

    col1, col2, col3 = st.columns(3)
    tipo_parley = col1.radio("Modalidad:", ["Mix (Diferentes ligas)", "Misma liga"], horizontal=True)
    min_prob = col2.slider("Prob. mínima por pick", 0.55, 0.85, 0.65, 0.01)
    max_legs = col3.slider("Máx. selecciones", 3, 8, 5)
    
    mode = "mix" if "Mix" in tipo_parley else "same"

    sub_mate, sub_ai = st.tabs(["🎯 Parley Matemático", "🤖 Parley IA"])

    # ---------- MATEMÁTICO ----------
    with sub_mate:
        st.caption(f"Combinaciones generadas solo con el modelo Poisson. Modo: {tipo_parley}.")
        with st.spinner("Calculando parleys matemáticos..."):
            parlays = generate_parlays(
                db, ligas_sel, min_prob=min_prob, max_prob=0.92,
                min_legs=3, max_legs=max_legs, mode=mode, top_n=3
            )
        if not parlays:
            st.warning("No se encontraron suficientes picks. Baja el mínimo o amplía ligas.")
        else:
            for i, pl in enumerate(parlays, 1):
                prob_pct = pl["combined_prob"] * 100
                color = "🟢" if prob_pct >= 35 else "🟡" if prob_pct >= 20 else "🔴"
                with st.expander(
                    f"{color} Parley Mate #{i} · {pl['n_legs']} picks · Prob: {prob_pct:.1f}%",
                    expanded=(i == 1)
                ):
                    # Tabla modificada con el Favorito
                    rows = [{
                        "Partido": leg["match"],
                        "Liga": leg["league"],
                        "Mercado (Pick)": leg["market"],
                        "Prob. Pick": f"{leg['prob']*100:.1f}%",
                        "Favorito a Ganar": f"{leg['ganador']} ({leg['prob_ganador']})"
                    } for leg in pl["legs"]]
                    st.table(pd.DataFrame(rows))
                    st.metric("Probabilidad combinada", f"{prob_pct:.1f}%")

    # ---------- IA ----------
    with sub_ai:
        st.caption("La IA elige y justifica sus picks. Requiere Ollama corriendo.")
        if st.button("🧠 Generar Parley con IA", key="btn_ai_parley"):
            with st.spinner("La IA está analizando los partidos... (~30-60s)"):
                ai_parlays = generate_parlays_ai(
                    db, ligas_sel, min_prob=min_prob, max_prob=0.95,
                    min_legs=3, max_legs=max_legs, mode=mode
                )
            if not ai_parlays:
                st.warning("La IA no devolvió picks válidos.")
            else:
                for res in ai_parlays:
                    if "error" in res:
                        st.error(res["error"])
                        continue
                    prob_pct = res["combined_prob"] * 100
                    st.markdown(f"### 🤖 Parley IA · {res['n_legs']} picks · Prob: **{prob_pct:.1f}%**")
                    if res.get("veredicto"):
                        st.info(res["veredicto"])
                    
                    # Tabla modificada con el Favorito
                    rows = [{
                        "Partido": pk["partido"],
                        "Mercado (Pick)": pk["mercado"],
                        "Prob. Pick": f"{pk['prob']}%",
                        "Favorito a Ganar": pk.get("ganador", "—"), 
                        "Razón": pk.get("razon", ""),
                    } for pk in res["legs"]]
                    st.table(pd.DataFrame(rows))
        else:
            st.info("Pulsa el botón para que la IA arme su propio parley (tarda ~30-60s).")

# ---------- PESTAÑA 2: PRÓXIMOS PARTIDOS ----------
with tab_partidos:
    st.subheader(f"Próximos Partidos · {sel_liga}")
    rows, preds = [], {}
    for m in up:
        p = predict(db, m["league"], "football", m["home"], m["away"])
        label = f"{m['date'][:16]} · {m['home']} vs {m['away']}"
        preds[label] = (p, m["league"], m["home"], m["away"])
        base = {"Partido": label, "Liga": m["league"]}
        if p:
            base["Local"] = f"{p['markets'].get('Gana local', 0)*100:.0f}%"
            base["Empate"] = f"{p['markets'].get('Empate', 0)*100:.0f}%"
            base["Visita"] = f"{p['markets'].get('Gana visita', 0)*100:.0f}%"
        else:
            base["Nota"] = "Faltan datos"
        rows.append(base)
    st.dataframe(pd.DataFrame(rows).fillna("—"), use_container_width=True, hide_index=True)

# ---------- PESTAÑA 3: ANÁLISIS DETALLADO ----------
with tab_analisis:
    labels = [f"{m['date'][:16]} · {m['home']} vs {m['away']}" for m in up]
    sel = st.selectbox("Selecciona un partido", labels)
    if sel:
        m = next(mm for mm in up if f"{mm['date'][:16]} · {mm['home']} vs {mm['away']}" == sel)
        p = predict(db, m["league"], "football", m["home"], m["away"])
        if not p:
            st.warning("Faltan datos para este partido.")
        else:
            st.markdown(f"## {m['home']} vs {m['away']}")

            t1, t2 = st.tabs(["📊 Probabilidades", "🤖 Análisis IA"])
            with t1:
                c1, c2, c3 = st.columns(3)
                c1.metric("Gana Local", f"{p['markets'].get('Gana local', 0)*100:.1f}%")
                c2.metric("Empate", f"{p['markets'].get('Empate', 0)*100:.1f}%")
                c3.metric("Gana Visita", f"{p['markets'].get('Gana visita', 0)*100:.1f}%")
                st.bar_chart(pd.Series({
                    "Goles > 1.5": p["markets"].get("Más de 1.5 goles", 0),
                    "Goles > 2.5": p["markets"].get("Más de 2.5 goles", 0),
                    "Goles > 3.5": p["markets"].get("Más de 3.5 goles", 0),
                    "Córners > 9.5": p["markets"].get("Más de 9.5 córners", 0),
                }))
                h, a = p["h_stats"], p["a_stats"]
                dict_stats = {
                    "corners": "Córners", "shots": "Tiros Totales", "shots_on_target": "Tiros a Puerta",
                    "possession": "Posesión (%)", "passes": "Pases", "xg": "Goles Esperados (xG)",
                    "yellow_cards": "Tarjetas Amarillas", "red_cards": "Tarjetas Rojas",
                }
                comp = []
                for key, label in dict_stats.items():
                    hv, av = h.get(key), a.get(key)
                    if hv is not None or av is not None:
                        comp.append({"Estadística": label, m["home"]: hv if hv is not None else "-", m["away"]: av if av is not None else "-"})
                if comp: st.table(pd.DataFrame(comp).set_index("Estadística"))
                else: st.info("No hay estadísticas detalladas disponibles.")
            with t2:
                st.caption("Basado en Head-to-Head y estadísticas promedio.")
                if st.button("🧠 Generar análisis IA", key=f"ai_{m['id']}"):
                    with st.spinner("La IA está analizando..."):
                        txt = analyze_with_ai(db, m["league"], m["home"], m["away"], p["h_stats"], p["a_stats"], p["markets"])
                    st.info(txt)
                else:
                    st.info("Pulsa el botón para generar el análisis (requiere Ollama corriendo).")