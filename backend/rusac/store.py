"""Almacenamiento en SQLite: histórico de incidentes y reportes comunitarios.

Privacidad: no se guarda ninguna ubicación del usuario ni sus rutas. De cada
reporte solo se guarda el punto del incidente y un hash de la sesión (con una
sal aleatoria que cambia cada vez que arranca el servidor), que sirve para el
límite de reportes y para que nadie confirme su propio reporte.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidentes (
    id INTEGER PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    hora INTEGER NOT NULL CHECK (hora BETWEEN 0 AND 23),
    categoria TEXT,
    fuente TEXT NOT NULL            -- 'fgj' (datos abiertos) o 'simulado'
);
CREATE TABLE IF NOT EXISTS reportes (
    id INTEGER PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    categoria TEXT NOT NULL,
    creado REAL NOT NULL,           -- epoch (s)
    sesion_hash TEXT NOT NULL,
    fuente TEXT NOT NULL DEFAULT 'usuario'
);
CREATE TABLE IF NOT EXISTS confirmaciones (
    reporte_id INTEGER NOT NULL REFERENCES reportes(id) ON DELETE CASCADE,
    sesion_hash TEXT NOT NULL,
    creado REAL NOT NULL,
    PRIMARY KEY (reporte_id, sesion_hash)
);
CREATE INDEX IF NOT EXISTS ix_reportes_creado ON reportes(creado);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._salt = secrets.token_bytes(16)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._db.commit()
        self.version = 0  # cambia con cada reporte/confirmación (sirve de caché)

    def hash_session(self, session: str) -> str:
        return hashlib.sha256(self._salt + session.encode()).hexdigest()[:32]

    # --- incidentes históricos ----------------------------------------------
    def incidents(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT lat, lon, hora, categoria, fuente FROM incidentes").fetchall()

    def add_incidents(self, rows: list[tuple[float, float, int, str, str]], replace_source: str | None = None) -> None:
        with self._lock:
            if replace_source:
                self._db.execute("DELETE FROM incidentes WHERE fuente = ?", (replace_source,))
            self._db.executemany(
                "INSERT INTO incidentes (lat, lon, hora, categoria, fuente) VALUES (?, ?, ?, ?, ?)", rows
            )
            self._db.commit()

    def incident_sources(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT fuente, COUNT(*) n FROM incidentes GROUP BY fuente").fetchall()
        return {r["fuente"]: r["n"] for r in rows}

    # --- reportes -----------------------------------------------------------
    def add_report(self, lat: float, lon: float, category: str, session_hash: str,
                   now: float | None = None, source: str = "usuario") -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO reportes (lat, lon, categoria, creado, sesion_hash, fuente) VALUES (?, ?, ?, ?, ?, ?)",
                (lat, lon, category, now or time.time(), session_hash, source),
            )
            self._db.commit()
            self.version += 1
            return int(cur.lastrowid)

    def recent_report_count(self, session_hash: str, since: float) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM reportes WHERE sesion_hash = ? AND creado >= ?", (session_hash, since)
            ).fetchone()[0]

    def get_report(self, report_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute("SELECT * FROM reportes WHERE id = ?", (report_id,)).fetchone()

    def confirm(self, report_id: int, session_hash: str, now: float | None = None) -> bool:
        """Devuelve False si esa sesión ya había confirmado."""
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO confirmaciones (reporte_id, sesion_hash, creado) VALUES (?, ?, ?)",
                    (report_id, session_hash, now or time.time()),
                )
            except sqlite3.IntegrityError:
                return False
            self._db.commit()
            self.version += 1
            return True

    def reports_since(self, since: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                """SELECT r.id, r.lat, r.lon, r.categoria, r.creado, r.fuente,
                          (SELECT COUNT(*) FROM confirmaciones c WHERE c.reporte_id = r.id) AS confirmaciones
                   FROM reportes r WHERE r.creado >= ? ORDER BY r.creado""",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def purge_reports_before(self, before: float) -> int:
        with self._lock:
            n = self._db.execute("DELETE FROM reportes WHERE creado < ?", (before,)).rowcount
            self._db.commit()
            if n:
                self.version += 1
            return n

    def clear_reports(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM reportes")
            self._db.commit()
            self.version += 1
