"""Lettura delle fatture elettroniche XML FatturaPA (singoli file, .p7m firmati, archivi zip).

Restituisce dati puliti in centesimi interi. Non salva nulla: il salvataggio lo fa l'API
dopo l'anteprima confermata dall'utente.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from xml.etree import ElementTree as ET


class ErroreImport(Exception):
    pass


@dataclass
class Riga:
    n: int
    descrizione: str
    quantita: str
    prezzo_cent: int
    imponibile_cent: int


@dataclass
class FatturaImportata:
    file: str
    sha256: str
    piva_emittente: str
    tipo: str  # TD01 fattura, TD04 nota di credito, ...
    numero: str
    data: str  # ISO
    anno: int
    cliente_denominazione: str
    cliente_piva: str
    cliente_cf: str
    cliente_codice_destinatario: str
    cliente_pec: str
    righe: list[Riga] = field(default_factory=list)
    imponibile_cent: int = 0
    rivalsa_cent: int = 0
    bollo_cent: int = 0
    bollo_addebitato: bool = False
    totale_cent: int = 0
    collegata_a: str = ""  # numero della fattura stornata (note di credito)
    avvisi: list[str] = field(default_factory=list)

    def come_dict(self) -> dict:
        return asdict(self)


def _cent(testo: str | None) -> int:
    if not testo:
        return 0
    return int((Decimal(testo.strip()) * 100).quantize(Decimal("1")))


def _senza_namespace(radice: ET.Element) -> ET.Element:
    for el in radice.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return radice


def estrai_xml_da_p7m(dati: bytes) -> bytes:
    """Estrae l'XML da un file firmato CAdES (.p7m) senza verificare la firma.

    Funziona con il contenuto incapsulato in chiaro (caso più comune). Se l'XML è
    codificato in base64 prova a decodificarlo.
    """
    if dati.lstrip().startswith(b"<"):
        return dati
    try:  # alcuni p7m sono interamente in base64
        import base64

        decodificato = base64.b64decode(dati, validate=False)
        if b"FatturaElettronica" in decodificato:
            dati = decodificato
    except Exception:
        pass
    inizio = dati.find(b"<?xml")
    if inizio < 0:
        m = re.search(rb"<[A-Za-z0-9_]*:?FatturaElettronica[\s>]", dati)
        inizio = m.start() if m else -1
    fine_m = list(re.finditer(rb"</[A-Za-z0-9_]*:?FatturaElettronica>", dati))
    if inizio < 0 or not fine_m:
        raise ErroreImport("Impossibile estrarre l'XML dal file .p7m")
    return dati[inizio : fine_m[-1].end()]


def leggi_fattura(nome_file: str, dati: bytes) -> list[FatturaImportata]:
    """Una FatturaPA può contenere più corpi (lotto): restituisce una fattura per corpo."""
    xml = estrai_xml_da_p7m(dati) if nome_file.lower().endswith(".p7m") else dati
    try:
        radice = _senza_namespace(ET.fromstring(xml))
    except ET.ParseError as e:
        raise ErroreImport(f"{nome_file}: XML non valido ({e})") from e
    if radice.tag != "FatturaElettronica":
        raise ErroreImport(f"{nome_file}: non è una FatturaPA")

    sha = hashlib.sha256(xml).hexdigest()
    head = radice.find("FatturaElettronicaHeader")
    if head is None:
        raise ErroreImport(f"{nome_file}: intestazione mancante")

    def t(nodo: ET.Element | None, percorso: str) -> str:
        if nodo is None:
            return ""
        el = nodo.find(percorso)
        return (el.text or "").strip() if el is not None and el.text else ""

    ced = head.find("CedentePrestatore/DatiAnagrafici")
    piva_emittente = t(ced, "IdFiscaleIVA/IdPaese") + t(ced, "IdFiscaleIVA/IdCodice")
    ces = head.find("CessionarioCommittente/DatiAnagrafici")
    denominazione = t(ces, "Anagrafica/Denominazione") or " ".join(
        x for x in (t(ces, "Anagrafica/Nome"), t(ces, "Anagrafica/Cognome")) if x
    )
    trasm = head.find("DatiTrasmissione")

    risultati: list[FatturaImportata] = []
    corpi = radice.findall("FatturaElettronicaBody")
    for i, corpo in enumerate(corpi):
        dg = corpo.find("DatiGenerali/DatiGeneraliDocumento")
        data = t(dg, "Data")
        f = FatturaImportata(
            file=nome_file if len(corpi) == 1 else f"{nome_file}#{i + 1}",
            sha256=sha if len(corpi) == 1 else hashlib.sha256(sha.encode() + bytes([i])).hexdigest(),
            piva_emittente=piva_emittente,
            tipo=t(dg, "TipoDocumento"),
            numero=t(dg, "Numero"),
            data=data,
            anno=int(data[:4]) if data else 0,
            cliente_denominazione=denominazione,
            cliente_piva=t(ces, "IdFiscaleIVA/IdPaese") + t(ces, "IdFiscaleIVA/IdCodice"),
            cliente_cf=t(ces, "CodiceFiscale"),
            cliente_codice_destinatario=t(trasm, "CodiceDestinatario"),
            cliente_pec=t(trasm, "PECDestinatario"),
        )
        f.totale_cent = _cent(t(dg, "ImportoTotaleDocumento"))
        for cassa in dg.findall("DatiCassaPrevidenziale") if dg is not None else []:
            f.rivalsa_cent += _cent(t(cassa, "ImportoContributoCassa"))
        if dg is not None and t(dg, "DatiBollo/BolloVirtuale").upper() == "SI":
            f.bollo_cent = _cent(t(dg, "DatiBollo/ImportoBollo")) or 200

        doc_corr = corpo.find("DatiGenerali/DatiFattureCollegate")
        f.collegata_a = t(doc_corr, "IdDocumento")

        for linea in corpo.findall("DatiBeniServizi/DettaglioLinee"):
            descr = t(linea, "Descrizione")
            riga = Riga(
                n=int(t(linea, "NumeroLinea") or 0),
                descrizione=descr,
                quantita=t(linea, "Quantita") or "1",
                prezzo_cent=_cent(t(linea, "PrezzoUnitario")),
                imponibile_cent=_cent(t(linea, "PrezzoTotale")),
            )
            if "bollo" in descr.lower():
                f.bollo_addebitato = True
            f.righe.append(riga)
        f.imponibile_cent = sum(r.imponibile_cent for r in f.righe)

        if not f.totale_cent:
            f.totale_cent = f.imponibile_cent + f.rivalsa_cent
            f.avvisi.append("ImportoTotaleDocumento assente: totale ricalcolato da righe e rivalsa")
        atteso = f.imponibile_cent + f.rivalsa_cent
        if f.totale_cent != atteso:
            f.avvisi.append(
                f"Totale documento {f.totale_cent / 100:.2f} € diverso da righe + rivalsa {atteso / 100:.2f} €: controllare"
            )
        if f.tipo == "TD04" and not f.collegata_a:
            f.avvisi.append("Nota di credito senza fattura collegata")
        risultati.append(f)
    return risultati


def leggi_file(nome_file: str, dati: bytes) -> tuple[list[FatturaImportata], list[str]]:
    """Accetta .xml, .p7m o .zip (anche con cartelle). Restituisce fatture ed errori."""
    fatture: list[FatturaImportata] = []
    errori: list[str] = []
    if nome_file.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(dati)) as z:
                for info in z.infolist():
                    n = info.filename
                    if info.is_dir() or not n.lower().endswith((".xml", ".p7m")) or "metadato" in n.lower():
                        continue
                    try:
                        fatture.extend(leggi_fattura(n.rsplit("/", 1)[-1], z.read(info)))
                    except ErroreImport as e:
                        errori.append(str(e))
        except zipfile.BadZipFile:
            errori.append(f"{nome_file}: archivio zip non valido")
    else:
        try:
            fatture.extend(leggi_fattura(nome_file, dati))
        except ErroreImport as e:
            errori.append(str(e))
    return fatture, errori
