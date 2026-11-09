"""Collega database e motore: trasforma i fatti salvati negli input del motore."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from backend.motore import (
    FatturaConBollo,
    Incasso,
    ParametroMancante,
    Profilo,
    Regole,
    RiepilogoAnno,
    Scadenza,
    Voce,
    calcola_anno,
    calcola_bollo,
    calcola_versamenti,
    verifica_soglia,
)
from backend.motore.tipi import Passo

D = Decimal


class ProfiloAssente(Exception):
    pass


def euro(cent: int) -> Decimal:
    return (D(cent) / 100).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def cent(importo: Decimal) -> int:
    return int((importo * 100).quantize(D("1"), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------ profilo e regole


def profilo_riga(con: sqlite3.Connection, alla_data: date | None = None) -> sqlite3.Row:
    """Versione del profilo in vigore alla data (oggi se non indicata). Le versioni future non contano ancora."""
    giorno = (alla_data or date.today()).isoformat()
    riga = con.execute(
        "SELECT * FROM profilo_fiscale WHERE valido_dal <= ? ORDER BY valido_dal DESC, id DESC LIMIT 1", (giorno,)
    ).fetchone()
    if riga is None:  # solo versioni future: si usa la prima, così l'app resta utilizzabile
        riga = con.execute("SELECT * FROM profilo_fiscale ORDER BY valido_dal, id LIMIT 1").fetchone()
    if riga is None:
        raise ProfiloAssente("Compila prima il profilo fiscale nelle Impostazioni")
    return riga


def profilo(con: sqlite3.Connection) -> Profilo:
    r = profilo_riga(con)
    return Profilo(
        data_inizio_attivita=date.fromisoformat(r["data_inizio_attivita"]),
        aliquota_agevolata=bool(r["aliquota_agevolata"]),
        altra_copertura_previdenziale=bool(r["altra_copertura_previdenziale"]),
        gestione=r["gestione"],
    )


def modifiche_parametri(con: sqlite3.Connection, anno: int) -> dict[str, str | None]:
    """Modifiche ai parametri di un anno d'imposta. Valgono per tutto l'anno indicato, a prescindere da
    valido_dal (che resta come data di registrazione nello storico): un'aliquota 2027 modificata oggi
    deve già valere nelle stime 2027. A parità di chiave vince la modifica più recente."""
    righe = con.execute(
        "SELECT chiave, valore FROM parametro WHERE anno = ? ORDER BY valido_dal, id", (anno,)
    ).fetchall()
    return {r["chiave"]: r["valore"] for r in righe}


def regole(con: sqlite3.Connection, anno: int, extra: dict | None = None) -> Regole:
    modifiche = modifiche_parametri(con, anno)
    if extra:
        modifiche.update(extra)
    return Regole.carica(anno, modifiche)


# ------------------------------------------------------------------ incassi


def _ripartisci(importo_cent: int, pesi: dict[str, int], primario: str) -> dict[str, int]:
    """Ripartisce un incasso tra gli ATECO in proporzione all'imponibile delle righe."""
    totale = sum(pesi.values())
    if totale <= 0:
        return {primario: importo_cent}
    quote = {a: importo_cent * p // totale for a, p in pesi.items()}
    resto = importo_cent - sum(quote.values())
    quote[max(pesi, key=pesi.get)] += resto
    return quote


def pesi_ateco(con: sqlite3.Connection, fattura_id: int) -> dict[str, int]:
    pesi: dict[str, int] = defaultdict(int)
    for r in con.execute("SELECT ateco, imponibile_cent FROM riga_fattura WHERE fattura_id = ?", (fattura_id,)):
        pesi[r["ateco"]] += r["imponibile_cent"]
    return dict(pesi)


def incassi_motore(con: sqlite3.Connection, anno: int | None = None) -> list[Incasso]:
    primario = profilo_riga(con)["ateco_primario"]
    sql = """SELECT i.*, f.numero, f.tipo FROM incasso i JOIN fattura f ON f.id = i.fattura_id
             WHERE i.annullato = 0 AND f.annullata = 0"""
    args: tuple = ()
    if anno is not None:
        sql += " AND substr(i.data, 1, 4) = ?"
        args = (str(anno),)
    risultato: list[Incasso] = []
    for r in con.execute(sql, args).fetchall():
        segno = -1 if r["tipo"] == "TD04" else 1  # rimborso di una nota di credito
        for ateco, c in _ripartisci(r["importo_cent"], pesi_ateco(con, r["fattura_id"]), primario).items():
            risultato.append(Incasso(date.fromisoformat(r["data"]), euro(segno * c), ateco, f"fattura {r['numero']}"))
    return risultato


def da_incassare_cent(con: sqlite3.Connection, anno: int) -> int:
    r = con.execute(
        """SELECT COALESCE(SUM(f.totale_cent - COALESCE((SELECT SUM(importo_cent) FROM incasso i
                 WHERE i.fattura_id = f.id AND i.annullato = 0), 0)), 0) AS residuo
           FROM fattura f WHERE f.annullata = 0 AND f.tipo != 'TD04' AND f.anno = ?""",
        (anno,),
    ).fetchone()
    return max(int(r["residuo"]), 0)


def fatture_con_bollo(con: sqlite3.Connection, anno: int) -> list[FatturaConBollo]:
    return [
        FatturaConBollo(r["numero"], date.fromisoformat(r["data"]), euro(r["bollo_cent"]))
        for r in con.execute("SELECT numero, data, bollo_cent FROM fattura WHERE annullata = 0 AND anno = ? AND bollo_cent > 0", (anno,))
    ]


def contributi_versati_nell_anno(con: sqlite3.Connection, anno: int) -> Decimal:
    r = con.execute(
        "SELECT COALESCE(SUM(importo_cent), 0) AS t FROM versamento WHERE sezione = 'inps' AND substr(data, 1, 4) = ?",
        (str(anno),),
    ).fetchone()
    return euro(r["t"])


def versato_per_prefisso(con: sqlite3.Connection, prefisso: str) -> Decimal:
    r = con.execute(
        "SELECT COALESCE(SUM(importo_cent), 0) AS t FROM versamento WHERE scadenza_chiave LIKE ?", (prefisso + "%",)
    ).fetchone()
    return euro(r["t"])


# ------------------------------------------------------------------ riepilogo e scadenze


def riepilogo(con: sqlite3.Connection, anno: int, extra_regole: dict | None = None) -> RiepilogoAnno:
    p = profilo(con)
    reg = regole(con, anno, extra_regole)
    r = calcola_anno(anno, p, incassi_motore(con, anno), reg, contributi_versati_nell_anno(con, anno))
    r.soglia = verifica_soglia(anno, p, r.ricavi.importo, reg, euro(da_incassare_cent(con, anno)))
    return r


def valori_anno(con: sqlite3.Connection, anno: int, extra_regole: dict | None = None) -> tuple[Voce, Voce]:
    """Imposta e contributi dell'anno: dichiarati se l'anno è chiuso, altrimenti stima del motore."""
    chiuso = con.execute("SELECT * FROM anno_fiscale WHERE anno = ? AND stato = 'chiuso'", (anno,)).fetchone()
    if chiuso:
        imp = euro(chiuso["imposta_dichiarata_cent"])
        gs = euro(chiuso["contributi_dichiarati_cent"])
        return (
            Voce(imp, (Passo("Imposta dichiarata", "valore della dichiarazione dei redditi", {}, imp),)),
            Voce(gs, (Passo("Contributi dichiarati", "valore della dichiarazione dei redditi", {}, gs),)),
        )
    r = riepilogo(con, anno, extra_regole)
    return Voce(r.imposta.importo, r.imposta.passi, True), Voce(r.contributi_gs.importo, r.contributi_gs.passi, True)


def scadenze_anno(con: sqlite3.Connection, anno: int, extra_regole: dict | None = None) -> list[dict]:
    """Versamenti generati dall'anno d'imposta `anno`: saldo, acconti dell'anno dopo, bollo."""
    reg = regole(con, anno, extra_regole)
    imp, gs = valori_anno(con, anno, extra_regole)
    sc: list[Scadenza] = calcola_versamenti(
        anno, imp, gs, reg,
        acconti_imposta_versati=versato_per_prefisso(con, f"acconto-imposta-{anno}-"),
        acconti_gs_versati=versato_per_prefisso(con, f"acconto-gs-{anno}-"),
    )
    sc += calcola_bollo(anno, fatture_con_bollo(con, anno), reg)
    out = []
    ordine = {"saldo": 0, "acconto": 1, "bollo": 2}
    for s in sorted(sc, key=lambda x: (x.data, x.sezione, ordine.get(x.chiave.split("-")[0], 9), x.chiave)):
        d = s.come_dict()
        versato = versato_per_prefisso(con, s.chiave)
        d["versato"] = str(versato)
        d["pagata"] = s.importo <= 0 or versato >= s.importo
        out.append(d)
    return out


def anni_con_dati(con: sqlite3.Connection) -> list[int]:
    righe = con.execute("SELECT DISTINCT anno FROM fattura UNION SELECT DISTINCT CAST(substr(data,1,4) AS INTEGER) FROM incasso").fetchall()
    anni = sorted({r[0] for r in righe if r[0]})
    if not anni:
        anni = [date.fromisoformat(profilo_riga(con)["data_inizio_attivita"]).year]
    return anni


def tutte_le_scadenze(con: sqlite3.Connection) -> list[dict]:
    tutte: list[dict] = []
    for anno in anni_con_dati(con):
        tutte += scadenze_anno(con, anno)
    ordine = {"saldo": 0, "acconto": 1, "bollo": 2}
    return sorted(tutte, key=lambda s: (s["data"], s["sezione"], ordine.get(s["chiave"].split("-")[0], 9), s["chiave"]))


def f24(con: sqlite3.Connection, chiave: str) -> dict:
    """Facsimile F24 della data in cui cade la scadenza indicata (tutte le voci di quel giorno)."""
    tutte = tutte_le_scadenze(con)
    trovata = next((s for s in tutte if s["chiave"] == chiave), None)
    if trovata is None:
        raise KeyError(chiave)
    giorno = trovata["data"]
    voci = [s for s in tutte if s["data"] == giorno and D(s["importo"]) > 0 and not s["pagata"]]
    p = profilo_riga(con)
    erario = [{"codice_tributo": s["codice"], "anno_riferimento": s["anno_riferimento"], "rateazione": "", "importo_a_debito": s["importo"], "descrizione": s["descrizione"]} for s in voci if s["sezione"] == "erario"]
    inps = [{"codice_sede": p["codice_sede_inps"] or "", "causale": s["codice"], "matricola": p["matricola_inps"] or "", "periodo_da": f"01/{s['anno_riferimento']}", "periodo_a": f"12/{s['anno_riferimento']}", "importo_a_debito": s["importo"], "descrizione": s["descrizione"]} for s in voci if s["sezione"] == "inps"]
    totale = sum((D(s["importo"]) for s in voci), D("0"))
    return {
        "scadenza": giorno,
        "contribuente": {"codice_fiscale": p["codice_fiscale"] or "", "denominazione": p["denominazione"] or ""},
        "sezione_erario": erario,
        "sezione_inps": inps,
        "saldo_finale": str(totale),
        "nota": "Facsimile da ricopiare nell'home banking o in F24 web: non è un modulo di pagamento.",
    }


# ------------------------------------------------------------------ quadro LM (F14)


def quadro_lm(con: sqlite3.Connection, anno: int) -> dict:
    """Valori per il quadro LM del Modello Redditi: una riga per attività, poi i totali.

    Le etichette dei righi sono parametri (quadro_lm.*) perché il modello cambia ogni anno.
    """
    reg = regole(con, anno)
    r = riepilogo(con, anno)
    righi = list(reg.valore("quadro_lm.righi_attivita"))
    attivita = []
    for i, (ateco, ricavi) in enumerate(r.ricavi_per_ateco.items()):
        coeff = reg.coefficiente(ateco)
        attivita.append({
            "rigo": righi[i] if i < len(righi) else "oltre i righi disponibili: chiedere allo studio",
            "codice_attivita": ateco,
            "ricavi": str(ricavi),
            "coefficiente": str(coeff * 100).rstrip("0").rstrip(".") + "%",
            "reddito": str((ricavi * coeff).quantize(D("0.01"), rounding=ROUND_HALF_UP)),
        })
    acconti = versato_per_prefisso(con, f"acconto-imposta-{anno}-")
    chiuso = con.execute("SELECT * FROM anno_fiscale WHERE anno = ? AND stato = 'chiuso'", (anno,)).fetchone()
    imponibile = r.imposta.passi[0].risultato
    contributi_dedotti = contributi_versati_nell_anno(con, anno)
    return {
        "anno": anno,
        "stima": chiuso is None,
        "attivita": attivita,
        "totali": [
            {"rigo": reg.testo("quadro_lm.reddito_lordo"), "voce": "Reddito lordo", "importo": str(r.reddito.importo)},
            {"rigo": reg.testo("quadro_lm.contributi_dedotti"), "voce": "Contributi previdenziali versati nell'anno", "importo": str(contributi_dedotti)},
            {"rigo": reg.testo("quadro_lm.reddito_netto"), "voce": "Reddito netto", "importo": str(imponibile)},
            {"rigo": reg.testo("quadro_lm.imposta_sostitutiva"), "voce": f"Imposta sostitutiva ({(r.aliquota_imposta * 100).normalize():f}%)" + (" · dichiarata" if chiuso else ""), "importo": str(euro(chiuso["imposta_dichiarata_cent"]) if chiuso else r.imposta.importo)},
            {"rigo": reg.testo("quadro_lm.acconti_versati"), "voce": "Acconti versati per l'anno", "importo": str(acconti)},
        ],
        "nota": "Valori di supporto per lo studio: i numeri dei righi vanno verificati sul modello dell'anno.",
    }


# ------------------------------------------------------------------ avvisi (RF-12)


def avvisi(con: sqlite3.Connection, oggi: date) -> list[dict]:
    """Scadenze in arrivo entro il preavviso, scadenze passate non pagate, soglia ricavi."""
    out: list[dict] = []
    for s in tutte_le_scadenze(con):
        if s["pagata"] or D(s["importo"]) <= 0:
            continue
        giorno = date.fromisoformat(s["data"])
        preavviso = int(regole(con, s["anno_riferimento"] if s["anno_riferimento"] in anni_regole() else max(anni_regole())).valore("avvisi.giorni_preavviso"))
        mancano = (giorno - oggi).days
        if mancano < 0:
            out.append({"tipo": "scadenza_saltata", "livello": "critico", "giorni": mancano, "testo": f"Scadenza del {giorno:%d/%m/%Y} non segnata come pagata: {s['descrizione']}, {s['importo']} € (codice {s['codice']}). Valuta il ravvedimento operoso.", "chiave": s["chiave"]})
        elif mancano <= preavviso:
            out.append({"tipo": "scadenza_vicina", "livello": "attenzione", "giorni": mancano, "testo": f"Tra {mancano} giorni ({giorno:%d/%m/%Y}): {s['descrizione']}, {s['importo']} € (codice {s['codice']}){' · stima' if s['stima'] else ''}.", "chiave": s["chiave"]})
    try:
        reg_oggi = regole(con, oggi.year)
        if str(reg_oggi.appiattite().get("provvisorio", "")).lower() in ("true", "1", "si", "sì"):
            out.append({"tipo": "regole_provvisorie", "livello": "attenzione", "giorni": 0, "testo": f"I parametri fiscali {oggi.year} sono provvisori (copiati dall'anno prima): verificali nelle Impostazioni appena esce la circolare INPS.", "chiave": f"provvisorio-{oggi.year}"})
        r = riepilogo(con, oggi.year)
        so = r.soglia
        for p in so["avvisi_superati"]:
            out.append({"tipo": "soglia", "livello": "critico" if so["stato"] != "dentro" else "attenzione", "giorni": 0, "testo": f"Ricavi {oggi.year}: {so['ricavi']} € su {so['limite']['importo']} € ({so['percentuale']}%), superata la soglia di avviso del {int(D(p) * 100)}%.", "chiave": f"soglia-{oggi.year}-{p}"})
        if so["proiezione_supera_limite"] and so["stato"] == "dentro":
            out.append({"tipo": "soglia_proiezione", "livello": "attenzione", "giorni": 0, "testo": f"Con le fatture ancora da incassare i ricavi {oggi.year} arriverebbero a {so['proiezione_con_da_incassare']} €, oltre il limite di {so['limite']['importo']} €.", "chiave": f"proiezione-{oggi.year}"})
    except ParametroMancante as e:  # un parametro mancante deve vedersi, non sparire
        out.append({"tipo": "parametro_mancante", "livello": "critico", "giorni": 0, "testo": f"Impossibile controllare la soglia dei ricavi: {e}", "chiave": f"parametro-{e.chiave}"})
    return out


def anni_regole() -> set[int]:
    from backend.motore.regole import CARTELLA_REGOLE
    return {int(p.stem) for p in CARTELLA_REGOLE.glob("*.yaml") if p.stem.isdigit()}


# ------------------------------------------------------------------ calendario .ics (F25)


def _ics_testo(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def calendario_ics(con: sqlite3.Connection, adesso_utc: str) -> str:
    """Scadenze non pagate come eventi di un giorno intero, con promemoria."""
    righe = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//B3D Lab//b3d-fisco//IT", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:B3D Lab · Scadenze fiscali", "X-WR-TIMEZONE:Europe/Rome"]
    for s in tutte_le_scadenze(con):
        if s["pagata"] or D(s["importo"]) <= 0:
            continue
        giorno = date.fromisoformat(s["data"])
        anno_reg = s["anno_riferimento"] if s["anno_riferimento"] in anni_regole() else max(anni_regole())
        preavviso = int(regole(con, anno_reg).valore("avvisi.giorni_preavviso"))
        importo = f"{D(s['importo']):.2f}".replace(".", ",")
        righe += [
            "BEGIN:VEVENT",
            f"UID:{s['chiave']}@b3d-fisco",
            f"DTSTAMP:{adesso_utc}",
            f"DTSTART;VALUE=DATE:{giorno:%Y%m%d}",
            f"DTEND;VALUE=DATE:{date.fromordinal(giorno.toordinal() + 1):%Y%m%d}",
            "SUMMARY:" + _ics_testo("F24 " + importo + " € · " + s["descrizione"]),
            "DESCRIPTION:" + _ics_testo("Codice " + s["codice"] + " · anno " + str(s["anno_riferimento"]) + (" · importo stimato" if s["stima"] else "")),
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_testo(s['descrizione'])}", f"TRIGGER:-P{preavviso}D", "END:VALARM",
            "END:VEVENT",
        ]
    righe.append("END:VCALENDAR")
    return "\r\n".join(_piega(r) for r in righe) + "\r\n"


def _piega(riga: str) -> str:
    """Le righe iCalendar non devono superare 75 byte: si continuano con uno spazio iniziale."""
    b = riga.encode("utf-8")
    if len(b) <= 75:
        return riga
    pezzi, corrente = [], b""
    for ch in riga:
        c = ch.encode("utf-8")
        if len(corrente) + len(c) > (75 if not pezzi else 74):
            pezzi.append(corrente.decode("utf-8"))
            corrente = b""
        corrente += c
    pezzi.append(corrente.decode("utf-8"))
    return "\r\n ".join(pezzi)
