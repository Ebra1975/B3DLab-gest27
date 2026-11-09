"""Motore fiscale del regime forfettario. Libreria pura: nessun database, rete o data di sistema."""
from .calcolo import (
    aliquota_gs,
    aliquota_imposta,
    calcola_anno,
    calcola_bollo,
    calcola_versamenti,
    limite_ricavi,
    quota_da_accantonare,
    verifica_soglia,
)
from .regole import Regole
from .tipi import (
    FatturaConBollo,
    Incasso,
    ParametroMancante,
    Passo,
    Profilo,
    RiepilogoAnno,
    Scadenza,
    Voce,
    arrotonda,
)

__all__ = [
    "Regole", "Profilo", "Incasso", "FatturaConBollo", "Passo", "Voce", "Scadenza", "RiepilogoAnno",
    "ParametroMancante", "arrotonda", "calcola_anno", "calcola_versamenti", "calcola_bollo",
    "quota_da_accantonare", "verifica_soglia", "limite_ricavi", "aliquota_imposta", "aliquota_gs",
]
