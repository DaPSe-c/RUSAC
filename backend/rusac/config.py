"""Configuración central del MVP de RUSAC.

Todos los parámetros ajustables del modelo de riesgo y de las reglas de
reportes viven aquí, para que el equipo pueda calibrarlos sin tocar la lógica.
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("RUSAC_DATA_DIR", ROOT / "data"))
FRONTEND_DIR = ROOT / "frontend"

GRAPH_FILE = DATA_DIR / "grafo.json"   # generado por scripts/descargar_mapa.py
DB_FILE = DATA_DIR / "rusac.sqlite3"   # reportes e histórico de incidentes

TZ = ZoneInfo("America/Mexico_City")

# --- Puntos de interés (respaldo) -------------------------------------------
# Coordenadas APROXIMADAS. scripts/descargar_mapa.py intenta obtener las reales
# de OpenStreetMap y las guarda en grafo.json; estas solo se usan si falla.
POIS_RESPALDO = {
    "escom": {"nombre": "ESCOM (IPN)", "lat": 19.50460, "lon": -99.14680},
    "metro_politecnico": {"nombre": "Metro Politécnico", "lat": 19.50080, "lon": -99.14910},
    "metro_lindavista": {"nombre": "Metro Lindavista", "lat": 19.48790, "lon": -99.13470},
}

# --- Caminata ----------------------------------------------------------------
VELOCIDAD_CAMINATA_M_MIN = 78.0  # ~4.7 km/h

# --- Modelo de riesgo --------------------------------------------------------
# riesgo(calle, hora) = W_HIST·histórico + W_EST·estructura + W_REP·reportes  (recortado a 0..1)
W_HIST = 0.55
W_EST = 0.20
W_REP = 0.60

RADIO_INCIDENTE_M = 60.0     # un incidente histórico "pesa" sobre calles a menos de esto
SUAVIZADO_HORAS = 2          # cada incidente también cuenta ±2 h alrededor de su hora
PERCENTIL_NORMALIZACION = 95 # densidad en este percentil = riesgo histórico 1.0

HORAS_NOCHE = set(range(20, 24)) | set(range(0, 6))

# Riesgo "estructural": calles chicas, andadores o callejones son peores de noche.
RIESGO_ESTRUCTURAL = {
    "trunk": 0.0, "primary": 0.0, "secondary": 0.05, "tertiary": 0.15,
    "residential": 0.30, "living_street": 0.30, "unclassified": 0.35,
    "pedestrian": 0.20, "service": 0.55, "footway": 0.45, "path": 0.60,
    "steps": 0.50, "track": 0.70, "alley": 0.70,
}
RIESGO_ESTRUCTURAL_DEFECTO = 0.30
FACTOR_ESTRUCTURAL_DIA = 0.3  # de día, la estructura de la calle pesa menos

# Parámetro α de la fórmula de costo: costo = longitud × (1 + α·riesgo)
ALFA_DEFECTO = 4.0
ALFA_MAX = 10.0

# --- Reportes comunitarios ---------------------------------------------------
# peso: aporte al riesgo (negativo = la calle se vuelve más segura)
# vida_min: τ del decaimiento exponencial e^(-t/τ); expira a las 3τ.
# bloquea: la calle queda excluida del cálculo de rutas
CATEGORIAS = {
    "alumbrado": {"nombre": "Falta de alumbrado", "icono": "💡", "peso": 0.45, "vida_min": 60 * 24 * 3, "bloquea": False},
    "sospechosa": {"nombre": "Actividad sospechosa", "icono": "👀", "peso": 0.90, "vida_min": 45, "bloquea": False},
    "patrulla": {"nombre": "Patrulla presente", "icono": "🚓", "peso": -0.35, "vida_min": 30, "bloquea": False},
    "cerrada": {"nombre": "Calle cerrada", "icono": "🚧", "peso": 0.0, "vida_min": 120, "bloquea": True},
}
RADIO_REPORTE_M = 70.0             # radio de influencia de un reporte sobre las calles
FACTOR_SIN_VERIFICAR = 0.5         # un reporte sin confirmar pesa la mitad
CONFIRMACIONES_PARA_VERIFICAR = 2  # confirmaciones de otras sesiones
DISTANCIA_MAX_REPORTE_M = 150.0    # el reporte debe hacerse cerca de donde estás
LIMITE_REPORTES = 3                # máximo de reportes por sesión...
VENTANA_LIMITE_MIN = 10            # ...en esta ventana de tiempo

# Semáforo
UMBRAL_AMARILLO = 0.33
UMBRAL_ROJO = 0.66
