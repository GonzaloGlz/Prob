from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from updater import conn, LEAGUES, run_all
from model import predict, analyze_with_ai  # <-- Importamos la función IA

st.set_page_config(page_title="PROB", page_icon="", layout="wide")
st.title(" Probabilidades y Estadísticas ")
st.caption("Predicciones segun el Ing Gonzalo y la IA")

ALL = " Todas las ligas"
sel_liga = st.sidebar.selectbox("Liga", [ALL] + sorted(list(LEAGUES.keys())))

if st.sidebar.button(" Actualizar datos (3 APIs)"):
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

st.subheader(f"Próximos Partidos · {sel_liga}")
st.dataframe(pd.DataFrame(rows).fillna("—"), use_container_width=True, hide_index=True)

st.markdown("---")
sel = st.selectbox("Análisis detallado", list(preds))
if sel:
    p, liga, local, visita = preds[sel]
    if not p:
        st.warning("Faltan datos.")
    else:
        # --- CABECERA CON LOGOS ---
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            st.write(f"### {local}")
            if p.get("home_logo"): st.image(p["home_logo"], width=100)
        with col2:
            st.write("### vs")
        with col3:
            st.write(f"### {visita}")
            if p.get("away_logo"): st.image(p["away_logo"], width=100)

        # --- PESTAÑAS: MATEMÁTICAS VS IA ---
        tab1, tab2 = st.tabs([" Probabilidades Matemáticas", "🤖 Análisis de la IA"])
        
        with tab1:
            st.write("###  Probabilidades (Modelo Poisson)")
            c1, c2, c3 = st.columns(3)
            c1.metric("Gana Local", f"{p['markets'].get('Gana local', 0)*100:.1f}%")
            c2.metric("Empate", f"{p['markets'].get('Empate', 0)*100:.1f}%")
            c3.metric("Gana Visita", f"{p['markets'].get('Gana visita', 0)*100:.1f}%")
            
            st.bar_chart(pd.Series({
                "Goles > 1.5": p['markets'].get('Más de 1.5 goles', 0),
                "Goles > 2.5": p['markets'].get('Más de 2.5 goles', 0),
                "Goles > 3.5": p['markets'].get('Más de 3.5 goles', 0),
                "Córners > 9.5": p['markets'].get('Más de 9.5 córners', 0)
            }))

            st.write("###  Comparativa de Promedios")
            h, a = p["h_stats"], p["a_stats"]
            dict_stats = {
                "corners": "Córners", "shots": "Tiros Totales", "shots_on_target": "Tiros a Puerta",
                "possession": "Posesión (%)", "passes": "Pases", "xg": "Goles Esperados (xG)",
                "yellow_cards": "Tarjetas Amarillas", "red_cards": "Tarjetas Rojas"
            }
            
            comp_data = []
            for key, label in dict_stats.items():
                h_val = h.get(key)
                a_val = a.get(key)
                if h_val is not None or a_val is not None:
                    comp_data.append({
                        "Estadística": label,
                        f"{local}": h_val if h_val is not None else "-",
                        f"{visita}": a_val if a_val is not None else "-"
                    })
            
            if comp_data:
                st.table(pd.DataFrame(comp_data).set_index("Estadística"))
            else:
                st.info("No hay estadísticas detalladas disponibles.")

        with tab2:
            st.write("### Análisis Generado por IA")
            st.caption("Basado en el historial Head-to-Head y las estadísticas promedio.")
            with st.spinner("La IA está analizando el partido..."):
                ai_analysis = analyze_with_ai(db, liga, local, visita, p["h_stats"], p["a_stats"], p["markets"])
                st.info(ai_analysis)