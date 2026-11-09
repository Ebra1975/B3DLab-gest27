"""Calcoli del regime forfettario con Gestione Separata INPS.

Ogni funzione restituisce importi con la traccia dei passaggi. Nessun I/O.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .calendario import da_mese_giorno, lavorativo
from .regole import Regole
from .tipi import (
    ZERO,
    FatturaConBollo,
    Incasso,
    Passo,
    Profilo,
    RiepilogoAnno,
    Scadenza,
    Voce,
    arrotonda,
)


def _euro(x: Decimal) -> str:
    return f"{x:.2f}"


def _pct(x: Decimal) -> str:
    return f"{(x * 100).normalize():f}%"


# --------------------------------------------------------------------------- aliquote


def aliquota_imposta(anno: int, profilo: Profilo, regole: Regole) -> tuple[Decimal, str]:
    """Aliquota dell'imposta sostitutiva e chiave della regola applicata."""
    if profilo.aliquota_agevolata:
        ultimo = profilo.data_inizio_attivita.year + int(regole.valore("imposta.anni_agevolata")) - 1
        if anno <= ultimo:
            return regole.dec("imposta.aliquota_agevolata"), "imposta.aliquota_agevolata"
    return regole.dec("imposta.aliquota_ordinaria"), "imposta.aliquota_ordinaria"


def aliquota_gs(profilo: Profilo, regole: Regole) -> tuple[Decimal, str]:
    if profilo.altra_copertura_previdenziale:
        return regole.dec("contributi_gs.aliquota_altra_copertura"), "contributi_gs.aliquota_altra_copertura"
    return regole.dec("contributi_gs.aliquota"), "contributi_gs.aliquota"


# --------------------------------------------------------------------------- anno


def calcola_anno(
    anno: int,
    profilo: Profilo,
    incassi: list[Incasso],
    regole: Regole,
    contributi_versati_nell_anno: Decimal = ZERO,
) -> RiepilogoAnno:
    """Ricavi, reddito, contributi GS e imposta sostitutiva di un anno, per cassa.

    `contributi_versati_nell_anno`: contributi previdenziali effettivamente pagati
    durante l'anno (saldo e acconti), deducibili dal reddito ai fini dell'imposta.
    """
    if profilo.gestione != "GS":
        raise NotImplementedError("Nel Traguardo 1 è implementata solo la Gestione Separata INPS")

    r = RiepilogoAnno(anno=anno)
    ricavi: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for inc in incassi:
        if inc.data.year == anno:
            ricavi[inc.ateco] += inc.importo
    r.ricavi_per_ateco = dict(sorted(ricavi.items()))

    totale_ricavi = sum(r.ricavi_per_ateco.values(), ZERO)
    r.ricavi = Voce(
        totale_ricavi,
        (Passo("Incassi dell'anno", "somma degli incassi registrati", {"incassi": str(sum(1 for i in incassi if i.data.year == anno))}, totale_ricavi),),
    )

    # Reddito: ogni attività con il suo coefficiente
    passi_reddito: list[Passo] = []
    reddito = ZERO
    for ateco, importo in r.ricavi_per_ateco.items():
        coeff = regole.coefficiente(ateco)
        parziale = arrotonda(importo * coeff)
        reddito += parziale
        passi_reddito.append(
            Passo(
                f"Reddito attività {ateco}",
                "incassi × coefficiente di redditività",
                {"incassi": _euro(importo), "coefficiente": _pct(coeff)},
                parziale,
                f"coefficienti.{ateco}",
            )
        )
    passi_reddito.append(Passo("Reddito forfettario", "somma dei redditi per attività", {}, reddito))
    r.reddito = Voce(reddito, tuple(passi_reddito))

    # Contributi Gestione Separata (dovuti sull'anno, con massimale)
    aliq_gs, chiave_gs = aliquota_gs(profilo, regole)
    massimale = regole.dec("contributi_gs.massimale_reddito")
    base_gs = min(max(reddito, ZERO), massimale)
    contributi = arrotonda(base_gs * aliq_gs)
    passi_gs = []
    if reddito > massimale:
        passi_gs.append(Passo("Reddito oltre il massimale", "reddito limitato al massimale", {"reddito": _euro(reddito), "massimale": _euro(massimale)}, base_gs, "contributi_gs.massimale_reddito"))
    passi_gs.append(Passo("Contributi Gestione Separata", "reddito × aliquota", {"reddito": _euro(base_gs), "aliquota": _pct(aliq_gs)}, contributi, chiave_gs))
    r.contributi_gs = Voce(contributi, tuple(passi_gs))

    # Imposta sostitutiva: reddito meno contributi versati nell'anno
    aliq_imp, chiave_imp = aliquota_imposta(anno, profilo, regole)
    r.aliquota_imposta = aliq_imp
    imponibile = max(reddito - contributi_versati_nell_anno, ZERO)
    imposta = arrotonda(imponibile * aliq_imp)
    r.imposta = Voce(
        imposta,
        (
            Passo(
                "Imponibile",
                "reddito − contributi previdenziali versati nell'anno",
                {"reddito": _euro(reddito), "contributi versati": _euro(contributi_versati_nell_anno)},
                imponibile,
            ),
            Passo("Imposta sostitutiva", "imponibile × aliquota", {"imponibile": _euro(imponibile), "aliquota": _pct(aliq_imp)}, imposta, chiave_imp),
        ),
    )

    r.soglia = verifica_soglia(anno, profilo, totale_ricavi, regole)
    return r


# --------------------------------------------------------------------------- soglia


def limite_ricavi(anno: int, profilo: Profilo, regole: Regole) -> Voce:
    """Soglia degli 85.000 €, ragguagliata ai giorni di attività nel primo anno."""
    limite = regole.dec("soglie.ricavi_uscita_anno_dopo")
    inizio = profilo.data_inizio_attivita
    if anno != inizio.year:
        return Voce(limite, (Passo("Limite dei ricavi", "valore pieno", {}, limite, "soglie.ricavi_uscita_anno_dopo"),))
    giorni_anno = 366 if calendar.isleap(anno) else 365
    giorni = (date(anno, 12, 31) - inizio).days + 1
    ragguagliato = arrotonda(limite * giorni / giorni_anno)
    return Voce(
        ragguagliato,
        (
            Passo(
                "Limite ragguagliato al primo anno",
                "limite × giorni di attività / giorni dell'anno",
                {"limite": _euro(limite), "giorni di attività": str(giorni), "giorni dell'anno": str(giorni_anno)},
                ragguagliato,
                "soglie.ricavi_uscita_anno_dopo",
            ),
        ),
    )


def verifica_soglia(anno: int, profilo: Profilo, ricavi: Decimal, regole: Regole, da_incassare: Decimal = ZERO) -> dict:
    limite = limite_ricavi(anno, profilo, regole)
    immediata = regole.dec("soglie.ricavi_uscita_immediata")
    if ricavi > immediata:
        stato = "uscita_immediata"
    elif ricavi > limite.importo:
        stato = "uscita_anno_dopo"
    else:
        stato = "dentro"
    quota = (ricavi / limite.importo) if limite.importo else ZERO
    avvisi = [p for p in regole.lista_dec("soglie.avvisi_percentuali") if quota >= p]
    proiezione = ricavi + da_incassare
    return {
        "limite": limite.come_dict(),
        "uscita_immediata": str(immediata),
        "ricavi": str(ricavi),
        "percentuale": str(arrotonda(quota * 100)),
        "margine": str(limite.importo - ricavi),
        "stato": stato,
        "avvisi_superati": [str(p) for p in avvisi],
        "proiezione_con_da_incassare": str(proiezione),
        "proiezione_supera_limite": proiezione > limite.importo,
    }


# --------------------------------------------------------------------------- accantonamento


def quota_da_accantonare(
    importo: Decimal,
    ateco: str,
    anno: int,
    profilo: Profilo,
    regole: Regole,
    bollo: Decimal = ZERO,
) -> dict:
    """Quanto mettere da parte su un incasso (RF-06).

    `importo` è il totale incassato (compenso + rivalsa 4% + bollo riaddebitato).
    `bollo` è l'imposta di bollo assolta sulla fattura, da versare comunque.
    """
    coeff = regole.coefficiente(ateco)
    reddito = importo * coeff
    aliq_gs, chiave_gs = aliquota_gs(profilo, regole)
    aliq_imp, chiave_imp = aliquota_imposta(anno, profilo, regole)
    gs = arrotonda(reddito * aliq_gs)
    imp = arrotonda(reddito * aliq_imp)
    totale = gs + imp + bollo
    passi = (
        Passo("Contributi Gestione Separata", "incasso × coefficiente × aliquota GS", {"incasso": _euro(importo), "coefficiente": _pct(coeff), "aliquota": _pct(aliq_gs)}, gs, chiave_gs),
        Passo("Imposta sostitutiva", "incasso × coefficiente × aliquota imposta", {"incasso": _euro(importo), "coefficiente": _pct(coeff), "aliquota": _pct(aliq_imp)}, imp, chiave_imp),
        Passo("Imposta di bollo", "bollo assolto sulla fattura", {}, bollo, "bollo.importo"),
        Passo("Da mettere da parte", "contributi + imposta + bollo", {}, totale),
    )
    return {
        "contributi_gs": str(gs),
        "imposta": str(imp),
        "bollo": str(bollo),
        "da_mettere_da_parte": Voce(totale, passi).come_dict(),
        "netto_disponibile": str(importo - totale),
    }


# --------------------------------------------------------------------------- versamenti


def _rate(totale: Decimal, quote: list[Decimal]) -> list[Decimal]:
    """Divide il totale nelle rate: il resto dell'arrotondamento va sull'ultima."""
    rate = [arrotonda(totale * q) for q in quote[:-1]]
    rate.append(totale - sum(rate, ZERO))
    return rate


def calcola_versamenti(
    anno: int,
    imposta_anno: Voce,
    contributi_anno: Voce,
    regole: Regole,
    acconti_imposta_versati: Decimal = ZERO,
    acconti_gs_versati: Decimal = ZERO,
) -> list[Scadenza]:
    """Saldo dell'anno `anno` e acconti dell'anno successivo (metodo storico).

    Le scadenze cadono nell'anno `anno + 1`. Se `imposta_anno`/`contributi_anno` sono stime
    (anno non chiuso), le scadenze sono marcate come stima.
    """
    pagamento = anno + 1
    d_giugno = lavorativo(da_mese_giorno(pagamento, regole.testo("scadenze.saldo_e_primo_acconto")))
    d_novembre = lavorativo(da_mese_giorno(pagamento, regole.testo("scadenze.secondo_acconto")))
    stima = imposta_anno.stima or contributi_anno.stima
    scadenze: list[Scadenza] = []

    # --- Saldo imposta
    saldo_imp = imposta_anno.importo - acconti_imposta_versati
    scadenze.append(
        Scadenza(
            f"saldo-imposta-{anno}",
            f"Saldo imposta sostitutiva {anno}" if saldo_imp >= 0 else f"Credito d'imposta {anno} (acconti versati in eccesso)",
            d_giugno,
            regole.testo("imposta.codici.saldo"),
            anno,
            Voce(
                saldo_imp,
                (Passo("Saldo", "imposta dell'anno − acconti versati", {"imposta": _euro(imposta_anno.importo), "acconti versati": _euro(acconti_imposta_versati)}, saldo_imp),),
                stima,
            ),
        )
    )

    # --- Acconti imposta anno successivo
    base = imposta_anno.importo
    soglia_zero = regole.dec("imposta.acconto.soglia_nessun_acconto")
    if base > soglia_zero:
        totale = arrotonda(base * regole.dec("imposta.acconto.percentuale"))
        p_tot = Passo("Acconto dovuto", "imposta dell'anno prima × percentuale (metodo storico)", {"imposta": _euro(base), "percentuale": _pct(regole.dec("imposta.acconto.percentuale"))}, totale, "imposta.acconto.percentuale")
        if totale < regole.dec("imposta.acconto.soglia_rata_unica"):
            scadenze.append(
                Scadenza(
                    f"acconto-imposta-{pagamento}-unico",
                    f"Acconto imposta sostitutiva {pagamento} in unica rata",
                    d_novembre,
                    regole.testo("imposta.codici.secondo_acconto"),
                    pagamento,
                    Voce(totale, (p_tot, Passo("Rata unica", f"acconto sotto {regole.testo('imposta.acconto.soglia_rata_unica')} €: tutto a novembre", {}, totale, "imposta.acconto.soglia_rata_unica")), stima),
                )
            )
        else:
            prima, seconda = _rate(totale, regole.lista_dec("imposta.acconto.rate"))
            scadenze.append(Scadenza(f"acconto-imposta-{pagamento}-1", f"Primo acconto imposta sostitutiva {pagamento}", d_giugno, regole.testo("imposta.codici.primo_acconto"), pagamento, Voce(prima, (p_tot, Passo("Prima rata", "acconto × quota prima rata", {"quota": _pct(regole.lista_dec('imposta.acconto.rate')[0])}, prima, "imposta.acconto.rate")), stima)))
            scadenze.append(Scadenza(f"acconto-imposta-{pagamento}-2", f"Secondo acconto imposta sostitutiva {pagamento}", d_novembre, regole.testo("imposta.codici.secondo_acconto"), pagamento, Voce(seconda, (p_tot, Passo("Seconda rata", "acconto − prima rata (il resto dell'arrotondamento va qui)", {"acconto": _euro(totale), "prima rata": _euro(prima)}, seconda, "imposta.acconto.rate")), stima)))

    # --- Saldo contributi GS
    causale = regole.testo("contributi_gs.causale")
    saldo_gs = contributi_anno.importo - acconti_gs_versati
    scadenze.append(
        Scadenza(
            f"saldo-gs-{anno}",
            f"Saldo contributi Gestione Separata {anno}" if saldo_gs >= 0 else f"Credito contributi {anno}",
            d_giugno,
            causale,
            anno,
            Voce(saldo_gs, (Passo("Saldo contributi", "contributi dell'anno − acconti versati", {"contributi": _euro(contributi_anno.importo), "acconti versati": _euro(acconti_gs_versati)}, saldo_gs),), stima),
            sezione="inps",
        )
    )

    # --- Acconti contributi GS anno successivo
    if contributi_anno.importo > 0:
        perc = regole.dec("contributi_gs.acconto.percentuale")
        tot_gs = arrotonda(contributi_anno.importo * perc)
        p_gs = Passo("Acconto contributi dovuto", "contributi dell'anno prima × percentuale", {"contributi": _euro(contributi_anno.importo), "percentuale": _pct(perc)}, tot_gs, "contributi_gs.acconto.percentuale")
        prima, seconda = _rate(tot_gs, regole.lista_dec("contributi_gs.acconto.rate"))
        scadenze.append(Scadenza(f"acconto-gs-{pagamento}-1", f"Primo acconto contributi Gestione Separata {pagamento}", d_giugno, causale, pagamento, Voce(prima, (p_gs, Passo("Prima rata", "acconto × quota prima rata", {}, prima, "contributi_gs.acconto.rate")), stima), sezione="inps"))
        scadenze.append(Scadenza(f"acconto-gs-{pagamento}-2", f"Secondo acconto contributi Gestione Separata {pagamento}", d_novembre, causale, pagamento, Voce(seconda, (p_gs, Passo("Seconda rata", "acconto − prima rata", {}, seconda, "contributi_gs.acconto.rate")), stima), sezione="inps"))

    return sorted(scadenze, key=lambda s: (s.data, s.sezione, s.chiave))


# --------------------------------------------------------------------------- bollo


def calcola_bollo(anno: int, fatture: list[FatturaConBollo], regole: Regole) -> list[Scadenza]:
    """Bollo sulle fatture elettroniche per trimestre, con accorpamento sotto soglia."""
    per_trim: dict[int, Decimal] = defaultdict(lambda: ZERO)
    conteggio: dict[int, int] = defaultdict(int)
    for f in fatture:
        if f.data.year == anno and f.bollo > 0:
            t = (f.data.month - 1) // 3 + 1
            per_trim[t] += f.bollo
            conteggio[t] += 1

    soglia = regole.dec("bollo.soglia_accorpamento")
    date_ord = {t: da_mese_giorno(anno + (1 if t == 4 else 0), regole.testo(f"scadenze.bollo.{t}")) for t in (1, 2, 3, 4)}
    data_effettiva = dict(date_ord)
    nota: dict[int, str] = {}
    if per_trim[1] <= soglia:
        data_effettiva[1] = date_ord[2]
        nota[1] = "importo sotto soglia: rinviato con il 2° trimestre"
        if per_trim[1] + per_trim[2] <= soglia:
            data_effettiva[1] = data_effettiva[2] = date_ord[3]
            nota[1] = nota[2] = "1° e 2° trimestre sotto soglia: rinviati con il 3° trimestre"

    scadenze = []
    for t in (1, 2, 3, 4):
        if per_trim[t] <= 0:
            continue
        passi = [Passo(f"Bollo {t}° trimestre", "somma dei bolli delle fatture del trimestre", {"fatture": str(conteggio[t])}, per_trim[t], "bollo.importo")]
        if t in nota:
            passi.append(Passo("Accorpamento", nota[t], {"soglia": _euro(soglia)}, per_trim[t], "bollo.soglia_accorpamento"))
        scadenze.append(
            Scadenza(
                f"bollo-{anno}-T{t}",
                f"Imposta di bollo fatture elettroniche {t}° trimestre {anno}",
                lavorativo(data_effettiva[t]),
                regole.testo(f"bollo.codici.{t}"),
                anno,
                Voce(per_trim[t], tuple(passi)),
            )
        )
    return scadenze
