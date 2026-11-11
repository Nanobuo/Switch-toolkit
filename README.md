# Switch Toolkit

Programma per Windows che fa da coltellino svizzero per i giochi **Nintendo Switch**:

- **converte** un NSP in XCI, NUP o NX;
- **unisce** gioco, aggiornamento e DLC in un unico XCI, NUP o NX;
- **estrae** ExeFS e RomFS, anche con l'aggiornamento già applicato (utile per
  mod e traduzioni).

![Pagina Giochi](docs/giochi-chiaro.png)

## Download

Scarica `SwitchToolkit-windows-x64.exe` dalla pagina
[**Releases**](https://github.com/Nanobuo/Switch-toolkit/releases/latest) e
avvialo: non serve installare nulla. Il programma si aggiorna da solo quando
esce una nuova versione.

## Le chiavi

Serve il file `prod.keys` ricavato dalla **tua** console con Lockpick_RCM: il
programma non contiene chiavi. Mettilo nella stessa cartella del programma
oppure sceglilo in **Impostazioni → Chiavi**. Se usi già un emulatore (Yuzu,
Ryujinx e simili) viene trovato da solo.

Usa il programma solo con giochi di cui possiedi una copia.

## Come si usa

1. Trascina i file `.nsp`, `.xci`, `.nup` o `.nx` nella pagina **Giochi**. Puoi aggiungere
   insieme gioco, aggiornamenti e DLC, in qualsiasi ordine: vengono raggruppati
   per gioco.
2. In basso scegli dove salvare i file.
3. Premi:
   - **Crea XCI** per ottenere un unico file con gioco, ultimo aggiornamento e
     DLC (con un solo NSP è una semplice conversione);
   - **Crea archivio → NX** o **NUP** per ottenere lo stesso in un formato con i
     dati già decifrati (vedi sotto);
   - **Estrai ExeFS/RomFS** per ottenere le cartelle `exefs` e `romfs`.

Puoi premere i pulsanti anche mentre un'operazione è in corso: le nuove
richieste si mettono **in coda** e partono da sole. Nella pagina
**Avanzamento** puoi riordinarle o toglierle; **Annulla** ferma solo quella in
corso.

In **Impostazioni** puoi scegliere cosa includere nell'XCI e cosa estrarre,
oltre a tema e lingua.

![Operazione completata](docs/completato-scuro.png)

## I file NX e NUP

NX e NUP sono due nuovi formati creati dalla comunità per i giochi Switch 1 e
Switch 2 **già decifrati**: un unico file con gioco, aggiornamenti e DLC.

- **NX** ([nxformat](https://github.com/fab918/nxformat)) è il consigliato:
  legge molto più velocemente i dati compressi e salva una sola volta i file
  che gioco e aggiornamento hanno in comune.
- **NUP** ([nup](https://github.com/a55uka/nup)) comprime ogni file per
  intero.
- Si aprono, si estraggono e si convertono l'uno nell'altro **senza chiavi**.
  Per crearli da NSP e XCI serve il tuo `prod.keys`.
- **Non** possono tornare XCI o NSP: non contengono più i file originali
  cifrati.
- Sono formati appena nati e gli emulatori li stanno ancora adottando:
  controlla che il tuo li supporti.

## Da sapere

- Gli XCI creati funzionano con gli **emulatori** e con gli installer
  **homebrew**.
- I file compressi (**NSZ**, **XCZ**) vanno prima decompressi.
- Per estrarre la RomFS di un aggiornamento serve anche il gioco base.
- Gli XCI più grandi di 4 GB non stanno su una scheda SD in formato FAT32: usa
  exFAT.
- Se qualcosa non funziona, la pagina **Registro** spiega il motivo.

## Riga di comando

Per chi preferisce il terminale c'è anche `switchtk-cli-windows-x64.exe`:

```bat
switchtk-cli xci gioco.nsp aggiornamento.nsp dlc.nsp -o D:\Switch
switchtk-cli nx gioco.nsp aggiornamento.nsp dlc.nsp -o D:\Switch
switchtk-cli nup gioco.nsp aggiornamento.nsp dlc.nsp -o D:\Switch
switchtk-cli extract gioco.nsp aggiornamento.nsp -o D:\Switch
switchtk-cli info gioco.nsp
```

`switchtk-cli --help` elenca tutte le opzioni.

## Per sviluppatori

Per compilare serve Python 3.10 o successivo: lancia `build.bat`, che esegue
i test e crea gli `.exe` in `dist\`. I test NX usano le fixture di conformità
del progetto nxformat (licenza MIT, in `tests/nx_fixtures`).

Per pubblicare una nuova versione aggiorna `__version__` in
`switchtk/__init__.py`, aggiungi le novità in [`CHANGELOG.md`](CHANGELOG.md) e
avvia **Actions → Build → Run workflow** indicando la versione (es. `v1.1.0`).

## Licenza

GPL-3.0. Switch Toolkit non è affiliato a Nintendo.
