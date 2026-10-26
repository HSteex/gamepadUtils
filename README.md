# AppUtili

App di utilità nel tray di Windows, con:

1. **Monitor Setup**
   - `Main` → lancia `MonitorSwitcher.exe -load:"Main Setup.xml"`
   - `TV` → lancia `MonitorSwitcher.exe -load:"TV.xml"`
   - Hotkey globali: `Ctrl+Alt+Giu` = Main, `Ctrl+Alt+Su` = TV

2. **Mappa tasto Home** (radio button nel menu del tray)
   - `Windows` → il tasto multimediale "Home"/"Browser Home" invia il tasto Windows
   - `Alt+Tab` → lo stesso tasto invia Alt+Tab
   - `Nessuno` → il tasto resta con il suo comportamento originale (apre il browser)

   Tecnicamente il blocco del comportamento originale è fatto con `RegisterHotKey`
   di Windows (la stessa API usata dai player multimediali per "riservarsi" un
   tasto), non con un semplice hook sulla tastiera: i tasti browser/multimediali
   generano anche un messaggio speciale (`WM_APPCOMMAND`) che un normale hook
   non riesce a bloccare, mentre `RegisterHotKey` impedisce del tutto a Windows
   di generarlo.

3. **Controller** (Xbox, PlayStation, 8BitDo e qualsiasi controller supportato da SDL)
   - `Attivo` → toggle on/off per l'intero gruppo
   - Combinazioni fisse (numeri come li stampa `rileva_controller.py`):
     - `Back + LB + D-pad Su` (4 + 9 + 11) → profilo monitor TV, poi apre
       Steam Big Picture (`steam://open/bigpicture`)
     - `Back + LB + D-pad Giù` (4 + 9 + 12) → chiude Steam Big Picture
       (`steam://close/bigpicture`), poi profilo monitor Main
     - `Back + click stick sinistro + click stick destro` (4 + 7 + 8) →
       `Win+D`, riduce a icona tutte le finestre (mostra desktop)
     - `Back + LB + D-pad Sinistra` (4 + 9 + 13) → `Ctrl+è`
     - `Back + LB + D-pad Destra` (4 + 9 + 14) → `Ctrl++`

       Queste due sono le scorciatoie mappate in SteelSeries GG per cambiare
       dispositivo audio. Vengono inviate come tasti fisici della tastiera
       italiana (scan code), non come caratteri: se cambi layout di tastiera
       vanno aggiornate le costanti `SCAN_*` in `app.py`.
     - `Back + Start + LB + RB + LT + RT` tutti insieme → `Alt+F4`, chiude
       la finestra attiva (utile se un gioco si blocca a schermo intero).
       Volutamente scomoda per non premerla per sbaglio. LT e RT sono
       grilletti analogici: contano come premuti oltre metà corsa.
     - `Back + RB` (4 + 10) → apre/chiude l'**overlay**: un pannello in
       alto a sinistra sul monitor della finestra attiva. Scatta al
       *rilascio* e solo se non premi altro, così non si apre mentre
       componi la combo di Alt+F4. In alto: ora, batteria del pad (solo
       se wireless e la comunica), profilo monitor e le tre tab
       (Controlli, Giochi, Spotify), che si cambiano con **LT** / **RT**.

       Comandi validi ovunque: **B** chiude; **LB** / **RB** / **X** =
       brano precedente / successivo / play-pausa; **R3** (clic della
       levetta destra) attiva/disattiva la riproduzione casuale di
       Spotify, indicata dall'icona shuffle accanto al play (azzurra
       quando è attiva). Le azioni partono
       dopo aver ridato il focus alla finestra di prima.

       **Tab Controlli**: le azioni del tray divise per sezioni, con la
       voce selezionata spiegata in una riga sotto.
       - **Su/giù** (D-pad o stick sinistro) scorre le voci; tenendo
         premuto la selezione si ripete
       - Righe a **scelte affiancate**: *Monitor* [Main · TV], *Big
         Picture* [Avvia su TV · Esci], *Energia* [Sospendi · Riavvia ·
         Spegni]. Sinistra/destra sposta il cursore, **A** applica. Per
         *Energia* serve una seconda A entro 3 secondi (la pillola diventa
         rossa, "Conferma?")
       - *Uscita audio*: sinistra/destra manda `Ctrl+è` / `Ctrl++` a
         SteelSeries GG e mostra il nome del dispositivo scelto
       - *Volume*: sinistra/destra ±5% (tenendo premuto si ripete), A muto.
         È il volume dell'uscita predefinita di Windows (con Sonar, il
         canale Gaming)
       - *Notifiche*: interruttore
       - **Scheda media** in fondo: copertina, titolo, artista e app di
         quello che sta suonando (Spotify, browser, ecc.: la stessa fonte
         del riquadro media di Windows). Selezionata: sinistra/destra
         cambia brano, A play/pausa

       **Tab Giochi**: griglia di copertine dei giochi Steam installati e
       dei giochi non-Steam aggiunti a Steam, dal più recente (quelli mai
       avviati in fondo). D-pad/stick si muovono nella griglia, **A**
       avvia il gioco tramite Steam (`steam://rungameid/...`). Tutto viene
       letto dai file locali di Steam, senza login né API web: nome e
       ultima partita dagli `appmanifest_*.acf`, copertine dalla cache
       della libreria (`appcache\librarycache`), giochi non-Steam da
       `userdata\<utente>\config\shortcuts.vdf` con le copertine
       personalizzate di `config\grid`. Se un gioco non ha la copertina
       verticale ne viene composta una dallo sfondo e dal logo, o dal nome.

       **Tab Spotify**: lista dei brani ascoltati di recente o delle tue
       playlist (**Y**, oppure sinistra/destra, cambia filtro), con
       copertina, artista e quando li hai ascoltati, più una barra con il
       brano in corso. **A** avvia la riproduzione e l'overlay resta
       aperto: un brano recente riparte dentro la playlist o l'album da
       cui veniva. Se Spotify non è aperto, viene avviata l'app desktop.
       Va configurata una volta sola (vedi *Spotify* più sotto).

       Il nome dell'uscita audio viene letto dall'API web locale di
       SteelSeries GG/Sonar (non documentata, solo in lettura). Se GG non è
       in esecuzione o l'API cambia, la voce mostra le scorciatoie al posto
       del nome e il cambio dispositivo funziona lo stesso.

       Finché l'overlay è aperto le combo sono sospese e l'overlay prende il
       focus. Windows però non permette di bloccare davvero il pad agli altri
       programmi: i giochi e Big Picture che ignorano il pad quando non sono in
       primo piano smettono di reagire, quelli che lo leggono anche in
       background continuano a riceverlo. Sopra i giochi in fullscreen
       *esclusivo* l'overlay non si vede, e prendendo il focus il gioco può
       ridursi a icona. Funziona su desktop, finestre, borderless e Big Picture.
   - Big Picture si apre e si chiude solo dal controller: da menu e da
     tastiera (`Ctrl+Alt+Su/Giù`) si cambia solo il profilo monitor

   Usa la mappatura standard **SDL GameController** (tramite `pygame._sdl2.controller`),
   la stessa usata dai videogiochi moderni: i pulsanti vengono identificati
   per funzione (A, B, dorsali, ecc.) e non per indice grezzo, quindi le
   combinazioni restano valide cambiando marca o modello di controller.
   Supporta anche il collegamento/scollegamento "a caldo".

   Per i controller 8BitDo: impostali in modalità **XInput** (switch sul
   retro) per la massima compatibilità con questa API.

4. **Aspetto**
   - L'icona nel tray mostra il profilo monitor attuale: **blu "M"** = Main,
     **viola "TV"** = TV. Il pallino in basso a destra è **verde** se il
     controller è attivo, **grigio** se è disattivato. Passandoci sopra col
     mouse compare lo stesso riepilogo in testo.
   - Il menu segue il tema chiaro/scuro di Windows.
   - `🔔 Notifiche` → toggle per le notifiche di Windows mostrate quando
     cambi profilo monitor (da menu, hotkey o controller).

La configurazione (radio button Home, stato Controller, notifiche, ultimo
profilo monitor)
viene salvata in:
`%APPDATA%\AppUtili\config.json`
e ricaricata automaticamente al riavvio dell'app.

## Come compilare l'exe

Su una macchina **Windows** con Python 3.9+ installato:

0. **Solo la prima volta**, da un PowerShell come amministratore:
   `powershell -NoProfile -ExecutionPolicy Bypass -File certificato.ps1`.
   Crea un certificato di firma del codice personale e lo rende attendibile
   su questo PC (vedi *Overlay sopra i giochi a schermo intero* più sotto).
1. Lancia `build.bat`: installa le dipendenze, genera l'icona `app.ico`,
   compila e firma l'exe.
2. L'eseguibile finale sarà in `dist\AppUtili.exe`. Da lì **non si avvia**:
   con UIAccess Windows lo esegue solo da Program Files.
3. Lancia `installa.bat` (chiede da solo i privilegi di amministratore):
   - controlla che l'exe sia firmato
   - chiude la versione di AppUtili in esecuzione
   - salva l'exe precedente come `C:\Program Files\AppUtili\AppUtili.exe.old`
   - copia il nuovo exe in `C:\Program Files\AppUtili\AppUtili.exe`
   - registra l'avvio con Windows (sostituendo l'attività esistente)
   - avvia la nuova versione

Per tornare alla versione precedente: chiudi AppUtili e rinomina
`AppUtili.exe.old` in `AppUtili.exe`.

### Overlay sopra i giochi a schermo intero

Una normale finestra "sempre in primo piano" finisce sotto i giochi a
schermo intero. Le app con **UIAccess** (quelle di accessibilità, come la
tastiera su schermo) stanno invece in una fascia superiore e compaiono
sopra i giochi. Questo vale per il fullscreen "ottimizzato" che Windows
10/11 usa per quasi tutti i giochi DirectX 11/12 (testato con Rocket
League); un vero fullscreen esclusivo resta irraggiungibile. Il gioco
non viene toccato in alcun modo: niente DLL iniettate, quindi nessun
problema con gli anti-cheat.

Windows concede UIAccess solo a un exe che:
- lo richiede nel manifest (`--uac-uiaccess` in `build.bat`)
- è firmato con un certificato attendibile sul PC (`certificato.ps1`)
- è installato in una cartella protetta come Program Files (`installa.bat`)

Il certificato vale solo per firmare codice e la sua chiave privata non è
esportabile. Tieni presente però che Windows considera attendibile
qualunque cosa firmata con quella chiave. Per rimuoverlo: `certmgr.msc` /
`certlm.msc` → cerca "AppUtili Local Code Signing" in *Personale*,
*Autorità di certificazione radice attendibili* e *Editori attendibili*.

Lanciata da sorgente (`python app.py`) l'app non ha UIAccess: l'overlay
funziona, ma non compare sopra i giochi a schermo intero.

`build.bat` compila con `--uac-admin`: l'exe chiederà automaticamente
i privilegi di amministratore all'avvio. Sono necessari perché:
- gli hotkey globali (Ctrl+Alt+Su/Giu) devono funzionare anche quando
  hai in primo piano finestre con privilegi elevati
- l'intercettazione del tasto Browser Home richiede un hook di basso
  livello sulla tastiera

## Spotify

La tab Spotify usa la Web API di Spotify con un'app personale. Va creata
una volta sola, e dal 2026 funziona solo se l'account che la crea ha
**Spotify Premium**.

1. Apri la configurazione: con il pad, tab Spotify → **A**, oppure dal
   tray, `🎵 Spotify` → `Configura…`
2. Premi **Apri dashboard Spotify** e crea un'app (nome e descrizione a
   piacere)
3. Come **Redirect URI** inserisci `http://127.0.0.1:8765/callback` (c'è
   il pulsante Copia) e, tra le API, seleziona **Web API**. Salva.
   Controlla che il dashboard non l'abbia trasformato in `localhost`:
   Spotify non lo accetta più
4. Copia il **Client ID** dalle impostazioni dell'app, incollalo nella
   finestra e premi **Collega**
5. Nel browser che si apre accedi a Spotify e autorizza AppUtili

Il Client ID resta in `%APPDATA%\AppUtili\config.json` e l'accesso in
`%APPDATA%\AppUtili\spotify_token.json`: dopo un riavvio non viene
chiesto niente. L'accesso si rinnova da solo; per toglierlo usa
`🎵 Spotify` → `Scollega`, oppure revocalo dal tuo account Spotify. Le
copertine scaricate stanno in `%APPDATA%\AppUtili\spotify_cache`.

Il login usa il flusso PKCE (quello previsto da Spotify per le app
desktop): non serve nessun "client secret".

## Avvio automatico con Windows (opzionale)

Dal menu del tray: `🚀 Avvia con Windows`.

Crea (o rimuove) un'attività pianificata chiamata `AppUtili` che parte
all'accesso dell'utente, già con i privilegi di amministratore e senza
richiesta UAC. Non si usa la cartella `shell:startup` né la chiave di
registro `Run` perché Windows salta in silenzio all'accesso i programmi
che chiedono l'elevazione.

L'attività è configurata senza il limite predefinito di 72 ore di
esecuzione (che altrimenti chiuderebbe l'app dopo 3 giorni) e parte anche
a batteria. Avvia l'eseguibile da cui hai attivato la voce: se sposti
`AppUtili.exe`, disattiva e riattiva la voce.

Per attivarla l'app deve essere in esecuzione come amministratore (l'exe
compilato con `build.bat` lo è già).

## Personalizzare / estendere

Il file `app.py` è strutturato in sezioni chiare:
- `MONITOR SETUP` per il primo gruppo
- `MAPPA TASTO HOME` per il secondo gruppo
- `CONTROLLER` per il terzo gruppo
- `MEDIA IN RIPRODUZIONE`, `STEELSERIES SONAR`, `LIBRERIA GIOCHI (Steam)`,
  `VOLUME, BATTERIA DEL PAD, ALIMENTAZIONE` e `SPOTIFY`: i dati mostrati
  dall'overlay
- `OVERLAY GAMEPAD` per l'overlay (le voci della tab Controlli sono nella
  lista `OVERLAY_ITEMS`)
- `TRAY ICON / MENU` per il menu del tray

Per aggiungere nuovi gruppi di utilità in futuro basta:
1. Scrivere le funzioni "azione"
2. Aggiungere le voci al `pystray.Menu` in `build_menu()`

## Note

- I profili `.xml` vengono cercati in `%APPDATA%\MonitorSwitcher\Profiles`.
  Se cambi i percorsi di `MonitorSwitcher.exe` o dei profili, modifica le
  costanti `MONITOR_SWITCHER_EXE`, `MONITOR_PROFILES_DIR`, `PROFILE_MAIN`,
  `PROFILE_TV` in cima ad `app.py`.
- Se `Ctrl+Alt+Su/Giu` è già usato dai driver della scheda video
  (es. rotazione schermo Intel), potresti avere conflitti: in tal caso
  ti basta cambiare `HOTKEY_MAIN` / `HOTKEY_TV` in `app.py`.
- Se selezioni "Windows" o "Alt+Tab" ma il tasto continua ad aprire il
  browser, controlla la console (lanciando `python app.py` da un prompt,
  non l'exe compilato): se vedi il messaggio "Impossibile registrare
  l'hotkey sul tasto Home", vuol dire che un altro programma (spesso il
  software di gestione della tastiera, es. Logitech Options, Razer
  Synapse, ecc.) si è già registrato su quel tasto e va disattivato o
  disinstallato prima che AppUtili possa intercettarlo.
- Se il gruppo "Controller" mostra solo "pygame non installato" e in
  grigio, manca la libreria `pygame`: esegui `pip install -r requirements.txt`.
- Se premendo i pulsanti del controller non succede nulla, usa
  `rileva_controller.py` per verificare che SDL lo riconosca come
  GameController standard (vedi sotto).

## Diagnostica inclusa

- `rileva_tasto.py` — stampa nome e scan code di ogni tasto premuto sulla
  tastiera. Utile se cambi tastiera e il tasto Home smette di essere
  riconosciuto (va aggiornata la costante `VK_BROWSER_HOME` in `app.py`
  solo nel caso in cui, da test, risultasse diversa da `0xAC`).
- `rileva_controller.py` — verifica che il controller collegato venga
  riconosciuto come GameController standard da SDL e stampa ogni pulsante
  premuto con il relativo codice. Lancialo con `python rileva_controller.py`
  prima di usare il gruppo Controller la prima volta.
