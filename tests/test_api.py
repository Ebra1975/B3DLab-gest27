import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

XML = (Path(__file__).parent / "xml" / "IT00000000000_esempio1.xml").read_bytes()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("B3D_DB", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("B3D_XML", str(tmp_path / "xml"))
    monkeypatch.delenv("B3D_PASSWORD", raising=False)
    import backend.api.main as main

    importlib.reload(main)
    c = TestClient(main.app)
    r = c.put("/api/v1/profilo", json={
        "valido_dal": "2026-09-09", "data_inizio_attivita": "2026-09-09", "ateco_primario": "70.20.09",
        "piva": "IT00000000000", "codice_fiscale": "XXXXXX00X00X000X", "codice_sede_inps": "4955",
    })
    assert r.status_code == 200, r.text
    return c


def _importa(client):
    r = client.post("/api/v1/import/anteprima", files={"files": ("esempio.xml", XML, "application/xml")})
    assert r.status_code == 200, r.text
    dati = r.json()
    assert dati["fatture"][0]["duplicata"] is False
    r = client.post("/api/v1/import/conferma", json={"id": dati["id"]})
    assert r.json()["salvate"] == ["1"]
    return client.get("/api/v1/fatture?anno=2026").json()[0]


def test_flusso_completo(client):
    f = _importa(client)
    assert f["totale_cent"] == 208208 and f["residuo_cent"] == 208208

    # stesso file una seconda volta: duplicato, non salvato
    r = client.post("/api/v1/import/anteprima", files={"files": ("esempio.xml", XML, "application/xml")})
    assert r.json()["fatture"][0]["duplicata"] is True
    assert client.post("/api/v1/import/conferma", json={"id": r.json()["id"]}).json()["salvate"] == []

    # incasso: restituisce subito quanto accantonare
    r = client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    assert r.status_code == 200, r.text
    acc = r.json()["accantonamento"]
    # 2082,08 × 78% = 1624,0224 ; × 26,07% = 423,38 ; × 5% = 81,20 ; + bollo 2,00
    assert acc["contributi_gs"] == "423.38" and acc["imposta"] == "81.20"
    assert acc["da_mettere_da_parte"]["importo"] == "506.58"

    # incasso oltre il totale rifiutato
    assert client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-06", "importo_cent": 1}).status_code == 422

    rie = client.get("/api/v1/anni/2026/riepilogo").json()
    assert rie["ricavi"]["importo"] == "2082.08"
    assert rie["reddito"]["importo"] == "1624.02"
    assert rie["contributi_gs"]["importo"] == "423.38"  # 1624,02 × 26,07%
    assert rie["imposta"]["importo"] == "81.20"
    assert rie["soglia"]["limite"]["importo"] == "26547.95"

    sc = client.get("/api/v1/anni/2026/scadenze").json()
    chiavi = {s["chiave"]: s for s in sc}
    assert chiavi["saldo-imposta-2026"]["data"] == "2027-06-30"
    assert chiavi["saldo-imposta-2026"]["stima"] is True
    # 81,20 > 51,65 ma < 257,52 → acconto 2027 in rata unica a novembre, codice 1791
    unico = chiavi["acconto-imposta-2027-unico"]
    assert unico["data"] == "2027-11-30" and unico["codice"] == "1791" and unico["importo"] == "81.20"
    assert "acconto-imposta-2027-1" not in chiavi
    assert chiavi["bollo-2026-T4"]["data"] == "2027-03-01"

    f24 = client.get("/api/v1/scadenze/saldo-imposta-2026/f24").json()
    assert f24["sezione_erario"][0]["codice_tributo"] == "1792"
    assert f24["sezione_inps"][0]["codice_sede"] == "4955"

    # segna pagato il saldo
    assert client.post("/api/v1/scadenze/saldo-imposta-2026/versamenti", json={"data": "2027-06-25", "importo_cent": 8120}).status_code == 200
    pagata = next(s for s in client.get("/api/v1/anni/2026/scadenze").json() if s["chiave"] == "saldo-imposta-2026")
    assert pagata["pagata"] is True

    csv = client.get("/api/v1/export?dal=2026-01-01&al=2027-12-31").text
    assert "2082,08" in csv and "incasso" in csv


def test_impostazioni_anteprima_e_salvataggio(client):
    f = _importa(client)
    client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    r = client.put("/api/v1/impostazioni/2026", json={"chiave": "contributi_gs.aliquota", "valore": "0.24", "valido_dal": "2026-01-01"})
    eff = r.json()
    assert eff["salvato"] is False
    assert eff["effetto"]["prima"]["contributi_gs"] == "423.38"
    assert eff["effetto"]["dopo"]["contributi_gs"] == "389.76"
    assert client.get("/api/v1/anni/2026/riepilogo").json()["contributi_gs"]["importo"] == "423.38"

    client.put("/api/v1/impostazioni/2026", json={"chiave": "contributi_gs.aliquota", "valore": "0.24", "valido_dal": "2026-01-01", "conferma": True})
    assert client.get("/api/v1/anni/2026/riepilogo").json()["contributi_gs"]["importo"] == "389.76"
    imp = client.get("/api/v1/impostazioni/2026").json()
    assert any(p["chiave"] == "contributi_gs.aliquota" and p["modificato"] for p in imp["parametri"])
    assert any(p["chiave"] == "coefficienti.70.20.09" for p in imp["parametri"])


def test_ateco_sconosciuto_su_riga(client):
    f = _importa(client)
    r = client.patch(f"/api/v1/fatture/{f['id']}/righe/1", json={"ateco": "99.99.99"})
    assert r.status_code == 422
    r = client.patch(f"/api/v1/fatture/{f['id']}/righe/1", json={"ateco": "74.12.09"})
    assert r.status_code == 200


def test_password_richiesta(client, monkeypatch):
    monkeypatch.setenv("B3D_PASSWORD", "segreta")
    assert client.get("/api/v1/profilo").status_code == 401
    tok = client.post("/api/v1/auth/login", json={"password": "segreta"}).json()["token"]
    assert client.get("/api/v1/profilo", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    monkeypatch.setenv("B3D_TOKEN_LETTURA", "n8n-token")
    assert client.get("/api/v1/scadenze", headers={"Authorization": "Bearer n8n-token"}).status_code == 200
    assert client.put("/api/v1/anni/2026/chiusura", headers={"Authorization": "Bearer n8n-token"},
                       json={"imposta_dichiarata_cent": 0, "contributi_dichiarati_cent": 0}).status_code == 401


def test_quadro_lm(client):
    f = _importa(client)
    client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    lm = client.get("/api/v1/anni/2026/quadro-lm").json()
    assert lm["stima"] is True
    riga = lm["attivita"][0]
    assert riga == {"rigo": "LM22", "codice_attivita": "70.20.09", "ricavi": "2082.08", "coefficiente": "78%", "reddito": "1624.02"}
    totali = {t["rigo"]: t["importo"] for t in lm["totali"]}
    assert totali["LM34"] == "1624.02" and totali["LM39"] == "81.20"


def test_avvisi_scadenze_e_saltate(client):
    f = _importa(client)
    client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    # nessun avviso a novembre 2026: la prima scadenza è il bollo del 1/3/2027
    assert client.get("/api/v1/avvisi?oggi=2026-11-20").json()["numero"] == 0
    r = client.get("/api/v1/avvisi?oggi=2027-02-25").json()
    scad = [a for a in r["avvisi"] if a["tipo"].startswith("scadenza")]
    assert len(scad) == 1 and scad[0]["tipo"] == "scadenza_vicina" and "Imposta di bollo" in r["messaggio"]
    assert "regole_provvisorie" in [a["tipo"] for a in r["avvisi"]]  # 2027.yaml è ancora provvisorio
    r = client.get("/api/v1/avvisi?oggi=2027-03-05").json()
    saltata = next(a for a in r["avvisi"] if a["tipo"] == "scadenza_saltata")
    assert "ravvedimento" in saltata["testo"]


def test_avviso_soglia(client):
    f = _importa(client)
    client.put("/api/v1/impostazioni/2026", json={"chiave": "soglie.ricavi_uscita_anno_dopo", "valore": "2500", "valido_dal": "2026-01-01", "conferma": True})
    client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    tipi = [a["tipo"] for a in client.get("/api/v1/avvisi?oggi=2026-11-20").json()["avvisi"]]
    assert "soglia" in tipi  # 2082,08 su 2500 × 114/365 = 780,82 → superata


def test_calendario_ics(client, monkeypatch):
    f = _importa(client)
    client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    ics = client.get("/api/v1/scadenze.ics").text
    assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") == 6
    assert "DTSTART;VALUE=DATE:20270630" in ics and "TRIGGER:-P7D" in ics
    assert all(len(r.encode()) <= 75 for r in ics.split("\r\n"))
    monkeypatch.setenv("B3D_PASSWORD", "segreta")
    monkeypatch.setenv("B3D_TOKEN_LETTURA", "cal-token")
    assert client.get("/api/v1/scadenze.ics").status_code == 401
    assert client.get("/api/v1/scadenze.ics?token=cal-token").status_code == 200


def test_profilo_futuro_non_vale_ancora(client):
    client.put("/api/v1/profilo", json={
        "valido_dal": "2099-01-01", "data_inizio_attivita": "2026-09-09", "ateco_primario": "70.20.09",
        "aliquota_agevolata": False, "piva": "IT00000000000",
    })
    assert client.get("/api/v1/profilo").json()["aliquota_agevolata"] == 1  # vale ancora la versione di oggi


def test_parametro_mancante_diventa_avviso(client):
    f = _importa(client)
    client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    # nessun file di regole per il 2031: deve comparire un avviso, non un silenzio
    tipi = [a["tipo"] for a in client.get("/api/v1/avvisi?oggi=2031-01-10").json()["avvisi"]]
    assert "parametro_mancante" in tipi


def test_quadro_lm_anno_chiuso_usa_dichiarato(client):
    f = _importa(client)
    client.post(f"/api/v1/fatture/{f['id']}/incassi", json={"data": "2026-11-05", "importo_cent": 208208})
    client.put("/api/v1/anni/2026/chiusura", json={"imposta_dichiarata_cent": 8100, "contributi_dichiarati_cent": 42300})
    lm = client.get("/api/v1/anni/2026/quadro-lm").json()
    assert lm["stima"] is False
    assert next(t for t in lm["totali"] if t["rigo"] == "LM39")["importo"] == "81.00"


def test_regole_provvisorie_si_confermano_dalle_impostazioni(client):
    _importa(client)
    tipi = lambda: [a["tipo"] for a in client.get("/api/v1/avvisi?oggi=2027-01-15").json()["avvisi"]]
    assert "regole_provvisorie" in tipi()
    r = client.put("/api/v1/impostazioni/2027", json={"chiave": "provvisorio", "valore": "false", "valido_dal": "2027-01-15", "conferma": True})
    assert r.status_code == 200, r.text
    assert "regole_provvisorie" not in tipi()
