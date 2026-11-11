# Novità

Ogni release pubblica come descrizione la sezione della propria versione.
Prima di creare una release aggiungi qui una sezione `## vX.Y.Z`.

## v1.1.1

- Corretto l'avviso «Manca il gioco base» sui NUP che contengono il gioco
  completo già aggiornato (un solo pacchetto «aggiornamento autonomo», come nei
  dump di giochi installati): ora compaiono come **Gioco aggiornato**, senza
  avvisi, e si estraggono e convertono normalmente. Lo stesso vale per gli
  aggiornamenti autonomi negli archivi NX.
- Corretto l'avviso «Manca il gioco base» su archivi NX che contengono il
  gioco: i DLC tenuti in un'applicazione separata (ad esempio i pacchetti
  Nintendo Switch 2 Edition) ora vengono uniti al gioco dello stesso file.
- Un gioco con soli DLC mostra ora un avviso chiaro («Contiene solo DLC»).
- Gli archivi NX con la prima intestazione danneggiata vengono riconosciuti
  (si usa la copia di riserva).
- Nei giochi con più programmi tutti i programmi compaiono nell'elenco.

## v1.1.0

- **Supporto agli archivi NX** (`.nx`, [nxformat](https://github.com/fab918/nxformat)
  1.0.2): si aprono, si estraggono e si creano da NSP, XCI, NUP o da altri NX
  (**Crea archivio → NX**, o `switchtk-cli nx`). Gioco e aggiornamento
  condividono i dati uguali, ogni blocco è verificato in lettura e il lettore
  rispetta tutte le 61 prove di conformità ufficiali del formato. Gli archivi
  NX "nativi" (con gli NCA decifrati) si estraggono così come sono.
- **Più veloce**: compressione su più core (NX e NUP) e niente tentativi di
  compressione inutili sui dati già compressi.
- **Supporto ai file NUP** (`.nup`), il nuovo contenitore della comunità per i
  giochi Switch 1 e Switch 2 già decifrati (formato 1.0, ancora in bozza):
  - i NUP si aprono nella pagina Giochi con nome, icona e versione, e si
    possono **estrarre** ExeFS e RomFS, anche con l'aggiornamento applicato.
    Non servono chiavi;
  - **Crea archivio → NUP**: decifra gioco, aggiornamento e DLC di NSP/XCI e
    li unisce in un unico NUP compresso (zstd);
  - verifica dell'integrità dei NUP da riga di comando (`verify`) e nuovo
    comando `nup`.
- NUP e NX non possono essere convertiti in XCI: non contengono gli NCA
  cifrati originali.
- **Coda di lavoro**: «Crea XCI», «Crea archivio» ed «Estrai» si possono premere
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
