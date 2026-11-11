# Novità

Ogni release pubblica come descrizione la sezione della propria versione.
Prima di creare una release aggiungi qui una sezione `## vX.Y.Z`.

## v1.1.0

- **Supporto ai file NUP** (`.nup`), il nuovo contenitore della comunità per i
  giochi Switch 1 e Switch 2 già decifrati (formato 1.0, ancora in bozza):
  - i NUP si aprono nella pagina Giochi con nome, icona e versione, e si
    possono **estrarre** ExeFS e RomFS, anche con l'aggiornamento applicato.
    Non servono chiavi;
  - nuovo pulsante **Crea NUP**: decifra gioco, aggiornamento e DLC di NSP/XCI
    e li unisce in un unico NUP compresso (zstd);
  - verifica dell'integrità dei NUP da riga di comando (`verify`) e nuovo
    comando `nup`.
- I NUP non possono essere convertiti in XCI: non contengono gli NCA cifrati
  originali.
- **Coda di lavoro**: «Crea XCI», «Crea NUP» ed «Estrai» si possono premere
  anche mentre un'operazione è in corso; le nuove richieste si mettono in coda
  e partono da sole. Nella pagina **Avanzamento** le operazioni in attesa si
  possono spostare su e giù o togliere (**Svuota coda** le toglie tutte).
  **Annulla** ferma solo quella in corso e un errore non blocca le successive:
  alla fine un riepilogo indica quali non sono riuscite.

## v1.0.1

- Corretta l'estrazione con l'aggiornamento applicato, che si fermava subito
  con l'errore «unpack_from requires a buffer…» (le tabelle dell'aggiornamento
  venivano decifrate in modo sbagliato).
- Se le tabelle di un aggiornamento sono illeggibili ora compare un messaggio
  chiaro invece di «Errore inatteso».
- Corretto il nome del gioco illeggibile (simboli strani) con gli aggiornamenti
  recenti, che salvano i nomi in forma compressa (fino a 32 lingue).

## v1.0.0

- Prima versione.
- **NSP → XCI**: converte un NSP in XCI, senza decomprimere né ricifrare i dati
  (cambia solo l'intestazione degli NCA, la title key passa dal ticket all'NCA).
- **Unione in un unico XCI**: gioco base + ultimo aggiornamento + DLC dello
  stesso gioco, aggiunti in qualsiasi ordine (anche da XCI esistenti).
- **Estrazione di ExeFS e RomFS**, con l'aggiornamento già applicato (la RomFS
  aggiornata viene ricostruita dal gioco base + patch BKTR); RomFS come cartella
  o come unico `romfs.bin`.
- **Verifica** degli NCA di un NSP (SHA-256) da riga di comando.
- Riga di comando `switchtk-cli` con `info`, `xci`, `extract`, `verify`.
- **Aggiornamento dall'app**: avviso quando esce una nuova versione, download
  verificato e riavvio automatico.
- Interfaccia Fluent (stile Windows 11), tema chiaro e scuro, italiano e inglese.
