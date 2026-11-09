"""Database SQLite: solo fatti. Importi in centesimi interi, date ISO."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profilo_fiscale (
    id INTEGER PRIMARY KEY,
    valido_dal TEXT NOT NULL,
    data_inizio_attivita TEXT NOT NULL,
    gestione TEXT NOT NULL DEFAULT 'GS',
    aliquota_agevolata INTEGER NOT NULL DEFAULT 1,
    altra_copertura_previdenziale INTEGER NOT NULL DEFAULT 0,
    ateco_primario TEXT NOT NULL,
    piva TEXT, codice_fiscale TEXT, denominazione TEXT,
    codice_sede_inps TEXT, matricola_inps TEXT,
    creato_il TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cliente (
    id INTEGER PRIMARY KEY,
    denominazione TEXT NOT NULL,
    piva TEXT, codice_fiscale TEXT, codice_destinatario TEXT, pec TEXT,
    UNIQUE (piva, codice_fiscale)
);

CREATE TABLE IF NOT EXISTS fattura (
    id INTEGER PRIMARY KEY,
    numero TEXT NOT NULL,
    anno INTEGER NOT NULL,
    data TEXT NOT NULL,
    tipo TEXT NOT NULL,
    cliente_id INTEGER REFERENCES cliente(id),
    imponibile_cent INTEGER NOT NULL,
    rivalsa_cent INTEGER NOT NULL DEFAULT 0,
    bollo_cent INTEGER NOT NULL DEFAULT 0,
    bollo_addebitato INTEGER NOT NULL DEFAULT 0,
    totale_cent INTEGER NOT NULL,
    collegata_a TEXT,
    xml_sha256 TEXT NOT NULL UNIQUE,
    file_path TEXT,
    importata_il TEXT NOT NULL,
    annullata INTEGER NOT NULL DEFAULT 0,
    UNIQUE (anno, numero, tipo)
);

CREATE TABLE IF NOT EXISTS riga_fattura (
    id INTEGER PRIMARY KEY,
    fattura_id INTEGER NOT NULL REFERENCES fattura(id),
    n INTEGER NOT NULL,
    descrizione TEXT,
    quantita TEXT,
    prezzo_cent INTEGER,
    imponibile_cent INTEGER NOT NULL,
    ateco TEXT NOT NULL,
    UNIQUE (fattura_id, n)
);

CREATE TABLE IF NOT EXISTS incasso (
    id INTEGER PRIMARY KEY,
    fattura_id INTEGER NOT NULL REFERENCES fattura(id),
    data TEXT NOT NULL,
    importo_cent INTEGER NOT NULL,
    conto TEXT,
    note TEXT,
    annullato INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS anno_fiscale (
    anno INTEGER PRIMARY KEY,
    stato TEXT NOT NULL DEFAULT 'aperto',
    imposta_dichiarata_cent INTEGER,
    contributi_dichiarati_cent INTEGER,
    chiuso_il TEXT
);

CREATE TABLE IF NOT EXISTS versamento (
    id INTEGER PRIMARY KEY,
    scadenza_chiave TEXT NOT NULL,
    codice_tributo TEXT NOT NULL,
    sezione TEXT NOT NULL,
    anno_riferimento INTEGER NOT NULL,
    data TEXT NOT NULL,
    importo_cent INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS parametro (
    id INTEGER PRIMARY KEY,
    anno INTEGER NOT NULL,
    chiave TEXT NOT NULL,
    valore TEXT,
    valido_dal TEXT NOT NULL,
    nota TEXT,
    creato_il TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS registro_eventi (
    id INTEGER PRIMARY KEY,
    quando TEXT NOT NULL,
    azione TEXT NOT NULL,
    entita TEXT,
    entita_id INTEGER,
    prima_json TEXT,
    dopo_json TEXT
);
"""


def percorso_db() -> Path:
    return Path(os.environ.get("B3D_DB", Path(__file__).resolve().parents[2] / "dati" / "b3d-fisco.sqlite"))


def adesso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connetti(percorso: Path | None = None) -> sqlite3.Connection:
    p = percorso or percorso_db()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, detect_types=0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def transazione(con: sqlite3.Connection):
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def registra(con: sqlite3.Connection, azione: str, entita: str, entita_id: int | None, prima=None, dopo=None) -> None:
    con.execute(
        "INSERT INTO registro_eventi (quando, azione, entita, entita_id, prima_json, dopo_json) VALUES (?,?,?,?,?,?)",
        (adesso(), azione, entita, entita_id, json.dumps(prima, default=str) if prima is not None else None, json.dumps(dopo, default=str) if dopo is not None else None),
    )
