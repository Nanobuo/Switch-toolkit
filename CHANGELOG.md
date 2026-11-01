# Novità

Ogni release pubblica come descrizione la sezione della propria versione.
Prima di creare una release aggiungi qui una sezione `## vX.Y.Z`.

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
