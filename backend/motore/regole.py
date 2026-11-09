"""Caricamento delle regole fiscali: file YAML di partenza + valori modificati dalle Impostazioni."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .tipi import ParametroMancante

CARTELLA_REGOLE = Path(__file__).resolve().parents[2] / "regole" / "forfettario"


def _cerca(nodo: Any, parti: list[str]) -> Any:
    """Segue la chiave puntata; accetta chiavi che contengono punti (es. codici ATECO '70.20.09')."""
    if not parti:
        return nodo
    if not isinstance(nodo, dict):
        return None
    for n in range(len(parti), 0, -1):  # prima la chiave più lunga possibile
        k = ".".join(parti[:n])
        if k in nodo:
            trovato = _cerca(nodo[k], parti[n:])
            if trovato is not None:
                return trovato
    return None


class Regole:
    """Parametri di un anno d'imposta.

    `base` viene dal file YAML; `modifiche` (chiave puntata -> valore) dalle Impostazioni
    e ha la precedenza. Nessun valore viene indovinato: se manca, ParametroMancante.
    """

    def __init__(self, anno: int, base: dict, modifiche: dict[str, Any] | None = None):
        self.anno = anno
        self.base = base
        self.modifiche = dict(modifiche or {})

    @classmethod
    def carica(cls, anno: int, modifiche: dict[str, Any] | None = None, cartella: Path | None = None) -> "Regole":
        percorso = (cartella or CARTELLA_REGOLE) / f"{anno}.yaml"
        if not percorso.exists():
            raise ParametroMancante(f"file regole {percorso.name}", anno)
        with percorso.open(encoding="utf-8") as f:
            base = yaml.safe_load(f) or {}
        return cls(anno, base, modifiche)

    def valore(self, chiave: str) -> Any:
        if chiave in self.modifiche and self.modifiche[chiave] is not None:
            return self.modifiche[chiave]
        nodo = _cerca(self.base, chiave.split("."))
        if nodo is None:
            raise ParametroMancante(chiave, self.anno)
        return nodo

    def dec(self, chiave: str) -> Decimal:
        v = self.valore(chiave)
        try:
            return Decimal(str(v))
        except InvalidOperation as e:
            raise ParametroMancante(chiave, self.anno) from e

    def lista_dec(self, chiave: str) -> list[Decimal]:
        return [Decimal(str(x)) for x in self.valore(chiave)]

    def testo(self, chiave: str) -> str:
        return str(self.valore(chiave))

    def coefficiente(self, ateco: str) -> Decimal:
        return self.dec(f"coefficienti.{ateco}")

    def appiattite(self) -> dict[str, Any]:
        """Tutti i parametri come chiavi puntate (per la pagina Impostazioni)."""
        piatte: dict[str, Any] = {}

        def visita(nodo: Any, prefisso: str) -> None:
            if isinstance(nodo, dict):
                for k, v in nodo.items():
                    visita(v, f"{prefisso}.{k}" if prefisso else str(k))
            else:
                piatte[prefisso] = nodo

        visita(self.base, "")
        piatte.update(self.modifiche)
        return piatte
