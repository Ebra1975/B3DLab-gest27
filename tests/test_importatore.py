import io
import zipfile
from pathlib import Path

from backend.importatore.fatturapa import leggi_file

XML = (Path(__file__).parent / "xml" / "IT00000000000_esempio1.xml").read_bytes()


def test_legge_fattura_con_rivalsa_e_bollo():
    fatture, errori = leggi_file("esempio.xml", XML)
    assert not errori
    f = fatture[0]
    assert (f.numero, f.data, f.anno, f.tipo) == ("1", "2026-10-08", 2026, "TD01")
    assert f.cliente_denominazione == "Cliente Esempio S.p.A."
    assert f.cliente_piva == "IT11111111111"
    assert f.imponibile_cent == 200200
    assert f.rivalsa_cent == 8008
    assert f.bollo_cent == 200 and f.bollo_addebitato
    assert f.totale_cent == 208208
    assert f.avvisi == []
    assert len(f.righe) == 2


def test_legge_zip_e_ignora_metadati():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2026/IT00000000000_esempio1.xml", XML)
        z.writestr("2026/IT00000000000_esempio1_metaDato.xml", b"<x/>")
        z.writestr("leggimi.txt", b"ciao")
    fatture, errori = leggi_file("fatture.zip", buf.getvalue())
    assert len(fatture) == 1 and not errori


def test_legge_p7m():
    firmato = b"\x30\x82\x10\x00FIRMA-FINTA" + XML + b"\x00\xa0CERTIFICATO"
    fatture, errori = leggi_file("esempio.xml.p7m", firmato)
    assert not errori and fatture[0].totale_cent == 208208


def test_file_non_valido_genera_errore():
    fatture, errori = leggi_file("rotto.xml", b"<non chiuso")
    assert not fatture and errori


def test_nota_di_credito_senza_collegamento_avvisa():
    nota = XML.replace(b"<TipoDocumento>TD01", b"<TipoDocumento>TD04")
    f = leggi_file("nota.xml", nota)[0][0]
    assert f.tipo == "TD04" and any("collegata" in a for a in f.avvisi)
