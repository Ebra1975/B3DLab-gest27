# b3d-fisco · Cruscotto fiscale B3D Lab (Traguardo 1)

Dalle fatture elettroniche XML emesse con Mexal e dagli incassi calcola, per il regime forfettario con Gestione Separata INPS:
imposta sostitutiva, contributi, saldo e acconti, bollo, soglia dei ricavi (ragguagliata nel primo anno) e il facsimile dell'F24.
Ogni cifra ha il pulsante **i** che mostra i passaggi del calcolo.

Specifica: «B3D Lab Gestionale – Specifica Traguardo 1» (Claude Docs). Regole per chi scrive codice (persone e assistenti AI): [AGENTS.md](AGENTS.md).

## Cosa c'è

| Parte | Cartella | Stato |
|---|---|---|
| Motore fiscale (libreria pura, Decimal, traccia dei passaggi) | `backend/motore/` | fatto, testato |
| Regole 2026 (valori di partenza) | `regole/forfettario/2026.yaml` | fatto |
| Import XML FatturaPA (.xml, .p7m, .zip) con anteprima e controllo doppioni | `backend/importatore/` | fatto, testato |
| API REST `/api/v1` + SQLite | `backend/api/` | fatto, testato |
| Pagina Impostazioni (parametri con data di validità, storico, effetto sui totali prima di salvare) | API + interfaccia | fatto |
| Interfaccia | `frontend/index.html` | **provvisoria** (HTML semplice, nessuna build) |
| Docker, backup, prova di ripristino | `deploy/`, `docker-compose.yml` | fatto |

Fatti anche: valori per il **quadro LM** (Riepilogo), **calendario .ics** abbonabile (Scadenze), endpoint **/api/v1/avvisi** con workflow n8n pronto in `deploy/n8n/`.
Non ancora fatto nel Traguardo 1: le aggiunte fiscali che aspettano parametri dallo studio (inversione contabile, ravvedimento, rateizzazione).

### Avvisi su Telegram (n8n)

1. In n8n: *Import from file* → `deploy/n8n/B3D-Amministrazione-Avvisi-Fiscali.json`.
2. Nel nodo HTTP sostituisci `INCOLLA_QUI_B3D_TOKEN_LETTURA` con il valore di `B3D_TOKEN_LETTURA` del file `.env` (`grep TOKEN ~/b3d-fisco/.env`).
3. Nel nodo Telegram scegli la credenziale del bot esistente; manda al gruppo B3D Lab Control, topic Amministrazione (1332).
4. Attiva il workflow: ogni giorno alle 08:00 manda un messaggio solo se c'è qualcosa (scadenze entro 7 giorni, scadenze saltate, soglia ricavi).

### Calendario

In **Scadenze** c'è l'indirizzo da copiare: su iPhone/Mac *Calendario → Aggiungi calendario in abbonamento*, su Google Calendar *Altri calendari → Da URL* (solo se raggiungibile da internet; con Tailscale funziona su iPhone e Mac).

## Installazione sul BMAX

```bash
# 1. Copia la cartella sul BMAX (es. in ~/b3d-fisco) e controlla che la porta 8200 sia libera
ss -tlnp | grep 8200 || echo "porta libera"

# 2. Password
cd ~/b3d-fisco
cp .env.esempio .env
nano .env            # imposta B3D_PASSWORD

# 3. Build (esegue anche i test: se uno fallisce, si ferma) e avvio
docker compose up -d --build

# 4. Apri dal PC o dal telefono (LAN o Tailscale)
#    http://192.168.1.143:8200  oppure, via Tailscale, http://100.109.199.99:8200
```

Primo uso: **Impostazioni → Profilo fiscale** (inizio attività 09/09/2026, ATECO primario 70.20.09, aliquota agevolata, codice sede INPS), poi **Fatture → Importa** con gli XML scaricati da Mexal o da Fatture e Corrispettivi, poi segna gli incassi con la data dell'estratto conto.

### Backup

```bash
chmod +x deploy/backup.sh deploy/ripristino.sh
crontab -e
# aggiungi:
30 2 * * * /home/<utente>/b3d-fisco/deploy/backup.sh >> /home/<utente>/b3d-fisco/dati/backup.log 2>&1
```

Una volta al mese: `deploy/ripristino.sh` (confronta il backup con il database in uso). Una copia settimanale di `dati/backup/` va fuori dal BMAX (tower Lenovo o disco esterno).

### Aggiornare

```bash
git pull            # o copia i file nuovi
docker compose up -d --build
```

## Sviluppo (VS Code Remote SSH sul BMAX)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                                   # devono essere tutti verdi
uvicorn backend.api.main:app --reload --port 8201     # istanza di prova, database in ./dati
```

Con Codex (ChatGPT Plus) o altri assistenti: il file `AGENTS.md` viene letto in automatico e contiene le regole da rispettare.

## Verifica dei numeri

`tests/casi/esempi_pubblici.yaml` contiene esempi pubblici (flatfour.it) su cui il motore dà gli stessi risultati al centesimo,
tra cui il primo anno: saldo 2026 + primo acconto 2027 = 6.863,69 € il 30/06/2027 e 2.016,77 € il 30/11/2027.

Il collaudo vero (criteri di accettazione della specifica) è il confronto con lo Studio ABR sulle fatture reali:
compila `tests/casi/casi_reali.yaml` quando hai i numeri dello studio.

## Note importanti

- Non trasmette nulla allo SdI: emissione e conservazione restano su Mexal.
- La NASpI anticipata **non** va registrata come incasso.
- Ogni importo delle scadenze dell'anno in corso è una **stima** finché l'anno non è chiuso con i valori della dichiarazione (`PUT /api/v1/anni/{anno}/chiusura`).
