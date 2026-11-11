"""English translations, keyed by the Italian source text (see i18n.tr)."""

EN = {
    # -- command line --------------------------------------------------------------
    "file prod.keys (predefinito: cercato nelle cartelle abituali)":
        "prod.keys file (default: looked for in the usual folders)",
    "file title.keys facoltativo": "optional title.keys file",
    "elenca anche gli NCA contenuti": "also list the NCAs inside",
    "converte in XCI, unendo gioco, aggiornamento e DLC dello stesso gioco":
        "convert to XCI, merging game, update and DLC of the same game",
    "file NSP/XCI o cartelle (in qualsiasi ordine)": "NSP/XCI files or folders (in any order)",
    "cartella di destinazione": "destination folder",
    "un XCI per ogni file, senza unire": "one XCI per file, without merging",
    "non includere l'aggiornamento": "do not include the update",
    "non includere i DLC": "do not include DLC",
    "non controllare lo spazio libero": "do not check the free space",
    "estrae ExeFS e RomFS (con l'aggiornamento applicato)":
        "extract ExeFS and RomFS (with the update applied)",
    "non estrarre la ExeFS": "do not extract the ExeFS",
    "non estrarre la RomFS": "do not extract the RomFS",
    "salva la RomFS come unico file romfs.bin": "save the RomFS as a single romfs.bin file",
    "estrai il gioco base senza applicare l'aggiornamento":
        "extract the base game without applying the update",
    "ATTENZIONE: {message}": "WARNING: {message}",
    "ERRORE: {message}": "ERROR: {message}",
    "Annullato.": "Cancelled.",
    "Creo {name}": "Creating {name}",
    "NON CORRISPONDE": "DOES NOT MATCH",
    "MANCANTE": "MISSING",
    "Nota: negli XCI creati da un NSP l'intestazione degli NCA è modificata, quindi l'impronta "
    "non corrisponde più.":
        "Note: in XCIs made from an NSP the NCA headers are modified, so the digest no longer "
        "matches.",

    # -- keys ----------------------------------------------------------------------
    "File prod.keys non trovato. Ricavalo dalla tua console con Lockpick_RCM e indicalo nelle "
    "impostazioni (o con --keys).":
        "prod.keys not found. Dump it from your console with Lockpick_RCM and choose it in the "
        "settings (or with --keys).",
    "Impossibile leggere {path}: {error}": "Cannot read {path}: {error}",
    "{path} non contiene header_key: il file non è un prod.keys valido.":
        "{path} has no header_key: it is not a valid prod.keys file.",
    "Manca la chiave {name} in prod.keys: probabilmente il gioco richiede un firmware più "
    "recente di quello da cui sono state ricavate le chiavi.":
        "The key {name} is missing from prod.keys: the game probably needs a newer firmware "
        "than the one the keys were dumped from.",

    # -- file formats --------------------------------------------------------------
    "File troncato: mancano {n} byte.": "Truncated file: {n} bytes missing.",
    "Dati fuori dai limiti del contenitore (file troncato?).":
        "Data beyond the end of the container (truncated file?).",
    "Intestazione PFS0/HFS0 non valida.": "Invalid PFS0/HFS0 header.",
    "Intestazione RomFS non valida (chiavi errate?).": "Invalid RomFS header (wrong keys?).",
    "Ticket non valido.": "Invalid ticket.",
    "Metadati CNMT non validi.": "Invalid CNMT metadata.",
    "Tipo di contenuto non supportato (0x{kind:02X}): non è un gioco, un aggiornamento o un DLC.":
        "Unsupported content type (0x{kind:02X}): not a game, an update or a DLC.",
    "{name}: non è un file NSP o XCI valido.": "{name}: not a valid NSP or XCI file.",
    "{name}: XCI senza partizione secure.": "{name}: XCI without a secure partition.",
    "{name}: file compresso (NSZ/XCZ). Decomprimilo prima, ad esempio con «nsz -D».":
        "{name}: compressed file (NSZ/XCZ). Decompress it first, for example with \"nsz -D\".",
    "{name}: nessun metadato CNMT trovato.": "{name}: no CNMT metadata found.",
    "{name}: file NCA troppo piccolo.": "{name}: NCA file too small.",
    "{name}: formato {magic} (molto vecchio) non supportato.":
        "{name}: {magic} format (very old) not supported.",
    "{name}: intestazione NCA non valida. header_key errata o file danneggiato/compresso (NCZ).":
        "{name}: invalid NCA header. Wrong header_key or damaged/compressed (NCZ) file.",
    "{name}: manca il ticket (.tik) con la title key {rights_id}. Usa l'NSP completo di ticket "
    "o aggiungila a title.keys.":
        "{name}: the ticket (.tik) with title key {rights_id} is missing. Use the complete NSP "
        "with its ticket or add the key to title.keys.",
    "{name}: sezione {index} assente.": "{name}: section {index} missing.",
    "{name}: NCA compresso (formato dei giochi più recenti) non ancora supportato per "
    "l'estrazione.":
        "{name}: compressed NCA (used by recent games) not supported for extraction yet.",
    "{name}: NCA sparse non supportato per l'estrazione.":
        "{name}: sparse NCA not supported for extraction.",
    "{name}: sezione di aggiornamento: serve anche il gioco base.":
        "{name}: update section: the base game is needed as well.",
    "{name}: cifratura della sezione non supportata ({kind}).":
        "{name}: unsupported section encryption ({kind}).",
    "{name}: intestazione BKTR non valida.": "{name}: invalid BKTR header.",
    "{name}: nessuna RomFS.": "{name}: no RomFS.",
    "{name}: NCA del programma non trovato.": "{name}: program NCA not found.",
    "{name}: mancano {n} NCA indicati nei metadati (file incompleto).":
        "{name}: {n} NCAs listed in the metadata are missing (incomplete file).",
    "Tabella di rilocazione dell'aggiornamento non valida.": "Invalid update relocation table.",
    "{name}: tabelle dell'aggiornamento illeggibili (chiavi errate o file danneggiato).":
        "{name}: unreadable update tables (wrong keys or damaged file).",
    "L'aggiornamento richiede il gioco base per ricostruire la RomFS: aggiungi anche il gioco "
    "base.":
        "The update needs the base game to rebuild the RomFS: add the base game as well.",

    # -- titles, XCI, extraction ---------------------------------------------------
    "Gioco base": "Base game",
    "Aggiornamento": "Update",
    "DLC": "DLC",
    "Aggiornamento delta": "Delta update",
    "Manca il gioco base: l'XCI conterrà solo aggiornamenti/DLC e l'estrazione della RomFS "
    "dell'aggiornamento non è possibile.":
        "The base game is missing: the XCI will only hold updates/DLC and the update's RomFS "
        "cannot be extracted.",
    "Più aggiornamenti: viene usato solo il più recente ({version}), gli aggiornamenti sono "
    "cumulativi.":
        "Several updates: only the latest ({version}) is used, updates are cumulative.",
    "Gli aggiornamenti delta non sono supportati.": "Delta updates are not supported.",
    "Nessun contenuto da convertire.": "Nothing to convert.",
    "Preparazione di {name}…": "Preparing {name}…",
    "Spazio insufficiente in {path}: servono {needed:.1f} GB, liberi {free:.1f} GB.":
        "Not enough space in {path}: {needed:.1f} GB needed, {free:.1f} GB free.",
    "Niente da estrarre: aggiungi il gioco base.": "Nothing to extract: add the base game.",
    "ExeFS non trovata.": "ExeFS not found.",
    "ATTENZIONE: senza il gioco base la RomFS dell'aggiornamento non può essere ricostruita: "
    "estraggo solo la ExeFS.":
        "WARNING: without the base game the update's RomFS cannot be rebuilt: extracting the "
        "ExeFS only.",
    "Applico l'aggiornamento {version}.": "Applying update {version}.",
    "Estratti {n} file in {dest}": "Extracted {n} files to {dest}",

    # -- updater -------------------------------------------------------------------
    "Nessuna release trovata (il repository potrebbe essere privato).":
        "No release found (the repository may be private).",
    "GitHub ha risposto con l'errore {code}.": "GitHub answered with error {code}.",
    "Impossibile contattare GitHub: {error}": "Cannot reach GitHub: {error}",
    "Questa release non contiene il file per il tuo sistema.":
        "This release has no file for your system.",
    "Aggiornamento annullato.": "Update cancelled.",
    "Download incompleto.": "Incomplete download.",
    "Il file scaricato è danneggiato (SHA-256 diverso).":
        "The downloaded file is damaged (SHA-256 differs).",
    "Download non riuscito: {error}": "Download failed: {error}",

    # -- GUI: games page -----------------------------------------------------------
    "Aggiungi file…": "Add files…",
    "Aggiungi cartella…": "Add folder…",
    "Rimuovi questo file": "Remove this file",
    "{tid} · {files} file · {size}": "{tid} · {files} files · {size}",
    "Versione": "Version",
    "Rimuovi questo gioco dall'elenco": "Remove this game from the list",
    "Contenuti": "Contents",
    "Attenzione": "Warning",
    "I tuoi giochi": "Your games",
    "Cartella in cui salvare XCI ed estrazioni": "Folder where XCIs and extractions are saved",
    "Svuota": "Clear",
    "Estrai ExeFS/RomFS": "Extract ExeFS/RomFS",
    "Crea XCI": "Create XCI",
    "Nessun file": "No files",
    "Chiavi mancanti": "Keys missing",
    "Alcuni file non sono stati aggiunti": "Some files were not added",
    "{games} giochi · {files} file · {size}": "{games} games · {files} files · {size}",
    "Scegli la cartella di destinazione…": "Choose the destination folder…",
    "Cartella di destinazione": "Destination folder",

    # -- GUI: progress page --------------------------------------------------------
    "Estrazione: ": "Extraction: ",
    "Operazione annullata.": "Operation cancelled.",
    "Errore inatteso: {error}": "Unexpected error: {error}",
    "In attesa": "Waiting",
    "Completato": "Done",
    "Nessuna operazione in corso": "Nothing in progress",
    "Annulla": "Cancel",
    "Preparazione…": "Preparing…",
    "Sto scrivendo: {name}": "Writing: {name}",
    "{size} scritti": "{size} written",
    "circa {min} min {sec} s rimanenti": "about {min} min {sec} s left",
    "circa {sec} s rimanenti": "about {sec} s left",
    "Operazione completata": "Operation completed",
    "Tempo impiegato: {sec} s": "Time taken: {sec} s",
    "Gli XCI si aprono con gli emulatori o si installano sulla console con un installer di "
    "homebrew.":
        "The XCIs open in emulators or install on the console with a homebrew installer.",
    "ExeFS e RomFS sono pronte: usale per mod e traduzioni (LayeredFS).":
        "ExeFS and RomFS are ready: use them for mods and translations (LayeredFS).",
    "Fatto": "Done",
    "Apri cartella": "Open folder",
    "Errore": "Error",

    # -- GUI: settings -------------------------------------------------------------
    "Attivo": "On",
    "Disattivo": "Off",
    "Impostazioni": "Settings",
    "Chiavi": "Keys",
    "Scegli file": "Choose file",
    "title.keys (facoltativo)": "title.keys (optional)",
    "Cartelle": "Folders",
    "Scegli cartella": "Choose folder",
    "Includi l'aggiornamento": "Include the update",
    "Solo il più recente: gli aggiornamenti sono cumulativi.":
        "Only the latest: updates are cumulative.",
    "Includi i DLC": "Include DLC",
    "Estrazione": "Extraction",
    "Estrai la ExeFS": "Extract the ExeFS",
    "Eseguibili del gioco (main, main.npdm, subsdk…) nella cartella exefs.":
        "Game executables (main, main.npdm, subsdk…) in the exefs folder.",
    "Estrai la RomFS": "Extract the RomFS",
    "Dati del gioco (grafica, testi, audio) nella cartella romfs.":
        "Game data (graphics, text, audio) in the romfs folder.",
    "RomFS come unico file": "RomFS as a single file",
    "Salva romfs.bin invece della cartella con i singoli file.":
        "Save romfs.bin instead of the folder with the individual files.",
    "Applica l'aggiornamento": "Apply the update",
    "Estrae la RomFS e la ExeFS della versione aggiornata (serve anche il gioco base).":
        "Extracts the RomFS and ExeFS of the updated version (the base game is needed too).",
    "Notifica a fine operazione": "Notify when done",
    "Mostra una notifica di sistema se nel frattempo stai usando un'altra finestra.":
        "Show a system notification if you are using another window meanwhile.",
    "Aspetto": "Appearance",
    "Tema": "Theme",
    "Chiaro, scuro o come Windows": "Light, dark or like Windows",
    "Chiaro": "Light",
    "Scuro": "Dark",
    "Usa impostazione di sistema": "Use system setting",
    "Lingua": "Language",
    "Si applica al prossimo avvio": "Applies at the next start",
    "Automatica (lingua del sistema)": "Automatic (system language)",
    "Aggiornamenti": "Updates",
    "Controlla gli aggiornamenti all'avvio": "Check for updates at startup",
    "Cerca una nuova versione su GitHub quando apri il programma.":
        "Look for a new version on GitHub when the program opens.",
    "Controlla ora": "Check now",
    "Versione installata": "Installed version",
    "Informazioni": "About",
    "Codice sorgente": "Source code",
    "Versione {version} · GPL-3.0 · usa le chiavi della tua console":
        "Version {version} · GPL-3.0 · uses your console's keys",
    "File non trovato: {path}": "File not found: {path}",
    "Trovato automaticamente: {path}": "Found automatically: {path}",
    "Non trovato: ricavalo dalla tua console con Lockpick_RCM":
        "Not found: dump it from your console with Lockpick_RCM",
    "Serve solo per NSP senza ticket": "Only needed for NSPs without a ticket",
    "Non impostata": "Not set",
    "File di chiavi (*.keys *.txt);;Tutti i file (*)": "Key files (*.keys *.txt);;All files (*)",
    "Scegli prod.keys": "Choose prod.keys",
    "Scegli title.keys": "Choose title.keys",
    "Registro": "Log",

    # -- GUI: main window ----------------------------------------------------------
    "Giochi": "Games",
    "Avanzamento": "Progress",
    "Cambia tema": "Toggle theme",
    "Chiavi: {path}": "Keys: {path}",
    "Scegli prod.keys…": "Choose prod.keys…",
    "Controllo in corso…": "Checking…",
    "Switch Toolkit {current} · disponibile la versione {version}":
        "Switch Toolkit {current} · version {version} available",
    "Disponibile Switch Toolkit {version}": "Switch Toolkit {version} available",
    "Aggiornamento disponibile": "Update available",
    "Dettagli": "Details",
    "Switch Toolkit {current} · è l'ultima versione":
        "Switch Toolkit {current} · latest version",
    "Nessun aggiornamento": "No updates",
    "Stai già usando l'ultima versione ({version}).":
        "You are already using the latest version ({version}).",
    "Switch Toolkit {current} · controllo non riuscito":
        "Switch Toolkit {current} · check failed",
    "Controllo aggiornamenti: {message}": "Update check: {message}",
    "Controllo non riuscito": "Check failed",
    "Operazione in corso": "Operation in progress",
    "Riavvio necessario": "Restart needed",
    "La nuova lingua verrà usata al prossimo avvio.":
        "The new language will be used at the next start.",
    "Niente da estrarre": "Nothing to extract",
    "Attiva ExeFS o RomFS in Impostazioni → Estrazione.":
        "Turn on ExeFS or RomFS in Settings → Extraction.",
    "Niente da fare": "Nothing to do",
    "Nessun gioco base tra i file aggiunti: aggiungilo per estrarre.":
        "No base game among the added files: add it to extract.",
    "Esiste già": "Already exists",
    "Verranno sovrascritti:\n{names}\n\nContinuare?":
        "These will be overwritten:\n{names}\n\nContinue?",
    "Sovrascrivi": "Overwrite",
    "Spazio su disco insufficiente": "Not enough disk space",
    "Servono circa {needed}, liberi {free}. Libera spazio oppure scegli un'altra cartella.":
        "About {needed} needed, {free} free. Free some space or choose another folder.",
    "Continua comunque": "Continue anyway",
    "Operazione completata.": "Operation completed.",
    "Annullamento…": "Cancelling…",
    "Vuoi interrompere l'operazione e uscire?": "Stop the operation and quit?",
    "Interrompi ed esci": "Stop and quit",
    "Continua": "Continue",

    # -- GUI: update dialog --------------------------------------------------------
    "Impossibile sostituire il programma: {error}. Scarica la nuova versione dalla pagina delle "
    "release.":
        "Cannot replace the program: {error}. Download the new version from the releases page.",
    "Switch Toolkit {version} disponibile": "Switch Toolkit {version} available",
    "Nessuna nota per questa versione.": "No notes for this version.",
    "Più tardi": "Later",
    "Aggiorna e riavvia": "Update and restart",
    "Il programma verrà scaricato, sostituito e riavviato.":
        "The program will be downloaded, replaced and restarted.",
    "Apri la pagina della release": "Open the release page",
    "Aggiornamento automatico non disponibile per questa copia (avviata dal codice sorgente o "
    "file mancante nella release).":
        "Automatic update not available for this copy (run from source or file missing from "
        "the release).",
    "Download in corso…": "Downloading…",
    "Download: {done} / {total}": "Download: {done} / {total}",
    "Aggiornamento installato. Riavvio…": "Update installed. Restarting…",
    "Aggiornamento installato. Riavvia Switch Toolkit a mano ({error}).":
        "Update installed. Restart Switch Toolkit by hand ({error}).",
    "Chiudi": "Close",
    "Riprova": "Retry",

    # -- NUP -----------------------------------------------------------------------
    "Coltellino svizzero per i file Nintendo Switch: NSP → XCI o NUP, unione di gioco, "
    "aggiornamenti e DLC, estrazione di ExeFS e RomFS.":
        "Swiss army knife for Nintendo Switch files: NSP → XCI or NUP, merging of game, updates "
        "and DLC, ExeFS and RomFS extraction.",
    "mostra i contenuti di file NSP/XCI/NUP": "show the contents of NSP/XCI/NUP files",
    "verifica l'integrità (SHA-256) di NSP e NUP":
        "check the integrity (SHA-256) of NSPs and NUPs",
    "crea un NUP (file decifrati e compressi) con gioco, aggiornamento e DLC":
        "create a NUP (decrypted, compressed files) with game, update and DLC",
    "un NUP per ogni file, senza unire": "one NUP per file, without merging",
    "livello di compressione zstd (predefinito 3)": "zstd compression level (default 3)",
    "i file NUP non possono diventare XCI (contengono dati già decifrati, senza gli NCA): "
    "vengono ignorati.":
        "NUP files cannot become XCIs (they hold decrypted data, without the NCAs): skipped.",
    "Nessun file NSP/XCI/NUP trovato.": "No NSP/XCI/NUP files found.",
    "Manca il modulo zstandard, necessario per i file NUP.":
        "The zstandard module, needed for NUP files, is missing.",
    "{name}: file danneggiato nel NUP (impronta SHA-256 diversa).":
        "{name}: damaged file in the NUP (SHA-256 differs).",
    "{name}: file NUP non valido.": "{name}: invalid NUP file.",
    "{name}: file NUP non valido ({why}).": "{name}: invalid NUP file ({why}).",
    "{name}: versione del formato NUP non supportata ({version}).":
        "{name}: unsupported NUP format version ({version}).",
    "file troncato": "truncated file",
    "indice danneggiato": "damaged index",
    "{name}: compressione non supportata nel NUP.": "{name}: unsupported compression in the NUP.",
    "{name}: il NUP non contiene giochi, aggiornamenti o DLC.":
        "{name}: the NUP holds no game, update or DLC.",
    "I NUP contengono i singoli file: estraggo la cartella romfs invece di romfs.bin.":
        "NUPs hold the individual files: extracting the romfs folder instead of romfs.bin.",
    "Trascina qui i file NSP, XCI o NUP (gioco, aggiornamenti, DLC, in qualsiasi ordine) oppure":
        "Drop NSP, XCI or NUP files here (game, updates, DLC, in any order) or",
    "Nessun file aggiunto. Aggiungi insieme gioco base, aggiornamenti e DLC: vengono "
    "raggruppati per gioco. «Crea XCI» li unisce in un unico XCI (o converte un singolo NSP), "
    "«Crea NUP» in un unico NUP con i file già decifrati, «Estrai» salva ExeFS e RomFS con "
    "l'aggiornamento già applicato.":
        "No files added. Add base game, updates and DLC together: they are grouped by game. "
        "\"Create XCI\" merges them into a single XCI (or converts a single NSP), \"Create NUP\" "
        "into a single NUP with the files already decrypted, \"Extract\" saves ExeFS and RomFS "
        "with the update already applied.",
    "Crea NUP": "Create NUP",
    "File unico con i dati già decifrati e compressi, per gli emulatori che supportano il "
    "formato NUP":
        "Single file with the data already decrypted and compressed, for emulators that support "
        "the NUP format",
    "Nessun file NSP, XCI o NUP tra quelli scelti.":
        "No NSP, XCI or NUP files among the chosen ones.",
    "Seleziona i file NSP/XCI/NUP": "Select the NSP/XCI/NUP files",
    "File Switch (*.nsp *.xci *.nup);;Tutti i file (*)":
        "Switch files (*.nsp *.xci *.nup);;All files (*)",
    "Cartella con file NSP/XCI/NUP": "Folder with NSP/XCI/NUP files",
    "I NUP si aprono con gli emulatori che supportano il formato.":
        "NUPs open in emulators that support the format.",
    "NUP esclusi": "NUPs skipped",
    "I file NUP non possono diventare XCI: contengono i dati già decifrati, senza gli NCA "
    "originali.":
        "NUP files cannot become XCIs: they hold the decrypted data, without the original NCAs.",
    "Creazione XCI e NUP": "XCI and NUP creation",
    "Unisci in un unico file": "Merge into a single file",
    "Gioco base, ultimo aggiornamento e DLC dello stesso gioco in un solo XCI o NUP. Se "
    "disattivato ogni file diventa un XCI o NUP separato.":
        "Base game, latest update and DLC of the same game in one XCI or NUP. When off, every "
        "file becomes a separate XCI or NUP.",

    # -- job queue -----------------------------------------------------------------
    "Aggiungi i file nella pagina Giochi e premi «Crea XCI», «Crea NUP» o «Estrai "
    "ExeFS/RomFS». Puoi aggiungere altre operazioni anche mentre una è in corso: si mettono "
    "in coda.":
        "Add files in the Games page and press \"Create XCI\", \"Create NUP\" or \"Extract "
        "ExeFS/RomFS\". You can add more operations while one is running: they are queued.",
    "Aggiunto alla coda": "Added to the queue",
    "Vedi coda": "Show queue",
    "{n} operazioni in attesa: partiranno al termine di quella in corso.":
        "{n} operations waiting: they start when the current one ends.",
    "Già in coda": "Already queued",
    "Le operazioni già in coda non vengono aggiunte di nuovo.":
        "Operations already in the queue are not added again.",
    "Svuota coda": "Clear queue",
    "Togli le operazioni in attesa": "Remove the waiting operations",
    "Interrompe l'operazione in corso; la coda prosegue":
        "Stops the running operation; the queue goes on",
    "Sposta su": "Move up",
    "Sposta giù": "Move down",
    "Togli dalla coda": "Remove from the queue",
    "Annullato": "Cancelled",
    "Operazione in corso · {n} in coda": "Operation in progress · {n} queued",
    "Destinazione: {dest}": "Destination: {dest}",
    "Coda terminata: {ok} completate, {bad} non riuscite":
        "Queue finished: {ok} completed, {bad} failed",
    "Tutte le operazioni sono state completate.": "All operations were completed.",
    "{n} operazioni non riuscite.": "{n} operations failed.",
    "Aggiorna quando la coda è finita.": "Update once the queue is finished.",
    "Vuoi interrompere l'operazione, togliere le {n} in coda e uscire?":
        "Stop the operation, remove the {n} queued and quit?",
}
