"""Date delle scadenze: slittamento al primo giorno lavorativo successivo."""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def pasqua(anno: int) -> date:
    """Domenica di Pasqua (algoritmo gregoriano anonimo)."""
    a = anno % 19
    b, c = divmod(anno, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mese = (h + l - 7 * m + 114) // 31
    giorno = ((h + l - 7 * m + 114) % 31) + 1
    return date(anno, mese, giorno)


def festivi(anno: int) -> set[date]:
    fissi = [(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)]
    giorni = {date(anno, m, g) for m, g in fissi}
    giorni.add(pasqua(anno) + timedelta(days=1))  # lunedì dell'Angelo
    return giorni


def lavorativo(giorno: date) -> date:
    """Se la scadenza cade di sabato, domenica o festivo, slitta al primo giorno lavorativo."""
    while giorno.weekday() >= 5 or giorno in festivi(giorno.year):
        giorno += timedelta(days=1)
    return giorno


def da_mese_giorno(anno: int, mese_giorno: str) -> date:
    """'06-30' -> 30 giugno; '02-ultimo' -> ultimo giorno di febbraio. Senza slittamento."""
    mese_s, giorno_s = mese_giorno.split("-")
    mese = int(mese_s)
    if giorno_s == "ultimo":
        return date(anno, mese, calendar.monthrange(anno, mese)[1])
    return date(anno, mese, int(giorno_s))
