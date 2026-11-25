"""Importa el histórico real de delitos de la FGJ-CDMX (datos abiertos).

1. Descarga a mano el CSV "Víctimas en carpetas de investigación FGJ" (o
   "Carpetas de investigación FGJ de la Ciudad de México") desde
   https://datos.cdmx.gob.mx  (búscalo como "carpetas de investigación FGJ").
2. Ejecuta:
       python scripts/importar_fgj.py ruta/al/archivo.csv --desde 2023

Solo se conservan los delitos que afectan a peatones dentro del área del mapa.
Reemplaza cualquier importación anterior de la FGJ (los datos simulados se quedan
a menos que uses --sin-simulados).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rusac import config as C  # noqa: E402
from rusac.store import Store  # noqa: E402

PALABRAS_PEATON = [
    "transeunte", "peaton", "lesiones", "acoso sexual", "abuso sexual", "violacion",
    "homicidio", "secuestro", "privacion de la libertad", "robo de celular",
    "robo de objetos", "amenazas",
]
COLUMNAS = {
    "lat": ["latitud", "lat", "y"],
    "lon": ["longitud", "lon", "long", "x"],
    "hora": ["hora_hecho", "hora_hechos", "hora_inicio", "hora"],
    "fecha": ["fecha_hecho", "fecha_hechos", "fecha_inicio", "fecha"],
    "delito": ["delito", "categoria_delito", "categoria", "tipo_delito"],
}


def norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().strip()


def pick(header: list[str], options: list[str]) -> str | None:
    low = {norm(h): h for h in header}
    return next((low[o] for o in options if o in low), None)


def open_csv(path: Path):
    for enc in ("utf-8-sig", "latin-1"):
        try:
            f = path.open(encoding=enc, newline="")
            f.readline()
            f.seek(0)
            return f
        except UnicodeDecodeError:
            continue
    raise SystemExit("No pude leer el archivo (codificación desconocida).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--desde", type=int, default=2022, help="año mínimo del hecho (por defecto 2022)")
    ap.add_argument("--todos", action="store_true", help="no filtrar por tipo de delito")
    ap.add_argument("--sin-simulados", action="store_true", help="borra los incidentes simulados")
    args = ap.parse_args()

    if not C.GRAPH_FILE.exists():
        sys.exit("Primero descarga el mapa: python scripts/descargar_mapa.py")
    nodes = json.loads(C.GRAPH_FILE.read_text(encoding="utf-8"))["nodos"].values()
    lats = [n[0] for n in nodes]
    lons = [n[1] for n in nodes]
    s, n, w, e = min(lats), max(lats), min(lons), max(lons)

    rows, total, fuera, otro_delito, sin_dato = [], 0, 0, 0, 0
    with open_csv(args.csv) as f:
        reader = csv.DictReader(f)
        cols = {k: pick(reader.fieldnames or [], v) for k, v in COLUMNAS.items()}
        faltan = [k for k in ("lat", "lon", "hora") if not cols[k]]
        if faltan:
            sys.exit(f"No encontré las columnas {faltan}. Columnas del archivo: {reader.fieldnames}")
        for r in reader:
            total += 1
            try:
                lat, lon = float(r[cols["lat"]]), float(r[cols["lon"]])
                hora = int(str(r[cols["hora"]]).split(":")[0])
            except (TypeError, ValueError):
                sin_dato += 1
                continue
            if not (s <= lat <= n and w <= lon <= e) or not 0 <= hora <= 23:
                fuera += 1
                continue
            if cols["fecha"]:
                anio = str(r[cols["fecha"]])[:4]
                if anio.isdigit() and int(anio) < args.desde:
                    fuera += 1
                    continue
            delito = r.get(cols["delito"], "") if cols["delito"] else ""
            if not args.todos and not any(p in norm(delito) for p in PALABRAS_PEATON):
                otro_delito += 1
                continue
            rows.append((lat, lon, hora, delito[:120], "fgj"))

    store = Store(C.DB_FILE)
    if args.sin_simulados:
        store.add_incidents([], replace_source="simulado")
    store.add_incidents(rows, replace_source="fgj")
    print(f"Registros leídos: {total:,}")
    print(f"  importados (peatón, dentro del mapa, desde {args.desde}): {len(rows):,}")
    print(f"  fuera del área o del periodo: {fuera:,} · otro tipo de delito: {otro_delito:,} · sin coordenadas/hora: {sin_dato:,}")
    print("Reinicia el servidor para recalcular el riesgo histórico.")


if __name__ == "__main__":
    main()
