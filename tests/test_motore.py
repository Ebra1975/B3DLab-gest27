from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from backend.motore import (
    FatturaConBollo,
    Incasso,
    ParametroMancante,
    Profilo,
    Regole,
    Voce,
    calcola_anno,
    calcola_bollo,
    calcola_versamenti,
    limite_ricavi,
    quota_da_accantonare,
)
from backend.motore.calendario import lavorativo, pasqua

CASI = yaml.safe_load((Path(__file__).parent / "casi" / "esempi_pubblici.yaml").read_text(encoding="utf-8"))
D = Decimal


@pytest.fixture(scope="module")
def regole():
    return Regole.carica(2026)


# ------------------------------------------------------------------ casi pubblici


@pytest.mark.parametrize("caso", CASI["accantonamento"], ids=lambda c: c["nome"])
def test_accantonamento(caso, regole):
    profilo = Profilo(date.fromisoformat(caso["inizio_attivita"]), caso["agevolata"])
    r = quota_da_accantonare(D(caso["incasso"]), caso["ateco"], caso["anno"], profilo, regole, D(caso["bollo"]))
    a = caso["atteso"]
    assert r["contributi_gs"] == a["contributi_gs"]
    assert r["imposta"] == a["imposta"]
    assert r["da_mettere_da_parte"]["importo"] == a["da_mettere_da_parte"]
    assert r["netto_disponibile"] == a["netto_disponibile"]


@pytest.mark.parametrize("caso", CASI["anno"], ids=lambda c: c["nome"])
def test_anno(caso, regole):
    profilo = Profilo(date.fromisoformat(caso["inizio_attivita"]), caso["agevolata"])
    incassi = [Incasso(date.fromisoformat(i["data"]), D(i["importo"]), i["ateco"]) for i in caso["incassi"]]
    r = calcola_anno(caso["anno"], profilo, incassi, regole, D(caso["contributi_versati"]))
    a = caso["atteso"]
    assert str(r.reddito.importo) == a["reddito"]
    assert str(r.contributi_gs.importo) == a["contributi_gs"]
    assert str(r.imposta.importo) == a["imposta"]


@pytest.mark.parametrize("caso", CASI["versamenti"], ids=lambda c: c["nome"])
def test_versamenti(caso, regole):
    sc = calcola_versamenti(caso["anno"], Voce(D(caso["imposta"])), Voce(D(caso["contributi"])), regole)
    if "atteso_per_data" in caso:
        per_data: dict[str, Decimal] = {}
        for s in sc:
            per_data[s.data.isoformat()] = per_data.get(s.data.isoformat(), D(0)) + s.importo
        for giorno, totale in caso["atteso_per_data"].items():
            assert str(per_data[giorno]) == totale, giorno
    for chiave, importo in caso.get("atteso_per_chiave", {}).items():
        assert str(next(s for s in sc if s.chiave == chiave).importo) == importo


# ------------------------------------------------------------------ caso B3D Lab


def test_soglia_ragguagliata_primo_anno_b3d(regole):
    profilo = Profilo(date(2026, 9, 9))
    v = limite_ricavi(2026, profilo, regole)
    assert v.importo == D("26547.95")  # 85.000 × 114 / 365
    assert v.passi[0].valori["giorni di attività"] == "114"
    assert limite_ricavi(2027, profilo, regole).importo == D("85000.00")


def test_aliquota_agevolata_fino_al_2030(regole):
    from backend.motore import aliquota_imposta

    profilo = Profilo(date(2026, 9, 9), aliquota_agevolata=True)
    assert aliquota_imposta(2030, profilo, regole)[0] == D("0.05")
    assert aliquota_imposta(2031, profilo, regole)[0] == D("0.15")


def test_nessun_acconto_sotto_51_65(regole):
    sc = calcola_versamenti(2026, Voce(D("50.00")), Voce(D("0")), regole)
    assert not [s for s in sc if s.chiave.startswith("acconto-imposta")]


def test_acconto_rata_unica_sotto_257_52(regole):
    sc = calcola_versamenti(2026, Voce(D("200.00")), Voce(D("0")), regole)
    acc = [s for s in sc if s.chiave.startswith("acconto-imposta")]
    assert len(acc) == 1 and acc[0].data == date(2027, 11, 30) and acc[0].codice == "1791"


def test_credito_se_acconti_eccedenti(regole):
    sc = calcola_versamenti(2027, Voce(D("1500.00")), Voce(D("0")), regole, acconti_imposta_versati=D("3000.00"))
    saldo = next(s for s in sc if s.chiave == "saldo-imposta-2027")
    assert saldo.importo == D("-1500.00") and "Credito" in saldo.descrizione


def test_incassi_di_altri_anni_esclusi(regole):
    profilo = Profilo(date(2026, 9, 9))
    incassi = [Incasso(date(2026, 12, 20), D("1000"), "70.20.09"), Incasso(date(2027, 1, 5), D("2082.08"), "70.20.09")]
    assert calcola_anno(2026, profilo, incassi, regole).ricavi.importo == D("1000")


def test_piu_attivita_soglia_unica(regole):
    profilo = Profilo(date(2026, 9, 9))
    incassi = [Incasso(date(2026, 10, 1), D("1000"), "70.20.09"), Incasso(date(2026, 11, 1), D("500"), "74.12.09")]
    r = calcola_anno(2026, profilo, incassi, regole)
    assert r.ricavi.importo == D("1500")
    assert r.reddito.importo == D("1170.00")
    assert set(r.ricavi_per_ateco) == {"70.20.09", "74.12.09"}


def test_nota_di_credito_riduce_i_ricavi(regole):
    profilo = Profilo(date(2026, 9, 9))
    incassi = [Incasso(date(2026, 10, 1), D("1200"), "70.20.09"), Incasso(date(2026, 10, 20), D("-1200"), "70.20.09")]
    assert calcola_anno(2026, profilo, incassi, regole).imposta.importo == D("0.00")


# ------------------------------------------------------------------ bollo


def test_bollo_accorpato_fino_a_novembre(regole):
    fatture = [FatturaConBollo("1", date(2026, 2, 10), D("2")), FatturaConBollo("2", date(2026, 5, 3), D("2")), FatturaConBollo("3", date(2026, 10, 8), D("2"))]
    sc = {s.chiave: s for s in calcola_bollo(2026, fatture, regole)}
    assert sc["bollo-2026-T1"].data == date(2026, 11, 30) and sc["bollo-2026-T1"].codice == "2521"
    assert sc["bollo-2026-T2"].data == date(2026, 11, 30)
    assert sc["bollo-2026-T4"].data == date(2027, 3, 1)  # 28/2/2027 è domenica
    assert sc["bollo-2026-T4"].codice == "2524"


# ------------------------------------------------------------------ calendario


def test_slittamento_e_pasqua():
    assert pasqua(2027) == date(2027, 3, 28)
    assert lavorativo(date(2027, 6, 30)) == date(2027, 6, 30)  # mercoledì
    assert lavorativo(date(2028, 6, 30)) == date(2028, 6, 30)
    assert lavorativo(date(2029, 6, 30)) == date(2029, 7, 2)  # sabato -> lunedì


# ------------------------------------------------------------------ parametri


def test_parametro_mancante_blocca_il_calcolo():
    base = Regole.carica(2026).base
    regole = Regole(2026, base, {"coefficienti.70.20.09": None})
    regole.base = {**base, "coefficienti": {"70.20.09": None}}
    with pytest.raises(ParametroMancante):
        calcola_anno(2026, Profilo(date(2026, 9, 9)), [Incasso(date(2026, 10, 1), D("100"), "70.20.09")], regole)


def test_modifica_da_impostazioni_ha_precedenza():
    regole = Regole.carica(2026, {"contributi_gs.aliquota": "0.25"})
    r = calcola_anno(2026, Profilo(date(2026, 9, 9)), [Incasso(date(2026, 10, 1), D("1000"), "70.20.09")], regole)
    assert r.contributi_gs.importo == D("195.00")


def test_ateco_sconosciuto_errore_esplicito(regole):
    with pytest.raises(ParametroMancante):
        calcola_anno(2026, Profilo(date(2026, 9, 9)), [Incasso(date(2026, 10, 1), D("100"), "99.99.99")], regole)


# ------------------------------------------------------------------ proprietà


importi = st.decimals(min_value=D("0"), max_value=D("200000"), places=2, allow_nan=False, allow_infinity=False)


@given(imposta=importi, contributi=importi)
def test_rate_sommano_all_acconto(imposta, contributi):
    regole = Regole.carica(2026)
    sc = calcola_versamenti(2026, Voce(imposta), Voce(contributi), regole)
    acc_imp = sum((s.importo for s in sc if s.chiave.startswith("acconto-imposta")), D(0))
    if imposta > D("51.65"):
        assert acc_imp == imposta.quantize(D("0.01"))
    acc_gs = sum((s.importo for s in sc if s.chiave.startswith("acconto-gs")), D(0))
    assert acc_gs == (contributi * D("0.80")).quantize(D("0.01"), rounding="ROUND_HALF_UP")
    assert all(s.importo >= 0 for s in sc)


@given(incasso=importi)
def test_accantonamento_mai_oltre_l_incasso(incasso):
    regole = Regole.carica(2026)
    r = quota_da_accantonare(incasso, "70.20.09", 2026, Profilo(date(2026, 9, 9)), regole)
    assert D(r["da_mettere_da_parte"]["importo"]) <= incasso
    assert D(r["netto_disponibile"]) >= 0
