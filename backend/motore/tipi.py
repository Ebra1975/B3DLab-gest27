"""Tipi del motore fiscale. Nessun I/O, nessuna data di sistema."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def arrotonda(valore: Decimal) -> Decimal:
    """Arrotonda al centesimo (ROUND_HALF_UP). Da usare solo sul risultato di un passaggio."""
    return valore.quantize(CENT, rounding=ROUND_HALF_UP)


class ParametroMancante(Exception):
    """Un parametro fiscale necessario al calcolo non è valorizzato."""

    def __init__(self, chiave: str, anno: int | None = None):
        self.chiave = chiave
        self.anno = anno
        dove = f" per il {anno}" if anno else ""
        super().__init__(f"Parametro mancante{dove}: '{chiave}'. Compilalo nella pagina Impostazioni.")


@dataclass(frozen=True)
class Passo:
    """Un passaggio del calcolo, mostrato sotto 'come si arriva a questa cifra'."""

    etichetta: str
    formula: str
    valori: dict[str, str]
    risultato: Decimal
    regola: str | None = None

    def come_dict(self) -> dict:
        return {
            "etichetta": self.etichetta,
            "formula": self.formula,
            "valori": self.valori,
            "risultato": str(self.risultato),
            "regola": self.regola,
        }


@dataclass(frozen=True)
class Voce:
    """Un importo con la traccia di come è stato calcolato."""

    importo: Decimal
    passi: tuple[Passo, ...] = ()
    stima: bool = False

    def come_dict(self) -> dict:
        return {
            "importo": str(self.importo),
            "stima": self.stima,
            "traccia": [p.come_dict() for p in self.passi],
        }


@dataclass(frozen=True)
class Profilo:
    data_inizio_attivita: date
    aliquota_agevolata: bool = True
    altra_copertura_previdenziale: bool = False
    gestione: str = "GS"  # solo Gestione Separata implementata nel Traguardo 1


@dataclass(frozen=True)
class Incasso:
    """Incasso già attribuito a un codice ATECO. Negativo per i rimborsi di note di credito."""

    data: date
    importo: Decimal
    ateco: str
    riferimento: str = ""


@dataclass(frozen=True)
class FatturaConBollo:
    numero: str
    data: date
    bollo: Decimal  # importo del bollo virtuale assolto sulla fattura (0 o 2,00)


@dataclass(frozen=True)
class Scadenza:
    chiave: str
    descrizione: str
    data: date
    codice: str
    anno_riferimento: int
    voce: Voce
    sezione: str = "erario"  # "erario" o "inps"

    @property
    def importo(self) -> Decimal:
        return self.voce.importo

    def come_dict(self) -> dict:
        return {
            "chiave": self.chiave,
            "descrizione": self.descrizione,
            "data": self.data.isoformat(),
            "codice": self.codice,
            "anno_riferimento": self.anno_riferimento,
            "sezione": self.sezione,
            **self.voce.come_dict(),
        }


@dataclass
class RiepilogoAnno:
    anno: int
    ricavi_per_ateco: dict[str, Decimal] = field(default_factory=dict)
    ricavi: Voce = field(default_factory=lambda: Voce(ZERO))
    reddito: Voce = field(default_factory=lambda: Voce(ZERO))
    contributi_gs: Voce = field(default_factory=lambda: Voce(ZERO))
    imposta: Voce = field(default_factory=lambda: Voce(ZERO))
    aliquota_imposta: Decimal = ZERO
    soglia: dict = field(default_factory=dict)

    def come_dict(self) -> dict:
        return {
            "anno": self.anno,
            "ricavi_per_ateco": {k: str(v) for k, v in self.ricavi_per_ateco.items()},
            "ricavi": self.ricavi.come_dict(),
            "reddito": self.reddito.come_dict(),
            "contributi_gs": self.contributi_gs.come_dict(),
            "imposta": self.imposta.come_dict(),
            "aliquota_imposta": str(self.aliquota_imposta),
            "soglia": self.soglia,
        }
