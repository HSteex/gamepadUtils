# AppUtili

App di utilità nel tray di Windows, con:

1. **Monitor Setup** (con [Monitor Profile Switcher](https://monitorprofileswitcher.com/))
   - I profili sono i file `.xml` che MonitorSwitcher salva in
     `%APPDATA%\MonitorSwitcher\Profiles`: AppUtili li legge da lì, con i
     loro nomi. Se non ce ne sono, la voce Monitor non compare.
   - Due ruoli, scelti in *Impostazioni → Monitor* (all'inizio dedotti dai
     nomi: "Main"/"Scrivania" e "TV", altrimenti primo e secondo profilo):
     `Ctrl+Alt+Giu` carica la **Scrivania**, `Ctrl+Alt+Su` la **TV**. Le
     stesse dal pad: `Back + LB + Giù / Su`.
   - MonitorSwitcher è portatile (uno zip, nessuna installazione), quindi
     AppUtili cerca `MonitorSwitcher.exe`: il percorso già salvato, il
     processo se è aperto nel tray, Download / Documenti / Desktop /
     Program Files e i dischi locali fino a due cartelle di profondità.
     Se non lo trova: *Impostazioni → Monitor → Scegli…*

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
     - `Back + LB + D-pad Su` (4 + 9 + 11) → profilo monitor TV
     - `Back + LB + D-pad Giù` (4 + 9 + 12) → profilo monitor Scrivania
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
       se wireless e la comunica), profilo monitor e le quattro tab
       (Controlli, Giochi, Spotify, Discord), che si cambiano con **LT** / **RT**.

       Comandi validi ovunque: **B** chiude; **LB** / **RB** / **X** =
       brano precedente / successivo / play-pausa; **R3** (clic della
       levetta destra) attiva/disattiva la riproduzione casuale di
       Spotify, indicata dall'icona shuffle accanto al play (azzurra
       quando è attiva); **L3** (clic della levetta sinistra) muta/smuta
       il microfono su Discord. Le azioni partono
       dopo aver ridato il focus alla finestra di prima.

       **Tab Controlli**: le azioni del tray divise per sezioni, con la
       voce selezionata spiegata in una riga sotto.
       - **Su/giù** (D-pad o stick sinistro) scorre le voci; tenendo
         premuto la selezione si ripete
       - Righe a **scelte affiancate**: *Monitor* (i profili trovati; con
         più di 3 profili o nomi lunghi diventa ◀ nome ▶ e **A** carica),
         *Big Picture* [Apri · Esci] (solo Steam, i monitor non cambiano),
         *Energia* [Sospendi · Riavvia · Spegni]. Sinistra/destra sposta il
         cursore, **A** applica. Per *Energia* serve una seconda A entro 3
         secondi (la pillola diventa rossa, "Conferma?")
       - *Uscita audio*: con SteelSeries GG/Sonar aperto (icona col logo
         SteelSeries) sinistra/destra manda `Ctrl+è` / `Ctrl++` a GG e
         mostra il nome del dispositivo scelto. Con GG chiuso (icona
         cuffie) cambia invece l'uscita predefinita di Windows, scorrendo
         tra quelle attive in ordine alfabetico
       - *Volume*: sinistra/destra ±5% (tenendo premuto si ripete), A muto.
         È il volume dell'uscita predefinita di Windows (con Sonar, il
         canale Gaming)
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

       **Tab Discord**: due viste che si cambiano con **Y** (o cliccando
       l'interruttore in alto):
       - *Recenti*: i canali vocali in cui sei entrato, dal più recente,
         con l'icona del server e la pila di avatar di chi c'è dentro.
         **Destra** apre la riga e mostra le persone una per una (con
         muto/silenziato), **sinistra** la chiude
       - *Server*: come in Discord, colonna dei server a sinistra (pallino
         verde dove c'è qualcuno in vocale) e canali vocali del server con
         le persone sempre elencate sotto. **Sinistra/destra** passano
         dalla colonna dei server ai canali, **su/giù** scorrono.
         **Start** (o il tasto destro del mouse sull'icona) aggiunge o
         toglie il server dai *preferiti*: stanno in cima, nell'ordine in
         cui li aggiungi, separati dagli altri da una lineetta; gli altri
         sono in ordine alfabetico (l'ordine scelto in Discord non è
         leggibile dall'RPC). I preferiti restano in `config.json`
       **A** su un canale ci entra. Nel canale in cui sei, chi parla ha
       l'anello verde attorno all'avatar. In basso c'è la barra con il tuo
       avatar, "Connesso in *canale*" e i pulsanti **Muto · Silenzia ·
       Schermo · Esci** (in rosso quando attivi): ci si arriva con **giù**
       dall'ultima riga o, senza scorrere tutta la lista, con **destra**
       da un canale (in *Recenti* da una riga già aperta); **sinistra**
       dal primo pulsante torna dov'eri. **L3** (clic della levetta sinistra) muta/smuta
       da qualsiasi tab. *Schermo* chiude l'overlay e invia la
       scorciatoia di Discord per condividere lo schermo. Col mouse: clic
       su un canale per entrare, sulla pila di avatar per aprire la riga.
       Va configurata una volta sola (vedi *Discord* più sotto).

       Il nome dell'uscita audio viene letto dall'API web locale di
       SteelSeries GG/Sonar (non documentata, solo in lettura). GG si
       considera aperto se tra le uscite di Windows ci sono i dispositivi
       virtuali "SteelSeries Sonar"; altrimenti si usa direttamente Windows
       (`SetDefaultDevice` di pycaw). Il logo viene letto
       dall'installazione di GG; se manca, al suo posto c'è l'icona del
       tipo di dispositivo.

       **Impostazioni**: **Select** (View) tenuto premuto da solo per mezzo
       secondo, oppure l'ingranaggio in alto. Prendono il posto delle tab,
       **B** torna indietro. Voci (con la solita navigazione):
       - *Tab*: mostra o nasconde Controlli, Giochi, Spotify, Discord (una
         resta sempre); LT/RT saltano quelle nascoste
       - *Servizi*: stato di Spotify e Discord, *Collega* / *Scollega* e
         *Configura*
       - *Monitor*: profilo *Scrivania* e *TV* (◀ ▶ tra i profili e
         "Nessuno") e dove sta MonitorSwitcher (*Scegli…*)
       - *Generale*: Notifiche, Avvio con Windows, Combo del pad, Tasto Home
       - *Altro*: **Informazioni** e *Esci da AppUtili* (con conferma)

       **Informazioni**: pannelli espandibili, uno aperto alla volta (**A**
       o destra/sinistra sull'intestazione):
       - *Scorciatoie*: tastiera, pad ovunque e pad nell'overlay, con i
         tasti disegnati come nella legenda
       - *Servizi collegati*: stato di Spotify e Discord, Redirect URI
         (**A** li copia), link al dashboard / portale, note
       - *Monitor*: dove sta MonitorSwitcher, profili, cartella, ruoli
       - *Sistema*: versione, amministratore, UIAccess (cioè sopra i
         giochi a schermo intero), uscita audio, pad, cartella di
         configurazione

       **A** sulle righe con *Apri* / *Copia* apre la cartella o il sito, o
       copia il valore.

       **Da tastiera e mouse**: `Shift+F1` (o un clic sull'icona del tray)
       apre e chiude l'overlay anche senza pad, pure con il
       Controller disattivato. Poi funziona tutto col mouse: clic su tab,
       filtri, voci e righe, **rotella** per scorrere le liste di Giochi,
       Spotify e Discord. Da tastiera: frecce, `Invio`, `Esc`, `Tab` /
       `Shift+Tab` (o `PagSu` / `PagGiù`) per cambiare tab. Il tasto è
       riservato con `RegisterHotKey`, quindi non arriva più ai programmi
       che lo usano (es. Word, "Mostra formattazione").

       **Si chiude da solo** quando un'altra finestra va in primo piano
       (un clic fuori, Alt+Tab, la barra delle applicazioni…), senza
       rubare il focus alla finestra cliccata. Le finestre di
       configurazione di Spotify e Discord non lo chiudono.

       Finché l'overlay è aperto le combo sono sospese e l'overlay prende il
       focus. Windows però non permette di bloccare davvero il pad agli altri
       programmi: i giochi e Big Picture che ignorano il pad quando non sono in
       primo piano smettono di reagire, quelli che lo leggono anche in
       background continuano a riceverlo. Sopra i giochi in fullscreen
       *esclusivo* l'overlay non si vede, e prendendo il focus il gioco può
       ridursi a icona. Funziona su desktop, finestre, borderless e Big Picture.

   Usa la mappatura standard **SDL GameController** (tramite `pygame._sdl2.controller`),
   la stessa usata dai videogiochi moderni: i pulsanti vengono identificati
   per funzione (A, B, dorsali, ecc.) e non per indice grezzo, quindi le
   combinazioni restano valide cambiando marca o modello di controller.
   Supporta anche il collegamento/scollegamento "a caldo".

   Per i controller 8BitDo: impostali in modalità **XInput** (switch sul
   retro) per la massima compatibilità con questa API.

4. **Aspetto**
   - L'icona nel tray mostra il profilo monitor caricato: **blu** = il
     profilo Scrivania, **viola** = il profilo TV, verde/arancio gli altri;
     la scritta è il nome se cortissimo ("TV"), altrimenti l'iniziale.
     Senza profili resta la "M" blu. Il pallino in basso a destra è
     **verde** se il controller è attivo, **grigio** se è disattivato.
     Passandoci sopra col mouse compare lo stesso riepilogo in testo.
   - Clic sinistro sull'icona: apre l'overlay. Tasto destro: il menu.
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
   Crea un certificato di firma del codice personale, con la chiave
   protetta da una password che Windows ti chiede di scegliere, e lo rende
   attendibile su questo PC (vedi *Overlay sopra i giochi a schermo
   intero* più sotto). Con `-Rinnova` cancella quello esistente e ne crea
   uno nuovo.
1. Lancia `build.bat`: installa le dipendenze, genera l'icona `app.ico`,
   compila e firma l'exe. Per la firma Windows chiede la password della
   chiave. La firma ha un timestamp (serve internet; senza, firma lo
   stesso e avvisa): così resta valida anche dopo la scadenza del
   certificato, tra 5 anni.
2. Il risultato è la cartella `dist\AppUtili` (`AppUtili.exe` più le
   librerie in `_internal`). Da lì **non si avvia**: con UIAccess Windows
   lo esegue solo da Program Files.
3. Lancia `installa.bat` (chiede da solo i privilegi di amministratore):
   - controlla che l'exe sia firmato
   - chiude la versione di AppUtili in esecuzione
   - sposta la versione precedente in `C:\Program Files\AppUtili.old`
   - copia la nuova cartella in `C:\Program Files\AppUtili`
   - registra l'avvio con Windows (sostituendo l'attività esistente)
   - avvia la nuova versione

Per tornare alla versione precedente: chiudi AppUtili, cancella
`C:\Program Files\AppUtili` e rinomina `AppUtili.old` in `AppUtili`.

Per disinstallare: `disinstalla.bat` (toglie l'avvio con Windows, le
cartelle in Program Files e il certificato dalle autorità attendibili;
impostazioni e accessi in `%APPDATA%\AppUtili` restano). Sul PC dove
compili, poi, per tornare a firmare basta rilanciare `certificato.ps1`.

### Mandarlo a un'altra persona

**Col repo (la via normale).** Chi lo riceve clona il repo (o lo
scarica) e segue i passi qui sopra: Python, `certificato.ps1` come
amministratore (crea un certificato **suo**, con una sua password),
`build.bat` e `installa.bat`. Così non deve fidarsi della tua chiave:
ognuno firma solo il proprio exe. Per aggiornare: `git pull`, poi di
nuovo `build.bat` e `installa.bat`.

**Con lo zip (per chi non ha Python).** Dopo `build.bat`, lancia
`pacchetto.bat`: crea `dist\AppUtili-pacchetto.zip` con l'app firmata,
`installa.bat`, `disinstalla.bat`, `LEGGIMI.txt` (istruzioni per chi lo
riceve) e `AppUtili.cer`, la **parte pubblica** del certificato con cui è
firmata. Chi lo riceve estrae lo zip e lancia `installa.bat`, che rende
attendibile il certificato sul suo PC (serve per UIAccess) e installa
come sul tuo. Non servono Python né il sorgente. Da sapere:
- il `.cer` non contiene la chiave privata: con lui si verificano le
  firme, non se ne creano. Non finisce mai nel repo (`dist\` e `*.cer`
  sono in `.gitignore`)
- chi lo installa si fida di **qualunque** cosa firmata con la tua chiave:
  per questo la chiave è protetta da password
- se rinnovi il certificato (`certificato.ps1 -Rinnova`) va rifatto il
  pacchetto e reinstallato da chi l'ha ricevuto

In entrambi i casi Spotify e Discord vanno collegati da chi lo riceve, con
le sue app (vedi *Spotify* e *Discord*): per Discord l'RPC funziona solo
per il proprietario dell'app, quindi non può usare la tua.

Perché una cartella e non un exe singolo (`--onefile`): l'exe singolo a
ogni avvio si estrae in `%TEMP%`, una cartella che qualsiasi programma
dell'utente può modificare, e da lì carica DLL e moduli mentre gira da
amministratore. Un programma malevolo senza privilegi potrebbe
sostituirli e ottenere i diritti di amministratore. In Program Files le
librerie sono modificabili solo dagli amministratori.

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

Il certificato vale solo per firmare codice; la sua chiave privata non è
esportabile ed è protetta da password, chiesta da Windows a ogni firma,
così nessun programma può usarla di nascosto. Tieni presente però che
Windows considera attendibile qualunque cosa firmata con quella chiave.
Per rimuoverlo: `disinstalla.bat` (solo dalle autorità attendibili), o
`certmgr.msc` / `certlm.msc` → cerca "AppUtili Local Code Signing" in
*Personale*, *Autorità di certificazione radice attendibili* e *Editori
attendibili*.

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

## Discord

La tab Discord parla con il client desktop di Discord tramite il suo RPC
locale (una named pipe, niente bot né token dell'account). Serve
un'applicazione Discord personale, creata una volta sola:

1. Apri la configurazione: con il pad, tab Discord → **A**, oppure dal
   tray, `🎧 Discord` → `Configura…`
2. Premi **Apri portale Discord** e crea un'applicazione (nome a piacere)
3. In **OAuth2 → Redirects** aggiungi `http://127.0.0.1` (c'è il
   pulsante Copia) e salva
4. Sempre in **OAuth2** copia il **Client ID** e il **Client Secret**
   (premi *Reset Secret*: Discord lo mostra una volta sola) e incollali
   nella finestra
5. Controlla la **scorciatoia** per la condivisione schermo: deve essere
   la stessa di Discord → Impostazioni → Tasti di scelta rapida
   (predefinita `F7`)
6. Premi **Collega** e accetta il popup che compare dentro Discord

Per le app non approvate da Discord l'RPC funziona solo per il loro
proprietario, cioè per te: va bene così, l'app serve solo a te.

Client ID, Client Secret e scorciatoia restano in
`%APPDATA%\AppUtili\config.json`, l'accesso in
`%APPDATA%\AppUtili\discord_token.json` (si rinnova da solo), i canali
recenti in `discord_recent.json` e le icone dei server e gli avatar in
`discord_cache`. Per togliere l'accesso: `🎧 Discord` → `Scollega`, oppure
Discord → Impostazioni → App autorizzate.

Limiti:
- la condivisione schermo non ha un comando RPC: AppUtili invia la
  scorciatoia di Discord, che condivide il gioco che Discord ha
  rilevato. AppUtili non sa se lo stream è attivo
- Discord riporta muto e silenzia a com'erano quando un'app RPC si
  scollega: se li cambi da AppUtili e poi chiudi AppUtili, tornano come
  prima
- emoji e simboli decorativi nei nomi di canali e persone non vengono
  mostrati (il font dell'overlay non li ha)

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
  `VOLUME, BATTERIA DEL PAD, ALIMENTAZIONE`, `SPOTIFY` e `DISCORD`: i dati
  mostrati dall'overlay
- `OVERLAY GAMEPAD` per l'overlay (le voci della tab Controlli sono nella
  lista `OVERLAY_ITEMS`)
- `TRAY ICON / MENU` per il menu del tray

Per aggiungere nuovi gruppi di utilità in futuro basta:
1. Scrivere le funzioni "azione"
2. Aggiungere le voci al `pystray.Menu` in `build_menu()`

## Note

- I profili `.xml` vengono cercati in `%APPDATA%\MonitorSwitcher\Profiles`
  (costante `MONITOR_PROFILES_DIR` in `app.py`); il percorso di
  `MonitorSwitcher.exe` trovato o scelto resta in `config.json`
  (`monitor_switcher_exe`).
- Se `Ctrl+Alt+Su/Giu` è già usato dai driver della scheda video
  (es. rotazione schermo Intel), potresti avere conflitti: in tal caso
  ti basta cambiare `HOTKEY_DESK` / `HOTKEY_TV` in `app.py`.
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

- `rileva_controller.py` — verifica che il controller collegato venga
  riconosciuto come GameController standard da SDL e stampa ogni pulsante
  premuto con il relativo codice. Lancialo con `python rileva_controller.py`
  prima di usare il gruppo Controller la prima volta.
