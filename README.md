# Probabilidades deportivas

## Instalación (Windows/Linux/Mac, Python 3.9+)
    pip install -r requirements.txt
    python updater.py --backfill 45     # primera carga de historial (varios minutos)
    streamlit run app.py                # abre el dashboard en el navegador

## Actualización diaria
Opción A (simple):  python updater.py --loop 06:00   (dejar corriendo)
Opción B (mejor):   Programador de tareas de Windows / cron ejecutando `python updater.py` cada día.

## Qué calcula
- Fútbol: gana local/empate/visita, más de 1.5/2.5/3.5 goles, ambos anotan, marcador probable,
  córners totales (8.5–11.5) y tiros a puerta (modelo Poisson con forma de los últimos 10 partidos).
- NFL/NBA/NHL/MLB: probabilidad de ganar y de más/menos puntos totales (modelo normal).

## Fuente de datos
API pública de ESPN (sin API key, no oficial: puede cambiar sin aviso).
Para usar API-Football (100 pet/día gratis) habría que añadir un proveedor en updater.py.

Las probabilidades son estimaciones estadísticas, no garantías.
