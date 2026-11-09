"""API REST del cruscotto fiscale (prefisso /api/v1). Importi in centesimi interi."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.importatore.fatturapa import FatturaImportata, leggi_file
from backend.motore import ParametroMancante, quota_da_accantonare

from . import servizi
from .db import adesso, connetti, registra, transazione

app = FastAPI(title="B3D Lab · Cruscotto fiscale", version="0.1.0")
CON = connetti()
ANTEPRIME: dict[str, list[FatturaImportata]] = {}
CARTELLA_XML = Path(os.environ.get("B3D_XML", Path(__file__).resolve().parents[2] / "dati" / "xml"))


# ------------------------------------------------------------------ errori


@app.exception_handler(ParametroMancante)
async def _parametro_mancante(_: Request, e: ParametroMancante):
    return JSONResponse({"errore": str(e), "chiave": e.chiave, "anno": e.anno}, status_code=422)


@app.exception_handler(servizi.ProfiloAssente)
async def _profilo_assente(_: Request, e: Exception):
    return JSONResponse({"errore": str(e)}, status_code=409)


# ------------------------------------------------------------------ accesso
# Una sola password (B3D_PASSWORD) per l'utente; un token separato in sola lettura (B3D_TOKEN_LETTURA) per n8n.


def _token_da_password(pw: str) -> str:
    return hashlib.sha256(("b3d-fisco:" + pw).encode()).hexdigest()


def autorizza(request: Request, authorization: str = Header(default="")) -> None:
    password = os.environ.get("B3D_PASSWORD")
    if not password:
        return  # sviluppo locale senza password
    token = authorization.removeprefix("Bearer ").strip()
    if hmac.compare_digest(token, _token_da_password(password)):
        return
    lettura = os.environ.get("B3D_TOKEN_LETTURA")
    if lettura and request.method == "GET" and hmac.compare_digest(token, lettura):
        return
    raise HTTPException(401, "Accesso non autorizzato")


class Login(BaseModel):
    password: str


@app.post("/api/v1/auth/login")
def login(dati: Login):
    password = os.environ.get("B3D_PASSWORD")
    if password and not hmac.compare_digest(dati.password, password):
        raise HTTPException(401, "Password errata")
    return {"token": _token_da_password(dati.password) if password else "sviluppo"}


protetto = [Depends(autorizza)]


# ------------------------------------------------------------------ profilo


class ProfiloIn(BaseModel):
    valido_dal: date
    data_inizio_attivita: date
    ateco_primario: str
    aliquota_agevolata: bool = True
    altra_copertura_previdenziale: bool = False
    gestione: str = "GS"
    piva: str | None = None
    codice_fiscale: str | None = None
    denominazione: str | None = None
    codice_sede_inps: str | None = None
    matricola_inps: str | None = None


@app.get("/api/v1/profilo", dependencies=protetto)
def leggi_profilo():
    return dict(servizi.profilo_riga(CON))


@app.put("/api/v1/profilo", dependencies=protetto)
def salva_profilo(p: ProfiloIn):
    with transazione(CON):
        cur = CON.execute(
            """INSERT INTO profilo_fiscale (valido_dal, data_inizio_attivita, gestione, aliquota_agevolata,
               altra_copertura_previdenziale, ateco_primario, piva, codice_fiscale, denominazione,
               codice_sede_inps, matricola_inps, creato_il) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p.valido_dal.isoformat(), p.data_inizio_attivita.isoformat(), p.gestione, int(p.aliquota_agevolata),
             int(p.altra_copertura_previdenziale), p.ateco_primario, p.piva, p.codice_fiscale, p.denominazione,
             p.codice_sede_inps, p.matricola_inps, adesso()),
        )
        registra(CON, "profilo_aggiornato", "profilo_fiscale", cur.lastrowid, dopo=p.model_dump())
    return leggi_profilo()


# ------------------------------------------------------------------ import XML


def _gia_presente(f: FatturaImportata) -> bool:
    return CON.execute(
        "SELECT 1 FROM fattura WHERE xml_sha256 = ? OR (anno = ? AND numero = ? AND tipo = ?)",
        (f.sha256, f.anno, f.numero, f.tipo),
    ).fetchone() is not None


@app.post("/api/v1/import/anteprima", dependencies=protetto)
async def import_anteprima(files: list[UploadFile] = File(...)):
    fatture: list[FatturaImportata] = []
    errori: list[str] = []
    for up in files:
        fs, es = leggi_file(up.filename or "file", await up.read())
        fatture += fs
        errori += es
    piva = servizi.profilo_riga(CON)["piva"]
    for f in fatture:
        if piva and f.piva_emittente and f.piva_emittente.removeprefix("IT") != piva.removeprefix("IT"):
            f.avvisi.append("Fattura non emessa dalla tua P.IVA: verrà ignorata")
    id_anteprima = uuid.uuid4().hex
    ANTEPRIME[id_anteprima] = fatture
    return {
        "id": id_anteprima,
        "fatture": [{**f.come_dict(), "duplicata": _gia_presente(f)} for f in fatture],
        "errori": errori,
    }


class Conferma(BaseModel):
    id: str


@app.post("/api/v1/import/conferma", dependencies=protetto)
def import_conferma(c: Conferma):
    fatture = ANTEPRIME.pop(c.id, None)
    if fatture is None:
        raise HTTPException(404, "Anteprima scaduta: ricarica i file")
    primario = servizi.profilo_riga(CON)["ateco_primario"]
    piva = servizi.profilo_riga(CON)["piva"]
    CARTELLA_XML.mkdir(parents=True, exist_ok=True)
    salvate, saltate = [], []
    with transazione(CON):
        for f in fatture:
            if _gia_presente(f) or (piva and f.piva_emittente and f.piva_emittente.removeprefix("IT") != piva.removeprefix("IT")):
                saltate.append(f.numero)
                continue
            CON.execute(
                "INSERT OR IGNORE INTO cliente (denominazione, piva, codice_fiscale, codice_destinatario, pec) VALUES (?,?,?,?,?)",
                (f.cliente_denominazione, f.cliente_piva or None, f.cliente_cf or None, f.cliente_codice_destinatario, f.cliente_pec),
            )
            cliente = CON.execute(
                "SELECT id FROM cliente WHERE piva IS ? AND codice_fiscale IS ?", (f.cliente_piva or None, f.cliente_cf or None)
            ).fetchone()
            cur = CON.execute(
                """INSERT INTO fattura (numero, anno, data, tipo, cliente_id, imponibile_cent, rivalsa_cent, bollo_cent,
                   bollo_addebitato, totale_cent, collegata_a, xml_sha256, file_path, importata_il)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f.numero, f.anno, f.data, f.tipo, cliente["id"] if cliente else None, f.imponibile_cent, f.rivalsa_cent,
                 f.bollo_cent, int(f.bollo_addebitato), f.totale_cent, f.collegata_a or None, f.sha256, f.file, adesso()),
            )
            for r in f.righe:
                CON.execute(
                    "INSERT INTO riga_fattura (fattura_id, n, descrizione, quantita, prezzo_cent, imponibile_cent, ateco) VALUES (?,?,?,?,?,?,?)",
                    (cur.lastrowid, r.n, r.descrizione, r.quantita, r.prezzo_cent, r.imponibile_cent, primario),
                )
            registra(CON, "fattura_importata", "fattura", cur.lastrowid, dopo={"numero": f.numero, "file": f.file})
            salvate.append(f.numero)
    return {"salvate": salvate, "saltate": saltate}


# ------------------------------------------------------------------ fatture e incassi


@app.get("/api/v1/fatture", dependencies=protetto)
def elenco_fatture(anno: int | None = None):
    sql = """SELECT f.*, c.denominazione AS cliente,
             COALESCE((SELECT SUM(importo_cent) FROM incasso i WHERE i.fattura_id = f.id AND i.annullato = 0), 0) AS incassato_cent
             FROM fattura f LEFT JOIN cliente c ON c.id = f.cliente_id WHERE f.annullata = 0"""
    args: tuple = ()
    if anno:
        sql += " AND f.anno = ?"
        args = (anno,)
    righe = [dict(r) for r in CON.execute(sql + " ORDER BY f.data DESC, f.numero DESC", args)]
    for r in righe:
        r["residuo_cent"] = r["totale_cent"] - r["incassato_cent"]
    return righe


@app.get("/api/v1/fatture/{fattura_id}", dependencies=protetto)
def dettaglio_fattura(fattura_id: int):
    f = CON.execute("SELECT * FROM fattura WHERE id = ?", (fattura_id,)).fetchone()
    if not f:
        raise HTTPException(404, "Fattura non trovata")
    return {
        **dict(f),
        "righe": [dict(r) for r in CON.execute("SELECT * FROM riga_fattura WHERE fattura_id = ? ORDER BY n", (fattura_id,))],
        "incassi": [dict(r) for r in CON.execute("SELECT * FROM incasso WHERE fattura_id = ? AND annullato = 0 ORDER BY data", (fattura_id,))],
    }


class AtecoRiga(BaseModel):
    ateco: str


@app.patch("/api/v1/fatture/{fattura_id}/righe/{n}", dependencies=protetto)
def cambia_ateco(fattura_id: int, n: int, dati: AtecoRiga):
    anno = CON.execute("SELECT anno FROM fattura WHERE id = ?", (fattura_id,)).fetchone()
    if not anno:
        raise HTTPException(404, "Fattura non trovata")
    servizi.regole(CON, anno["anno"]).coefficiente(dati.ateco)  # errore esplicito se l'ATECO non ha coefficiente
    with transazione(CON):
        prima = CON.execute("SELECT ateco FROM riga_fattura WHERE fattura_id = ? AND n = ?", (fattura_id, n)).fetchone()
        if not prima:
            raise HTTPException(404, "Riga non trovata")
        CON.execute("UPDATE riga_fattura SET ateco = ? WHERE fattura_id = ? AND n = ?", (dati.ateco, fattura_id, n))
        registra(CON, "ateco_riga", "fattura", fattura_id, prima={"n": n, "ateco": prima["ateco"]}, dopo={"n": n, "ateco": dati.ateco})
    return dettaglio_fattura(fattura_id)


class IncassoIn(BaseModel):
    data: date
    importo_cent: int = Field(gt=0)
    conto: str | None = "Finom"
    note: str | None = None


@app.post("/api/v1/fatture/{fattura_id}/incassi", dependencies=protetto)
def registra_incasso(fattura_id: int, inc: IncassoIn):
    f = CON.execute("SELECT * FROM fattura WHERE id = ? AND annullata = 0", (fattura_id,)).fetchone()
    if not f:
        raise HTTPException(404, "Fattura non trovata")
    gia = CON.execute("SELECT COALESCE(SUM(importo_cent),0) FROM incasso WHERE fattura_id = ? AND annullato = 0", (fattura_id,)).fetchone()[0]
    if gia + inc.importo_cent > f["totale_cent"]:
        raise HTTPException(422, f"Gli incassi superano il totale della fattura ({f['totale_cent'] / 100:.2f} €)")
    with transazione(CON):
        cur = CON.execute(
            "INSERT INTO incasso (fattura_id, data, importo_cent, conto, note) VALUES (?,?,?,?,?)",
            (fattura_id, inc.data.isoformat(), inc.importo_cent, inc.conto, inc.note),
        )
        registra(CON, "incasso_registrato", "incasso", cur.lastrowid, dopo=inc.model_dump())
    # Quanto mettere da parte su questo incasso (ATECO prevalente della fattura)
    pesi = servizi.pesi_ateco(CON, fattura_id) or {servizi.profilo_riga(CON)["ateco_primario"]: 1}
    ateco = max(pesi, key=pesi.get)
    quota_bollo = Decimal(f["bollo_cent"]) / 100 if gia == 0 else Decimal("0")
    quota = quota_da_accantonare(servizi.euro(inc.importo_cent), ateco, inc.data.year, servizi.profilo(CON), servizi.regole(CON, inc.data.year), quota_bollo)
    return {"incasso_id": cur.lastrowid, "accantonamento": quota}


@app.delete("/api/v1/incassi/{incasso_id}", dependencies=protetto)
def annulla_incasso(incasso_id: int):
    with transazione(CON):
        CON.execute("UPDATE incasso SET annullato = 1 WHERE id = ?", (incasso_id,))
        registra(CON, "incasso_annullato", "incasso", incasso_id)
    return {"annullato": incasso_id}


# ------------------------------------------------------------------ anni e scadenze


@app.get("/api/v1/anni/{anno}/riepilogo", dependencies=protetto)
def riepilogo(anno: int):
    r = servizi.riepilogo(CON, anno)
    return {**r.come_dict(), "da_incassare": str(servizi.euro(servizi.da_incassare_cent(CON, anno)))}


@app.get("/api/v1/anni/{anno}/scadenze", dependencies=protetto)
def scadenze(anno: int):
    return servizi.scadenze_anno(CON, anno)


@app.get("/api/v1/scadenze", dependencies=protetto)
def tutte_le_scadenze(da_pagare: bool = True):
    tutte = servizi.tutte_le_scadenze(CON)
    return [s for s in tutte if not s["pagata"]] if da_pagare else tutte


@app.get("/api/v1/scadenze/{chiave}/f24", dependencies=protetto)
def facsimile_f24(chiave: str):
    try:
        return servizi.f24(CON, chiave)
    except KeyError:
        raise HTTPException(404, "Scadenza non trovata")


class VersamentoIn(BaseModel):
    data: date
    importo_cent: int = Field(gt=0)


@app.post("/api/v1/scadenze/{chiave}/versamenti", dependencies=protetto)
def segna_versata(chiave: str, v: VersamentoIn):
    s = next((x for x in servizi.tutte_le_scadenze(CON) if x["chiave"] == chiave), None)
    if not s:
        raise HTTPException(404, "Scadenza non trovata")
    with transazione(CON):
        cur = CON.execute(
            "INSERT INTO versamento (scadenza_chiave, codice_tributo, sezione, anno_riferimento, data, importo_cent) VALUES (?,?,?,?,?,?)",
            (chiave, s["codice"], s["sezione"], s["anno_riferimento"], v.data.isoformat(), v.importo_cent),
        )
        registra(CON, "versamento", "versamento", cur.lastrowid, dopo={"chiave": chiave, **v.model_dump()})
    return {"versamento_id": cur.lastrowid}


class Chiusura(BaseModel):
    imposta_dichiarata_cent: int = Field(ge=0)
    contributi_dichiarati_cent: int = Field(ge=0)


@app.put("/api/v1/anni/{anno}/chiusura", dependencies=protetto)
def chiudi_anno(anno: int, c: Chiusura):
    with transazione(CON):
        CON.execute(
            """INSERT INTO anno_fiscale (anno, stato, imposta_dichiarata_cent, contributi_dichiarati_cent, chiuso_il)
               VALUES (?, 'chiuso', ?, ?, ?) ON CONFLICT(anno) DO UPDATE SET stato='chiuso',
               imposta_dichiarata_cent=excluded.imposta_dichiarata_cent,
               contributi_dichiarati_cent=excluded.contributi_dichiarati_cent, chiuso_il=excluded.chiuso_il""",
            (anno, c.imposta_dichiarata_cent, c.contributi_dichiarati_cent, adesso()),
        )
        registra(CON, "anno_chiuso", "anno_fiscale", anno, dopo=c.model_dump())
    return {"anno": anno, "stato": "chiuso"}


# ------------------------------------------------------------------ impostazioni (RNF-09)


@app.get("/api/v1/impostazioni/{anno}", dependencies=protetto)
def impostazioni(anno: int):
    reg = servizi.regole(CON, anno)
    modificati = servizi.modifiche_parametri(CON, anno)
    storico = [dict(r) for r in CON.execute("SELECT * FROM parametro WHERE anno = ? ORDER BY id DESC", (anno,))]
    return {
        "anno": anno,
        "parametri": [{"chiave": k, "valore": v, "modificato": k in modificati} for k, v in reg.appiattite().items()],
        "storico": storico,
    }


class ParametroIn(BaseModel):
    chiave: str
    valore: str | None
    valido_dal: date
    nota: str | None = None
    conferma: bool = False  # False = mostra solo l'effetto sui totali


def _totali(anno: int, extra: dict | None) -> dict:
    try:
        r = servizi.riepilogo(CON, anno, extra)
        sc = servizi.scadenze_anno(CON, anno, extra)
        return {"imposta": str(r.imposta.importo), "contributi_gs": str(r.contributi_gs.importo),
                "versamenti": str(sum((Decimal(s["importo"]) for s in sc if Decimal(s["importo"]) > 0), Decimal("0")))}
    except ParametroMancante as e:
        return {"errore": str(e)}


@app.put("/api/v1/impostazioni/{anno}", dependencies=protetto)
def modifica_parametro(anno: int, p: ParametroIn):
    reg = servizi.regole(CON, anno)
    if p.chiave not in reg.appiattite():
        raise HTTPException(422, f"Parametro sconosciuto: {p.chiave}")
    if p.valore is not None:
        try:
            Decimal(p.valore)
        except Exception:
            if p.chiave.split(".")[-1] not in {"metodo", "causale", "provvisorio"} and not p.chiave.startswith(("imposta.codici", "bollo.codici", "scadenze")):
                raise HTTPException(422, "Valore non numerico")
    prima = _totali(anno, None)
    dopo = _totali(anno, {p.chiave: p.valore})
    if p.conferma:
        with transazione(CON):
            CON.execute(
                "INSERT INTO parametro (anno, chiave, valore, valido_dal, nota, creato_il) VALUES (?,?,?,?,?,?)",
                (anno, p.chiave, p.valore, p.valido_dal.isoformat(), p.nota, adesso()),
            )
            registra(CON, "parametro_modificato", "parametro", None, dopo=p.model_dump())
    return {"salvato": p.conferma, "effetto": {"prima": prima, "dopo": dopo}}


# ------------------------------------------------------------------ export


@app.get("/api/v1/export", dependencies=protetto)
def export_csv(dal: date, al: date):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["tipo", "data", "numero", "cliente", "importo", "note"])

    def it(cent: int) -> str:
        return f"{cent / 100:.2f}".replace(".", ",")

    for r in CON.execute(
        "SELECT f.*, c.denominazione AS cliente FROM fattura f LEFT JOIN cliente c ON c.id = f.cliente_id WHERE f.annullata = 0 AND f.data BETWEEN ? AND ? ORDER BY f.data",
        (dal.isoformat(), al.isoformat()),
    ):
        w.writerow(["fattura" if r["tipo"] != "TD04" else "nota di credito", r["data"], r["numero"], r["cliente"], it(r["totale_cent"]), ""])
    for r in CON.execute(
        "SELECT i.*, f.numero FROM incasso i JOIN fattura f ON f.id = i.fattura_id WHERE i.annullato = 0 AND i.data BETWEEN ? AND ? ORDER BY i.data",
        (dal.isoformat(), al.isoformat()),
    ):
        w.writerow(["incasso", r["data"], r["numero"], "", it(r["importo_cent"]), r["conto"] or ""])
    for s in servizi.tutte_le_scadenze(CON):
        if dal.isoformat() <= s["data"] <= al.isoformat():
            w.writerow(["scadenza", s["data"], s["codice"], s["descrizione"], s["importo"].replace(".", ","), "pagata" if s["pagata"] else "da pagare"])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename=b3d-fisco_{dal}_{al}.csv"})


# ------------------------------------------------------------------ quadro LM, avvisi, calendario


@app.get("/api/v1/anni/{anno}/quadro-lm", dependencies=protetto)
def quadro_lm(anno: int):
    return servizi.quadro_lm(CON, anno)


@app.get("/api/v1/avvisi", dependencies=protetto)
def avvisi(oggi: date | None = None):
    """Per n8n: elenco degli avvisi del giorno (vuoto = nessun messaggio da mandare)."""
    elenco = servizi.avvisi(CON, oggi or date.today())
    testo = "\n".join(("🔴 " if a["livello"] == "critico" else "🟡 ") + a["testo"] for a in elenco)
    return {"numero": len(elenco), "avvisi": elenco, "messaggio": ("B3D Lab · avvisi fiscali\n" + testo) if elenco else ""}


@app.get("/api/v1/scadenze.ics")
def calendario(token: str = ""):
    """Calendario abbonabile: l'indirizzo contiene il token di sola lettura (B3D_TOKEN_LETTURA)."""
    password = os.environ.get("B3D_PASSWORD")
    lettura = os.environ.get("B3D_TOKEN_LETTURA")
    if password:
        validi = [_token_da_password(password)] + ([lettura] if lettura else [])
        if not any(hmac.compare_digest(token, v) for v in validi):
            raise HTTPException(401, "Token non valido")
    from datetime import datetime, timezone

    ics = servizi.calendario_ics(CON, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    return PlainTextResponse(ics, media_type="text/calendar; charset=utf-8",
                             headers={"Content-Disposition": "inline; filename=b3d-scadenze.ics"})


# ------------------------------------------------------------------ interfaccia

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
