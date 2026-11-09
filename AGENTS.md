# Regole del progetto b3d-fisco

Queste regole valgono per chiunque scriva codice in questo repository: persone, Claude, Codex o altri assistenti. Leggerle prima di ogni modifica.

## Cos'è

Cruscotto fiscale di B3D Lab (ditta individuale in regime forfettario, Gestione Separata INPS).
Dalle fatture elettroniche XML e dagli incassi calcola imposta, contributi, acconti, saldo, bollo e scadenze, mostrando ogni passaggio del calcolo.
La specifica completa è il documento "B3D Lab Gestionale – Specifica Traguardo 1" (link nel README). In caso di dubbio vince la specifica.

## Confini (non superarli senza aggiornare la specifica)

- Emissione delle fatture, invio allo SdI e conservazione restano su **Mexal** (Studio ABR). Questo progetto non trasmette nulla all'Agenzia delle Entrate.
- I dati entrano solo da file XML FatturaPA (singoli, zip o .p7m) e da incassi inseriti a mano.
- Traguardo 1 = solo cruscotto fiscale. Preventivi, previsione di cassa e solleciti sono Traguardi 2 e 3: non si iniziano finché il Traguardo 1 non è usato ogni settimana per 4 settimane (regola di stop).

## Regole sui soldi

1. **Mai float.** Importi in `Decimal` nel motore e in centesimi interi (`*_cent`) nel database.
2. Arrotondamento `ROUND_HALF_UP` al centesimo **solo sul risultato di ogni passaggio**, mai sui valori intermedi di una formula.
3. Ogni importo restituito dal motore porta con sé la **traccia** del calcolo (`Passo`: etichetta, formula, valori, risultato, chiave della regola usata).

## Regole sui parametri

4. **Nessun numero fiscale nel codice.** Aliquote, coefficienti, soglie, codici tributo, scadenze stanno in `regole/forfettario/<anno>.yaml` (valori di partenza) e sono modificabili dalla pagina **Impostazioni** con data di validità e storico. Vale per tutti gli strumenti B3D.
5. Un parametro mancante (`null`) deve produrre un **errore esplicito** (`ParametroMancante`), mai un valore indovinato.
6. Cambiare un valore fiscale richiede un caso di test che lo verifichi.

## Regole sul codice

7. `backend/motore/` è una **libreria pura**: niente database, niente rete, niente `date.today()`. Riceve fatti e parametri, restituisce risultati.
8. Si salvano solo i **fatti** (fatture, incassi, versamenti, valori dichiarati, parametri). Scadenze e totali si ricalcolano sempre.
9. Nessuna cancellazione fisica di fatture o incassi: si annulla con un evento nel registro.
10. Entrate non da fattura (es. NASpI anticipata) **non** sono ricavi del forfettario e non vanno registrate come incassi.
11. Nessuna AI nel Traguardo 1: il calcolo deve essere deterministico e verificabile.

## Test

12. `pytest` deve essere verde prima di ogni commit. La build Docker esegue i test e fallisce se uno è rosso.
13. I casi in `tests/casi/` sono la verità: se un cambio li rompe, il cambio è sbagliato finché lo studio non conferma il contrario.
14. Mai dati personali reali nei test: XML anonimizzati, P.IVA e IBAN finti.

## Stile

- Python 3.12+, tipi annotati, nomi in italiano per il dominio fiscale (`incasso`, `acconto`, `scadenza`).
- Messaggi all'utente in italiano.
- Modifiche piccole e motivate; aggiornare il Decision Log della specifica per ogni scelta di architettura.
