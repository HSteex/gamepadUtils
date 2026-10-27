"""
AppUtili - Utility tray per Windows
------------------------------------
Gruppi disponibili:
  1) Monitor Setup: carica con MonitorSwitcher.exe (portatile, cercato da
     solo) i profili .xml che salva in %APPDATA%\\MonitorSwitcher\\Profiles.
     Hotkey globali: Ctrl+Alt+Giu -> profilo Scrivania, Ctrl+Alt+Su ->
     profilo TV (ruoli scelti nelle impostazioni dell'overlay)
  2) Mappa tasto Home: intercetta il tasto multimediale "Browser Home"
     tramite RegisterHotKey (Windows) e lo rimappa su Windows, Alt+Tab
     oppure lo lascia disattivato (Nessuno = comportamento originale).
  3) Controller: toggle Attivo/Disattivo + combinazioni fisse:
       Back + LB + D-pad Su / Giu -> profilo monitor TV / Scrivania
       Back + click stick sx + click stick dx -> Win+D (mostra desktop)
       Back + LB + D-pad Sinistra/Destra -> Ctrl+è / Ctrl++ (cambio
       dispositivo audio, mappato in SteelSeries GG)
       Back + Start + LB + RB + LT + RT -> Alt+F4 (chiude la finestra attiva)
       Back + RB (al rilascio) -> overlay in alto a sinistra con quattro tab
       (LT/RT per cambiare): "Controlli" con monitor, Big Picture, uscita
       audio, volume, alimentazione e il brano in riproduzione; "Giochi"
       con la griglia dei giochi Steam (anche non-Steam aggiunti a Steam)
       dal piu' recente; "Spotify" con brani recenti e playlist
       dell'account collegato; "Discord" con i canali vocali (recenti o
       per server, con avatar di chi c'e' dentro), muto, silenzia e
       condivisione schermo. D-pad/stick per navigare, A conferma/avvia,
       B chiude, LB/RB/X brani, L3 muto su Discord. Select tenuto premuto
       (o l'ingranaggio) apre le Impostazioni, con la pagina Info. Si apre
       anche con Shift+F1 o con un clic sull'icona del tray e si usa col
       mouse (clic, rotella); si chiude da solo quando un'altra finestra
       prende il primo piano (es. clic fuori)
     Usa la mappatura standard SDL GameController, quindi funziona con
     qualsiasi controller supportato (Xbox, PlayStation, 8BitDo, ecc.).

Richiede privilegi di amministratore per funzionare in modo affidabile
(hotkey che funzionano anche su finestre elevate).
"""

import asyncio
import base64
import concurrent.futures
import ctypes
import datetime
import functools
import gc
import hashlib
import http.server
import io
import json
import msvcrt
import os
import queue
import re
import secrets
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import warnings
import webbrowser
import winreg
from ctypes import wintypes
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import keyboard
import pystray
from PIL import Image, ImageDraw, ImageFont

try:
    import pygame
    import pygame._sdl2.controller as sdl_controller
    CONTROLLER_SUPPORT = True
except Exception:
    CONTROLLER_SUPPORT = False

try:
    import tkinter as tk
    from PIL import ImageTk
    OVERLAY_SUPPORT = True
except Exception:
    OVERLAY_SUPPORT = False

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import DataReader
    MEDIA_SUPPORT = True
except Exception:
    MEDIA_SUPPORT = False

try:
    from pycaw.pycaw import AudioUtilities
    from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
    import comtypes
    VOLUME_SUPPORT = True
except Exception:
    VOLUME_SUPPORT = False

APP_NAME = "AppUtili"

# ---------------------------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

# MonitorSwitcher (monitorprofileswitcher.com) e' portatile: l'exe puo' stare
# ovunque e si cerca (find_monitor_switcher). I profili sono gli XML che salva qui.
MONITOR_SWITCHER_NAME = "MonitorSwitcher.exe"
MONITOR_SWITCHER_SITE = "https://monitorprofileswitcher.com/"
MONITOR_PROFILES_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "MonitorSwitcher" / "Profiles"

HOTKEY_DESK = "ctrl+alt+down"  # profilo con il ruolo Scrivania
HOTKEY_TV = "ctrl+alt+up"  # profilo con il ruolo TV

# Combinazioni di pulsanti del controller, secondo la mappatura standard SDL
# "GameController" (la stessa numerazione che stampa rileva_controller.py):
#   4 = Back/Select, 7 = click stick sinistro, 8 = click stick destro,
#   9 = LB, 11 = D-pad Su, 12 = D-pad Giu, 13 = D-pad Sinistra,
#   14 = D-pad Destra
# Vale per qualsiasi marca (Xbox, PlayStation, 8BitDo, ecc.) perche' SDL
# traduce sempre sullo stesso schema.
COMBO_MONITOR_TV = frozenset({4, 9, 11})
COMBO_MONITOR_DESK = frozenset({4, 9, 12})
COMBO_SHOW_DESKTOP = frozenset({4, 7, 8})
COMBO_AUDIO_PREV = frozenset({4, 9, 13})
COMBO_AUDIO_NEXT = frozenset({4, 9, 14})

# LT/RT per SDL non sono pulsanti ma assi analogici (0..32767): li trattiamo
# come pulsanti "virtuali" con questi numeri, premuti oltre meta' corsa.
BUTTON_LT = 100
BUTTON_RT = 101
TRIGGER_PRESS = 16000
TRIGGER_RELEASE = 8000  # piu' basso della soglia di pressione: niente sfarfallii

# Alt+F4 volutamente scomodo: Back + Start + LB + RB + LT + RT tutti insieme
COMBO_CLOSE_WINDOW = frozenset({4, 6, 9, 10, BUTTON_LT, BUTTON_RT})

# Overlay: Back + RB. Scatta al RILASCIO e solo se non e' stato premuto
# nient'altro: {4, 10} e' contenuta nella combo di Alt+F4, che altrimenti
# aprirebbe l'overlay mentre la si compone.
COMBO_OVERLAY = frozenset({4, 10})

# Pulsanti usati per navigare nell'overlay
BUTTON_A = 0
BUTTON_B = 1
BUTTON_X = 2
BUTTON_Y = 3
BUTTON_LEFTSTICK = 7  # clic della levetta sinistra (L3)
BUTTON_RIGHTSTICK = 8  # clic della levetta destra (R3)
BUTTON_BACK = 4
BUTTON_START = 6
BUTTON_LB = 9
BUTTON_RB = 10
BUTTON_DPAD_UP = 11
BUTTON_DPAD_DOWN = 12
BUTTON_DPAD_LEFT = 13
BUTTON_DPAD_RIGHT = 14

# Select (Back) tenuto premuto da solo, a overlay aperto: impostazioni
SETTINGS_HOLD_SECONDS = 0.6

# Tenendo premuto su/giu' (D-pad o stick) la selezione si ripete
NAV_REPEAT_DELAY = 0.4
NAV_REPEAT_INTERVAL = 0.12

# Scorciatoie mappate in SteelSeries GG per cambiare dispositivo audio.
# Sono scan code dei tasti fisici (tastiera italiana) e non nomi: "+" per la
# libreria keyboard e' ambiguo e finirebbe sul "+" del tastierino numerico.
SCAN_CTRL = 29
SCAN_E_GRAVE = 26  # tasto "è"
SCAN_PLUS = 27     # tasto "+" accanto a "è"
HOTKEY_AUDIO_PREV = [SCAN_CTRL, SCAN_E_GRAVE]  # Ctrl+è
HOTKEY_AUDIO_NEXT = [SCAN_CTRL, SCAN_PLUS]     # Ctrl++

STEAM_OPEN_BIGPICTURE = "steam://open/bigpicture"
STEAM_CLOSE_BIGPICTURE = "steam://close/bigpicture"

DEFAULT_CONFIG = {
    "home_key_mode": "none",  # "windows" | "alttab" | "none"
    "controller_enabled": True,
    "notifications_enabled": True,
    "monitor_profile": "",  # nome dell'ultimo profilo caricato ("main"/"tv" nelle versioni vecchie)
    "monitor_desk": "",  # profilo con il ruolo Scrivania ("" = dedotto dal nome, "-" = nessuno)
    "monitor_tv": "",  # profilo con il ruolo TV (come sopra)
    "monitor_switcher_exe": "",  # trovato da find_monitor_switcher o scelto nelle impostazioni
    "hidden_tabs": [],  # tab dell'overlay nascoste dalle impostazioni
    "spotify_client_id": "",  # dell'app creata su developer.spotify.com
    "discord_client_id": "",  # dell'app creata su discord.com/developers
    "discord_client_secret": "",
    "discord_golive_hotkey": "f7",  # la stessa scorciatoia di Discord per condividere lo schermo
    "discord_favorites": [],  # id dei server preferiti, in cima alla vista Server dell'overlay
}

config_lock = threading.Lock()


def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # solo le chiavi note: ignora residui di versioni precedenti
            # (es. la vecchia "controller_mapping")
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
        except Exception:
            pass
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


config = load_config()

# Icona del tray, impostata in main(): serve per aggiornarla e per le notifiche
_tray_icon = None

# L'exe compilato non ha console: gli errori finiscono qui invece di sparire
ERROR_LOG = CONFIG_DIR / "errori.log"
ERROR_LOG_MAX = 256 * 1024  # oltre, si ricomincia da capo
_log_lock = threading.Lock()


def log_error(text):
    """Aggiunge una riga (o un traceback) a errori.log, con data e ora."""
    try:
        with _log_lock:
            mode = "w" if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > ERROR_LOG_MAX else "a"
            with open(ERROR_LOG, mode, encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {text.rstrip()}\n")
    except OSError:
        pass
    print(f"[AppUtili] {text.rstrip()}")


def notify(message):
    with config_lock:
        enabled = config["notifications_enabled"]
    if not enabled or _tray_icon is None:
        return
    try:
        _tray_icon.notify(message, APP_NAME)
    except Exception as e:
        print(f"[AppUtili] Errore notifica: {e}")

# ---------------------------------------------------------------------------
# MONITOR SETUP
# ---------------------------------------------------------------------------


# Ruoli: quale profilo caricano Ctrl+Alt+Giu (Scrivania) e Ctrl+Alt+Su (TV),
# e le combo Back+LB+Giu/Su del pad. Finche' l'utente non li sceglie nelle
# impostazioni si deducono dai nomi, altrimenti primo e secondo profilo.
MONITOR_ROLES = {
    "desk": ("monitor_desk", ("main", "desk", "scrivania"), 0),
    "tv": ("monitor_tv", ("tv",), 1),
}
MONITOR_NO_ROLE = "-"
# Colori dei profili (icona del tray, chip nell'overlay): Scrivania, TV, altri
MONITOR_COLORS = ((0, 120, 215), (136, 23, 152), (16, 137, 62), (202, 80, 16))
MONITOR_NO_COLOR = (110, 110, 110)

_monitor_profiles_cache = (0.0, [])
_monitor_switcher = None  # percorso trovato, "" = non trovato, None = non ancora cercato


def monitor_profiles():
    """Nomi dei profili salvati da MonitorSwitcher (i file .xml), in ordine
    alfabetico. Riletti al massimo ogni 2 secondi: servono a ogni disegno."""
    global _monitor_profiles_cache
    stamp, profiles = _monitor_profiles_cache
    if time.monotonic() - stamp > 2:
        try:
            profiles = sorted((p.stem for p in MONITOR_PROFILES_DIR.glob("*.xml")), key=str.casefold)
        except OSError:
            profiles = []
        _monitor_profiles_cache = (time.monotonic(), profiles)
    return profiles


def monitor_role(role):
    """Profilo con il ruolo "desk" (Scrivania) o "tv"; "" se nessuno."""
    key, words, fallback = MONITOR_ROLES[role]
    profiles = monitor_profiles()
    with config_lock:
        name = config[key]
    if name == MONITOR_NO_ROLE:
        return ""
    if name in profiles:
        return name
    for profile in profiles:
        if any(word in profile.casefold() for word in words):
            return profile
    return profiles[fallback] if len(profiles) > fallback else ""


def set_monitor_role(role, name):
    with config_lock:
        config[MONITOR_ROLES[role][0]] = name or MONITOR_NO_ROLE
        save_config(config)
    refresh_tray_icon()


def monitor_current():
    """Ultimo profilo caricato."""
    with config_lock:
        name = config["monitor_profile"]
    if name in ("main", "tv"):  # config delle versioni con i due profili fissi
        name = monitor_role("desk" if name == "main" else "tv")
    return name


def monitor_style(name):
    """(colore, testo) di un profilo: blu la Scrivania, viola la TV, poi gli
    altri. Il testo e' il nome se cortissimo ("TV"), altrimenti l'iniziale."""
    if not name:
        return MONITOR_NO_COLOR, "?"
    roles = [monitor_role("desk"), monitor_role("tv")]
    if name in roles:
        color = MONITOR_COLORS[roles.index(name)]
    else:
        others = [p for p in monitor_profiles() if p not in roles]
        index = others.index(name) if name in others else 0
        color = MONITOR_COLORS[2 + index % (len(MONITOR_COLORS) - 2)]
    return color, name.upper() if len(name) <= 3 else name[:1].upper()


# --- dove sta MonitorSwitcher.exe ---

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DRIVE_FIXED = 3


def _running_exe_path(exe_name):
    """Percorso completo di un processo in esecuzione con quel nome, o None."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == exe_name.casefold():
                process = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                          entry.th32ProcessID)
                if process:
                    try:
                        size = wintypes.DWORD(1024)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if k32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                            return buffer.value
                    finally:
                        k32.CloseHandle(process)
            ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snapshot)
    return None


def _find_in_folders(roots, name, depth=2):
    """Primo file `name` dentro le cartelle date, fino a `depth` livelli sotto."""
    for root in roots:
        level = [root]
        for _ in range(depth + 1):
            deeper = []
            for folder in level:
                if (folder / name).is_file():
                    return folder / name
                try:
                    with os.scandir(folder) as entries:
                        deeper += [Path(e.path) for e in entries
                                   if e.is_dir(follow_symlinks=False)
                                   and not e.name.startswith(("$", "."))
                                   and e.name.casefold() != "windows"]
                except OSError:
                    pass
            level = deeper
    return None


def find_monitor_switcher():
    """Cerca MonitorSwitcher.exe: percorso salvato, processo in esecuzione
    (di solito resta nel tray), cartelle dell'utente, poi i dischi locali.
    Salva il risultato; "" se non c'e'."""
    global _monitor_switcher
    with config_lock:
        saved = config["monitor_switcher_exe"]
    found = saved if saved and Path(saved).is_file() else None
    if not found:
        try:
            found = _running_exe_path(MONITOR_SWITCHER_NAME)
        except Exception as e:
            print(f"[AppUtili] Errore lettura processi: {e}")
    if not found:
        home = Path.home()
        roots = [home / "Downloads", home / "Documents", home / "Desktop",
                 Path(os.getenv("LOCALAPPDATA", str(home))) / "Programs",
                 Path(os.getenv("ProgramFiles", r"C:\Program Files")),
                 Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
        drives = ctypes.windll.kernel32.GetLogicalDrives()
        roots += [Path(f"{chr(65 + i)}:\\") for i in range(26)
                  if drives >> i & 1 and ctypes.windll.kernel32.GetDriveTypeW(f"{chr(65 + i)}:\\")
                  == DRIVE_FIXED]
        found = _find_in_folders([r for r in roots if r.is_dir()], MONITOR_SWITCHER_NAME)
    found = str(found) if found else ""
    if found != saved:
        with config_lock:
            config["monitor_switcher_exe"] = found
            save_config(config)
    _monitor_switcher = found
    return found


def set_monitor_switcher(path):
    """Percorso scelto a mano nelle impostazioni."""
    global _monitor_switcher
    with config_lock:
        config["monitor_switcher_exe"] = path
        save_config(config)
    _monitor_switcher = path


def monitor_switcher_exe():
    """Percorso gia' trovato ("" se non c'e', None se la ricerca non e' finita)."""
    return _monitor_switcher


def start_monitor_search():
    threading.Thread(target=find_monitor_switcher, daemon=True).start()


def switch_monitor(name):
    """Carica un profilo con MonitorSwitcher."""
    if not name:
        return
    exe = _monitor_switcher if _monitor_switcher else find_monitor_switcher()
    with config_lock:
        config["monitor_profile"] = name
        save_config(config)
    refresh_tray_icon()
    if not exe:
        notify("⚠ MonitorSwitcher non trovato: vedi Impostazioni → Info")
        return
    try:
        subprocess.Popen([exe, f"-load:{MONITOR_PROFILES_DIR / (name + '.xml')}"])
        notify(f"🖥 Monitor: {name}")
    except Exception as e:
        print(f"[AppUtili] Errore avvio MonitorSwitcher: {e}")
        notify("⚠ Impossibile avviare MonitorSwitcher")


def switch_to_desk(icon=None, item=None):
    switch_monitor(monitor_role("desk"))


def switch_to_tv(icon=None, item=None):
    switch_monitor(monitor_role("tv"))


def _open_steam_url(url):
    try:
        os.startfile(url)
    except Exception as e:
        print(f"[AppUtili] Errore apertura {url}: {e}")


def open_bigpicture():
    _open_steam_url(STEAM_OPEN_BIGPICTURE)


def close_bigpicture():
    _open_steam_url(STEAM_CLOSE_BIGPICTURE)


def register_monitor_hotkeys():
    try:
        keyboard.add_hotkey(HOTKEY_DESK, switch_to_desk)
        keyboard.add_hotkey(HOTKEY_TV, switch_to_tv)
    except Exception as e:
        print(f"[AppUtili] Errore registrazione hotkey monitor: {e}")


# ---------------------------------------------------------------------------
# MAPPA TASTO HOME (tasto multimediale "Browser Home")
# ---------------------------------------------------------------------------
#
# I tasti "browser"/multimediali delle tastiere generano, oltre al normale
# evento di tastiera, anche un messaggio speciale di Windows (WM_APPCOMMAND)
# che la shell intercetta per aprire il browser. Un hook a basso livello
# (quello usato dalla libreria "keyboard" con suppress=True) NON riesce a
# bloccare questo messaggio.
#
# La soluzione affidabile e' RegisterHotKey: l'API che i player multimediali
# usano per "riservarsi" un tasto in esclusiva. Se la registrazione va a buon
# fine, Windows smette di generare l'azione di default per quel tasto e manda
# solo il messaggio WM_HOTKEY al nostro thread.

user32 = ctypes.windll.user32 if os.name == "nt" else None

VK_BROWSER_HOME = 0xAC
VK_F1 = 0x70
WM_HOTKEY = 0x0312
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
HOTKEY_ID_HOME = 1
# Lo stesso thread registra anche Shift+F1, che apre/chiude l'overlay da
# tastiera: con RegisterHotKey il tasto non arriva all'app in primo piano e
# Windows concede a chi riceve l'hotkey di prendersi il primo piano.
HOTKEY_ID_OVERLAY = 2
HOTKEY_OVERLAY_LABEL = "Shift+F1"

_home_stop_event = threading.Event()
_home_thread = None


def _home_hotkey_loop():
    """Gira in un thread dedicato: registra/deregistra l'hotkey in base alla
    configurazione corrente e processa i messaggi WM_HOTKEY (anche quello
    dell'overlay, registrato una volta sola)."""
    registered = False
    msg = wintypes.MSG()

    overlay_registered = OVERLAY_SUPPORT and bool(
        user32.RegisterHotKey(None, HOTKEY_ID_OVERLAY, MOD_SHIFT | MOD_NOREPEAT, VK_F1))
    if OVERLAY_SUPPORT and not overlay_registered:
        print(f"[AppUtili] Impossibile registrare {HOTKEY_OVERLAY_LABEL} per l'overlay "
              "(forse e' gia' usato da un altro programma o da un'altra AppUtili).")

    while not _home_stop_event.is_set():
        with config_lock:
            mode = config["home_key_mode"]
        should_be_registered = mode in ("windows", "alttab")

        if should_be_registered and not registered:
            ok = user32.RegisterHotKey(None, HOTKEY_ID_HOME, MOD_NOREPEAT, VK_BROWSER_HOME)
            if ok:
                registered = True
            else:
                print(
                    "[AppUtili] Impossibile registrare l'hotkey sul tasto Home "
                    "(forse e' gia' usato da un altro programma, es. software della tastiera)."
                )
        elif not should_be_registered and registered:
            user32.UnregisterHotKey(None, HOTKEY_ID_HOME)
            registered = False

        # Processa eventuali messaggi in coda senza bloccare troppo a lungo,
        # cosi' il ciclo puo' ricontrollare periodicamente la configurazione.
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # 1 = PM_REMOVE
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_OVERLAY:
                _overlay_queue.put("toggle")
            elif msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_HOME:
                with config_lock:
                    current_mode = config["home_key_mode"]
                if current_mode == "windows":
                    keyboard.send("windows")
                elif current_mode == "alttab":
                    keyboard.send("alt+tab")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        _home_stop_event.wait(0.05)

    if registered:
        user32.UnregisterHotKey(None, HOTKEY_ID_HOME)
    if overlay_registered:
        user32.UnregisterHotKey(None, HOTKEY_ID_OVERLAY)


def start_home_hotkey_thread():
    global _home_thread
    _home_stop_event.clear()
    _home_thread = threading.Thread(target=_home_hotkey_loop, daemon=True)
    _home_thread.start()


def stop_home_hotkey_thread():
    _home_stop_event.set()
    if _home_thread is not None:
        _home_thread.join(timeout=1)


def set_home_mode(mode):
    def _handler(icon, item):
        with config_lock:
            config["home_key_mode"] = mode
            save_config(config)
        # Nessuna azione aggiuntiva necessaria: il thread rileva il cambio
        # di configurazione da solo entro ~50ms e (de)registra l'hotkey.
    return _handler


def home_mode_checked(mode):
    def _checked(item):
        with config_lock:
            return config["home_key_mode"] == mode
    return _checked


# ---------------------------------------------------------------------------
# CONTROLLER (gamepad/joystick: Xbox, PlayStation, 8BitDo, ecc.)
# ---------------------------------------------------------------------------
#
# Usiamo l'API "GameController" di SDL (via pygame._sdl2.controller) invece
# del vecchio Joystick API grezzo: SDL mappa automaticamente ogni controller
# supportato sullo schema standard A/B/X/Y/dorsali/stick/D-pad, quindi le
# combinazioni restano valide anche cambiando marca/modello di controller.

_controller_stop_event = threading.Event()
_controller_thread = None
_open_controllers = {}  # instance_id -> oggetto Controller
_pressed_buttons = {}  # instance_id -> set dei pulsanti attualmente premuti

def show_desktop():
    keyboard.send("windows+d")


def audio_prev():
    keyboard.send(HOTKEY_AUDIO_PREV)


def audio_next():
    keyboard.send(HOTKEY_AUDIO_NEXT)


def close_window():
    keyboard.send("alt+f4")


CONTROLLER_COMBOS = [
    (COMBO_CLOSE_WINDOW, close_window),
    (COMBO_MONITOR_TV, switch_to_tv),
    (COMBO_MONITOR_DESK, switch_to_desk),
    (COMBO_SHOW_DESKTOP, show_desktop),
    (COMBO_AUDIO_PREV, audio_prev),
    (COMBO_AUDIO_NEXT, audio_next),
]


def _check_combos(pressed, new_button):
    """Scatta solo sul pulsante che completa la combinazione: tenendola
    premuta non si ripete, va rilasciata e ripremuta."""
    for combo, action in CONTROLLER_COMBOS:
        if new_button in combo and combo <= pressed:
            action()


_overlay_armed = set()  # instance_id che stanno tenendo solo Back + RB
_stick_direction = {}  # (instance_id, asse "x"/"y") -> direzione o None (stick sinistro)
_nav_repeat = {}  # chiave (instance_id, sorgente) -> (comando, prossima ripetizione)
_back_hold = {}  # instance_id -> quando e' stato premuto Select da solo

OVERLAY_BUTTONS = {
    BUTTON_DPAD_UP: "up",
    BUTTON_DPAD_DOWN: "down",
    BUTTON_DPAD_LEFT: "left",
    BUTTON_DPAD_RIGHT: "right",
    BUTTON_A: "a",
    BUTTON_B: "b",
    BUTTON_X: "play_pause",
    BUTTON_Y: "y",
    BUTTON_START: "start",
    BUTTON_LEFTSTICK: "discord_mute",
    BUTTON_RIGHTSTICK: "shuffle",
    BUTTON_LB: "prev_track",
    BUTTON_RB: "next_track",
    BUTTON_LT: "prev_tab",
    BUTTON_RT: "next_tab",
}
# Direzioni che si ripetono tenendo premuto. Sinistra/destra si ripetono come
# "left_repeat"/"right_repeat": l'overlay le usa solo dove ha senso (slider del
# volume, griglia dei giochi) e mai per cambiare l'uscita audio
NAV_REPEATING = ("up", "down", "left", "right")


def _nav_press(key, command):
    _overlay_queue.put(command)
    _nav_repeat[key] = (command, time.monotonic() + NAV_REPEAT_DELAY)


def _nav_tick():
    """Chiamata a ogni giro del ciclo controller: ripete su/giu' tenuti premuti
    e apre le impostazioni quando Select resta premuto da solo."""
    if not _overlay_open.is_set():
        _nav_repeat.clear()
        _back_hold.clear()
        return
    now = time.monotonic()
    for instance_id, since in list(_back_hold.items()):
        if _pressed_buttons.get(instance_id) != {BUTTON_BACK}:
            del _back_hold[instance_id]  # premuto altro: e' una combo (es. Back+RB)
        elif now - since >= SETTINGS_HOLD_SECONDS:
            del _back_hold[instance_id]
            _overlay_queue.put("settings")
    for key, (command, due) in list(_nav_repeat.items()):
        if now >= due:
            _overlay_queue.put(command if command in ("up", "down") else command + "_repeat")
            _nav_repeat[key] = (command, now + NAV_REPEAT_INTERVAL)


def _on_button_down(instance_id, button):
    pressed = _pressed_buttons.setdefault(instance_id, set())
    pressed.add(button)

    # Overlay aperto: i pulsanti servono solo a navigarlo, niente combo
    if _overlay_open.is_set():
        command = OVERLAY_BUTTONS.get(button)
        if button in (BUTTON_LB, BUTTON_RB) and BUTTON_BACK in pressed:
            command = None  # combo con Back (es. Back + RB per chiudere), non cambio brano
        if command in NAV_REPEATING:
            _nav_press((instance_id, button), command)
        elif command is not None:
            _overlay_queue.put(command)
        if pressed == {BUTTON_BACK}:
            _back_hold[instance_id] = time.monotonic()
        _update_overlay_armed(instance_id, pressed)
        return

    with config_lock:
        enabled = config["controller_enabled"]
    if enabled:
        _update_overlay_armed(instance_id, pressed)
        _check_combos(pressed, button)


def _update_overlay_armed(instance_id, pressed):
    if OVERLAY_SUPPORT and pressed == COMBO_OVERLAY:
        _overlay_armed.add(instance_id)
    else:
        _overlay_armed.discard(instance_id)


def _on_button_up(instance_id, button):
    _pressed_buttons.get(instance_id, set()).discard(button)
    _nav_repeat.pop((instance_id, button), None)
    if button == BUTTON_BACK:
        _back_hold.pop(instance_id, None)
    if button in COMBO_OVERLAY and instance_id in _overlay_armed:
        _overlay_armed.discard(instance_id)
        _overlay_queue.put("toggle")


def _on_stick(instance_id, axis, value, negative, positive):
    # SDL: valori negativi = stick in su / a sinistra. Stesse soglie dei
    # grilletti, con una fascia intermedia in cui la direzione non cambia
    # (isteresi).
    if value <= -TRIGGER_PRESS:
        direction = negative
    elif value >= TRIGGER_PRESS:
        direction = positive
    elif abs(value) <= TRIGGER_RELEASE:
        direction = None
    else:
        return
    if direction == _stick_direction.get((instance_id, axis)):
        return
    _stick_direction[(instance_id, axis)] = direction
    key = (instance_id, "stick-" + axis)
    _nav_repeat.pop(key, None)
    if direction is None or not _overlay_open.is_set():
        return
    _nav_press(key, direction)


def _controller_loop():
    if not CONTROLLER_SUPPORT:
        return

    pygame.init()
    sdl_controller.init()

    trigger_axes = {
        pygame.CONTROLLER_AXIS_TRIGGERLEFT: BUTTON_LT,
        pygame.CONTROLLER_AXIS_TRIGGERRIGHT: BUTTON_RT,
    }

    try:
        for i in range(sdl_controller.get_count()):
            if sdl_controller.is_controller(i):
                c = sdl_controller.Controller(i)
                _open_controllers[c.as_joystick().get_instance_id()] = c
    except Exception as e:
        print(f"[AppUtili] Errore apertura controller gia' connessi: {e}")

    while not _controller_stop_event.is_set():
        for event in pygame.event.get():
            if event.type == pygame.CONTROLLERDEVICEADDED:
                try:
                    if sdl_controller.is_controller(event.device_index):
                        c = sdl_controller.Controller(event.device_index)
                        _open_controllers[c.as_joystick().get_instance_id()] = c
                except Exception as e:
                    print(f"[AppUtili] Errore apertura nuovo controller: {e}")
            elif event.type == pygame.CONTROLLERDEVICEREMOVED:
                _pressed_buttons.pop(event.instance_id, None)
                _overlay_armed.discard(event.instance_id)
                for state in (_stick_direction, _nav_repeat):
                    for key in [k for k in state if k[0] == event.instance_id]:
                        del state[key]
                c = _open_controllers.pop(event.instance_id, None)
                if c is not None:
                    try:
                        c.as_joystick().quit()
                    except Exception:
                        pass
                # senza controller l'overlay non si potrebbe piu' chiudere
                if not _open_controllers and _overlay_open.is_set():
                    _overlay_queue.put("b")
            elif event.type == pygame.CONTROLLERBUTTONDOWN:
                _on_button_down(event.instance_id, event.button)
            elif event.type == pygame.CONTROLLERBUTTONUP:
                _on_button_up(event.instance_id, event.button)
            elif event.type == pygame.CONTROLLERAXISMOTION:
                if event.axis == pygame.CONTROLLER_AXIS_LEFTY:
                    _on_stick(event.instance_id, "y", event.value, "up", "down")
                    continue
                if event.axis == pygame.CONTROLLER_AXIS_LEFTX:
                    _on_stick(event.instance_id, "x", event.value, "left", "right")
                    continue
                trigger = trigger_axes.get(event.axis)
                if trigger is None:
                    continue
                pressed = _pressed_buttons.get(event.instance_id, set())
                if event.value >= TRIGGER_PRESS and trigger not in pressed:
                    _on_button_down(event.instance_id, trigger)
                elif event.value <= TRIGGER_RELEASE:
                    pressed.discard(trigger)

        _nav_tick()
        _controller_stop_event.wait(0.02)

    for c in list(_open_controllers.values()):
        try:
            c.as_joystick().quit()
        except Exception:
            pass
    _open_controllers.clear()
    _pressed_buttons.clear()
    try:
        sdl_controller.quit()
    except Exception:
        pass
    try:
        pygame.quit()
    except Exception:
        pass


def start_controller_thread():
    global _controller_thread
    if not CONTROLLER_SUPPORT:
        print("[AppUtili] pygame non disponibile: il gruppo Controller resta disattivato.")
        return
    _controller_stop_event.clear()
    _controller_thread = threading.Thread(target=_controller_loop, daemon=True)
    _controller_thread.start()


def stop_controller_thread():
    _controller_stop_event.set()
    if _controller_thread is not None:
        _controller_thread.join(timeout=1)


def toggle_controller_enabled(icon, item):
    with config_lock:
        config["controller_enabled"] = not config["controller_enabled"]
        save_config(config)
    refresh_tray_icon()


def toggle_notifications(icon, item):
    with config_lock:
        config["notifications_enabled"] = not config["notifications_enabled"]
        save_config(config)


def notifications_checked(item):
    with config_lock:
        return config["notifications_enabled"]


def controller_enabled_checked(item):
    with config_lock:
        return config["controller_enabled"]


def build_controller_menu():
    if not CONTROLLER_SUPPORT:
        return pystray.Menu(
            pystray.MenuItem("pygame non installato", None, enabled=False)
        )

    return pystray.Menu(
        pystray.MenuItem("Attivo", toggle_controller_enabled,
                          checked=controller_enabled_checked),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("📺  Back+LB+Su: monitor TV", None, enabled=False),
        pystray.MenuItem("🖥  Back+LB+Giù: monitor Scrivania", None, enabled=False),
        pystray.MenuItem("🪟  Back+L3+R3: mostra desktop (Win+D)", None, enabled=False),
        pystray.MenuItem("🔊  Back+LB+Sinistra: audio (Ctrl+è)", None, enabled=False),
        pystray.MenuItem("🔊  Back+LB+Destra: audio (Ctrl++)", None, enabled=False),
        pystray.MenuItem("✖  Back+Start+LB+RB+LT+RT: chiudi finestra (Alt+F4)",
                          None, enabled=False),
        pystray.MenuItem("☰  Back+RB: overlay (LT/RT tab, A conferma, B chiude, LB/RB/X brani)",
                          None, enabled=False),
    )


# ---------------------------------------------------------------------------
# MEDIA IN RIPRODUZIONE (controlli multimediali di Windows)
# ---------------------------------------------------------------------------
#
# Stessa fonte del riquadro media di Windows (GlobalSystemMediaTransport-
# Controls): titolo, artista, copertina e comandi di Spotify, browser, ecc.
# L'API WinRT e' asincrona, quindi gira in un thread con un suo ciclo
# asyncio. Interroga la sessione solo mentre l'overlay e' aperto.

MEDIA_POLL_INTERVAL = 1.0
# Ripiego senza WinRT (o senza sessioni): i tasti multimediali della tastiera
MEDIA_KEYS = {
    "play_pause": "play/pause media",
    "next_track": "next track",
    "prev_track": "previous track",
}
MEDIA_METHODS = {
    "play_pause": "try_toggle_play_pause_async",
    "next_track": "try_skip_next_async",
    "prev_track": "try_skip_previous_async",
}
MEDIA_APP_NAMES = {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox",
                   "zunemusic": "Lettore multimediale", "vlc": "VLC"}

_media_lock = threading.Lock()
_media_state = None  # dict con title/artist/app/playing/cover, None = niente in riproduzione
_media_loop = None
_media_wake = None  # asyncio.Event del thread media: sveglia il ciclo in anticipo
_media_manager = None


def _media_app_name(app_id):
    # "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify" -> "Spotify", "chrome.exe" -> "Chrome"
    name = app_id.split("!")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    name = name.split(".")[-1]
    return MEDIA_APP_NAMES.get(name.lower(), name[:1].upper() + name[1:])


async def _media_read_cover(thumbnail):
    if thumbnail is None:
        return None
    stream = await thumbnail.open_read_async()
    reader = DataReader(stream.get_input_stream_at(0))
    await reader.load_async(stream.size)
    data = bytearray(stream.size)
    reader.read_bytes(data)
    return Image.open(io.BytesIO(bytes(data))).convert("RGB")


async def _media_poll(last):
    """Legge la sessione corrente. Ritorna (stato, chiave) e riusa la
    copertina gia' letta se il brano non e' cambiato."""
    session = _media_manager.get_current_session()
    if session is None:
        return None, None
    props = await session.try_get_media_properties_async()
    key = (session.source_app_user_model_id, props.title, props.artist)
    cover = last["cover"] if last and last["key"] == key else await _media_read_cover(props.thumbnail)
    return {
        "key": key,
        "title": props.title or "Senza titolo",
        "artist": props.artist or "",
        "app": _media_app_name(session.source_app_user_model_id),
        "playing": session.get_playback_info().playback_status == PlaybackStatus.PLAYING,
        "cover": cover,
    }, key


async def _media_main():
    global _media_wake, _media_manager, _media_state
    _media_wake = asyncio.Event()
    _media_manager = await MediaManager.request_async()
    while True:
        if _overlay_open.is_set():
            try:
                with _media_lock:
                    last = _media_state
                state, _ = await _media_poll(last)
                signature = lambda s: s and (s["key"], s["playing"])
                if signature(state) != signature(last):
                    with _media_lock:
                        _media_state = state
                    _overlay_queue.put("redraw")
            except Exception as e:
                print(f"[AppUtili] Errore lettura media: {e}")
        try:
            await asyncio.wait_for(_media_wake.wait(), MEDIA_POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
        _media_wake.clear()


def _media_thread_main():
    global _media_loop
    _media_loop = asyncio.new_event_loop()
    try:
        _media_loop.run_until_complete(_media_main())
    except Exception as e:
        print(f"[AppUtili] Controlli multimediali non disponibili: {e}")


def start_media_thread():
    if MEDIA_SUPPORT:
        threading.Thread(target=_media_thread_main, daemon=True).start()


def media_refresh():
    """Chiede una lettura immediata (es. all'apertura dell'overlay)."""
    if _media_loop is not None and _media_wake is not None:
        _media_loop.call_soon_threadsafe(_media_wake.set)


def get_media_state():
    with _media_lock:
        return _media_state


async def _media_do(command):
    session = _media_manager.get_current_session() if _media_manager else None
    if session is None:
        keyboard.send(MEDIA_KEYS[command])
    else:
        await getattr(session, MEDIA_METHODS[command])()
    await asyncio.sleep(0.3)  # tempo all'app per aggiornare lo stato
    _media_wake.set()


def media_command(command):
    """command: "play_pause" | "next_track" | "prev_track"."""
    if _media_loop is not None and _media_manager is not None:
        asyncio.run_coroutine_threadsafe(_media_do(command), _media_loop)
    else:
        keyboard.send(MEDIA_KEYS[command])


# ---------------------------------------------------------------------------
# USCITA AUDIO (SteelSeries Sonar oppure Windows)
# ---------------------------------------------------------------------------
#
# Con Sonar l'uscita predefinita di Windows e' sempre il dispositivo virtuale
# "SteelSeries Sonar - Gaming": le scorciatoie Ctrl+è / Ctrl++ cambiano il
# dispositivo reale DENTRO Sonar. Per mostrarne il nome si legge l'API web
# locale di SteelSeries GG (non documentata, la stessa usata da vari progetti
# della community). Solo letture: il cambio resta affidato alle scorciatoie.
#
# Se GG/Sonar non risponde si cambia invece l'uscita predefinita di Windows
# (con pycaw), scorrendo tra quelle attive.

SONAR_CORE_PROPS = (Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
                    / "SteelSeries" / "GG" / "coreProps.json")
SONAR_REFRESH_DELAY = 0.7  # attesa dopo Ctrl+è / Ctrl++ prima di rileggere il nome
SONAR_DEVICE_NAME = "SteelSeries Sonar"  # i suoi dispositivi virtuali, saltati in modalita' Windows
# Logo preso dall'installazione di GG: icona della riga Uscita quando c'e' Sonar
STEELSERIES_LOGO = (Path(os.getenv("PROGRAMFILES", r"C:\Program Files")) / "SteelSeries" / "GG"
                    / "resources" / "overlays" / "steelseries_logo_64x64.png")

_sonar_base = None  # es. "http://127.0.0.1:61188", cambia a ogni avvio di GG
_audio_output = None  # (tipo, nome) dell'uscita attuale, es. ("Cuffie", "Arctis Nova Pro Wireless")
_audio_source = None  # "sonar" | "windows" | None (sconosciuta, o pycaw mancante)
_audio_switching = False
_audio_lock = threading.Lock()  # un cambio di uscita alla volta


def _sonar_get(url):
    # GG espone https su localhost con un certificato auto-firmato
    ctx = ssl._create_unverified_context() if url.startswith("https") else None
    with urllib.request.urlopen(url, context=ctx, timeout=1.5) as response:
        return json.loads(response.read())


def _sonar_resolve_base():
    with open(SONAR_CORE_PROPS, "r", encoding="utf-8") as f:
        props = json.load(f)
    sub_apps = _sonar_get(f"https://{props['ggEncryptedAddress']}/subApps")
    return sub_apps["subApps"]["sonar"]["metadata"]["webServerAddress"]


def _sonar_output_device_name():
    global _sonar_base
    for attempt in range(2):
        try:
            if _sonar_base is None:
                _sonar_base = _sonar_resolve_base()
            if _sonar_get(f"{_sonar_base}/mode") == "stream":
                redirections = _sonar_get(f"{_sonar_base}/streamRedirections")
                device_id = next(r["deviceId"] for r in redirections
                                 if r["streamRedirectionId"] == "monitoring")
            else:
                redirections = _sonar_get(f"{_sonar_base}/classicRedirections")
                device_id = next(r["deviceId"] for r in redirections if r["id"] == "game")
            devices = _sonar_get(f"{_sonar_base}/audioDevices")
            return next(d["friendlyName"] for d in devices if d["id"] == device_id)
        except Exception:
            _sonar_base = None  # GG riavviato = porta nuova: si riprova da capo
    return None


def _split_device_name(name):
    # "Cuffie (Arctis Nova Pro Wireless)" -> ("Cuffie", "Arctis Nova Pro Wireless")
    match = re.match(r"^(.*?) \((.*)\)$", name)
    return (match.group(1), match.group(2)) if match else ("", name)


def _windows_outputs():
    """([(id, nome)] delle uscite attive di Windows senza quelle virtuali di
    Sonar, (id, nome) di quella predefinita, Sonar attivo?). Da un thread
    con COM attivo."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pycaw avvisa per ogni proprieta' illeggibile
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        default = AudioUtilities.GetSpeakers()
    names = [(d.id, d.FriendlyName or "") for d in devices]
    outputs = sorted(((i, n) for i, n in names if n and SONAR_DEVICE_NAME not in n),
                     key=lambda o: o[1].casefold())
    sonar = any(SONAR_DEVICE_NAME in n for _, n in names)
    return outputs, (default.id, default.FriendlyName) if default else (None, None), sonar


def _windows_switch_output(step):
    """Passa all'uscita attiva successiva/precedente e la rende predefinita."""
    outputs, (default_id, _), _ = _windows_outputs()
    if not outputs:
        return
    ids = [device_id for device_id, _ in outputs]
    # se la predefinita non e' in elenco (es. Sonar rimasto predefinito) si parte da un capo
    index = ids.index(default_id) if default_id in ids else (-1 if step > 0 else 0)
    AudioUtilities.SetDefaultDevice(ids[(index + step) % len(ids)],
                                    [ERole.eConsole, ERole.eMultimedia])


def refresh_audio_output(delay=0, windows_step=0):
    """Rilegge in un thread a parte il nome dell'uscita (prima Sonar, poi
    Windows) e ridisegna. windows_step: prima cambia l'uscita di Windows."""
    def _worker():
        global _audio_output, _audio_source, _audio_switching
        if VOLUME_SUPPORT:
            comtypes.CoInitialize()
        try:
            with _audio_lock:
                if windows_step:
                    _windows_switch_output(windows_step)
                time.sleep(delay)
                windows = _windows_outputs() if VOLUME_SUPPORT else None
                # senza i dispositivi virtuali di Sonar GG e' chiuso: inutile
                # interrogarlo (una connessione rifiutata costa secondi)
                name = _sonar_output_device_name() if windows is None or windows[2] else None
                source = "sonar" if name else None
                if name is None and windows is not None:
                    source = "windows"
                    name = windows[1][1]
        except Exception as e:
            print(f"[AppUtili] Errore uscita audio: {e}")
            name = source = None
        finally:
            if VOLUME_SUPPORT:
                comtypes.CoUninitialize()
        _audio_output = _split_device_name(name) if name else None
        _audio_source = source
        _audio_switching = False
        _overlay_queue.put("redraw")
    threading.Thread(target=_worker, daemon=True).start()


def switch_audio_output(step):
    global _audio_switching
    _audio_switching = True
    if _audio_source == "windows":
        refresh_audio_output(windows_step=step)
    else:
        (audio_next if step > 0 else audio_prev)()
        refresh_audio_output(SONAR_REFRESH_DELAY)


# ---------------------------------------------------------------------------
# LIBRERIA GIOCHI (Steam)
# ---------------------------------------------------------------------------
#
# Giochi Steam installati e giochi "non-Steam" aggiunti a Steam a mano, letti
# dai file locali di Steam (niente API web ne' login):
#   - steamapps\appmanifest_<appid>.acf: nome e data dell'ultima partita
#   - appcache\librarycache\<appid>\: copertine, a volte in sottocartelle con
#     nome a hash (la verticale si chiama library_600x900 o library_capsule)
#   - userdata\<utente>\config\shortcuts.vdf (binario): i giochi non-Steam,
#     con le copertine personalizzate in config\grid
# Si avviano tutti tramite Steam con steam://rungameid/.

STEAM_EXCLUDED_APPIDS = {228980}  # Steamworks Common Redistributables
STEAM_SHORTCUT_FLAG = 0x02000000  # gameid di un non-Steam: (appid << 32) | questo
STEAM_ART_NAMES = {
    "capsule": ("library_600x900.jpg", "library_capsule.jpg"),
    "hero": ("library_hero.jpg",),
    "logo": ("logo.png",),
    "header": ("library_header.jpg", "header.jpg"),
}
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

_games_lock = threading.Lock()
_games = None  # None = non ancora caricati


def _steam_registry(path, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _steam_root():
    path = _steam_registry(r"Software\Valve\Steam", "SteamPath")
    return Path(path) if path and Path(path).is_dir() else None


def _steam_active_user(root):
    user = _steam_registry(r"Software\Valve\Steam\ActiveProcess", "ActiveUser")
    if user:
        return str(user)
    # Steam chiuso: l'utente la cui configurazione e' cambiata per ultima
    configs = sorted((root / "userdata").glob("*/config/localconfig.vdf"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return configs[0].parent.parent.name if configs else None


def _vdf_values(path):
    """Coppie "chiave" "valore" di un VDF di testo (la prima occorrenza di
    ogni chiave): per i manifest bastano appid, name e LastPlayed."""
    values = {}
    for key, value in re.findall(r'"([^"]+)"\s+"([^"]*)"',
                                 path.read_text(encoding="utf-8", errors="replace")):
        values.setdefault(key, value)
    return values


def _steam_libraries(root):
    libraries = {root.resolve()}
    try:
        text = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        for path in re.findall(r'"path"\s+"([^"]+)"', text):
            libraries.add(Path(path.replace("\\\\", "\\")).resolve())
    except OSError:
        pass
    return libraries


def _steam_art(root, appid):
    found = {}
    folder = root / "appcache" / "librarycache" / str(appid)
    files = {}
    if folder.is_dir():
        files = {p.name.lower(): p for p in folder.rglob("*") if p.is_file()}
    for kind, names in STEAM_ART_NAMES.items():
        for name in names:
            # layout attuale (cartella per gioco) o vecchio (file "<appid>_nome")
            path = files.get(name) or root / "appcache" / "librarycache" / f"{appid}_{name}"
            if path.is_file():
                found[kind] = path
                break
    return found


def _read_binary_vdf(data):
    """Parser minimo del VDF binario di Steam: 0x00 = mappa, 0x01 = stringa,
    0x02 = intero a 32 bit, 0x08 = fine mappa."""
    pos = 0

    def read_string():
        nonlocal pos
        end = data.index(b"\x00", pos)
        text = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        return text

    def read_map():
        nonlocal pos
        result = {}
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == 0x08:
                break
            key = read_string().lower()
            if kind == 0x00:
                result[key] = read_map()
            elif kind == 0x01:
                result[key] = read_string()
            elif kind == 0x02:
                result[key] = int.from_bytes(data[pos:pos + 4], "little", signed=True)
                pos += 4
            else:
                raise ValueError(f"tipo VDF sconosciuto: {kind}")
        return result

    return read_map()


def _shortcut_art(grid, appid, icon):
    found = {}
    patterns = {"capsule": f"{appid}p", "hero": f"{appid}_hero", "logo": f"{appid}_logo",
                "header": f"{appid}"}
    for kind, stem in patterns.items():
        for ext in IMAGE_EXTENSIONS:
            if (grid / (stem + ext)).is_file():
                found[kind] = grid / (stem + ext)
                break
    if "header" not in found and icon and Path(icon).suffix.lower() in IMAGE_EXTENSIONS \
            and Path(icon).is_file():
        found["header"] = Path(icon)
    return found


def load_games():
    """Giochi installati, dal piu' recente; quelli mai avviati in fondo."""
    root = _steam_root()
    if root is None:
        return []
    games = []
    for library in _steam_libraries(root):
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            try:
                values = _vdf_values(manifest)
                appid = int(values["appid"])
            except (OSError, KeyError, ValueError):
                continue
            name = values.get("name", f"App {appid}")
            if appid in STEAM_EXCLUDED_APPIDS or "redistributable" in name.lower():
                continue
            games.append({
                "name": name, "source": "Steam", "url": f"steam://rungameid/{appid}",
                "last_played": int(values.get("LastPlayed") or 0),
                "art": _steam_art(root, appid),
            })

    user = _steam_active_user(root)
    shortcuts = root / "userdata" / str(user) / "config" / "shortcuts.vdf"
    if user and shortcuts.is_file():
        try:
            entries = _read_binary_vdf(shortcuts.read_bytes()).get("shortcuts", {})
            for entry in entries.values():
                appid = entry.get("appid", 0) & 0xFFFFFFFF
                gameid = (appid << 32) | STEAM_SHORTCUT_FLAG
                games.append({
                    "name": entry.get("appname") or "Senza nome", "source": "Non-Steam",
                    "url": f"steam://rungameid/{gameid}",
                    "last_played": max(0, entry.get("lastplaytime", 0)),
                    "art": _shortcut_art(shortcuts.parent / "grid", appid, entry.get("icon")),
                })
        except Exception as e:
            print(f"[AppUtili] Errore lettura giochi non-Steam: {e}")

    games.sort(key=lambda g: g["last_played"], reverse=True)
    return games


def refresh_games():
    """Ricarica la libreria in un thread a parte e ridisegna l'overlay."""
    def _worker():
        global _games
        try:
            games = load_games()
        except Exception as e:
            print(f"[AppUtili] Errore lettura libreria giochi: {e}")
            games = []
        with _games_lock:
            _games = games
        _overlay_queue.put("redraw")
    threading.Thread(target=_worker, daemon=True).start()


def get_games():
    with _games_lock:
        return _games


def _relative_day(timestamp):
    """Quanto tempo fa, a giorni: "oggi", "ieri", "3 giorni fa", "2 mesi fa"..."""
    days = (datetime.date.today() - datetime.date.fromtimestamp(timestamp)).days
    if days <= 0:
        return "oggi"
    if days == 1:
        return "ieri"
    if days < 7:
        return f"{days} giorni fa"
    if days < 30:
        weeks = days // 7
        return "una settimana fa" if weeks == 1 else f"{weeks} settimane fa"
    if days < 365:
        months = days // 30
        return "un mese fa" if months == 1 else f"{months} mesi fa"
    years = days // 365
    return "un anno fa" if years == 1 else f"{years} anni fa"


def _relative_time(timestamp):
    return f"giocato {_relative_day(timestamp)}" if timestamp else "mai giocato"


# ---------------------------------------------------------------------------
# VOLUME, BATTERIA DEL PAD, ALIMENTAZIONE
# ---------------------------------------------------------------------------

VOLUME_STEP = 0.05


def _endpoint_volume():
    # si rilegge ogni volta: l'uscita predefinita puo' cambiare
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume():
    """(livello 0..1, muto) dell'uscita predefinita, None se non leggibile."""
    if not VOLUME_SUPPORT:
        return None
    try:
        endpoint = _endpoint_volume()
        return endpoint.GetMasterVolumeLevelScalar(), bool(endpoint.GetMute())
    except Exception:
        return None


def change_volume(step):
    """Alza/abbassa di VOLUME_STEP (step = +1 / -1), a multipli esatti."""
    if not VOLUME_SUPPORT:
        return
    try:
        endpoint = _endpoint_volume()
        level = round(endpoint.GetMasterVolumeLevelScalar() / VOLUME_STEP) + step
        endpoint.SetMasterVolumeLevelScalar(min(1.0, max(0.0, level * VOLUME_STEP)), None)
        if step > 0 and endpoint.GetMute():
            endpoint.SetMute(False, None)
    except Exception as e:
        print(f"[AppUtili] Errore volume: {e}")


def toggle_mute():
    if not VOLUME_SUPPORT:
        return
    try:
        endpoint = _endpoint_volume()
        endpoint.SetMute(not endpoint.GetMute(), None)
    except Exception as e:
        print(f"[AppUtili] Errore volume: {e}")


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


BATTERY_TYPES_WITHOUT_LEVEL = {0x00, 0x01, 0xFF}  # scollegato, via cavo, sconosciuto
_xinput = None


def pad_battery_level():
    """Livello 0..3 (vuota..piena) del primo pad wireless XInput, o None."""
    global _xinput
    try:
        if _xinput is None:
            _xinput = ctypes.WinDLL("xinput1_4")
        for user_index in range(4):
            info = XINPUT_BATTERY_INFORMATION()
            if (_xinput.XInputGetBatteryInformation(user_index, 0, ctypes.byref(info)) == 0
                    and info.BatteryType not in BATTERY_TYPES_WITHOUT_LEVEL):
                return info.BatteryLevel
    except Exception:
        pass
    return None


def power_sleep():
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def power_restart():
    subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=CREATE_NO_WINDOW)


def power_off():
    subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=CREATE_NO_WINDOW)


# ---------------------------------------------------------------------------
# SPOTIFY
# ---------------------------------------------------------------------------
#
# Brani ascoltati di recente e playlist dell'utente, letti con la Web API di
# Spotify, e avvio della riproduzione. Serve un'app creata dall'utente su
# developer.spotify.com (in "development mode" funziona solo se il suo
# proprietario ha Premium). Il login usa il flusso Authorization Code + PKCE,
# pensato per le app desktop: basta il Client ID, nessun segreto.
#
# Il Client ID sta in config.json e i token in spotify_token.json: dopo il
# primo collegamento l'access token si rinnova da solo con il refresh token,
# anche dopo un riavvio del PC.

SPOTIFY_ACCOUNTS = "https://accounts.spotify.com"
SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_DASHBOARD = "https://developer.spotify.com/dashboard"
SPOTIFY_PORT = 8765
# Spotify non accetta "localhost", solo l'indirizzo di loopback esplicito
SPOTIFY_REDIRECT_URI = f"http://127.0.0.1:{SPOTIFY_PORT}/callback"
SPOTIFY_SCOPES = ("user-read-recently-played playlist-read-private playlist-read-collaborative "
                  "user-read-playback-state user-modify-playback-state")
SPOTIFY_TOKEN_FILE = CONFIG_DIR / "spotify_token.json"
SPOTIFY_CACHE_DIR = CONFIG_DIR / "spotify_cache"
SPOTIFY_CLIENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPOTIFY_LOGIN_TIMEOUT = 300  # secondi per completare il login nel browser
SPOTIFY_RECENT_MAX = 20
SPOTIFY_PLAYLISTS_MAX = 200
SPOTIFY_DEVICE_WAIT = 12  # secondi di attesa dell'app desktop appena aperta

SPOTIFY_PAGE = """<!doctype html><html lang="it"><meta charset="utf-8">
<title>AppUtili</title><body style="background:#202020;color:#fff;font:16px 'Segoe UI',sans-serif;
display:grid;place-items:center;height:100vh;margin:0"><div style="text-align:center">
<h2>{title}</h2><p style="color:#aaa">{text}</p></div></body></html>"""

_spotify_token_lock = threading.Lock()
_spotify_connecting = threading.Event()
_spotify_state_lock = threading.Lock()
# None = mai caricato, "no_client_id", "not_connected", "error: ..." oppure
# {"recent": [...], "playlists": [...]}
_spotify_state = None
_spotify_status = None  # (testo, errore?) mostrato sotto la griglia


def spotify_client_id():
    with config_lock:
        return config.get("spotify_client_id", "")


def set_spotify_client_id(client_id):
    with config_lock:
        config["spotify_client_id"] = client_id
        save_config(config)


def _spotify_load_token():
    try:
        with open(SPOTIFY_TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _spotify_save_token(token):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SPOTIFY_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f)


def spotify_connected():
    token = _spotify_load_token()
    return bool(spotify_client_id() and token and token.get("refresh_token"))


def _spotify_token_request(data, previous=None):
    """POST /api/token. Ritorna il token da salvare: se Spotify non manda un
    nuovo refresh token si tiene il precedente."""
    request = urllib.request.Request(
        f"{SPOTIFY_ACCOUNTS}/api/token", data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read())
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or (previous or {}).get("refresh_token"),
        "expires_at": time.time() + payload.get("expires_in", 3600) - 60,
    }


def _spotify_access_token(force_refresh=False):
    """Access token valido, rinnovato se scaduto. None se non collegato."""
    with _spotify_token_lock:
        token = _spotify_load_token()
        if not token or not token.get("refresh_token"):
            return None
        if force_refresh or time.time() >= token.get("expires_at", 0):
            try:
                token = _spotify_token_request({
                    "grant_type": "refresh_token",
                    "refresh_token": token["refresh_token"],
                    "client_id": spotify_client_id(),
                }, previous=token)
            except urllib.error.HTTPError as e:
                if e.code in (400, 401):
                    # accesso revocato o Client ID cambiato: va ricollegato
                    SPOTIFY_TOKEN_FILE.unlink(missing_ok=True)
                    return None
                raise
            _spotify_save_token(token)
        return token["access_token"]


def _spotify_api(method, path, params=None, body=None):
    """Chiamata alla Web API. Ritorna (stato HTTP, JSON o None); 401 se non
    collegato."""
    for attempt in range(2):
        access = _spotify_access_token(force_refresh=attempt == 1)
        if access is None:
            return 401, None
        url = SPOTIFY_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {access}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                try:
                    return response.status, json.loads(raw) if raw else None
                except ValueError:
                    return response.status, None  # alcuni comandi rispondono con testo
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                continue  # token rifiutato: si rinnova e si riprova una volta
            try:
                return e.code, json.loads(e.read())
            except ValueError:
                return e.code, None
    return 401, None


def spotify_connect():
    """Collega l'account: apre il login nel browser e aspetta la risposta su
    127.0.0.1. Gira in un thread a parte."""
    if spotify_client_id() and not _spotify_connecting.is_set():
        threading.Thread(target=_spotify_connect_worker, daemon=True).start()


def _spotify_connect_worker():
    _spotify_connecting.set()
    try:
        client_id = spotify_client_id()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        result = {}

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                result.update({k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()})
                ok = result.get("state") == state and "code" in result
                page = SPOTIFY_PAGE.format(
                    title="Spotify collegato ad AppUtili" if ok else "Collegamento non riuscito",
                    text="Puoi chiudere questa scheda." if ok else "Riprova dal menu di AppUtili.")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page.encode("utf-8"))

            def log_message(self, *args):
                pass  # niente log sulla console

        server = http.server.HTTPServer(("127.0.0.1", SPOTIFY_PORT), CallbackHandler)
        server.timeout = 1
        webbrowser.open(f"{SPOTIFY_ACCOUNTS}/authorize?" + urllib.parse.urlencode({
            "client_id": client_id, "response_type": "code",
            "redirect_uri": SPOTIFY_REDIRECT_URI, "scope": SPOTIFY_SCOPES, "state": state,
            "code_challenge_method": "S256", "code_challenge": challenge,
        }))
        deadline = time.monotonic() + SPOTIFY_LOGIN_TIMEOUT
        try:
            while "state" not in result and "error" not in result and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if result.get("state") != state or "code" not in result:
            notify("⚠ Spotify: collegamento annullato")
            return
        _spotify_save_token(_spotify_token_request({
            "grant_type": "authorization_code", "code": result["code"],
            "redirect_uri": SPOTIFY_REDIRECT_URI, "client_id": client_id,
            "code_verifier": verifier,
        }))
        notify("🎵 Spotify collegato")
        refresh_spotify()
    except OSError as e:
        print(f"[AppUtili] Errore collegamento Spotify: {e}")
        notify("⚠ Spotify: collegamento non riuscito")
    finally:
        _spotify_connecting.clear()
        refresh_tray_icon()


def spotify_disconnect(icon=None, item=None):
    SPOTIFY_TOKEN_FILE.unlink(missing_ok=True)
    global _spotify_state
    with _spotify_state_lock:
        _spotify_state = None
    refresh_tray_icon()


def _spotify_image(images):
    """URL della piu' piccola tra le immagini >= 300 px (o della piu' grande)."""
    if not images:
        return None
    images = sorted(images, key=lambda i: i.get("width") or 0)
    for image in images:
        if (image.get("width") or 0) >= 300:
            return image["url"]
    return images[-1]["url"]


def load_spotify():
    """Brani recenti e playlist, o una stringa che descrive perche' mancano."""
    if not spotify_client_id():
        return "no_client_id"
    if not spotify_connected():
        return "not_connected"
    status, data = _spotify_api("GET", "/me/player/recently-played", {"limit": 50})
    if status == 401:
        return "not_connected"
    if status != 200:
        return f"error: Spotify ha risposto {status}"

    recent, seen = [], set()
    for entry in (data or {}).get("items", []):
        track = entry.get("track") or {}
        uri = track.get("uri")
        if not uri or uri in seen:
            continue
        seen.add(uri)
        # Si riparte da quel brano dentro la playlist da cui veniva o, se non
        # c'e', dentro il suo album. Non con {"uris": [brano]}: Spotify risponde
        # 204 ma poi la riproduzione si ferma subito (verificato sul campo).
        context = entry.get("context") or {}
        album_uri = (track.get("album") or {}).get("uri")
        album_play = {"context_uri": album_uri, "offset": {"uri": uri}} if album_uri else None
        if context.get("type") in ("playlist", "album"):
            play = {"context_uri": context["uri"], "offset": {"uri": uri}}
        else:
            play = album_play or {"uris": [uri]}
        try:
            played = datetime.datetime.fromisoformat(entry["played_at"].replace("Z", "+00:00"))
            when = f" · {_relative_day(played.timestamp())}"
        except (KeyError, ValueError):
            when = ""
        recent.append({
            "name": track.get("name") or "Senza titolo",
            "subtitle": ", ".join(a.get("name", "") for a in track.get("artists", [])) + when,
            "image_url": _spotify_image((track.get("album") or {}).get("images")),
            "play": play,
            # se la playlist non lo contiene piu' (o non e' accessibile) si usa l'album
            "fallback": album_play if album_play != play else None,
        })
        if len(recent) == SPOTIFY_RECENT_MAX:
            break

    playlists, params = [], {"limit": 50, "offset": 0}
    while len(playlists) < SPOTIFY_PLAYLISTS_MAX:
        status, page = _spotify_api("GET", "/me/playlists", params)
        if status != 200 or not page:
            break
        for playlist in page.get("items", []):
            if not playlist:
                continue
            # nel 2026 il campo "tracks" e' diventato "items": si accettano entrambi
            count = (playlist.get("items") or playlist.get("tracks") or {}).get("total")
            owner = (playlist.get("owner") or {}).get("display_name")
            parts = [f"{count} brani" if count is not None else None,
                     f"di {owner}" if owner else None]
            playlists.append({
                "name": playlist.get("name") or "Senza nome",
                "subtitle": " · ".join(p for p in parts if p),
                "image_url": _spotify_image(playlist.get("images")),
                "play": {"context_uri": playlist["uri"]},
            })
        if not page.get("next"):
            break
        params["offset"] += params["limit"]
    return {"recent": recent, "playlists": playlists}


def _fetch_images(items, cache_dir):
    """Scarica le immagini ("image_url") in cache_dir (una volta sola) e le
    collega agli elementi con "image_path"; ridisegna man mano che arrivano.
    La usano Spotify (copertine) e Discord (icone dei server)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for item in items:
        url = item.get("image_url")
        if not url:
            continue
        path = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
        if path.is_file():
            item["image_path"] = str(path)
        else:
            jobs.append((item, url, path))

    def fetch(job):
        item, url, path = job
        try:
            # il CDN di Discord rifiuta lo user agent predefinito di urllib
            request = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
            with urllib.request.urlopen(request, timeout=10) as response:
                path.write_bytes(response.read())
            item["image_path"] = str(path)
        except OSError:
            pass

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for done, _ in enumerate(pool.map(fetch, jobs), 1):
            if done % 6 == 0:
                _overlay_queue.put("redraw")
    _overlay_queue.put("redraw")


def refresh_spotify():
    """Ricarica recenti e playlist in un thread a parte e ridisegna."""
    def _worker():
        global _spotify_state
        try:
            state = load_spotify()
        except Exception as e:
            print(f"[AppUtili] Errore lettura Spotify: {e}")
            state = "error: Spotify non raggiungibile"
        with _spotify_state_lock:
            _spotify_state = state
        if isinstance(state, dict):
            _spotify_read_player()
        _overlay_queue.put("redraw")
        if isinstance(state, dict):
            _fetch_images(state["recent"] + state["playlists"], SPOTIFY_CACHE_DIR)
    threading.Thread(target=_worker, daemon=True).start()


def get_spotify_state():
    with _spotify_state_lock:
        return _spotify_state


def _set_spotify_status(text, error=False):
    global _spotify_status
    _spotify_status = (text, error) if text else None
    _overlay_queue.put("redraw")


def _spotify_pick_device():
    """Id di un dispositivo Spotify disponibile, preferendo i computer."""
    status, data = _spotify_api("GET", "/me/player/devices")
    devices = [d for d in (data or {}).get("devices", []) if not d.get("is_restricted")] \
        if status == 200 else []
    devices.sort(key=lambda d: d.get("type") != "Computer")
    return devices[0]["id"] if devices else None


_spotify_shuffle = None  # riproduzione casuale attiva? None = non ancora letto


def _spotify_read_player():
    """Rilegge lo stato dello shuffle: puo' essere cambiato dall'app di Spotify."""
    global _spotify_shuffle
    try:
        status, player = _spotify_api("GET", "/me/player")
    except OSError:
        return
    if status == 200 and player:
        _spotify_shuffle = bool(player.get("shuffle_state"))


def spotify_toggle_shuffle():
    if spotify_connected():
        threading.Thread(target=_spotify_shuffle_worker, daemon=True).start()


def _spotify_shuffle_worker():
    global _spotify_shuffle
    _spotify_read_player()
    enable = not _spotify_shuffle
    try:
        status, _ = _spotify_api("PUT", "/me/player/shuffle",
                                 {"state": "true" if enable else "false"})
    except OSError:
        status = 0
    if status in (200, 202, 204):
        _spotify_shuffle = enable
        _set_spotify_status("Riproduzione casuale attiva" if enable
                            else "Riproduzione casuale disattivata")
    elif status == 404:
        _set_spotify_status("Nessun dispositivo Spotify attivo", error=True)
    else:
        _set_spotify_status(f"Spotify non ha cambiato la riproduzione casuale ({status or 'rete'})",
                            error=True)


def spotify_play(item):
    threading.Thread(target=_spotify_play_worker, args=(item,), daemon=True).start()


def _spotify_play_worker(item):
    _set_spotify_status(f"Avvio di {item['name']}…")
    body, params = item["play"], None
    try:
        status, payload = _spotify_api("PUT", "/me/player/play", body=body)
        if status == 404:
            # nessun dispositivo attivo: se ce n'e' uno fermo si usa quello,
            # altrimenti si apre l'app desktop e si aspetta che compaia
            device = _spotify_pick_device()
            if device is None:
                os.startfile("spotify:")
                deadline = time.monotonic() + SPOTIFY_DEVICE_WAIT
                while device is None and time.monotonic() < deadline:
                    time.sleep(1)
                    device = _spotify_pick_device()
            if device is not None:
                params = {"device_id": device}
                status, payload = _spotify_api("PUT", "/me/player/play", params, body)
        if status in (400, 403, 404) and item.get("fallback"):
            # la playlist non e' utilizzabile (brano rimosso, playlist di
            # Spotify non accessibile alle app personali): si usa l'album
            status, payload = _spotify_api("PUT", "/me/player/play", params, item["fallback"])
    except OSError as e:
        print(f"[AppUtili] Errore riproduzione Spotify: {e}")
        status, payload = 0, None

    if status in (200, 202, 204):
        _set_spotify_status(f"In riproduzione: {item['name']}")
        media_refresh()
        return
    reason = ((payload or {}).get("error") or {}).get("reason")
    if reason == "PREMIUM_REQUIRED":
        text = "Serve Spotify Premium per avviare la riproduzione"
    elif status == 404:
        text = "Nessun dispositivo Spotify disponibile: apri Spotify e riprova"
    else:
        text = f"Spotify non ha avviato la riproduzione ({status or 'rete'})"
    _set_spotify_status(text, error=True)


# ---------------------------------------------------------------------------
# DISCORD
# ---------------------------------------------------------------------------
#
# Canali vocali, muto e silenzia tramite l'RPC locale del client desktop di
# Discord: una named pipe (\\?\pipe\discord-ipc-N) su cui viaggiano frame
# <opcode, lunghezza, JSON>. Serve un'app creata dall'utente su
# discord.com/developers: per le app non approvate da Discord l'RPC funziona
# solo per il proprietario (e i tester), che qui e' proprio l'utente.
#
# Il primo collegamento passa da AUTHORIZE (popup dentro Discord) e dallo
# scambio del codice con Client ID + Client Secret; poi basta il token
# salvato in discord_token.json, rinnovato da solo.
#
# Discord riporta le impostazioni voce cambiate via RPC a com'erano quando
# l'app si scollega: la connessione resta quindi aperta finche' AppUtili gira.
# La condivisione schermo non ha un comando RPC: si invia la scorciatoia
# "Attiva/disattiva condivisione schermo" configurata in Discord.

DISCORD_API = "https://discord.com/api"
DISCORD_DASHBOARD = "https://discord.com/developers/applications"
# Non c'e' nessun server in ascolto: Discord lo confronta solo con quello
# registrato nell'app quando si scambia il codice di autorizzazione
DISCORD_REDIRECT_URI = "http://127.0.0.1"
DISCORD_SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write", "identify"]
DISCORD_TOKEN_FILE = CONFIG_DIR / "discord_token.json"
DISCORD_RECENT_FILE = CONFIG_DIR / "discord_recent.json"
DISCORD_CACHE_DIR = CONFIG_DIR / "discord_cache"
DISCORD_CLIENT_ID_PATTERN = re.compile(r"^\d{17,20}$")
DISCORD_RECENT_MAX = 15
DISCORD_RETRY = 5  # secondi tra un tentativo di connessione e l'altro
DISCORD_TIMEOUT = 5  # secondi di attesa di una risposta
DISCORD_JOIN_TIMEOUT = 15  # entrare in un canale puo' richiedere di piu'
DISCORD_STATUS_SECONDS = 5  # durata degli avvisi nella barra in basso
DISCORD_AUTHORIZE_TIMEOUT = 120  # secondi per accettare il popup in Discord
DISCORD_BATCH = 50  # richieste inviate insieme quando si leggono tutti i canali
DISCORD_VOICE_TYPES = (2, 13)  # canale vocale, canale stage
DISCORD_VOICE_STATE_EVENTS = ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE")
DISCORD_SPEAKING_EVENTS = ("SPEAKING_START", "SPEAKING_STOP")  # solo per il canale in cui si e'
DISCORD_CDN = "https://cdn.discordapp.com"


class DiscordError(Exception):
    pass


_discord_kernel32 = ctypes.WinDLL("kernel32") if os.name == "nt" else None
if _discord_kernel32 is not None:
    _discord_kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

_discord_thread = None
_discord_stop = threading.Event()
_discord_wake = threading.Event()  # interrompe l'attesa tra due tentativi
_discord_reset = threading.Event()  # chiude la connessione (credenziali cambiate)
_discord_connected = threading.Event()  # pipe aperta e handshake fatto
_discord_ready = threading.Event()  # autenticato
_discord_connecting = threading.Event()  # collegamento dell'account in corso
_discord_loading = threading.Event()
_discord_outbox = queue.Queue()  # (payload, Future) da inviare
_discord_pending = {}  # nonce -> Future, solo dal thread della pipe
_discord_token_lock = threading.Lock()
_discord_current_lock = threading.Lock()
_discord_lock = threading.Lock()
# "no_client_id", "not_running" (Discord chiuso), "not_connected", "error: ..." o "ready"
_discord_state = None
_discord_data = {"guilds": None, "channels": {}, "current": None, "mute": False, "deaf": False,
                 "user": None}  # user: l'account collegato, come le persone nei canali
_discord_speaking = set()  # id di chi sta parlando nel canale attuale
_discord_avatars = {}  # url dell'avatar -> file scaricato in DISCORD_CACHE_DIR
_discord_watched = None  # canale di cui si seguono entrate, uscite e chi parla
_discord_status = None  # (testo, errore?, scadenza) mostrato nella barra in basso


def discord_client_id():
    with config_lock:
        return config.get("discord_client_id", "")


def discord_client_secret():
    with config_lock:
        return config.get("discord_client_secret", "")


def discord_golive_hotkey():
    with config_lock:
        return config.get("discord_golive_hotkey") or DEFAULT_CONFIG["discord_golive_hotkey"]


def set_discord_settings(client_id, client_secret, hotkey):
    with config_lock:
        changed = (config.get("discord_client_id"), config.get("discord_client_secret")) \
            != (client_id, client_secret)
        config["discord_client_id"] = client_id
        config["discord_client_secret"] = client_secret
        config["discord_golive_hotkey"] = hotkey
        save_config(config)
    if changed:
        # altra app: il token vecchio non vale piu' e l'handshake va rifatto
        DISCORD_TOKEN_FILE.unlink(missing_ok=True)
        _discord_reset.set()
        _discord_wake.set()


def _discord_load_token():
    try:
        with open(DISCORD_TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _discord_save_token(token):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(DISCORD_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f)


def discord_connected():
    return _discord_ready.is_set()


def _discord_token_request(data, previous=None):
    """POST /oauth2/token con le credenziali dell'app. Ritorna il token da salvare."""
    data = dict(data, client_id=discord_client_id(), client_secret=discord_client_secret())
    request = urllib.request.Request(
        f"{DISCORD_API}/oauth2/token", data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": APP_NAME})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read())
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or (previous or {}).get("refresh_token"),
        "expires_at": time.time() + payload.get("expires_in", 604800) - 3600,
    }


def _discord_access_token(force_refresh=False):
    """Access token valido, rinnovato se scaduto. None se non collegato."""
    with _discord_token_lock:
        token = _discord_load_token()
        if not token or not token.get("refresh_token"):
            return None
        if force_refresh or time.time() >= token.get("expires_at", 0):
            try:
                token = _discord_token_request({"grant_type": "refresh_token",
                                                "refresh_token": token["refresh_token"]},
                                               previous=token)
            except urllib.error.HTTPError as e:
                if e.code in (400, 401):
                    # accesso revocato o credenziali cambiate: va ricollegato
                    DISCORD_TOKEN_FILE.unlink(missing_ok=True)
                    return None
                raise
            _discord_save_token(token)
        return token["access_token"]


# --- la pipe ---------------------------------------------------------------

def _discord_open_pipe():
    for n in range(10):  # Discord usa il primo numero libero (piu' client aperti)
        try:
            return open(rf"\\?\pipe\discord-ipc-{n}", "r+b", buffering=0)
        except OSError:
            continue
    return None


def _discord_write(pipe, op, payload):
    data = json.dumps(payload).encode()
    pipe.write(struct.pack("<II", op, len(data)) + data)


def _discord_read_exact(pipe, size):
    data = b""
    while len(data) < size:
        chunk = pipe.read(size - len(data))
        if not chunk:
            raise OSError("pipe di Discord chiusa")
        data += chunk
    return data


def _discord_read(pipe):
    op, length = struct.unpack("<II", _discord_read_exact(pipe, 8))
    return op, json.loads(_discord_read_exact(pipe, length))


def _discord_available(handle):
    available = wintypes.DWORD()
    if not _discord_kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
        raise OSError("pipe di Discord chiusa")
    return available.value


def _discord_pump(pipe):
    """Invia le richieste in coda e smista risposte ed eventi. Un solo thread
    per entrambe le direzioni: su una pipe sincrona Windows serializza le
    operazioni, e una lettura bloccante fermerebbe anche le scritture."""
    handle = msvcrt.get_osfhandle(pipe.fileno())
    while not _discord_stop.is_set() and not _discord_reset.is_set():
        busy = False
        while True:
            try:
                payload, future = _discord_outbox.get_nowait()
            except queue.Empty:
                break
            _discord_pending[payload["nonce"]] = future
            _discord_write(pipe, 1, payload)
            busy = True
        while _discord_available(handle):
            op, message = _discord_read(pipe)
            busy = True
            if op == 2:  # CLOSE
                raise OSError(message.get("message", "Discord ha chiuso la connessione"))
            if op == 3:  # PING
                _discord_write(pipe, 4, message)
                continue
            _discord_dispatch(message)
        if not busy:
            time.sleep(0.01)


def _discord_dispatch(message):
    future = _discord_pending.pop(message.get("nonce"), None) if message.get("nonce") else None
    data = message.get("data") or {}
    if future is not None:
        if message.get("evt") == "ERROR":
            future.set_exception(DiscordError(data.get("message") or "errore di Discord"))
        else:
            future.set_result(message.get("data"))
    elif message.get("cmd") == "DISPATCH":
        _discord_event(message.get("evt"), data)


def _discord_send(cmd, args=None, evt=None):
    if not _discord_connected.is_set():
        raise DiscordError("Discord non è aperto")
    payload = {"cmd": cmd, "args": args or {}, "nonce": secrets.token_hex(8)}
    if evt:
        payload["evt"] = evt
    future = concurrent.futures.Future()
    _discord_outbox.put((payload, future))
    return future


def _discord_call(cmd, args=None, evt=None, timeout=DISCORD_TIMEOUT):
    """Comando RPC; ritorna il campo "data" della risposta."""
    try:
        return _discord_send(cmd, args, evt).result(timeout)
    except concurrent.futures.TimeoutError:
        raise DiscordError("Discord non risponde") from None


def _discord_call_many(calls):
    """Piu' comandi insieme, a gruppi; None per quelli falliti."""
    results = []
    for start in range(0, len(calls), DISCORD_BATCH):
        futures = [_discord_send(cmd, args) for cmd, args in calls[start:start + DISCORD_BATCH]]
        for future in futures:
            try:
                results.append(future.result(DISCORD_TIMEOUT))
            except (DiscordError, concurrent.futures.TimeoutError):
                results.append(None)
    return results


def _discord_fail_pending():
    error = DiscordError("Discord si è scollegato")
    for future in _discord_pending.values():
        future.set_exception(error)
    _discord_pending.clear()
    while True:
        try:
            _, future = _discord_outbox.get_nowait()
        except queue.Empty:
            break
        future.set_exception(error)


def _discord_loop():
    global _discord_watched
    while not _discord_stop.is_set():
        _discord_reset.clear()
        client_id = discord_client_id()
        pipe = _discord_open_pipe() if client_id and discord_client_secret() else None
        if not client_id or not discord_client_secret():
            _set_discord_state("no_client_id")
        elif pipe is None:
            _set_discord_state("not_running")
        else:
            try:
                _discord_write(pipe, 0, {"v": 1, "client_id": client_id})
                op, message = _discord_read(pipe)
                if op == 2:
                    _set_discord_state(f"error: Discord ha rifiutato l'app "
                                       f"({message.get('message', 'Client ID errato?')})")
                else:
                    _discord_watched = None
                    _discord_connected.set()
                    threading.Thread(target=_discord_session, daemon=True).start()
                    _discord_pump(pipe)
            except (OSError, ValueError, struct.error) as e:
                print(f"[AppUtili] Connessione a Discord interrotta: {e}")
            finally:
                _discord_connected.clear()
                _discord_ready.clear()
                _discord_fail_pending()
                pipe.close()
            if _discord_state == "ready":
                _set_discord_state("not_running")
            if _discord_reset.is_set():
                continue  # riconnessione immediata con le nuove credenziali
        _discord_wake.wait(DISCORD_RETRY)
        _discord_wake.clear()


def start_discord_thread():
    global _discord_thread
    if os.name != "nt":
        return
    _discord_thread = threading.Thread(target=_discord_loop, daemon=True)
    _discord_thread.start()


def stop_discord_thread():
    _discord_stop.set()
    _discord_wake.set()


# --- sessione ----------------------------------------------------------------

def _discord_authenticate():
    """AUTHENTICATE con il token salvato (rinnovandolo se serve). False se
    l'account non e' collegato."""
    token = _discord_access_token()
    if token is None:
        return False
    try:
        auth = _discord_call("AUTHENTICATE", {"access_token": token})
    except DiscordError:
        token = _discord_access_token(force_refresh=True)
        if token is None:
            return False
        auth = _discord_call("AUTHENTICATE", {"access_token": token})
    user = _discord_person((auth or {}).get("user"))
    with _discord_lock:
        _discord_data["user"] = user
    threading.Thread(target=_discord_fetch_avatars, args=([user],), daemon=True).start()
    for evt in ("VOICE_SETTINGS_UPDATE", "VOICE_CHANNEL_SELECT"):
        _discord_call("SUBSCRIBE", evt=evt)
    _discord_ready.set()
    _set_discord_state("ready")
    refresh_discord()
    return True


def _discord_session():
    """Appena aperta la pipe: autenticazione e prima lettura dei dati."""
    try:
        if not _discord_authenticate():
            _set_discord_state("not_connected")
    except DiscordError as e:
        _set_discord_state(f"error: {e}")
    except OSError as e:
        print(f"[AppUtili] Errore autenticazione Discord: {e}")
        _set_discord_state("error: Discord non raggiungibile")


def discord_connect():
    """Collega l'account: popup di autorizzazione dentro Discord."""
    if discord_client_id() and not _discord_connecting.is_set():
        threading.Thread(target=_discord_connect_worker, daemon=True).start()


def _discord_connect_worker():
    _discord_connecting.set()
    refresh_tray_icon()
    try:
        deadline = time.monotonic() + 10
        while (_discord_reset.is_set() or not _discord_connected.is_set()) \
                and time.monotonic() < deadline:
            time.sleep(0.2)
        if not _discord_connected.is_set():
            notify("Discord non è aperto: aprilo e riprova a collegarlo")
            return
        try:
            os.startfile("discord://")  # porta in primo piano Discord, dove compare il popup
        except OSError:
            pass
        data = _discord_call("AUTHORIZE", {"client_id": discord_client_id(),
                                           "scopes": DISCORD_SCOPES},
                             timeout=DISCORD_AUTHORIZE_TIMEOUT)
        token = _discord_token_request({"grant_type": "authorization_code", "code": data["code"],
                                        "redirect_uri": DISCORD_REDIRECT_URI})
        _discord_save_token(token)
        if _discord_authenticate():
            notify("Discord collegato ad AppUtili")
    except DiscordError as e:
        notify(f"Collegamento a Discord non riuscito: {e}")
    except urllib.error.HTTPError as e:
        reason = "Client Secret errato" if e.code == 401 else \
            f"controlla il Redirect {DISCORD_REDIRECT_URI} nell'app" if e.code == 400 else \
            f"errore {e.code}"
        notify(f"Collegamento a Discord non riuscito: {reason}")
    except OSError as e:
        notify(f"Collegamento a Discord non riuscito: {e}")
    finally:
        _discord_connecting.clear()
        refresh_tray_icon()
        _overlay_queue.put("redraw")


def discord_disconnect(icon=None, item=None):
    DISCORD_TOKEN_FILE.unlink(missing_ok=True)
    _discord_reset.set()  # la nuova connessione trova l'account scollegato
    _discord_wake.set()


def _set_discord_state(state):
    global _discord_state
    with _discord_lock:
        changed = _discord_state != state
        _discord_state = state
        if state != "ready":
            _discord_data.update(guilds=None, channels={}, current=None)
    if changed:
        refresh_tray_icon()
        _overlay_queue.put("redraw")


def _set_discord_status(text, error=False, seconds=DISCORD_STATUS_SECONDS):
    """Avviso nella barra in basso al posto di "Connesso in ...", per pochi
    secondi: solo cose che non si vedono gia' (entrata in corso, errori)."""
    global _discord_status
    _discord_status = (text, error, time.monotonic() + seconds) if text else None
    _overlay_queue.put("redraw")
    if text:  # ridisegno alla scadenza, per toglierlo
        threading.Timer(seconds, _overlay_queue.put, args=("redraw",)).start()


def get_discord_status():
    """(testo, errore?) dell'avviso ancora valido, o None."""
    status = _discord_status
    return status[:2] if status and time.monotonic() < status[2] else None


# --- dati ------------------------------------------------------------------

def _discord_clean(name):
    """Toglie emoji e simboli decorativi dai nomi (il font dell'overlay non li
    ha) e riporta le lettere "stilizzate" a quelle normali."""
    name = unicodedata.normalize("NFKC", name or "")

    def keep(ch):
        code, category = ord(ch), unicodedata.category(ch)
        if category in ("So", "Sk", "Cs", "Co", "Cn") or ch in "\u200d\ufe0f":
            return False
        if 0x0E00 <= code < 0x2000 and category[0] in "PS":  # es. ༻ tibetano
            return False
        # oltre la punteggiatura generale restano solo lettere e cifre (⊹ ⋆ ⊱ no)
        return code < 0x2070 or category[0] in "LN"
    text = "".join(ch for ch in name if keep(ch))
    return re.sub(r"\s+", " ", text).strip(" |・-–—:•·.") or name.strip()


def _discord_initials(name):
    """Sigla del server senza icona, come la mostra Discord."""
    words = _discord_clean(name).split()
    return "".join(w[0] for w in words[:3]).upper() or "?"


def _discord_person(user, nick=None, voice=None):
    """Persona in un canale vocale (o l'account collegato): nome, sigla,
    avatar e muto/silenziato."""
    user, voice = user or {}, voice or {}
    uid = str(user.get("id") or "")
    name = _discord_clean(nick or user.get("global_name") or user.get("username") or "?")
    if user.get("avatar"):
        url = f"{DISCORD_CDN}/avatars/{uid}/{user['avatar']}.png?size=64"
    else:  # avatar predefinito di Discord, scelto dall'id
        url = f"{DISCORD_CDN}/embed/avatars/{(int(uid) >> 22) % 6 if uid.isdigit() else 0}.png"
    return {"id": uid, "name": name, "initials": _discord_initials(name)[:2], "image_url": url,
            "mute": bool(voice.get("self_mute") or voice.get("mute")),
            "deaf": bool(voice.get("self_deaf") or voice.get("deaf"))}


def _discord_members(voice_states):
    return [_discord_person(s.get("user"), s.get("nick"), s.get("voice_state"))
            for s in voice_states or []]


def _discord_fetch_avatars(people):
    """Scarica gli avatar che mancano (poi restano in cache su disco)."""
    items = {p["image_url"]: {"image_url": p["image_url"]}
             for p in people if p and p["image_url"] not in _discord_avatars}
    _fetch_images(list(items.values()), DISCORD_CACHE_DIR)
    for url, item in items.items():
        if "image_path" in item:
            _discord_avatars[url] = item["image_path"]
    _overlay_queue.put("redraw")


def _discord_channel(data):
    return {"id": data["id"], "name": _discord_clean(data.get("name")) or "Canale vocale",
            "guild_id": data.get("guild_id"), "position": data.get("position", 0),
            "members": _discord_members(data.get("voice_states"))}


def _discord_load_recent():
    try:
        with open(DISCORD_RECENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _discord_add_recent(channel, guild_name):
    recent = [r for r in _discord_load_recent() if r.get("channel_id") != channel["id"]]
    recent.insert(0, {"channel_id": channel["id"], "guild_id": channel["guild_id"],
                      "name": channel["name"], "guild_name": guild_name, "time": time.time()})
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(DISCORD_RECENT_FILE, "w", encoding="utf-8") as f:
        json.dump(recent[:DISCORD_RECENT_MAX], f)


def _discord_load_current():
    """Canale in cui si e' adesso (con chi c'e' dentro) e stato di muto/silenzia."""
    global _discord_watched
    with _discord_current_lock:
        selected = _discord_call("GET_SELECTED_VOICE_CHANNEL")
        settings = _discord_call("GET_VOICE_SETTINGS") or {}
        current = _discord_channel(selected) if selected else None
        guild_name = None
        if current:
            with _discord_lock:
                guild = (_discord_data["guilds"] or {}).get(current["guild_id"])
            if guild:
                guild_name = guild["name"]
            elif current["guild_id"]:
                guild_name = _discord_clean((_discord_call("GET_GUILD", {"guild_id": current["guild_id"]})
                                             or {}).get("name"))
            else:
                guild_name = "Chiamata privata"
            current["guild_name"] = guild_name
        with _discord_lock:
            _discord_data.update(current=current, mute=bool(settings.get("mute")),
                                 deaf=bool(settings.get("deaf")))
            if current and current["id"] in _discord_data["channels"]:
                _discord_data["channels"][current["id"]]["members"] = current["members"]
            # uscendo (o cambiando canale) quello di prima non deve piu' elencarci:
            # i suoi presenti si rileggono solo alla prossima apertura dell'overlay
            previous = _discord_data["channels"].get(_discord_watched)
            if previous is not None and _discord_watched != (current or {}).get("id"):
                me = (_discord_data["user"] or {}).get("id")
                previous["members"] = [p for p in previous["members"] if p["id"] != me]
        # entrate e uscite dal canale attuale aggiornano la lista dei presenti,
        # gli eventi "speaking" dicono chi sta parlando
        new_id = current["id"] if current else None
        if new_id != _discord_watched:
            for evt in DISCORD_VOICE_STATE_EVENTS + DISCORD_SPEAKING_EVENTS:
                if _discord_watched:
                    _discord_send("UNSUBSCRIBE", {"channel_id": _discord_watched}, evt)
                if new_id:
                    _discord_send("SUBSCRIBE", {"channel_id": new_id}, evt)
            _discord_watched = new_id
            with _discord_lock:
                _discord_speaking.clear()
        if current:
            _discord_add_recent(current, guild_name)
            threading.Thread(target=_discord_fetch_avatars, args=(current["members"],),
                             daemon=True).start()
    _overlay_queue.put("redraw")


def _discord_update_current():
    try:
        _discord_load_current()
    except DiscordError as e:
        print(f"[AppUtili] Errore lettura canale Discord: {e}")


def _discord_event(evt, data):
    """Eventi a cui ci si e' iscritti; arrivano sul thread della pipe, quindi
    tutto cio' che chiede altro a Discord va in un thread a parte."""
    if evt == "VOICE_SETTINGS_UPDATE":
        with _discord_lock:
            _discord_data.update(mute=bool(data.get("mute")), deaf=bool(data.get("deaf")))
        _overlay_queue.put("redraw")
    elif evt in DISCORD_SPEAKING_EVENTS:
        user_id = str(data.get("user_id") or "")
        with _discord_lock:
            if evt == "SPEAKING_START":
                _discord_speaking.add(user_id)
            else:
                _discord_speaking.discard(user_id)
        _overlay_queue.put("redraw")
    elif evt == "VOICE_CHANNEL_SELECT" or evt in DISCORD_VOICE_STATE_EVENTS:
        threading.Thread(target=_discord_update_current, daemon=True).start()


def load_discord():
    """Server, canali vocali con i presenti, canale attuale."""
    guilds = (_discord_call("GET_GUILDS") or {}).get("guilds", [])
    guild_map = {}
    for guild in guilds:
        url = guild.get("icon_url")
        guild_map[guild["id"]] = {
            "id": guild["id"], "name": _discord_clean(guild.get("name")) or "Server",
            "initials": _discord_initials(guild.get("name")),
            # webp: Pillow potrebbe non leggerlo, il CDN da' anche il png
            "image_url": url.replace(".webp", ".png") if url else None,
        }
    lists = _discord_call_many([("GET_CHANNELS", {"guild_id": g["id"]}) for g in guilds])
    voice_ids = [c["id"] for result in lists if result
                 for c in result.get("channels", []) if c.get("type") in DISCORD_VOICE_TYPES]
    details = _discord_call_many([("GET_CHANNEL", {"channel_id": cid}) for cid in voice_ids])
    channels = {d["id"]: _discord_channel(d) for d in details if d}
    with _discord_lock:
        previous = _discord_data["guilds"] or {}
        for guild_id, guild in guild_map.items():  # le icone gia' scaricate restano
            if "image_path" in previous.get(guild_id, {}):
                guild["image_path"] = previous[guild_id]["image_path"]
        _discord_data.update(guilds=guild_map, channels=channels)
    _discord_load_current()
    _fetch_images(list(guild_map.values()), DISCORD_CACHE_DIR)
    _discord_fetch_avatars([p for c in channels.values() for p in c["members"]])


def refresh_discord():
    """Ricarica server e canali in un thread a parte (se collegati)."""
    if not _discord_ready.is_set() or _discord_loading.is_set():
        return

    def _worker():
        _discord_loading.set()
        try:
            load_discord()
        except DiscordError as e:
            print(f"[AppUtili] Errore lettura Discord: {e}")
        finally:
            _discord_loading.clear()
    threading.Thread(target=_worker, daemon=True).start()


def get_discord_state():
    """Stringa di stato (come get_spotify_state), None durante il caricamento,
    oppure un dict con "current", "mute", "deaf", "user"."""
    with _discord_lock:
        if _discord_state != "ready":
            return _discord_state
        if _discord_data["guilds"] is None:
            return None
        return {key: _discord_data[key] for key in ("current", "mute", "deaf", "user")}


def discord_speaking():
    with _discord_lock:
        return set(_discord_speaking)


def discord_recent():
    """Vista Recenti: i canali in cui si e' entrati, dal piu' recente, con
    chi c'e' dentro adesso. "guild" = il server (per l'icona)."""
    with _discord_lock:
        guilds = _discord_data["guilds"] or {}
        channels = dict(_discord_data["channels"])
        current = _discord_data["current"]
    current_id = current["id"] if current else None
    items = []
    for entry in _discord_load_recent():
        channel = channels.get(entry["channel_id"])
        if channel is None:
            if entry.get("guild_id") in guilds:
                continue  # canale eliminato
            channel = {"id": entry["channel_id"], "guild_id": entry.get("guild_id"),
                       "name": entry.get("name", "Canale"), "members": []}
        guild = guilds.get(channel["guild_id"]) or {
            "name": entry.get("guild_name") or "Chiamata privata",
            "initials": _discord_initials(entry.get("guild_name") or "?")}
        here = channel["id"] == current_id
        # nel canale attuale i presenti si aggiornano con gli eventi
        members = current["members"] if here else channel["members"]
        items.append({"id": channel["id"], "guild_id": channel["guild_id"], "name": channel["name"],
                      "guild": guild, "time": entry.get("time", 0), "members": members,
                      "here": here})
    return items


def discord_guilds():
    """Vista Server: i server con almeno un canale vocale, con quante persone
    sono in vocale. Prima i preferiti (nell'ordine in cui sono stati
    aggiunti), poi gli altri in ordine alfabetico: l'ordine scelto in
    Discord non passa dall'RPC."""
    with _discord_lock:
        guilds = _discord_data["guilds"] or {}
        channels = list(_discord_data["channels"].values())
    with config_lock:
        favorites = list(config["discord_favorites"])
    rank = {guild_id: i for i, guild_id in enumerate(favorites)}
    result = []
    for guild in sorted(guilds.values(),
                        key=lambda g: (rank.get(g["id"], len(rank)), g["name"].casefold())):
        voice = [c for c in channels if c["guild_id"] == guild["id"]]
        if voice:
            result.append({**guild, "inside": sum(len(c["members"]) for c in voice),
                           "favorite": guild["id"] in rank})
    return result


def discord_toggle_favorite(guild_id):
    """Aggiunge o toglie un server dai preferiti; True se ora e' preferito."""
    with config_lock:
        favorites = config["discord_favorites"]
        added = guild_id not in favorites
        config["discord_favorites"] = favorites + [guild_id] if added \
            else [g for g in favorites if g != guild_id]
        save_config(config)
    return added


def discord_guild_channels(guild_id):
    """Canali vocali di un server nell'ordine di Discord, con chi c'e' dentro."""
    with _discord_lock:
        channels = [c for c in _discord_data["channels"].values() if c["guild_id"] == guild_id]
        current = _discord_data["current"]
    current_id = current["id"] if current else None
    return [{**c, "here": c["id"] == current_id,
             "members": current["members"] if c["id"] == current_id else c["members"]}
            for c in sorted(channels, key=lambda c: c["position"])]


# --- azioni ----------------------------------------------------------------

def _discord_action(worker, *args):
    def run():
        try:
            worker(*args)
        except DiscordError as e:
            _set_discord_status(str(e), error=True)
    threading.Thread(target=run, daemon=True).start()


def discord_join(item):
    def worker():
        _set_discord_status(f"Entro in {item['name']}…", seconds=DISCORD_JOIN_TIMEOUT)
        try:
            _discord_call("SELECT_VOICE_CHANNEL", {"channel_id": item["id"], "force": True},
                          timeout=DISCORD_JOIN_TIMEOUT)
        finally:
            _set_discord_status(None)
        _discord_load_current()
    _discord_action(worker)


def discord_leave():
    def worker():
        _discord_call("SELECT_VOICE_CHANNEL", {"channel_id": None})
        _discord_load_current()
    _discord_action(worker)


def _discord_set_voice(key):
    with _discord_lock:
        enable = not _discord_data[key]
    settings = _discord_call("SET_VOICE_SETTINGS", {key: enable}) or {}
    with _discord_lock:
        _discord_data.update(mute=bool(settings.get("mute", _discord_data["mute"])),
                             deaf=bool(settings.get("deaf", _discord_data["deaf"])))
    _overlay_queue.put("redraw")  # l'icona nella barra diventa rossa (o torna normale)


def discord_toggle_mute():
    _discord_action(_discord_set_voice, "mute")


def discord_toggle_deaf():
    _discord_action(_discord_set_voice, "deaf")


def discord_golive():
    """Condivisione schermo: la scorciatoia impostata in Discord (non c'e' un
    comando RPC). Da chiamare dopo aver ridato il focus al gioco."""
    keyboard.send(discord_golive_hotkey())


# ---------------------------------------------------------------------------
# OVERLAY GAMEPAD
# ---------------------------------------------------------------------------
#
# Finestra tkinter senza bordi, sempre in primo piano, in alto a sinistra sul
# monitor della finestra attiva. Vive in un thread tutto suo: Tk va usato
# solo dal thread che l'ha creato, quindi il thread controller gli parla
# esclusivamente tramite _overlay_queue.
#
# Non si usa una finestra pygame perche', con una finestra SDL aperta, SDL
# scarta gli eventi del pad quando non ha il focus e le combo globali
# smetterebbero di funzionare.
#
# "Cattura" degli input: XInput li da' a chiunque li chieda, non si possono
# bloccare da un programma normale. L'overlay quindi prende il focus (molti
# giochi e Big Picture ignorano il pad quando non sono in primo piano) e,
# finche' e' aperto, le combo di AppUtili sono sospese.

_overlay_queue = queue.Queue()  # vedi _Overlay._handle per l'elenco dei comandi
_overlay_open = threading.Event()
_overlay_thread = None

# Pausa tra la restituzione del focus alla finestra precedente e l'azione:
# Alt+F4, Win+D e le scorciatoie audio devono arrivare a lei, non all'overlay
OVERLAY_ACTION_DELAY = 0.15
OVERLAY_MARGIN = 36  # px dal bordo del monitor (a 96 dpi)
OVERLAY_WIDTH = 420  # px a 96 dpi; tutte le misure del disegno sono a 96 dpi

# Palette scura in stile Windows 11
OV_BG = (32, 32, 32)
OV_SURFACE = (45, 45, 45)
OV_SELECTED = (50, 55, 63)
OV_BORDER = (62, 62, 62)
OV_TEXT = (255, 255, 255)
OV_MUTED = (165, 165, 165)
OV_SUBTLE = (115, 115, 115)
OV_ACCENT = (76, 194, 255)  # azzurro degli accenti di Windows 11 in tema scuro
# Colori dei pulsanti come sul pad Xbox
OV_PAD_COLORS = {"A": (96, 180, 70), "B": (220, 70, 70), "X": (60, 140, 235), "Y": (235, 190, 50)}

# Istanza di user32 tutta nostra: impostare argtypes/restype su
# ctypes.windll.user32 (condiviso con pystray) potrebbe rompere le sue chiamate
ov_user32 = ctypes.WinDLL("user32") if os.name == "nt" else None
kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GA_ROOT = 2
MONITOR_DEFAULTTOPRIMARY = 1
MDT_EFFECTIVE_DPI = 0
VK_MENU = 0x12
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
SW_HIDE = 0
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


if ov_user32 is not None:
    # HWND/HMONITOR sono puntatori: senza restype su Windows a 64 bit
    # verrebbero troncati a 32 bit
    ov_user32.GetForegroundWindow.restype = wintypes.HWND
    ov_user32.GetAncestor.restype = wintypes.HWND
    ov_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    ov_user32.MonitorFromWindow.restype = wintypes.HMONITOR
    ov_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    ov_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    ov_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    ov_user32.BringWindowToTop.argtypes = [wintypes.HWND]
    ov_user32.IsWindow.argtypes = [wintypes.HWND]
    ov_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    ov_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    ov_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    ov_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    ov_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT]


def _monitor_of(hwnd):
    """(area di lavoro come RECT, dpi) del monitor che contiene hwnd."""
    hmon = ov_user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTOPRIMARY)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    ov_user32.GetMonitorInfoW(hmon, ctypes.byref(info))
    dpi = 96
    try:
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        if ctypes.windll.shcore.GetDpiForMonitor(
                hmon, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
            dpi = dpi_x.value
    except Exception:
        pass
    return info.rcWork, dpi


def _force_foreground(hwnd):
    """Windows rifiuta SetForegroundWindow a chi non ha appena ricevuto input
    dall'utente (e il pad non conta). Agganciarsi alla coda di input del
    thread in primo piano lo aggira; come ultima spiaggia si simula Alt."""
    fg = ov_user32.GetForegroundWindow()
    fg_thread = ov_user32.GetWindowThreadProcessId(fg, None) if fg else 0
    our_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != our_thread:
        attached = bool(ov_user32.AttachThreadInput(our_thread, fg_thread, True))
    try:
        ov_user32.BringWindowToTop(hwnd)
        ov_user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            ov_user32.AttachThreadInput(our_thread, fg_thread, False)
    if ov_user32.GetForegroundWindow() != hwnd:
        ov_user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ov_user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        ov_user32.SetForegroundWindow(hwnd)


DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_BORDER_COLOR = 34


def _round_window_corners(hwnd):
    """Angoli arrotondati e bordo sottile disegnati da Windows 11 (su Windows
    10 la chiamata fallisce e la finestra resta squadrata)."""
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                 ctypes.c_void_p, wintypes.DWORD]
        corner = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                     ctypes.byref(corner), ctypes.sizeof(corner))
        r, g, b = OV_BORDER
        border = wintypes.DWORD(r | (g << 8) | (b << 16))  # COLORREF = 0x00BBGGRR
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR,
                                     ctypes.byref(border), ctypes.sizeof(border))
    except Exception:
        pass


TOKEN_QUERY = 0x0008
TOKEN_UI_ACCESS = 26  # classe TokenUIAccess di GetTokenInformation


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _has_uiaccess():
    """True se il processo ha UIAccess (overlay sopra i giochi a schermo intero)."""
    try:
        advapi32, k32 = ctypes.WinDLL("advapi32"), ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return False
        try:
            value, size = wintypes.DWORD(), wintypes.DWORD()
            ok = advapi32.GetTokenInformation(token, TOKEN_UI_ACCESS, ctypes.byref(value),
                                              ctypes.sizeof(value), ctypes.byref(size))
            return bool(ok and value.value)
        finally:
            k32.CloseHandle(token)
    except Exception:
        return False


def _controller_names():
    names = []
    for controller in list(_open_controllers.values()):
        try:
            names.append(getattr(controller, "name", None) or controller.as_joystick().get_name())
        except Exception:
            names.append("Controller")
    return names


# Glifi del font Segoe Fluent Icons (Segoe MDL2 Assets su Windows 10)
ICON_MONITOR = ""
ICON_GAME = ""
ICON_DESKTOP = ""
ICON_CLOSE = ""
ICON_BELL = ""
ICON_BELL_OFF = ""
ICON_HEADPHONE = ""
ICON_SPEAKER = ""
ICON_VOLUME = ""
ICON_VOLUME_LEVELS = ("", "", "", "")  # da basso ad alto
ICON_MUTE = ""
ICON_POWER = ""
ICON_BATTERY_LEVELS = ("", "", "", "")  # vuota..piena
ICON_CHECK = ""
ICON_CHEVRON_LEFT = ""
ICON_CHEVRON_RIGHT = ""
ICON_PREV = ""
ICON_NEXT = ""
ICON_PLAY = ""
ICON_PAUSE = ""
ICON_MUSIC = ""
ICON_SHUFFLE = ""

ICON_MIC = ""
ICON_MIC_OFF = ""
ICON_SCREEN_SHARE = ""
ICON_HANGUP = ""
ICON_JOIN = ""  # cornetta: entra nel canale vocale

ICON_CHEVRON_DOWN = ""
ICON_SETTINGS = "\ue713"
ICON_INFO = "\ue946"
ICON_HOME = "\ue80f"
ICON_FOLDER = "\ue8b7"
ICON_PC = "\ue770"
ICON_LIST = "\ue8fd"
ICON_KEYBOARD = "\ue765"
ICON_LINK = "\ue71b"
# frecce del D-pad (e della tastiera) nella legenda e nelle scorciatoie
PAD_ARROWS = {"LEFT": ICON_CHEVRON_LEFT, "RIGHT": ICON_CHEVRON_RIGHT,
              "UP": "\ue70e", "DOWN": ICON_CHEVRON_DOWN}

OV_DANGER = (220, 70, 70)  # pillola "Conferma?"
OV_SEGMENT_BG = (26, 26, 26)
OV_SEGMENT_ACTIVE = (68, 68, 68)

OVERLAY_TABS = ("Controlli", "Giochi", "Spotify", "Discord")
TAB_CONTROLS, TAB_GAMES, TAB_SPOTIFY, TAB_DISCORD = 0, 1, 2, 3
CONFIRM_TIMEOUT = 3.0  # secondi per la seconda pressione di A
OVERLAY_TICK_MS = 30000  # ridisegno periodico (orologio, batteria)
OVERLAY_FOCUS_CHECK_MS = 150  # controllo del primo piano: con un clic fuori si chiude
GRID_COLUMNS = 3  # griglia di copertine della tab Giochi
# Altezza minima del pannello (a 96 dpi): la decide la tab Controlli, ma
# senza la voce Monitor (nessun profilo) le altre tab non devono accorciarsi
PANEL_MIN_HEIGHT = 720
MONITOR_MAX_PILLS = 3  # oltre, la voce Monitor diventa ◀ nome ▶ + A
MONITOR_PILL_CHARS = 12
SPOTIFY_FILTERS = (("recent", "Recenti"), ("playlists", "Playlist"))  # Y per cambiare
# Y per cambiare: canali recenti (espandibili) o server sfogliati come in Discord
DISCORD_VIEWS = (("recent", "Recenti"), ("servers", "Server"))

OV_CONNECTED = OV_PAD_COLORS["A"]  # Discord: in vocale, sta parlando


_monitor_pick = None  # profilo scelto con ◀ ▶ nella voce Monitor, applicato con A


def _monitor_kind():
    """Pochi profili con nomi corti: pillole; altrimenti ◀ nome ▶."""
    profiles = monitor_profiles()
    if len(profiles) <= MONITOR_MAX_PILLS and all(len(p) <= MONITOR_PILL_CHARS for p in profiles):
        return "segmented"
    return "choice"


def _monitor_options():
    return [{"label": name, "action": _monitor_switcher_action(name),
             "active": _monitor_active_check(name)} for name in monitor_profiles()]


def _monitor_switcher_action(name):
    return lambda: switch_monitor(name)


def _monitor_active_check(name):
    return lambda: monitor_current() == name


def _monitor_pick_value():
    profiles = monitor_profiles()
    name = _monitor_pick if _monitor_pick in profiles else monitor_current()
    return name + ("  (attivo)" if name == monitor_current() else "")


def _monitor_pick_step(direction):
    global _monitor_pick
    profiles = monitor_profiles()
    current = _monitor_pick if _monitor_pick in profiles else monitor_current()
    index = profiles.index(current) if current in profiles else -1
    _monitor_pick = profiles[(index + direction) % len(profiles)]


def _monitor_pick_apply():
    name = _monitor_pick if _monitor_pick in monitor_profiles() else monitor_current()
    switch_monitor(name)


def _notifications_on():
    with config_lock:
        return config["notifications_enabled"]


def _audio_icon():
    """Chi gestisce l'uscita: logo SteelSeries = Sonar, cuffie = Windows. Un
    Path e' un logo da disegnare al posto del glifo."""
    if _audio_source == "windows":
        return ICON_HEADPHONE
    if _audio_output is None:
        return ICON_VOLUME
    if STEELSERIES_LOGO.is_file():
        return STEELSERIES_LOGO
    # senza logo: il tipo di dispositivo
    name = " ".join(_audio_output).lower()
    if any(word in name for word in ("cuffie", "headphone", "headset", "auricolar")):
        return ICON_HEADPHONE
    if any(word in name for word in ("altoparlant", "speaker")):
        return ICON_SPEAKER
    return ICON_MONITOR  # uscite HDMI/DisplayPort di monitor e TV


def _audio_value():
    if _audio_switching:
        return "…"
    if _audio_output is None:
        return "Nessuna uscita" if _audio_source == "windows" else "Ctrl+è / Ctrl++"
    return _audio_output[1]


def _audio_desc():
    if _audio_source == "windows":
        return "Sinistra / destra: cambia l'uscita predefinita di Windows (SteelSeries GG è chiuso)."
    return "Sinistra / destra: cambia il dispositivo di uscita in SteelSeries Sonar."


def _volume_icon():
    volume = get_volume()
    if volume is None:
        return ICON_VOLUME
    level, muted = volume
    if muted:
        return ICON_MUTE
    return ICON_VOLUME_LEVELS[min(3, int(level * 4))]


# Voci della tab Controlli, nell'ordine in cui compaiono. "kind":
#   action (predefinito): A esegue "action" dopo aver chiuso l'overlay
#   segmented: scelte affiancate ("options"); sinistra/destra spostano il
#              cursore, A esegue l'opzione. Con "confirm" serve una seconda A
#   choice: sinistra/destra (e A) chiamano step(-1 / +1), l'overlay resta aperto
#   slider: come choice ma ripete tenendo premuto; A chiama "toggle"
#   toggle: A o sinistra/destra chiamano toggle(), l'overlay resta aperto
#   media:  la scheda dei controlli multimediali, sempre l'ultima voce
# "icon", "value" e "active" possono essere funzioni: si rileggono a ogni disegno.
OVERLAY_ITEMS = [
    {"section": "Schermo", "kind": _monitor_kind, "icon": ICON_MONITOR, "label": "Monitor",
     "visible": lambda: bool(monitor_profiles()),
     "desc": "Profili salvati con MonitorSwitcher. A carica quello scelto.",
     "options": _monitor_options,
     "value": _monitor_pick_value, "step": _monitor_pick_step, "apply": _monitor_pick_apply},
    {"section": "Steam", "kind": "segmented", "icon": ICON_GAME, "label": "Big Picture",
     "desc": "Apre o chiude Steam Big Picture.",
     "options": [
         {"label": "Apri", "action": open_bigpicture},
         {"label": "Esci", "action": close_bigpicture},
     ]},
    {"section": "Audio", "kind": "choice", "icon": _audio_icon, "label": "Uscita",
     "value": _audio_value, "step": switch_audio_output, "desc": _audio_desc},
    {"kind": "slider", "icon": _volume_icon, "label": "Volume",
     "value": get_volume, "step": change_volume, "toggle": toggle_mute,
     "desc": "Sinistra / destra: volume (tieni premuto per andare veloce). A: muto."},
    {"section": "Finestre", "icon": ICON_DESKTOP, "label": "Mostra desktop",
     "desc": "Riduce a icona tutte le finestre (Win+D).",
     "action": show_desktop},
    {"icon": ICON_CLOSE, "label": "Chiudi finestra", "hint": "Alt+F4",
     "desc": "Chiude la finestra che era in primo piano prima dell'overlay.",
     "action": close_window},
    {"section": "Sistema", "kind": "segmented", "icon": ICON_POWER, "label": "Energia",
     "confirm": True,
     "desc": "Sospendi, riavvia o spegni il PC. Chiede conferma con una seconda A.",
     "options": [
         {"label": "Sospendi", "action": power_sleep},
         {"label": "Riavvia", "action": power_restart},
         {"label": "Spegni", "action": power_off},
     ]},
    {"kind": "media",
     "desc": "Sinistra / destra: cambia brano. A: play / pausa. "
             "LB, RB e X funzionano anche dalle altre voci."},
]

def _tab_visible(name):
    with config_lock:
        return name not in config["hidden_tabs"]


def visible_tabs():
    """Indici delle tab non nascoste dalle impostazioni."""
    with config_lock:
        hidden = list(config["hidden_tabs"])
    return [i for i, name in enumerate(OVERLAY_TABS) if name not in hidden]


def toggle_tab(name):
    """Mostra o nasconde una tab; l'ultima visibile resta."""
    with config_lock:
        hidden = list(config["hidden_tabs"])
        if name in hidden:
            hidden.remove(name)
        elif len(OVERLAY_TABS) - len(hidden) > 1:
            hidden.append(name)
        config["hidden_tabs"] = hidden
        save_config(config)


def _tab_item(name, icon, section=None):
    item = {"kind": "toggle", "icon": icon, "label": name,
            "value": lambda: _tab_visible(name), "toggle": lambda: toggle_tab(name),
            "desc": f"Mostra o nasconde la tab {name}. Almeno una resta visibile."}
    if section:
        item["section"] = section
    return item


def _spotify_service_options():
    options = []
    if spotify_connected():
        options.append({"label": "Scollega", "action": spotify_disconnect, "stay": True})
    elif spotify_client_id():
        options.append({"label": "Collega", "action": spotify_connect})  # login nel browser
    options.append({"label": "Configura", "action": lambda: _overlay_queue.put("spotify_setup")})
    return options


def _discord_service_options():
    options = []
    if _discord_load_token() is not None:
        options.append({"label": "Scollega", "action": discord_disconnect, "stay": True})
    elif discord_client_id() and discord_client_secret():
        options.append({"label": "Collega", "action": discord_connect})  # popup in Discord
    options.append({"label": "Configura", "action": lambda: _overlay_queue.put("discord_setup")})
    return options


def _monitor_role_value(role):
    return lambda: monitor_role(role) or "Nessuno"


def _monitor_role_step(role):
    def step(direction):
        choices = monitor_profiles() + [""]
        current = monitor_role(role)
        index = choices.index(current) if current in choices else len(choices) - 1
        set_monitor_role(role, choices[(index + direction) % len(choices)])
    return step


def _monitor_switcher_value():
    exe = monitor_switcher_exe()
    if exe is None:
        return "ricerca…"
    return "trovato" if exe else "non trovato"


def _home_mode_option(label, mode):
    return {"label": label, "action": lambda: set_home_mode(mode)(None, None), "stay": True,
            "active": lambda: home_mode_checked(mode)(None)}


def _controller_on():
    with config_lock:
        return config["controller_enabled"]


# Voci della pagina Impostazioni (Select tenuto premuto o ingranaggio), stessi
# "kind" della tab Controlli. In piu': "visible", "options" come funzione,
# "stay" (l'azione non chiude l'overlay) e "apply" (A sulle voci choice).
SETTINGS_ITEMS = [
    _tab_item("Controlli", ICON_LIST, "Tab"),
    _tab_item("Giochi", ICON_GAME),
    _tab_item("Spotify", ICON_MUSIC),
    _tab_item("Discord", ICON_HEADPHONE),
    {"section": "Servizi", "kind": "segmented", "icon": ICON_MUSIC,
     "label": lambda: f"Spotify · {_spotify_menu_status(None)}",
     "options": _spotify_service_options,
     "desc": "Collega l'account Spotify (login nel browser) o cambia il Client ID."},
    {"kind": "segmented", "icon": ICON_HEADPHONE,
     "label": lambda: f"Discord · {_discord_menu_status(None)}",
     "options": _discord_service_options,
     "desc": "Collega l'account Discord (popup dentro Discord) o cambia le credenziali."},
    {"section": "Monitor", "kind": "choice", "icon": ICON_PC, "label": "Scrivania",
     "visible": lambda: bool(monitor_profiles()),
     "value": _monitor_role_value("desk"), "step": _monitor_role_step("desk"),
     "desc": "Profilo caricato da Ctrl+Alt+Giù e da Back+LB+Giù sul pad."},
    {"kind": "choice", "icon": ICON_MONITOR, "label": "TV",
     "visible": lambda: bool(monitor_profiles()),
     "value": _monitor_role_value("tv"), "step": _monitor_role_step("tv"),
     "desc": "Profilo caricato da Ctrl+Alt+Su e da Back+LB+Su sul pad."},
    {"kind": "segmented", "icon": ICON_FOLDER,
     "label": lambda: f"MonitorSwitcher · {_monitor_switcher_value()}",
     "visible": lambda: bool(monitor_profiles()),
     "options": [{"label": "Scegli…", "stay": True,
                  "action": lambda: _overlay_queue.put("choose_monitor_switcher")}],
     "desc": "Dove sta MonitorSwitcher.exe (è portatile: si cerca da solo, o lo indichi tu)."},
    {"section": "Generale", "kind": "toggle", "label": "Notifiche",
     "icon": lambda: ICON_BELL if _notifications_on() else ICON_BELL_OFF,
     "value": _notifications_on, "toggle": lambda: toggle_notifications(None, None),
     "desc": "Avvisi di Windows quando cambi profilo monitor."},
    {"kind": "toggle", "icon": ICON_POWER, "label": "Avvio con Windows",
     "value": lambda: _startup_enabled, "toggle": lambda: toggle_startup(None, None),
     "desc": "Avvia AppUtili all'accesso, già da amministratore e senza richiesta UAC."},
    {"kind": "toggle", "icon": ICON_GAME, "label": "Combo del pad",
     "value": _controller_on, "toggle": lambda: toggle_controller_enabled(None, None),
     "desc": "Combinazioni con Back (monitor, audio, desktop, Alt+F4). L'overlay resta attivo."},
    {"kind": "segmented", "icon": ICON_HOME, "label": "Tasto Home",
     "options": [_home_mode_option("Windows", "windows"), _home_mode_option("Alt+Tab", "alttab"),
                 _home_mode_option("Nessuno", "none")],
     "desc": "Cosa fa il tasto multimediale Home della tastiera (Nessuno = apre il browser)."},
    {"section": "Altro", "icon": ICON_INFO, "label": "Informazioni", "stay": True,
     "action": lambda: _overlay_queue.put("info"),
     "desc": "Percorsi, stato dei servizi, scorciatoie e come configurare tutto."},
    {"kind": "segmented", "icon": ICON_CLOSE, "label": "Esci da AppUtili", "confirm": True,
     "options": [{"label": "Esci", "action": lambda: on_quit(_tray_icon, None)}],
     "desc": "Chiude AppUtili (anche il tray). Chiede conferma con una seconda A."},
]


OVERLAY_FOOTERS = {
    TAB_CONTROLS: [(("A",), "Conferma"), (("B",), "Chiudi"),
                   (("LB", "RB"), "Brano"), (("X",), "Play/pausa")],
    TAB_GAMES: [(("A",), "Avvia"), (("B",), "Chiudi"), (("LT", "RT"), "Cambia tab")],
    TAB_SPOTIFY: [(("A",), "Riproduci"), (("B",), "Chiudi"),
                  (("LB", "RB"), "Brano"), (("X",), "Pausa")],
    # negli stati vuoti; collegata, la legenda segue la selezione (_discord_footer)
    TAB_DISCORD: [(("A",), "Conferma"), (("B",), "Chiudi"), (("LT", "RT"), "Cambia tab"),
                  (("L3",), "Muto")],
}
SETTINGS_FOOTER = [(("A",), "Conferma"), (("B",), "Indietro"), (("LEFT", "RIGHT"), "Cambia")]
INFO_FOOTER = [(("A",), "Seleziona"), (("B",), "Indietro"), (("LEFT", "RIGHT"), "Chiudi / apri")]
INFO_KEY_SEPARATORS = ("+", "/", "·", "tieni")  # tra i tasti delle scorciatoie, come testo


def _kind(item):
    return _value(item.get("kind", "action"))


def _options(item):
    return _value(item["options"])


def _value(v):
    return v() if callable(v) else v


# --- primitive di disegno (Pillow) -----------------------------------------

_SUPERSAMPLE = 4  # le forme arrotondate si disegnano 4x e si riducono: bordi morbidi


@functools.lru_cache(maxsize=None)
def _ov_font(size, weight="Regular"):
    # Segoe UI Variable (Windows 11) con il peso scelto; su Windows 10 Segoe UI
    try:
        font = ImageFont.truetype("SegUIVar.ttf", size)
        font.set_variation_by_name(weight)
        return font
    except Exception:
        return ImageFont.truetype("segoeui.ttf" if weight == "Regular" else "segoeuib.ttf", size)


@functools.lru_cache(maxsize=None)
def _ov_icon_font(size):
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


@functools.lru_cache(maxsize=256)
def _ov_mask(width, height, radius):
    k = _SUPERSAMPLE
    mask = Image.new("L", (width * k, height * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width * k - 1, height * k - 1),
                                           radius * k, fill=255)
    return mask.resize((width, height), Image.LANCZOS)


@functools.lru_cache(maxsize=8)
def _ov_logo_mask(path, size):
    """Forma (canale alfa) di un logo PNG, da colorare come i glifi del font.
    None se il file non si legge."""
    try:
        with Image.open(path) as logo:
            return logo.convert("RGBA").getchannel("A").resize((size, size), Image.LANCZOS)
    except OSError:
        return None


def _ov_round(img, box, radius, fill):
    x0, y0, x1, y1 = (round(v) for v in box)
    if x1 > x0 and y1 > y0:
        img.paste(fill, (x0, y0, x1, y1), _ov_mask(x1 - x0, y1 - y0, round(radius)))


def _ov_fit(text, font, width):
    """Accorcia il testo con "…" finche' non sta nella larghezza."""
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _ov_wrap(text, font, width, max_lines=2):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if font.getlength(candidate) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ov_fit(lines[-1] + " …", font, width)
    return lines


def _ov_crop_to(image, width, height):
    """Ritaglia al centro con le proporzioni richieste e ridimensiona."""
    w, h = image.size
    if w * height > h * width:
        new_w = h * width // height
        image = image.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    else:
        new_h = w * height // width
        image = image.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
    return image.resize((width, height), Image.LANCZOS)


@functools.lru_cache(maxsize=160)
def _game_cover(capsule, hero, logo, header, name, width, height):
    """Copertina verticale di un gioco. Se manca, ne compone una: hero con il
    logo sopra, oppure header su una scheda scura, oppure solo il nome."""
    try:
        if capsule:
            return _ov_crop_to(Image.open(capsule).convert("RGB"), width, height)
        if hero:
            cover = _ov_crop_to(Image.open(hero).convert("RGB"), width, height)
            if logo:
                mark = Image.open(logo).convert("RGBA")
                mark.thumbnail((int(width * 0.85), int(height * 0.4)), Image.LANCZOS)
                cover.paste(mark, ((width - mark.width) // 2, (height - mark.height) // 2), mark)
            return cover
        cover = Image.new("RGB", (width, height), OV_SURFACE)
        if header:
            banner = Image.open(header).convert("RGB")
            banner.thumbnail((width, height), Image.LANCZOS)
            cover.paste(banner, (0, (height - banner.height) // 2))
            return cover
    except Exception:
        cover = Image.new("RGB", (width, height), OV_SURFACE)
    draw = ImageDraw.Draw(cover)
    font = _ov_font(max(10, width // 9), "Semibold Text")
    lines = _ov_wrap(name, font, width - width // 6, max_lines=4)
    line_h = font.size * 1.25
    top = (height - line_h * len(lines)) / 2 + line_h / 2
    for i, line in enumerate(lines):
        draw.text((width / 2, top + i * line_h), line, font=font, fill=OV_TEXT, anchor="mm")
    return cover


@functools.lru_cache(maxsize=160)
def _spotify_cover(path, width, height):
    try:
        return _ov_crop_to(Image.open(path).convert("RGB"), width, height)
    except Exception:
        return _spotify_placeholder(width, height)


@functools.lru_cache(maxsize=8)
def _spotify_placeholder(width, height):
    """Casella con la nota musicale, finche' la copertina non e' scaricata."""
    cover = Image.new("RGB", (width, height), OV_SURFACE)
    ImageDraw.Draw(cover).text((width / 2, height / 2), ICON_MUSIC,
                               font=_ov_icon_font(max(12, width // 4)), fill=OV_SUBTLE,
                               anchor="mm")
    return cover


@functools.lru_cache(maxsize=64)
def _discord_placeholder(initials, size):
    """Server senza icona: la sigla su fondo grigio, come in Discord."""
    image = Image.new("RGB", (size, size), OV_SELECTED)
    font = _ov_font(max(9, round(size * (0.36 if len(initials) < 3 else 0.28))), "Semibold Text")
    ImageDraw.Draw(image).text((size / 2, size / 2), initials, font=font, fill=OV_TEXT,
                               anchor="mm")
    return image


def _discord_icon(item, size):
    path = item.get("image_path")
    return _spotify_cover(path, size, size) if path \
        else _discord_placeholder(item.get("initials", "?"), size)


def _discord_avatar(person, size):
    """Avatar di una persona (la sigla finche' non e' scaricato)."""
    path = _discord_avatars.get(person.get("image_url"))
    return _spotify_cover(path, size, size) if path \
        else _discord_placeholder(person.get("initials", "?"), size)


class _CoverGrid:
    """Griglia di copertine a GRID_COLUMNS colonne, divisa in sezioni con un
    titolo opzionale: tiene l'elemento selezionato e lo scorrimento, e gestisce
    la navigazione col pad. La usano le tab Giochi e Spotify."""

    def __init__(self):
        self.index = 0  # elemento selezionato, contando tutte le sezioni di fila
        self.scroll = 0  # px gia' scorsi

    def reset(self):
        self.index = 0
        self.scroll = 0

    @staticmethod
    def _rows(sizes):
        """Righe della griglia come liste di indici: ogni sezione va a capo."""
        rows, start = [], 0
        for size in sizes:
            for first in range(0, size, GRID_COLUMNS):
                rows.append(list(range(start + first, start + min(size, first + GRID_COLUMNS))))
            start += size
        return rows

    def move(self, command, sizes):
        """Sposta la selezione ("left"/"right"/"up"/"down"); True se e' cambiata.
        Su/giu' vanno alla riga vicina, sulla colonna piu' vicina disponibile,
        anche passando da una sezione all'altra."""
        rows = self._rows(sizes)
        if not rows:
            return False
        self.index = min(self.index, rows[-1][-1])
        row = next(r for r, items in enumerate(rows) if self.index in items)
        if command in ("left", "right"):
            target = self.index + (-1 if command == "left" else 1)
        else:
            new_row = row + (-1 if command == "up" else 1)
            if not 0 <= new_row < len(rows):
                return False
            column = rows[row].index(self.index)
            target = rows[new_row][min(column, len(rows[new_row]) - 1)]
        if 0 <= target <= rows[-1][-1] and target != self.index:
            self.index = target
            return True
        return False

    def render(self, overlay, img, top, height, sections, aspect, cover_for, on_click, hitboxes):
        """Disegna la griglia in img, nella fascia [top, top + height).
        sections: lista di (titolo o None, elementi); cover_for(elemento, w, h)
        restituisce la copertina. aspect = altezza / larghezza delle copertine."""
        S = overlay._S
        width, pad = img.width, S(14)
        gap, border = S(8), S(3)
        inner = border + S(1)  # margine perche' il contorno non venga tagliato
        title_h = S(28)
        card_w = (width - 2 * pad - (GRID_COLUMNS - 1) * gap) // GRID_COLUMNS
        card_h = round(card_w * aspect)

        # posizioni nel contenuto (prima dello scorrimento)
        titles, cards, y, index = [], [], 0, 0
        for title, items in sections:
            if title:
                titles.append((title, y))
                y += title_h
            for i, item in enumerate(items):
                if i and i % GRID_COLUMNS == 0:
                    y += card_h + gap
                cards.append((index, pad + (i % GRID_COLUMNS) * (card_w + gap), y, item))
                index += 1
            y += card_h + gap
        if not cards:
            return
        self.index = min(self.index, len(cards) - 1)

        # scorrimento: la copertina selezionata deve essere tutta visibile, e se
        # e' nella prima riga di una sezione anche il titolo sopra di lei
        selected_y = cards[self.index][2]
        wanted_top = selected_y - (title_h if any(ty == selected_y - title_h for _, ty in titles) else 0)
        visible = height - 2 * inner
        if wanted_top < self.scroll:
            self.scroll = wanted_top
        elif selected_y + card_h > self.scroll + visible:
            self.scroll = selected_y + card_h - visible
        self.scroll = max(0, self.scroll)

        # si disegna su un'immagine a parte, cosi' le righe parziali vengono
        # tagliate ai bordi della fascia
        grid = Image.new("RGB", (width, height), OV_BG)
        draw = ImageDraw.Draw(grid)
        offset = inner - self.scroll
        title_font = _ov_font(S(11), "Semibold Text")
        for title, ty in titles:
            draw.text((pad + S(4), ty + offset + title_h // 2), title.upper(),
                      font=title_font, fill=OV_MUTED, anchor="lm")
        mask = _ov_mask(card_w, card_h, S(6))
        for i, x, cy, item in cards:
            gy = cy + offset
            if gy + card_h < 0 or gy > height:
                continue
            if i == self.index:
                _ov_round(grid, (x - border, gy - border, x + card_w + border,
                                 gy + card_h + border), S(9), OV_ACCENT)
            grid.paste(cover_for(item, card_w, card_h), (x, gy), mask)
            hitboxes.append((x, top + max(0, gy), x + card_w, top + min(height, gy + card_h),
                             lambda i=i: on_click(i)))
        img.paste(grid, (0, top))


def _run_overlay_action(action):
    time.sleep(OVERLAY_ACTION_DELAY)
    try:
        action()
    except Exception as e:
        print(f"[AppUtili] Errore azione overlay: {e}")


class _Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        bg = "#%02x%02x%02x" % OV_BG
        self.root.configure(bg=bg)

        # Tutto il pannello e' un'unica immagine disegnata con Pillow
        self.label = tk.Label(self.root, bd=0, highlightthickness=0, bg=bg)
        self.label.pack()

        # Con UIAccess le finestre "sempre in primo piano" stanno in uno strato
        # a parte: se la cornice di Tk nasce gia' topmost, Tk non riesce a
        # metterci dentro il contenuto (vedi _repair_frame). La cornice si crea
        # quindi una volta, fuori schermo e normale; topmost arriva in show().
        self.frame_broken = False
        self.position = (0, 0)
        self.root.geometry("+-32000+-32000")
        self.root.deiconify()
        self.root.update_idletasks()
        self.root.withdraw()
        # errori nei clic, nei tasti e nei timer di Tk: in errori.log
        self.root.report_callback_exception = lambda kind, value, tb: log_error(
            "Errore overlay:\n" + "".join(traceback.format_exception(kind, value, tb)))
        self.label.bind("<Button-1>", self._click)
        self.label.bind("<Button-3>", self._right_click)
        self.label.bind("<Motion>", self._hover)
        self.root.bind("<MouseWheel>", self._wheel)

        # con il focus, anche la tastiera funziona
        for key, command in (("<Up>", "up"), ("<Down>", "down"), ("<Left>", "left"),
                             ("<Right>", "right"), ("<Return>", "a"), ("<Escape>", "b"),
                             ("<Prior>", "prev_tab"), ("<Next>", "next_tab"),
                             ("<Tab>", "next_tab"), ("<Shift-Tab>", "prev_tab")):
            self.root.bind(key, lambda e, c=command: self._handle(c))

        self.tab = TAB_CONTROLS
        self.index = 0  # voce selezionata nella tab Controlli
        self.segment = 0  # opzione sotto il cursore nelle righe "segmented"
        self.pending = None  # (voce, opzione, scadenza) in attesa della seconda A
        self.games_grid = _CoverGrid()
        self.spotify_filter = 0  # indice in SPOTIFY_FILTERS
        self.spotify_index = [0] * len(SPOTIFY_FILTERS)  # riga selezionata per filtro
        self.spotify_scroll = 0  # px gia' scorsi nella lista
        self.discord_view = 0  # indice in DISCORD_VIEWS
        self.discord_recent = 0  # riga selezionata nella vista Recenti
        self.discord_expanded = set()  # canali aperti (chi c'e' dentro) nella vista Recenti
        self.discord_guild = None  # server scelto nella vista Server (None = quello attuale)
        self.discord_column = "rail"  # vista Server: "rail" (server) o "channels"
        self.discord_channel = 0  # canale selezionato nella vista Server
        self.discord_scroll = 0  # px scorsi nella lista
        self.discord_rail_scroll = 0  # px scorsi nella colonna dei server
        self.discord_focus = "list"  # "bar" = pulsanti della barra in basso
        self.discord_command = 0  # pulsante selezionato nella barra
        self.page = None  # None = tab, "settings" o "info"
        self.saved_controls = (0, 0)  # voce della tab Controlli, ripresa uscendo dalle impostazioni
        self.settings_scroll = 0
        self.info_index = 0  # voce selezionata tra intestazioni e righe con un'azione
        self.info_open = "keys"  # pannello aperto nella pagina Info (uno alla volta)
        self.info_scroll = 0
        self.setup_windows = {}  # finestre di configurazione aperte (spotify, discord)
        self.panel_height = None  # altezza della tab Controlli, usata da tutte le tab
        self.prev_hwnd = None
        self.scale = 1.0
        self.photo = None
        self.hitboxes = []  # (x0, y0, x1, y1, funzione) per i clic del mouse
        self._cover_cache = (None, 0, None)
        self._tick_id = None
        self._focus_id = None
        self.had_focus = False  # l'overlay ha avuto il primo piano da quando e' aperto
        self.root.after(15, self._poll)

    def _hwnd(self):
        return ov_user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

    def _poll(self):
        try:
            while True:
                command = _overlay_queue.get_nowait()
                if command == "quit":
                    _overlay_open.clear()
                    self.root.destroy()
                    return
                self._handle(command)
        except queue.Empty:
            pass
        except Exception:
            log_error("Errore overlay:\n" + traceback.format_exc())
        self.root.after(15, self._poll)

    def _handle(self, command):
        if command == "toggle":
            if _overlay_open.is_set():
                self.hide()
            else:
                self.show()
        elif command == "spotify_setup":  # dal menu del tray, anche a overlay chiuso
            self._open_spotify_setup()
        elif command == "discord_setup":
            self._open_discord_setup()
        elif not _overlay_open.is_set():
            return  # comandi arrivati mentre era chiuso: vecchi, si scartano
        elif command == "redraw":
            self._redraw()
        elif command == "settings":  # Select tenuto premuto, o l'ingranaggio
            self._open_page(None if self.page else "settings")
        elif command == "info":
            self._open_page("info")
        elif command == "choose_monitor_switcher":
            self._choose_monitor_switcher()
        elif command == "b":
            if self.page == "info":
                self._open_page("settings")
            elif self.page:
                self._open_page(None)
            else:
                self.hide()
        elif command in ("prev_tab", "next_tab"):
            if not self.page:
                self._cycle_tab(1 if command == "next_tab" else -1)
        elif command in MEDIA_KEYS:  # LB / RB / X: brani, da qualsiasi tab
            media_command(command)
        elif command == "shuffle":  # R3: riproduzione casuale di Spotify, da qualsiasi tab
            spotify_toggle_shuffle()
        elif command == "discord_mute":  # L3: muto su Discord, da qualsiasi tab
            discord_toggle_mute()
        elif command in ("wheel_up", "wheel_down") and (self.page or self.tab != TAB_DISCORD):
            self._handle(command.removeprefix("wheel_"))  # rotella = su/giu'
        elif self.page == "settings":
            self._controls_command(command)
        elif self.page == "info":
            self._info_command(command)
        elif self.tab == TAB_GAMES:
            self._games_command(command)
        elif self.tab == TAB_SPOTIFY:
            self._spotify_command(command)
        elif self.tab == TAB_DISCORD:
            self._discord_command(command)
        else:
            self._controls_command(command)

    def _set_tab(self, tab):
        self.tab = tab % len(OVERLAY_TABS)
        self.pending = None
        self._redraw()

    def _cycle_tab(self, delta):
        """LT / RT: tab precedente / successiva, saltando quelle nascoste."""
        tabs = visible_tabs()
        position = tabs.index(self.tab) if self.tab in tabs else 0
        self._set_tab(tabs[(position + delta) % len(tabs)])

    def _open_page(self, page):
        """Impostazioni, Info o di nuovo le tab (page None)."""
        if self.page is None and page:
            self.saved_controls = (self.index, self.segment)
        self.page = page
        self.pending = None
        if page == "settings":
            self.settings_scroll = 0
            self._select(0)
            self._ensure_selectable()
        elif page == "info":
            self.info_index = self.info_scroll = 0
            self.info_open = "keys"
        else:
            self.index, self.segment = self.saved_controls
            if self.tab not in visible_tabs():  # nascosta dalle impostazioni
                self.tab = visible_tabs()[0]
        self._redraw()

    def _choose_monitor_switcher(self):
        from tkinter import filedialog
        current = monitor_switcher_exe()
        path = filedialog.askopenfilename(
            parent=self.root, title="Dov'è MonitorSwitcher.exe?",
            initialdir=str(Path(current).parent) if current else str(Path.home()),
            filetypes=[("MonitorSwitcher", MONITOR_SWITCHER_NAME), ("Programmi", "*.exe")])
        if path:
            set_monitor_switcher(str(Path(path)))
        if _overlay_open.is_set():
            _force_foreground(self._hwnd())  # la finestra di scelta si e' presa il focus
            self._redraw()

    # --- tab Controlli (e pagina Impostazioni, stesse voci) ----------------

    def _items(self):
        return SETTINGS_ITEMS if self.page == "settings" else OVERLAY_ITEMS

    def _controls_command(self, command):
        # "*_repeat" = sinistra/destra tenuti premuti: contano solo per gli slider
        repeat = command.endswith("_repeat")
        command = command.removesuffix("_repeat")
        if command in ("up", "down"):
            self._move(-1 if command == "up" else 1)
        elif command in ("left", "right"):
            if not repeat or _kind(self._items()[self.index]) == "slider":
                self._step(-1 if command == "left" else 1)
        elif command == "a":
            self._confirm()

    def _selectable(self, index):
        item = self._items()[index]
        if "visible" in item and not item["visible"]():
            return False
        # la scheda media si seleziona solo se c'e' qualcosa in riproduzione
        return _kind(item) != "media" or get_media_state() is not None

    def _ensure_selectable(self):
        """Se la voce selezionata e' sparita (musica ferma, profili tolti) si
        passa alla piu' vicina sopra, o sotto."""
        items = self._items()
        self.index = min(self.index, len(items) - 1)
        if self._selectable(self.index):
            return
        for distance in range(1, len(items)):
            for index in (self.index - distance, self.index + distance):
                if 0 <= index < len(items) and self._selectable(index):
                    self._select(index)
                    return

    def _select(self, index):
        self.index = index
        self.pending = None
        item = self._items()[index]
        if _kind(item) == "segmented":
            # il cursore parte dall'opzione attiva (es. il profilo monitor in uso)
            active = [i for i, o in enumerate(_options(item)) if o.get("active") and o["active"]()]
            self.segment = active[0] if active else 0

    def _move(self, delta):
        items = self._items()
        index = self.index
        for _ in items:
            index = (index + delta) % len(items)
            if self._selectable(index):
                break
        self._select(index)
        self._redraw()

    def _step(self, direction):
        item = self._items()[self.index]
        kind = _kind(item)
        if kind == "segmented":
            self.segment = min(len(_options(item)) - 1, max(0, self.segment + direction))
            self.pending = None
        elif kind in ("choice", "slider"):
            item["step"](direction)
        elif kind == "toggle":
            item["toggle"]()
        elif kind == "media":
            media_command("next_track" if direction > 0 else "prev_track")
            return
        else:
            return
        self._redraw()

    def _confirm(self):
        item = self._items()[self.index]
        kind = _kind(item)
        if kind == "choice" and item.get("apply"):
            self._run(item["apply"])
        elif kind == "choice":
            self._step(1)
        elif kind in ("slider", "toggle"):
            item["toggle"]()
            self._redraw()
        elif kind == "media":
            media_command("play_pause")
        elif kind == "segmented":
            target = (self.index, self.segment)
            if item.get("confirm") and not (self.pending and self.pending[:2] == target
                                            and time.monotonic() < self.pending[2]):
                self.pending = (*target, time.monotonic() + CONFIRM_TIMEOUT)
                self.root.after(int(CONFIRM_TIMEOUT * 1000) + 50, self._expire_pending)
                self._redraw()
            else:
                option = _options(item)[min(self.segment, len(_options(item)) - 1)]
                self._do(option)
        else:
            self._do(item)

    def _do(self, entry):
        """Azione di una voce o di un'opzione: "stay" la esegue con l'overlay
        aperto, le altre dopo averlo chiuso (vanno alla finestra di prima)."""
        if entry.get("stay"):
            entry["action"]()
            self.pending = None
            if _overlay_open.is_set():
                self._redraw()
        else:
            self._run(entry["action"])

    def _expire_pending(self):
        if self.pending and time.monotonic() >= self.pending[2]:
            self.pending = None
            if _overlay_open.is_set():
                self._redraw()

    def _run(self, action):
        self.hide()
        threading.Thread(target=_run_overlay_action, args=(action,), daemon=True).start()

    # --- tab Giochi --------------------------------------------------------

    def _games_command(self, command):
        games = get_games() or []
        command = command.removesuffix("_repeat")  # nella griglia si ripete tutto
        if command in ("left", "right", "up", "down"):
            if self.games_grid.move(command, [len(games)]):
                self._redraw()
        elif command == "a" and games:
            self._launch(games[min(self.games_grid.index, len(games) - 1)])

    def _launch(self, game):
        self._run(lambda: os.startfile(game["url"]))

    # --- tab Spotify -------------------------------------------------------

    def _spotify_items(self, state):
        return state[SPOTIFY_FILTERS[self.spotify_filter][0]]

    def _set_spotify_filter(self, index):
        self.spotify_filter = index % len(SPOTIFY_FILTERS)
        self.spotify_scroll = 0
        self._redraw()

    def _spotify_command(self, command):
        state = get_spotify_state()
        if isinstance(state, dict):
            items = self._spotify_items(state)
            index = min(self.spotify_index[self.spotify_filter], max(0, len(items) - 1))
            if command in ("y", "left", "right"):  # cambia filtro (tenendo premuto no)
                self._set_spotify_filter(self.spotify_filter + (-1 if command == "left" else 1))
            elif command in ("up", "down") and items:
                index += -1 if command == "up" else 1
                if 0 <= index < len(items):
                    self.spotify_index[self.spotify_filter] = index
                    self._redraw()
            elif command == "a" and items:
                spotify_play(items[index])
        elif command == "a":
            if state == "no_client_id":
                self.hide()
                self._open_spotify_setup()
            elif state == "not_connected":
                self.hide()  # il login si fa nel browser
                spotify_connect()
            elif isinstance(state, str):  # errore: si riprova
                refresh_spotify()

    # --- tab Discord -------------------------------------------------------

    def _set_discord_view(self, index):
        self.discord_view = index % len(DISCORD_VIEWS)
        self.discord_scroll = 0
        self.discord_focus = "list"
        self._redraw()

    def _discord_guild_index(self, guilds, state):
        """Posizione del server scelto nella vista Server; all'inizio e' quello
        del canale in cui si e' (o il primo). -1 se non ci sono server."""
        ids = [g["id"] for g in guilds]
        if self.discord_guild not in ids:
            current = state["current"]
            self.discord_guild = current["guild_id"] if current and current["guild_id"] in ids                 else (ids[0] if ids else None)
        return ids.index(self.discord_guild) if self.discord_guild in ids else -1

    def _discord_select_guild(self, guild_id):
        if guild_id != self.discord_guild:
            self.discord_guild = guild_id
            self.discord_channel = 0
            self.discord_scroll = 0

    def _discord_join(self, channel):
        if not channel["here"]:  # nel canale in cui si e' gia', A non fa niente
            discord_join(channel)

    def _discord_commands(self, state):
        """Pulsanti della barra in basso, a destra dell'utente."""
        here = state["current"] is not None
        return [
            {"label": "Muto", "icon": ICON_MIC_OFF if state["mute"] else ICON_MIC,
             "on": state["mute"], "enabled": True, "action": discord_toggle_mute},
            {"label": "Silenzia", "icon": ICON_MUTE if state["deaf"] else ICON_HEADPHONE,
             "on": state["deaf"], "enabled": True, "action": discord_toggle_deaf},
            {"label": "Schermo", "icon": ICON_SCREEN_SHARE, "on": False, "enabled": here,
             "action": lambda: self._run(discord_golive)},  # la scorciatoia va al gioco
            {"label": "Esci", "icon": ICON_HANGUP, "on": False, "enabled": here,
             "action": discord_leave},
        ]

    def _discord_command(self, command):
        state = get_discord_state()
        if not isinstance(state, dict):
            if command != "a":
                return
            if state == "no_client_id":
                self.hide()
                self._open_discord_setup()
            elif state == "not_running":
                self._run(lambda: os.startfile("discord://"))
            elif state == "not_connected":
                self.hide()  # il popup di autorizzazione compare dentro Discord
                discord_connect()
            elif isinstance(state, str):  # errore: si riprova
                _discord_reset.set()
                _discord_wake.set()
            return
        if command == "y":  # Recenti <-> Server
            self._set_discord_view(self.discord_view + 1)
            return
        wheel = command.startswith("wheel_")  # rotella: come su/giu', ma solo nella lista
        command = command.removeprefix("wheel_")
        if self.discord_focus == "bar":
            self._discord_bar_command(command, state)
        elif DISCORD_VIEWS[self.discord_view][0] == "recent":
            self._discord_recent_command(command, wheel)
        else:
            self._discord_servers_command(command, wheel, state)

    def _discord_bar_command(self, command, state):
        buttons = self._discord_commands(state)
        if command == "up" or (command == "left" and self.discord_command == 0):
            self.discord_focus = "list"  # sinistra dal primo pulsante: si torna alla lista
        elif command in ("left", "right"):
            self.discord_command = min(len(buttons) - 1, max(
                0, self.discord_command + (-1 if command == "left" else 1)))
        elif command == "a" and buttons[self.discord_command]["enabled"]:
            buttons[self.discord_command]["action"]()
            return
        else:
            return
        self._redraw()

    def _discord_to_bar(self, wheel, first=False):
        """Giu' dall'ultima riga, o destra da una riga: si passa alla barra (la
        rotella si ferma). Da destra si parte dal primo pulsante."""
        if wheel:
            return False
        self.discord_focus = "bar"
        if first:
            self.discord_command = 0
        return True

    def _discord_recent_command(self, command, wheel):
        items = discord_recent()
        index = min(self.discord_recent, max(0, len(items) - 1))
        item = items[index] if items else None
        if command == "up" and index > 0:
            self.discord_recent = index - 1
        elif command == "down" and index + 1 < len(items):
            self.discord_recent = index + 1
        elif command == "down":
            if not self._discord_to_bar(wheel):
                return
        elif command == "right" and item and item["members"] \
                and item["id"] not in self.discord_expanded:  # chi c'e' dentro
            self.discord_expanded.add(item["id"])
        elif command == "right":  # gia' aperta (o vuota): ai pulsanti
            self._discord_to_bar(wheel, first=True)
        elif command == "left" and item:
            self.discord_expanded.discard(item["id"])
        elif command == "a" and item:
            self._discord_join(item)
            return
        else:
            return
        self._redraw()

    def _discord_servers_command(self, command, wheel, state):
        guilds = discord_guilds()
        g = self._discord_guild_index(guilds, state)
        if command == "start" and g >= 0:
            self._discord_toggle_favorite(guilds[g])
            return
        channels = discord_guild_channels(self.discord_guild) if g >= 0 else []
        if self.discord_column == "rail" or not channels:
            self.discord_column = "rail"
            if command == "up" and g > 0:
                self._discord_select_guild(guilds[g - 1]["id"])
            elif command == "down" and 0 <= g < len(guilds) - 1:
                self._discord_select_guild(guilds[g + 1]["id"])
            elif command == "down":
                if not self._discord_to_bar(wheel):
                    return
            elif command in ("right", "a") and channels:
                self.discord_column = "channels"
            else:
                return
        else:
            index = min(self.discord_channel, len(channels) - 1)
            if command == "up" and index > 0:
                self.discord_channel = index - 1
            elif command == "down" and index + 1 < len(channels):
                self.discord_channel = index + 1
            elif command == "down":
                if not self._discord_to_bar(wheel):
                    return
            elif command == "left":
                self.discord_column = "rail"
            elif command == "right":
                self._discord_to_bar(wheel, first=True)
            elif command == "a":
                self._discord_join(channels[index])
                return
            else:
                return
        self._redraw()

    def _discord_toggle_favorite(self, guild):
        """Start (o tasto destro): server in cima alla vista Server, o di nuovo
        tra gli altri. Resta selezionato."""
        self.discord_guild = guild["id"]
        discord_toggle_favorite(guild["id"])
        self._redraw()  # basta vederlo spostarsi in cima (o tornare tra gli altri)

    def _discord_footer(self):
        other = (("Y",), DISCORD_VIEWS[(self.discord_view + 1) % len(DISCORD_VIEWS)][1])
        if self.discord_focus == "bar":
            return [(("A",), "Conferma"), (("B",), "Chiudi"), (("LEFT", "RIGHT"), "Pulsanti"), other]
        if DISCORD_VIEWS[self.discord_view][0] == "recent":
            return [(("A",), "Entra"), (("B",), "Chiudi"), (("LEFT", "RIGHT"), "Chi c'è"), other]
        if self.discord_column == "rail":  # B si legge altrove: qui serve spazio per Start
            return [(("A",), "Apri"), (("LEFT", "RIGHT"), "Canali"), (("Start",), "Preferito"),
                    other]
        return [(("A",), "Entra"), (("LEFT",), "Server"), (("RIGHT",), "Pulsanti"), other]

    def _open_spotify_setup(self):
        def save(values):
            set_spotify_client_id(values["client_id"].lower())
            SPOTIFY_TOKEN_FILE.unlink(missing_ok=True)  # eventuale login con un altro Client ID
            spotify_connect()

        self._open_setup(
            "Spotify", "Collega Spotify",
            "1. Apri il dashboard degli sviluppatori di Spotify e crea un'app\n"
            "    (nome e descrizione a piacere).\n"
            "2. Come Redirect URI inserisci l'indirizzo qui sotto e, tra le API,\n"
            "    seleziona \"Web API\". Salva.\n"
            "3. Nelle impostazioni dell'app copia il Client ID e incollalo qui.\n"
            "Serve Spotify Premium sull'account che crea l'app.",
            ("Apri dashboard Spotify", SPOTIFY_DASHBOARD), SPOTIFY_REDIRECT_URI,
            [("client_id", "Client ID", spotify_client_id(), False,
              lambda v: None if SPOTIFY_CLIENT_ID_PATTERN.match(v.lower())
              else "Il Client ID è di 32 caratteri (cifre e lettere a-f).")],
            save)

    def _open_discord_setup(self):
        def check_hotkey(value):
            try:
                keyboard.parse_hotkey(value)
                return None
            except ValueError:
                return "Scorciatoia non valida (esempi: f7, ctrl+shift+s)."

        def save(values):
            set_discord_settings(values["client_id"], values["client_secret"],
                                 values["hotkey"].lower())
            if not _discord_load_token():
                discord_connect()

        self._open_setup(
            "Discord", "Collega Discord",
            "1. Apri il portale sviluppatori di Discord e crea un'applicazione\n"
            "    (nome a piacere).\n"
            "2. In OAuth2 → Redirects aggiungi l'indirizzo qui sotto. Salva.\n"
            "3. Sempre in OAuth2 copia il Client ID e il Client Secret (premi\n"
            "    \"Reset Secret\" per vederlo: Discord lo mostra una volta sola).\n"
            "4. Dopo \"Collega\" accetta il popup che compare dentro Discord.\n"
            "La scorciatoia deve essere quella di Discord → Impostazioni →\n"
            "Tasti di scelta rapida per la condivisione dello schermo.",
            ("Apri portale Discord", DISCORD_DASHBOARD), DISCORD_REDIRECT_URI,
            [("client_id", "Client ID", discord_client_id(), False,
              lambda v: None if DISCORD_CLIENT_ID_PATTERN.match(v)
              else "Il Client ID è un numero di 17-20 cifre."),
             ("client_secret", "Client Secret", discord_client_secret(), True,
              lambda v: None if len(v) >= 20 else "Incolla il Client Secret (OAuth2 → Reset Secret)."),
             ("hotkey", "Scorciatoia condivisione schermo", discord_golive_hotkey(), False,
              check_hotkey)],
            save)

    def _open_setup(self, service, heading, steps, link, redirect_uri, fields, on_save):
        """Finestra normale (mouse e tastiera) per incollare le credenziali di
        un servizio: si fa una volta sola, poi restano in config.json.
        fields: (chiave, etichetta, valore iniziale, nascosto?, controllo) dove
        controllo(valore) ritorna il messaggio d'errore o None."""
        existing = self.setup_windows.get(service)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        bg, surface, fg, muted = ("#%02x%02x%02x" % c for c in (OV_BG, OV_SURFACE, OV_TEXT, OV_MUTED))
        accent, danger = ("#%02x%02x%02x" % c for c in (OV_ACCENT, OV_DANGER))
        win = tk.Toplevel(self.root, bg=bg, padx=22, pady=18)
        self.setup_windows[service] = win
        win.title(f"{APP_NAME} – {service}")
        win.icon = ImageTk.PhotoImage(create_image(size=32, badge=False), master=win)
        win.iconphoto(False, win.icon)  # icona di AppUtili invece della piuma di Tk
        win.resizable(False, False)
        # niente -topmost: con UIAccess la cornice finirebbe in un altro strato
        # e Tk non riuscirebbe a metterci il contenuto (vedi _repair_frame);
        # la si porta comunque in primo piano piu' sotto

        def label(text, space=(0, 0), **kw):
            options = dict(bg=bg, fg=fg, font=("Segoe UI", 10), justify="left", anchor="w")
            options.update(kw)
            widget = tk.Label(win, text=text, **options)
            widget.pack(fill="x", pady=space)
            return widget

        def button(parent, text, command, primary=False):
            return tk.Button(parent, text=text, command=command, relief="flat", cursor="hand2",
                             bg=accent if primary else surface, fg=bg if primary else fg,
                             activebackground=accent, activeforeground=bg,
                             font=("Segoe UI", 10, "bold" if primary else "normal"), padx=12, pady=4)

        label(heading, font=("Segoe UI", 15, "bold"))
        label(steps, fg=muted, space=(8, 10))
        button(win, link[0], lambda: webbrowser.open(link[1])).pack(anchor="w")

        label("Redirect URI", space=(12, 2))
        row = tk.Frame(win, bg=bg)
        row.pack(fill="x")
        uri = tk.Entry(row, font=("Consolas", 10), relief="flat", bg=surface, fg=fg,
                       readonlybackground=surface, width=40)
        uri.insert(0, redirect_uri)
        uri.configure(state="readonly")
        uri.pack(side="left", ipady=4)

        def copy_uri():
            win.clipboard_clear()
            win.clipboard_append(redirect_uri)
            copy.configure(text="Copiato")
        copy = button(row, "Copia", copy_uri)
        copy.pack(side="left", padx=(8, 0))

        entries = {}
        for key, text, initial, hidden, _ in fields:
            label(text, space=(12, 2))
            entry = tk.Entry(win, font=("Consolas", 10), relief="flat", bg=surface, fg=fg,
                             insertbackground=fg, width=40, show="•" if hidden else "")
            entry.insert(0, initial)
            entry.pack(anchor="w", ipady=4)
            entries[key] = entry
        error = label("", fg=danger, space=(4, 0))

        def connect(event=None):
            values = {key: entry.get().strip() for key, entry in entries.items()}
            for key, _, _, _, check in fields:
                message = check(values[key])
                if message:
                    error.configure(text=message)
                    entries[key].focus_force()
                    return
            win.destroy()
            on_save(values)

        actions = tk.Frame(win, bg=bg)
        actions.pack(fill="x", pady=(6, 0))
        button(actions, "Collega", connect, primary=True).pack(side="right")
        button(actions, "Annulla", win.destroy).pack(side="right", padx=(0, 8))
        win.bind("<Return>", connect)
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        content, frame = ov_user32.GetAncestor(win.winfo_id(), GA_ROOT), int(win.wm_frame(), 16)
        log_error(f"finestra {service}: contenuto {'nella cornice' if content == frame else 'SEPARATO'}"
                  )  # DIAGNOSTICA TEMPORANEA
        work, _ = _monitor_of(ov_user32.GetForegroundWindow())
        win.geometry(f"+{(work.left + work.right - win.winfo_width()) // 2}"
                     f"+{(work.top + work.bottom - win.winfo_height()) // 3}")
        _force_foreground(ov_user32.GetAncestor(win.winfo_id(), GA_ROOT))
        next(iter(entries.values())).focus_force()

    # --- mostra / nascondi -------------------------------------------------

    def show(self):
        global _monitor_pick
        _overlay_open.set()
        self.prev_hwnd = ov_user32.GetForegroundWindow()
        self.page = None
        _monitor_pick = None
        if self.tab not in visible_tabs():
            self.tab = visible_tabs()[0]
        self._select(0)
        self._ensure_selectable()
        self.games_grid.reset()
        self.spotify_index = [0] * len(SPOTIFY_FILTERS)
        self.spotify_scroll = 0
        _set_spotify_status(None)
        self.discord_recent = 0
        self.discord_expanded = set()
        self.discord_guild = None
        self.discord_column = "rail"
        self.discord_channel = 0
        self.discord_scroll = self.discord_rail_scroll = 0
        self.discord_focus = "list"
        _set_discord_status(None)
        work, dpi = _monitor_of(self.prev_hwnd)
        self.scale = dpi / 96
        self.panel_height = None
        media_refresh()
        refresh_audio_output()
        refresh_games()
        refresh_spotify()
        refresh_discord()
        if monitor_switcher_exe() == "":  # magari scaricato nel frattempo
            start_monitor_search()
        self._redraw()
        margin = round(OVERLAY_MARGIN * self.scale)
        self.position = (work.left + margin, work.top + margin)
        self.root.geometry(f"+{self.position[0]}+{self.position[1]}")
        self.root.deiconify()
        self.root.update_idletasks()
        if self._repair_frame():
            log_error("cornice dell'overlay separata dal contenuto: riparata")  # DIAGNOSTICA

        hwnd = self._hwnd()
        style = ov_user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ov_user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                 (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        _round_window_corners(hwnd)
        self.root.attributes("-topmost", True)
        _force_foreground(hwnd)
        self.root.focus_force()
        self._tick_id = self.root.after(OVERLAY_TICK_MS, self._tick)
        self.had_focus = False
        self._focus_id = self.root.after(OVERLAY_FOCUS_CHECK_MS, self._watch_focus)
        self.root.after(500, self._diag_windows)  # DIAGNOSTICA TEMPORANEA

    def _repair_frame(self):
        """Con UIAccess puo' capitare che Tk non riesca a mettere il contenuto
        dentro la sua cornice (Windows rifiuta di spostarlo tra strati
        diversi): il contenuto resta una finestra a se' in (0,0) e la
        cornice, vuota e sopra di lui, si prende i clic. In quel caso si
        nasconde la cornice e si porta il contenuto dove dovrebbe stare.
        Ritorna True se e' servito."""
        content = self._hwnd()
        frame = int(self.root.wm_frame(), 16)
        self.frame_broken = content != frame
        if self.frame_broken:
            ov_user32.ShowWindow(frame, SW_HIDE)
            self._place_content()
        return self.frame_broken

    def _place_content(self):
        """Contenuto separato dalla cornice: Tk lo rimette in (0,0) a ogni
        cambio di dimensione, quindi lo si riporta in posizione."""
        x, y = self.position
        ov_user32.SetWindowPos(self._hwnd(), HWND_TOPMOST, x, y, 0, 0,
                               SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _diag_windows(self):
        """DIAGNOSTICA TEMPORANEA (clic con UIAccess): dove sta davvero la
        finestra e quale finestra trova Windows sotto alcuni punti."""
        try:
            hwnd = self._hwnd()
            rect = wintypes.RECT()
            ov_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            ov_user32.GetWindowRect(hwnd, ctypes.byref(rect))
            ov_user32.WindowFromPoint.restype = wintypes.HWND
            ov_user32.WindowFromPoint.argtypes = [wintypes.POINT]
            ov_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            label_hwnd = self.label.winfo_id()
            log_error(f"diag finestra hwnd={hwnd} label_hwnd={label_hwnd} rect=({rect.left},"
                      f"{rect.top},{rect.right},{rect.bottom}) tk_geom={self.root.winfo_geometry()} "
                      f"tk_root=({self.root.winfo_rootx()},{self.root.winfo_rooty()}) "
                      f"uiaccess={_has_uiaccess()} admin={_is_admin()}")
            width = rect.right - rect.left
            for fy in (0.04, 0.09, 0.15, 0.3, 0.6, 0.9):
                point = wintypes.POINT(rect.left + width // 2,
                                       rect.top + int((rect.bottom - rect.top) * fy))
                found = ov_user32.WindowFromPoint(point)
                name = ctypes.create_unicode_buffer(256)
                ov_user32.GetClassNameW(found, name, 256)
                pid = wintypes.DWORD()
                ov_user32.GetWindowThreadProcessId(found, ctypes.byref(pid))
                root = ov_user32.GetAncestor(found, GA_ROOT)
                log_error(f"diag punto ({point.x},{point.y}) -> hwnd={found} classe={name.value} "
                          f"pid={pid.value} nostro={pid.value == os.getpid()} radice={root}")
        except Exception:
            log_error("diag errore:\n" + traceback.format_exc())

    def hide(self, restore_focus=True):
        # Prima si ridà il focus e poi si nasconde: nascondendo per primo,
        # Windows attiverebbe un'altra finestra e la restituzione verrebbe rifiutata
        prev = self.prev_hwnd
        self.prev_hwnd = None
        if restore_focus and prev and ov_user32.IsWindow(prev):
            _force_foreground(prev)
        self.root.withdraw()
        _overlay_open.clear()
        self.pending = None
        for after_id in (self._tick_id, self._focus_id):
            if after_id is not None:
                self.root.after_cancel(after_id)
        self._tick_id = self._focus_id = None

    def _tick(self):
        self._redraw()  # orologio e batteria
        self._tick_id = self.root.after(OVERLAY_TICK_MS, self._tick)

    def _watch_focus(self):
        """Si chiude quando un'altra finestra prende il primo piano (es. un clic
        fuori), cosi' non resta aperto sotto di lei. Solo dopo aver avuto il
        focus almeno una volta: se all'apertura Windows non gliel'ha dato,
        resta aperto come prima. Le finestre di configurazione sono nostre e
        non contano."""
        self._focus_id = None
        fg = ov_user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        if fg:
            ov_user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value == os.getpid():
            self.had_focus = True
        elif self.had_focus:
            self.hide(restore_focus=False)  # il focus resta alla finestra cliccata
            return
        self._focus_id = self.root.after(OVERLAY_FOCUS_CHECK_MS, self._watch_focus)

    def _redraw(self):
        if self.page != "info":
            self._ensure_selectable()  # es. la musica si e' fermata
        image, self.hitboxes = self.render()
        self.photo = ImageTk.PhotoImage(image, master=self.root)
        self.label.configure(image=self.photo)
        if self.frame_broken and _overlay_open.is_set():
            self.root.update_idletasks()
            self._place_content()

    def _hitbox_at(self, x, y):
        """Hitbox sotto il punto: (x0, y0, x1, y1, clic[, tasto destro])."""
        for box in reversed(self.hitboxes):
            if box[0] <= x < box[2] and box[1] <= y < box[3]:
                return box
        return None

    def _click(self, event):
        box = self._hitbox_at(event.x, event.y)
        # DIAGNOSTICA TEMPORANEA (clic con UIAccess): da togliere
        try:
            ov_user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            ov_user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            awareness = ov_user32.GetAwarenessFromDpiAwarenessContext(
                ov_user32.GetThreadDpiAwarenessContext())
        except Exception as e:
            awareness = f"? ({e})"
        log_error(f"clic dpi_awareness={awareness} scala={self.scale} "
                  f"x={event.x} y={event.y} root=({event.x_root},{event.y_root}) "
                  f"label={self.label.winfo_width()}x{self.label.winfo_height()} "
                  f"immagine={self.photo.width() if self.photo else '?'}x"
                  f"{self.photo.height() if self.photo else '?'} pagina={self.page} tab={self.tab} "
                  f"hitbox={len(self.hitboxes)} trovato={box[:4] if box else None}")
        if box is not None:
            box[4]()

    def _right_click(self, event):
        box = self._hitbox_at(event.x, event.y)
        if box is not None and len(box) > 5:
            box[5]()

    def _hover(self, event):
        self.label.configure(cursor="hand2" if self._hitbox_at(event.x, event.y) else "")

    def _wheel(self, event):
        # le tab con liste e griglie e le pagine; nella tab Controlli non c'e' niente da scorrere
        if self.page or self.tab in (TAB_GAMES, TAB_SPOTIFY, TAB_DISCORD):
            self._handle("wheel_up" if event.delta > 0 else "wheel_down")

    # --- disegno -----------------------------------------------------------

    def _S(self, v):
        return round(v * self.scale)

    def render(self):
        """Disegna il pannello. Ritorna (immagine, hitbox per il mouse)."""
        if self.page is None and self.tab == TAB_CONTROLS:
            image, hitboxes = self._render_controls()
            self.panel_height = image.height
            return image, hitboxes
        if self.panel_height is None:
            self.panel_height = self._render_controls(measure=True)[0].height
        if self.page == "settings":
            return self._render_settings(self.panel_height)
        if self.page == "info":
            return self._render_info(self.panel_height)
        if self.tab == TAB_SPOTIFY:
            return self._render_spotify(self.panel_height)
        if self.tab == TAB_DISCORD:
            return self._render_discord(self.panel_height)
        return self._render_games(self.panel_height)

    def _new_canvas(self, height):
        img = Image.new("RGB", (self._S(OVERLAY_WIDTH), height), OV_BG)
        return img, ImageDraw.Draw(img)

    def _render_header(self, img, draw, hitboxes):
        """Titolo, orologio, batteria, profilo monitor e tab. Ritorna la y finale."""
        S = self._S
        width, pad = img.width, S(14)
        y = S(14)
        cy = y + S(14)
        draw.text((pad + S(6), cy), APP_NAME, font=_ov_font(S(20), "Semibold Display"),
                  fill=OV_TEXT, anchor="lm")

        # da destra: orologio, batteria del pad, profilo monitor
        x = width - pad - S(4)
        clock_font = _ov_font(S(14), "Semibold Text")
        clock = time.strftime("%H:%M")
        draw.text((x, cy), clock, font=clock_font, fill=OV_TEXT, anchor="rm")
        x -= clock_font.getlength(clock) + S(10)
        battery = pad_battery_level()
        if battery is not None:
            draw.text((x, cy), ICON_BATTERY_LEVELS[min(3, battery)], font=_ov_icon_font(S(16)),
                      fill=OV_DANGER if battery == 0 else OV_MUTED, anchor="rm")
            x -= S(30)
        profile = monitor_current() if monitor_profiles() else ""
        if profile:  # chip con il profilo monitor caricato
            chip_color = monitor_style(profile)[0]
            chip_font = _ov_font(S(12), "Semibold Text")
            chip_text = _ov_fit(profile, chip_font, S(110))
            x0 = x - S(30) - chip_font.getlength(chip_text)
            _ov_round(img, (x0, cy - S(12), x, cy + S(12)), S(12), OV_SURFACE)
            _ov_round(img, (x0 + S(10), cy - S(4), x0 + S(18), cy + S(4)), S(4), chip_color)
            draw.text((x0 + S(23), cy), chip_text, font=chip_font, fill=OV_TEXT, anchor="lm")
            x = x0 - S(8)
        # ingranaggio: impostazioni (col pad: Select tenuto premuto)
        gear = S(24)
        if self.page:
            _ov_round(img, (x - gear, cy - gear / 2, x, cy + gear / 2), gear / 2, OV_SURFACE)
        draw.text((x - gear / 2, cy), ICON_SETTINGS, font=_ov_icon_font(S(15)),
                  fill=OV_ACCENT if self.page else OV_MUTED, anchor="mm")
        hitboxes.append((x - gear - S(4), cy - S(14), x + S(4), cy + S(14),
                         lambda: self._handle("settings")))
        y += S(38)

        cy = y + S(14)
        if self.page:  # nelle pagine, al posto delle tab: titolo e "B Indietro"
            title = "Impostazioni" if self.page == "settings" else "Informazioni"
            draw.text((pad + S(10), cy), ICON_CHEVRON_LEFT, font=_ov_icon_font(S(11)),
                      fill=OV_MUTED, anchor="mm")
            draw.text((pad + S(24), cy), title, font=_ov_font(S(15), "Semibold Text"),
                      fill=OV_TEXT, anchor="lm")
            hint_font = _ov_font(S(12))
            right = width - pad - S(4)
            draw.text((right, cy), "Indietro", font=hint_font, fill=OV_MUTED, anchor="rm")
            self._pad_button(img, draw, right - hint_font.getlength("Indietro") - S(6)
                             - self._pad_button_width("B"), cy, "B")
            hitboxes.append((pad, cy - S(14), width - pad, cy + S(16), lambda: self._handle("b")))
            y += S(34)
            draw.rectangle((pad, y, width - pad - 1, y + max(1, S(1)) - 1), fill=OV_BORDER)
            return y + S(4)

        # tab: LT  Controlli  Giochi  RT (solo quelle visibili)
        x = pad + S(4)
        x = self._pad_button(img, draw, x, cy, "LT") + S(12)
        tab_font = _ov_font(S(14), "Semibold Text")
        for i, name in enumerate(OVERLAY_TABS):
            if i not in visible_tabs():
                continue
            w = tab_font.getlength(name)
            selected = i == self.tab
            draw.text((x, cy), name, font=tab_font, fill=OV_TEXT if selected else OV_SUBTLE,
                      anchor="lm")
            if selected:
                _ov_round(img, (x, cy + S(12), x + w, cy + S(15)), S(1.5), OV_ACCENT)
            hitboxes.append((x - S(6), cy - S(14), x + w + S(6), cy + S(16),
                             lambda i=i: self._set_tab(i)))
            x += w + S(18)
        self._pad_button(img, draw, x - S(6), cy, "RT")
        y += S(34)
        draw.rectangle((pad, y, width - pad - 1, y + max(1, S(1)) - 1), fill=OV_BORDER)
        return y + S(4)

    def _render_footer(self, img, draw, y, entries=None):
        S = self._S
        small_font = _ov_font(S(12))
        x = S(14) + S(6)
        cy = y + S(10)
        for buttons, text in entries or OVERLAY_FOOTERS[self.tab]:
            for button in buttons:
                x = self._pad_button(img, draw, x, cy, button) + S(3)
            x += S(3)
            draw.text((x, cy), text, font=small_font, fill=OV_MUTED, anchor="lm")
            x += small_font.getlength(text) + S(14)
        return y + S(20) + S(14)

    def _render_description(self, img, draw, y, text):
        """Due righe fisse, cosi' il pannello non cambia altezza."""
        S = self._S
        width, pad = img.width, S(14)
        draw.rectangle((pad, y, width - pad - 1, y + max(1, S(1)) - 1), fill=OV_BORDER)
        y += S(10)
        desc_font = _ov_font(S(12))
        for i, line in enumerate(_ov_wrap(text, desc_font, width - 2 * pad - S(12))):
            draw.text((pad + S(6), y + S(8) + i * S(17)), line, font=desc_font,
                      fill=OV_MUTED, anchor="lm")
        return y + S(46)

    def _render_rows(self, img, draw, y, items, hitboxes):
        """Righe di voci (tab Controlli, Impostazioni): sezioni, icona,
        etichetta e comando a destra. Ritorna (y finale, {indice: (y0, y1)})."""
        S = self._S
        width, pad = img.width, S(14)
        row_h = S(40)
        label_font = _ov_font(S(14))
        icon_font = _ov_icon_font(S(16))
        spans = {}
        for index, item in enumerate(items):
            kind = _kind(item)
            if kind == "media" or ("visible" in item and not item["visible"]()):
                continue
            section_top = y
            if item.get("section"):
                y += S(8)
                draw.text((pad + S(12), y + S(9)), item["section"].upper(),
                          font=_ov_font(S(11), "Semibold Text"), fill=OV_MUTED, anchor="lm")
                y += S(20)
            spans[index] = (section_top, y + row_h)
            selected = index == self.index
            x0, x1 = pad, width - pad
            cy = y + row_h // 2
            if selected:
                _ov_round(img, (x0, y, x1, y + row_h), S(6), OV_SELECTED)
                _ov_round(img, (x0, cy - S(8), x0 + S(3), cy + S(8)), S(2), OV_ACCENT)
            icon = _value(item["icon"])
            icon_color = OV_ACCENT if selected else OV_TEXT
            logo = _ov_logo_mask(icon, S(17)) if isinstance(icon, Path) else None
            if logo is not None:  # logo PNG colorato come i glifi
                lx, ly = x0 + S(24) - logo.width // 2, cy - logo.height // 2
                img.paste(icon_color, (lx, ly, lx + logo.width, ly + logo.height), logo)
            else:
                draw.text((x0 + S(24), cy), icon if isinstance(icon, str) else ICON_VOLUME,
                          font=icon_font, fill=icon_color, anchor="mm")
            right = x1 - S(10)
            label = _value(item["label"])
            if kind == "segmented":  # le pillole prima: l'etichetta si accorcia se serve
                left = self._render_segmented(img, draw, right, cy, index, item, selected, hitboxes)
                label = _ov_fit(label, label_font, left - S(10) - (x0 + S(46)))
            draw.text((x0 + S(46), cy), label, font=label_font, fill=OV_TEXT, anchor="lm")
            hitboxes.insert(0, (x0, y, x1, y + row_h,
                                lambda i=index: (self._select(i), self._confirm())))
            if kind == "action":
                if item.get("hint"):
                    self._keycap(img, draw, right - S(4), cy, item["hint"])
                elif item.get("stay"):
                    draw.text((right, cy), ICON_CHEVRON_RIGHT, font=_ov_icon_font(S(10)),
                              fill=OV_TEXT if selected else OV_SUBTLE, anchor="rm")
            elif kind == "toggle":
                self._switch(img, draw, right - S(4), cy, _value(item["value"]), selected)
            elif kind == "choice":
                self._render_choice(img, draw, right - S(4), cy, item, selected)
            elif kind == "slider":
                self._render_slider(img, draw, right - S(4), cy, item, selected)
            y += row_h + S(2)
        return y, spans

    def _selected_description(self):
        item = self._items()[self.index]
        if self.pending and self.pending[0] == self.index:
            option = _options(item)[self.pending[1]]["label"]
            return f"Premi di nuovo A per confermare: {option}."
        return _value(item.get("desc", ""))

    def _render_controls(self, measure=False):
        """measure: disegnata solo per misurare l'altezza del pannello (le
        altre tab e le pagine sono alte come lei)."""
        S = self._S
        img, draw = self._new_canvas(S(1600))
        width, pad = img.width, S(14)
        hitboxes = []
        y = self._render_header(img, draw, hitboxes)
        y, _ = self._render_rows(img, draw, y, OVERLAY_ITEMS, hitboxes)

        # scheda media (sempre l'ultima voce)
        y += S(12)
        y = self._render_media(img, draw, y, pad, width, hitboxes, S)

        y += S(12)
        description = "" if measure or self.page else self._selected_description()
        y = self._render_description(img, draw, y, description)
        # sempre la legenda dei Controlli: questa tab si disegna anche per
        # misurare l'altezza del pannello mentre ne e' aperta un'altra
        y = self._render_footer(img, draw, y, OVERLAY_FOOTERS[TAB_CONTROLS])
        return img.crop((0, 0, width, max(y, S(PANEL_MIN_HEIGHT)))), hitboxes

    def _render_settings(self, height):
        """Pagina Impostazioni: le voci scorrono, sotto la descrizione."""
        S = self._S
        img, draw = self._new_canvas(height)
        hitboxes = []
        top = self._render_header(img, draw, hitboxes)
        footer_h, desc_h = S(34), S(56)
        bottom = height - footer_h - desc_h
        list_h = bottom - top

        rows = Image.new("RGB", (img.width, S(2400)), OV_BG)
        row_hits = []
        total, spans = self._render_rows(rows, ImageDraw.Draw(rows), 0, SETTINGS_ITEMS, row_hits)
        y0, y1 = spans.get(self.index, (0, 0))
        self.settings_scroll = scroll = self._dc_scroll(y0, y1, self.settings_scroll, list_h,
                                                         total + S(8))
        img.paste(rows.crop((0, scroll, img.width, scroll + list_h)), (0, top))
        for box in row_hits:  # hitbox spostati dove la lista e' disegnata, tagliati
            hy0, hy1 = max(top, box[1] + top - scroll), min(bottom, box[3] + top - scroll)
            if hy1 > hy0:
                hitboxes.append((box[0], hy0, box[2], hy1, *box[4:]))
        self._render_description(img, draw, bottom, self._selected_description())
        self._render_footer(img, draw, height - footer_h, SETTINGS_FOOTER)
        return img, hitboxes

    # --- pagina Info --------------------------------------------------------

    def _info_panels(self):
        """Pannelli espandibili della pagina Info. Righe:
        ("sub", titoletto) | ("keys", descrizione, tasti) |
        ("info", etichetta, valore, azione o None, suggerimento)."""
        def sub(text):
            return ("sub", text)

        def keys(description, *tokens):
            return ("keys", description, tokens)

        def info(label, value, action=None, hint="Apri"):
            return ("info", label, value, action, hint)

        def opener(target):
            return lambda: os.startfile(target)

        def browser(url):
            return lambda: webbrowser.open(url)

        def copier(text):
            return lambda: (self.root.clipboard_clear(), self.root.clipboard_append(text))

        desk, tv = monitor_role("desk"), monitor_role("tv")
        shortcuts = [
            sub("Tastiera"),
            keys("Apri / chiudi l'overlay", "Shift", "+", "F1"),
            keys(f"Monitor {desk or 'Scrivania'}", "Ctrl", "+", "Alt", "+", "DOWN"),
            keys(f"Monitor {tv or 'TV'}", "Ctrl", "+", "Alt", "+", "UP"),
            sub("Pad, ovunque"),
            keys("Apri / chiudi l'overlay", "Back", "+", "RB"),
            keys("Monitor TV / Scrivania", "Back", "+", "LB", "+", "UP", "/", "DOWN"),
            keys("Uscita audio", "Back", "+", "LB", "+", "LEFT", "/", "RIGHT"),
            keys("Mostra desktop", "Back", "+", "L3", "+", "R3"),
            keys("Chiudi finestra", "Back", "+", "Start", "+", "LB", "+", "RB", "+", "LT", "+", "RT"),
            sub("Pad, nell'overlay"),
            keys("Impostazioni", "Back", "tieni"),
            keys("Cambia tab", "LT", "/", "RT"),
            keys("Brano · play / pausa", "LB", "/", "RB", "·", "X"),
            keys("Muto Discord · shuffle Spotify", "L3", "·", "R3"),
            keys("Server preferito (Discord)", "Start"),
        ]

        spotify, discord = _spotify_menu_status(None), _discord_menu_status(None)
        with config_lock:
            favorites = len(config["discord_favorites"])
        services = [
            sub(f"Spotify · {spotify}"),
            info("Redirect URI", SPOTIFY_REDIRECT_URI, copier(SPOTIFY_REDIRECT_URI), "Copia"),
            info("Dashboard", "developer.spotify.com", browser(SPOTIFY_DASHBOARD)),
            info("Requisiti", "Premium sull'account che crea l'app"),
            sub(f"Discord · {discord}"),
            info("Redirect URI", DISCORD_REDIRECT_URI, copier(DISCORD_REDIRECT_URI), "Copia"),
            info("Portale", "discord.com/developers", browser(DISCORD_DASHBOARD)),
            info("Schermo", f"{discord_golive_hotkey().upper()} (la stessa scorciatoia di Discord)"),
            info("Preferiti", f"{favorites} server"),
        ]

        exe = monitor_switcher_exe()
        profiles = monitor_profiles()
        monitor = [
            info("MonitorSwitcher", exe or ("ricerca…" if exe is None else "non trovato"),
                 opener(Path(exe).parent) if exe else browser(MONITOR_SWITCHER_SITE),
                 "Apri" if exe else "Scarica"),
            info("Profili", ", ".join(profiles) or "nessuno: salvali con MonitorSwitcher"),
            info("Cartella", str(MONITOR_PROFILES_DIR),
                 opener(MONITOR_PROFILES_DIR) if MONITOR_PROFILES_DIR.is_dir() else None),
            info("Scrivania · TV", f"{desk or '—'} · {tv or '—'}"),
        ]

        if _audio_source == "sonar":
            audio = "SteelSeries Sonar"
        elif _audio_source == "windows":
            audio = "Windows (GG chiuso)"
        else:
            audio = "non rilevata"
        if _audio_output:
            audio += f" · {_audio_output[1]}"
        battery = pad_battery_level()
        pads = ", ".join(_controller_names()) or "nessuno collegato"
        if battery is not None:
            pads += f" · batteria {('vuota', 'bassa', 'media', 'piena')[min(3, battery)]}"
        uiaccess = _has_uiaccess()
        frozen = getattr(sys, "frozen", False)
        system = [
            info("Versione", str(Path(sys.executable).parent) if frozen else "da sorgente (python app.py)"),
            info("Amministratore", "sì" if _is_admin() else "no"),
            info("Sopra i giochi", "sì (UIAccess)" if uiaccess
                 else "no: serve l'exe installato in Program Files"),
            info("Uscita audio", audio),
            info("Controller", pads),
            info("Configurazione", str(CONFIG_DIR), opener(CONFIG_DIR)),
        ]

        connected = sum(state == "Collegato" for state in (spotify, discord))
        return [
            {"id": "keys", "icon": ICON_KEYBOARD, "title": "Scorciatoie", "summary": "",
             "rows": shortcuts},
            {"id": "services", "icon": ICON_LINK, "title": "Servizi collegati",
             "summary": f"{connected} su 2 collegati", "rows": services},
            {"id": "monitor", "icon": ICON_MONITOR, "title": "Monitor",
             "summary": f"{len(profiles)} profili" if profiles else "da configurare",
             "rows": monitor},
            {"id": "system", "icon": ICON_PC, "title": "Sistema",
             "summary": "UIAccess attivo" if uiaccess else "senza UIAccess", "rows": system},
        ]

    def _info_entries(self, panels):
        """Voci selezionabili: le intestazioni e, nel pannello aperto, le
        righe con un'azione. Ognuna e' (pannello, riga o None)."""
        entries = []
        for p, panel in enumerate(panels):
            entries.append((p, None))
            if panel["id"] == self.info_open:
                entries += [(p, r) for r, row in enumerate(panel["rows"])
                            if row[0] == "info" and row[3]]
        return entries

    def _info_command(self, command):
        panels = self._info_panels()
        entries = self._info_entries(panels)
        command = command.removesuffix("_repeat")
        self.info_index = index = min(self.info_index, len(entries) - 1)
        p, r = entries[index]
        if command in ("up", "down"):
            self.info_index = min(len(entries) - 1, max(0, index + (-1 if command == "up" else 1)))
        elif command == "a" and r is None:
            self._info_toggle(p)
            return
        elif command == "a":
            self._info_activate(p, r)
            return
        elif command == "right" and r is None and panels[p]["id"] != self.info_open:
            self._info_toggle(p)
            return
        elif command == "left" and self.info_open == panels[p]["id"]:
            self._info_toggle(p)  # chiude il pannello e torna alla sua intestazione
            return
        else:
            return
        self._redraw()

    def _info_toggle(self, p):
        """Apre un pannello (chiudendo gli altri) o chiude quello aperto."""
        panels = self._info_panels()
        self.info_open = None if self.info_open == panels[p]["id"] else panels[p]["id"]
        self.info_index = self._info_entries(panels).index((p, None))
        self._redraw()

    def _info_activate(self, p, r):
        panels = self._info_panels()
        self.info_index = self._info_entries(panels).index((p, r))
        _, _, _, action, hint = panels[p]["rows"][r]
        action()
        if hint == "Copia":
            self.info_copied = (p, r, time.monotonic() + 2)
            self.root.after(2100, lambda: _overlay_open.is_set() and self._redraw())
        if _overlay_open.is_set():
            self._redraw()

    def _render_keys(self, img, draw, right, cy, tokens):
        """Tasti di una scorciatoia allineati a destra: pulsanti del pad e tasti
        della tastiera come nella legenda, separatori come testo. Ritorna la x
        a cui iniziano."""
        S = self._S
        font = _ov_font(S(11.5))
        gap = S(3)
        widths = [font.getlength(t) + S(2) if t in INFO_KEY_SEPARATORS else self._pad_button_width(t)
                  for t in tokens]
        x = start = right - sum(widths) - gap * (len(tokens) - 1)
        for token, w in zip(tokens, widths):
            if token in INFO_KEY_SEPARATORS:
                draw.text((x + w / 2, cy), token, font=font, fill=OV_MUTED, anchor="mm")
            else:
                self._pad_button(img, draw, x, cy, token)
            x += w + gap
        return start

    def _render_info(self, height):
        """Pagina Info: pannelli espandibili, uno aperto alla volta, con righe
        compatte (scorciatoie con i tasti disegnati, etichetta + valore)."""
        S = self._S
        img, draw = self._new_canvas(height)
        hitboxes = []
        top = self._render_header(img, draw, hitboxes)
        footer_h = S(34)
        list_h = height - footer_h - top - S(8)
        panels = self._info_panels()
        entries = self._info_entries(panels)
        self.info_index = min(self.info_index, len(entries) - 1)
        focus = entries[self.info_index]
        head_h, row_h, sub_h = S(38), S(25), S(24)

        # posizioni nel contenuto, prima dello scorrimento
        positions, spans, y = {}, {}, S(4)
        for p, panel in enumerate(panels):
            panel_top = y
            positions[(p, None)] = (y, y + head_h)
            y += head_h
            if panel["id"] == self.info_open:
                for r, row in enumerate(panel["rows"]):
                    h = sub_h if row[0] == "sub" else row_h
                    positions[(p, r)] = (y, y + h)
                    y += h
                y += S(8)
            spans[p] = (panel_top, y)
            y += S(6)
        f0, f1 = positions[focus]
        if focus[1] is None and panels[focus[0]]["id"] == self.info_open:
            p0, p1 = spans[focus[0]]
            if p1 - p0 <= list_h:
                f1 = p1  # intestazione del pannello aperto: si mostra tutto il pannello
        self.info_scroll = scroll = self._dc_scroll(f0, f1, self.info_scroll, list_h, y)

        area = Image.new("RGB", (img.width, list_h), OV_BG)
        adraw = ImageDraw.Draw(area)
        x0, x1 = S(14), img.width - S(14)
        title_font, summary_font = _ov_font(S(14), "Semibold Text"), _ov_font(S(12))
        text_font, label_font = _ov_font(S(12.5)), _ov_font(S(12))
        sub_font = _ov_font(S(10.5), "Semibold Text")
        copied = getattr(self, "info_copied", None)

        def hit(box_y0, box_y1, action):
            hy0, hy1 = top + max(0, box_y0), top + min(list_h, box_y1)
            if hy1 > hy0:
                hitboxes.append((x0, hy0, x1, hy1, action))

        for p, panel in enumerate(panels):
            p0, p1 = (v - scroll for v in spans[p])
            if p1 <= 0 or p0 >= list_h:
                continue
            opened = panel["id"] == self.info_open
            _ov_round(area, (x0, p0, x1, p1), S(8), OV_SURFACE)
            h0, h1 = (v - scroll for v in positions[(p, None)])
            cy = (h0 + h1) // 2
            focused = focus == (p, None)
            if focused:
                _ov_round(area, (x0, h0, x1, h1), S(8), OV_SELECTED)
                _ov_round(area, (x0, cy - S(8), x0 + S(3), cy + S(8)), S(2), OV_ACCENT)
            adraw.text((x0 + S(20), cy), panel["icon"], font=_ov_icon_font(S(15)),
                       fill=OV_ACCENT if focused else OV_TEXT, anchor="mm")
            adraw.text((x0 + S(40), cy), panel["title"], font=title_font, fill=OV_TEXT, anchor="lm")
            adraw.text((x1 - S(14), cy), ICON_CHEVRON_DOWN if opened else ICON_CHEVRON_RIGHT,
                       font=_ov_icon_font(S(10)), fill=OV_MUTED, anchor="rm")
            if panel["summary"]:
                adraw.text((x1 - S(32), cy), panel["summary"], font=summary_font, fill=OV_MUTED,
                           anchor="rm")
            hit(h0, h1, lambda p=p: self._info_toggle(p))
            if not opened:
                continue

            for r, row in enumerate(panel["rows"]):
                r0, r1 = (v - scroll for v in positions[(p, r)])
                if r1 <= 0 or r0 >= list_h:
                    continue
                cy = (r0 + r1) // 2
                if row[0] == "sub":
                    adraw.text((x0 + S(16), cy + S(3)), row[1].upper(), font=sub_font,
                               fill=OV_MUTED, anchor="lm")
                elif row[0] == "keys":
                    start = self._render_keys(area, adraw, x1 - S(12), cy, row[2])
                    adraw.text((x0 + S(16), cy), _ov_fit(row[1], text_font, start - S(8) - x0 - S(16)),
                               font=text_font, fill=OV_TEXT, anchor="lm")
                else:
                    _, label, value, action, hint = row
                    focused = focus == (p, r)
                    if focused:
                        _ov_round(area, (x0 + S(6), r0, x1 - S(6), r1), S(6), OV_SELECTED)
                    right = x1 - S(12)
                    if action:
                        done = copied and copied[:2] == (p, r) and time.monotonic() < copied[2]
                        text = "Copiato" if done else hint
                        adraw.text((right, cy), text, font=label_font,
                                   fill=OV_ACCENT if focused or done else OV_SUBTLE, anchor="rm")
                        right -= label_font.getlength(text) + S(10)
                        hit(r0, r1, lambda p=p, r=r: self._info_activate(p, r))
                    value_x = x0 + S(126)
                    adraw.text((x0 + S(16), cy), _ov_fit(label, label_font, value_x - S(6) - x0 - S(16)),
                               font=label_font, fill=OV_MUTED, anchor="lm")
                    adraw.text((value_x, cy), _ov_fit(value, text_font, right - value_x), font=text_font,
                               fill=OV_TEXT, anchor="lm")
        img.paste(area, (0, top))
        self._render_footer(img, draw, height - footer_h, INFO_FOOTER)
        return img, hitboxes

    def _render_choice(self, img, draw, right, cy, item, selected):
        S = self._S
        chevron_font = _ov_icon_font(S(10))
        value_font = _ov_font(S(13))
        chevron_color = OV_TEXT if selected else OV_SUBTLE
        value = _ov_fit(_value(item["value"]), value_font, img.width * 0.5)
        draw.text((right, cy), ICON_CHEVRON_RIGHT, font=chevron_font, fill=chevron_color, anchor="rm")
        value_x = right - S(20)
        draw.text((value_x, cy), value, font=value_font,
                  fill=OV_TEXT if selected else OV_MUTED, anchor="rm")
        draw.text((value_x - value_font.getlength(value) - S(10), cy), ICON_CHEVRON_LEFT,
                  font=chevron_font, fill=chevron_color, anchor="rm")

    def _render_slider(self, img, draw, right, cy, item, selected):
        S = self._S
        volume = _value(item["value"])
        font = _ov_font(S(13))
        if volume is None:
            draw.text((right, cy), "non disponibile", font=font, fill=OV_SUBTLE, anchor="rm")
            return
        level, muted = volume
        text = "Muto" if muted else f"{round(level * 100)}%"
        draw.text((right, cy), text, font=font, fill=OV_TEXT if selected else OV_MUTED, anchor="rm")
        bar_x1 = right - S(46)
        bar_x0 = bar_x1 - S(120)
        h = S(4)
        _ov_round(img, (bar_x0, cy - h / 2, bar_x1, cy + h / 2), h / 2, OV_BORDER)
        fill_color = OV_SUBTLE if muted else OV_ACCENT
        fill_x = bar_x0 + (bar_x1 - bar_x0) * level
        if fill_x - bar_x0 >= h:
            _ov_round(img, (bar_x0, cy - h / 2, fill_x, cy + h / 2), h / 2, fill_color)
        knob = S(7)
        _ov_round(img, (fill_x - knob, cy - knob, fill_x + knob, cy + knob), knob,
                  OV_TEXT if selected else OV_MUTED)

    def _render_segmented(self, img, draw, right, cy, index, item, selected, hitboxes):
        """Pillole affiancate, allineate a destra dentro un contenitore scuro."""
        S = self._S
        font = _ov_font(S(12), "Semibold Text")
        options = _options(item)
        pending = self.pending if self.pending and self.pending[0] == index else None
        labels = ["Conferma?" if pending and pending[1] == i else o["label"]
                  for i, o in enumerate(options)]
        widths = [font.getlength(label) + S(20) for label in labels]
        h, inset = S(28), S(3)
        x = right - sum(widths) - 2 * inset
        _ov_round(img, (x, cy - h / 2, right, cy + h / 2), h / 2, OV_SEGMENT_BG)
        x += inset
        for i, (option, label, w) in enumerate(zip(options, labels, widths)):
            box = (x, cy - h / 2 + inset, x + w, cy + h / 2 - inset)
            radius = (h - 2 * inset) / 2
            active = option.get("active") and option["active"]()
            if pending and pending[1] == i:
                _ov_round(img, box, radius, OV_DANGER)
                color = OV_TEXT
            elif selected and i == self.segment:
                _ov_round(img, box, radius, OV_ACCENT)
                color = OV_BG
            elif active:
                _ov_round(img, box, radius, OV_SEGMENT_ACTIVE)
                color = OV_TEXT
            else:
                color = OV_MUTED
            draw.text((x + w / 2, cy), label, font=font, fill=color, anchor="mm")
            hitboxes.append((*box, lambda i=i: (self._select(index), setattr(self, "segment", i),
                                                self._confirm())))
            x += w
        return right - sum(widths) - 2 * inset

    def _render_center_message(self, img, draw, top, height, title, hint=None):
        """Messaggio al centro di una tab vuota, con il pulsante A se serve."""
        S = self._S
        cy = top + height // 2
        draw.text((img.width / 2, cy - (S(12) if hint else 0)), title, font=_ov_font(S(14)),
                  fill=OV_MUTED, anchor="mm")
        if hint:
            font = _ov_font(S(13))
            w = S(18) + S(8) + font.getlength(hint)
            x = (img.width - w) / 2
            x = self._pad_button(img, draw, x, cy + S(16), "A") + S(8)
            draw.text((x, cy + S(16)), hint, font=font, fill=OV_TEXT, anchor="lm")

    def _render_item_info(self, img, draw, y, title, subtitle, subtitle_color=OV_MUTED):
        S = self._S
        width, pad = img.width, S(14)
        title_font = _ov_font(S(15), "Semibold Text")
        small_font = _ov_font(S(12))
        draw.text((pad + S(6), y + S(12)), _ov_fit(title, title_font, width - 2 * pad - S(12)),
                  font=title_font, fill=OV_TEXT, anchor="lm")
        draw.text((pad + S(6), y + S(33)), _ov_fit(subtitle, small_font, width - 2 * pad - S(12)),
                  font=small_font, fill=subtitle_color, anchor="lm")

    def _render_games(self, height):
        S = self._S
        img, draw = self._new_canvas(height)
        hitboxes = []
        y = self._render_header(img, draw, hitboxes)
        games = get_games()

        footer_h, info_h = S(34), S(58)
        grid_top = y + S(10)
        grid_h = height - grid_top - info_h - footer_h - S(6)
        if not games:
            message = "Caricamento…" if games is None else "Nessun gioco trovato in Steam"
            self._render_center_message(img, draw, grid_top, grid_h, message)
        else:
            def cover_for(game, w, h):
                art = game["art"]
                return _game_cover(*(str(art[k]) if k in art else None
                                     for k in ("capsule", "hero", "logo", "header")),
                                   game["name"], w, h)

            def on_click(i):
                self.games_grid.index = i
                self._games_command("a")

            self.games_grid.render(self, img, grid_top, grid_h, [(None, games)], 3 / 2,
                                   cover_for, on_click, hitboxes)
            index = self.games_grid.index
            game = games[index]
            self._render_item_info(img, draw, grid_top + grid_h + S(10), game["name"],
                                   f"{game['source']} · {_relative_time(game['last_played'])}"
                                   f" · {index + 1}/{len(games)}")

        self._render_footer(img, draw, height - footer_h)
        return img, hitboxes

    def _render_spotify(self, height):
        S = self._S
        img, draw = self._new_canvas(height)
        hitboxes = []
        y = self._render_header(img, draw, hitboxes)
        state = get_spotify_state()

        footer_h, player_h, status_h = S(34), S(58), S(24)
        body_top = y + S(8)
        body_h = height - body_top - status_h - player_h - footer_h
        if state is None:
            self._render_center_message(img, draw, body_top, body_h, "Caricamento…")
        elif state == "no_client_id":
            self._render_center_message(img, draw, body_top, body_h,
                                        "Spotify non è ancora configurato", "Configura")
        elif state == "not_connected":
            self._render_center_message(img, draw, body_top, body_h,
                                        "Account Spotify non collegato", "Collega")
        elif isinstance(state, str):
            self._render_center_message(img, draw, body_top, body_h,
                                        state.removeprefix("error: "), "Riprova")
        else:
            filter_h = S(44)
            self._render_filter(img, draw, body_top + filter_h // 2, SPOTIFY_FILTERS,
                                self.spotify_filter, self._set_spotify_filter,
                                (("Y",), "Cambia"), hitboxes)
            items = self._spotify_items(state)
            list_top = body_top + filter_h
            if not items:
                empty = "Nessun brano ascoltato di recente" if self.spotify_filter == 0 \
                    else "Nessuna playlist"
                self._render_center_message(img, draw, list_top, body_h - filter_h, empty)
            else:
                index = min(self.spotify_index[self.spotify_filter], len(items) - 1)
                self.spotify_index[self.spotify_filter] = index

                def play(i):
                    self.spotify_index[self.spotify_filter] = i
                    self._spotify_command("a")
                self.spotify_scroll = self._render_list(
                    img, list_top, body_h - filter_h, items, index, self.spotify_scroll,
                    lambda item, size: _spotify_cover(item["image_path"], size, size)
                    if item.get("image_path") else _spotify_placeholder(size, size),
                    lambda item: ICON_PLAY, play, hitboxes)

        status = _spotify_status
        if status:
            draw.text((S(14) + S(8), height - footer_h - player_h - status_h // 2),
                      _ov_fit(status[0], _ov_font(S(12)), img.width - 2 * S(22)),
                      font=_ov_font(S(12)), fill=OV_DANGER if status[1] else OV_ACCENT,
                      anchor="lm")
        self._render_now_playing(img, draw, height - footer_h - player_h, hitboxes)
        self._render_footer(img, draw, height - footer_h)
        return img, hitboxes

    def _render_discord(self, height):
        S = self._S
        img, draw = self._new_canvas(height)
        hitboxes = []
        y = self._render_header(img, draw, hitboxes)
        state = get_discord_state()
        footer_h = S(34)

        if not isinstance(state, dict):
            body_top = y + S(8)
            body_h = height - body_top - S(24) - footer_h
            if state is None:
                self._render_center_message(img, draw, body_top, body_h, "Caricamento…")
            elif state == "no_client_id":
                self._render_center_message(img, draw, body_top, body_h,
                                            "Discord non è ancora configurato", "Configura")
            elif state == "not_running":
                self._render_center_message(img, draw, body_top, body_h,
                                            "Discord non è aperto", "Apri Discord")
            elif state == "not_connected":
                self._render_center_message(img, draw, body_top, body_h,
                                            "Account Discord non collegato", "Collega")
            else:
                self._render_center_message(img, draw, body_top, body_h,
                                            state.removeprefix("error: "), "Riprova")
            self._render_footer(img, draw, height - footer_h)
            return img, hitboxes

        body_bottom = height - footer_h
        filter_h = S(48)
        self._render_filter(img, draw, y + S(4) + filter_h // 2, DISCORD_VIEWS, self.discord_view,
                            self._set_discord_view, (("Y",), "Cambia"), hitboxes)
        y += S(4) + filter_h
        bar_top = body_bottom - S(62)
        if DISCORD_VIEWS[self.discord_view][0] == "recent":
            self._render_discord_recent(img, y, bar_top - y, state, hitboxes)
        else:
            self._render_discord_servers(img, y, bar_top - y, state, hitboxes)
        self._render_discord_bar(img, draw, bar_top + S(4), S(54), state, hitboxes)
        self._render_footer(img, draw, body_bottom, self._discord_footer())
        return img, hitboxes

    @staticmethod
    def _dc_scroll(top, bottom, scroll, height, total):
        """Scorrimento che rende visibile [top, bottom) (dall'inizio, se non ci
        sta tutto), senza spazio vuoto in fondo."""
        if bottom - top > height or top < scroll:
            scroll = top
        elif bottom > scroll + height:
            scroll = bottom - height
        return max(0, min(scroll, total - height))

    def _dc_avatar(self, img, person, x, y, size, bg, speaking=False):
        """Avatar tondo; chi parla ha l'anello verde come in Discord."""
        S = self._S
        x, y = round(x), round(y)
        if speaking:
            for r, color in ((S(4), OV_CONNECTED), (S(2), bg)):
                _ov_round(img, (x - r, y - r, x + size + r, y + size + r), size / 2 + r, color)
        img.paste(_discord_avatar(person, size), (x, y), _ov_mask(size, size, size // 2))

    def _dc_stack(self, img, draw, people, right, cy, bg):
        """Pila di avatar (al massimo 3, poi "+N") che finisce a right.
        Ritorna la x a cui inizia."""
        S = self._S
        size, overlap, ring = S(22), S(6), S(2)
        if len(people) > 3:
            text, font = f"+{len(people) - 3}", _ov_font(S(12))
            draw.text((right, cy), text, font=font, fill=OV_MUTED, anchor="rm")
            right -= font.getlength(text) + S(6)
        shown = people[:3]
        x = start = right - size - (len(shown) - 1) * (size - overlap)
        for person in shown:
            _ov_round(img, (x - ring, cy - size / 2 - ring, x + size + ring, cy + size / 2 + ring),
                      size / 2 + ring, bg)
            img.paste(_discord_avatar(person, size), (round(x), round(cy - size / 2)),
                      _ov_mask(size, size, size // 2))
            x += size - overlap
        return start - ring

    def _dc_member(self, img, draw, person, x, y, right, h, bg, speaking, you):
        """Riga di una persona dentro un canale: avatar, nome, muto/silenziato."""
        S = self._S
        size = S(24)
        cy = y + h // 2
        self._dc_avatar(img, person, x, cy - size // 2, size, bg, person["id"] in speaking)
        icon_font = _ov_icon_font(S(13))
        for flag, glyph in ((person["deaf"], ICON_MUTE), (person["mute"], ICON_MIC_OFF)):
            if flag:
                draw.text((right, cy), glyph, font=icon_font, fill=OV_MUTED, anchor="rm")
                right -= S(20)
        mine = person["id"] == you
        font = _ov_font(S(13.5), "Semibold Text" if mine else "Regular")
        nx = x + size + S(8)
        draw.text((nx, cy), _ov_fit(person["name"], font, right - S(6) - nx), font=font,
                  fill=OV_TEXT if mine else OV_TEXT, anchor="lm")

    def _render_discord_recent(self, img, top, height, state, hitboxes):
        """Vista Recenti: un canale per riga con la pila di chi c'e' dentro;
        destra la apre e mostra le persone una per una."""
        S = self._S
        items = discord_recent()
        if not items:
            self._render_center_message(img, ImageDraw.Draw(img), top, height,
                                        "Nessun canale recente: premi Y per i server")
            return
        width = img.width
        row_h, member_h, gap = S(52), S(32), S(2)
        index = self.discord_recent = min(self.discord_recent, len(items) - 1)
        tops, heights, y = [], [], 0
        for item in items:
            h = row_h
            if item["id"] in self.discord_expanded and item["members"]:
                h += len(item["members"]) * member_h + S(6)
            tops.append(y)
            heights.append(h)
            y += h + gap
        total = y - gap
        scroll = self.discord_scroll
        if self.discord_focus == "list":
            scroll = self._dc_scroll(tops[index], tops[index] + heights[index], scroll, height, total)
        self.discord_scroll = scroll = max(0, min(scroll, total - height))

        area = Image.new("RGB", (width, height), OV_BG)
        draw = ImageDraw.Draw(area)
        title_font = _ov_font(S(14), "Semibold Text")
        small_font = _ov_font(S(12))
        speaking = discord_speaking()
        you = (state.get("user") or {}).get("id")
        x0, x1 = S(8), width - S(8)
        for i, item in enumerate(items):
            y, h = tops[i] - scroll, heights[i]
            if y + h <= 0 or y >= height:
                continue
            expanded = h > row_h
            selected = i == index and self.discord_focus == "list"
            # come le liste di Spotify: selezionata piu' chiara, aperta su una scheda
            bg = OV_SELECTED if selected else (OV_SURFACE if expanded else OV_BG)
            if bg != OV_BG:
                _ov_round(area, (x0, y, x1, y + h), S(8), bg)
            cy = y + row_h // 2
            icon = S(32)
            ix = x0 + S(8)
            area.paste(_discord_icon(item["guild"], icon), (ix, cy - icon // 2),
                       _ov_mask(icon, icon, S(11)))
            tx = ix + icon + S(10)
            # a destra: freccia e, a riga chiusa, la pila di avatar
            right = toggle_x = x1 - S(10)
            if item["members"]:
                draw.text((right, cy), ICON_CHEVRON_DOWN if expanded else ICON_CHEVRON_RIGHT,
                          font=_ov_icon_font(S(10)), fill=OV_MUTED, anchor="rm")
                right -= S(20)
                if not expanded:
                    right = self._dc_stack(area, draw, item["members"], right, cy, bg)
                toggle_x = right - S(4)
            color = OV_TEXT if item["members"] or item["here"] else OV_MUTED
            draw.text((tx, cy - S(9)), ICON_VOLUME, font=_ov_icon_font(S(12)), fill=color,
                      anchor="lm")
            name_x = tx + S(18)
            draw.text((name_x, cy - S(9)), _ov_fit(item["name"], title_font, right - S(8) - name_x),
                      font=title_font, fill=color, anchor="lm")
            people = len(item["members"])
            if item["here"]:
                subtitle, sub_color = f"Sei qui · {item['guild']['name']}", OV_CONNECTED
            else:
                subtitle = " · ".join((item["guild"]["name"], _relative_day(item["time"]),
                                       f"{people} dentro" if people else "vuoto"))
                sub_color = OV_MUTED
            draw.text((tx, cy + S(11)), _ov_fit(subtitle, small_font, right - S(8) - tx),
                      font=small_font, fill=sub_color, anchor="lm")
            if expanded:
                my = y + row_h
                for person in item["members"]:
                    self._dc_member(area, draw, person, x0 + S(50), my, x1 - S(12), member_h, bg,
                                    speaking if item["here"] else (), you)
                    my += member_h
            # mouse: la riga entra nel canale, la pila/freccia apre e chiude
            y0, y1 = top + max(0, y), top + min(height, y + row_h)
            if y1 > y0:
                hitboxes.append((x0, y0, toggle_x, y1, lambda i=i: self._dc_click_recent(i)))
                if item["members"]:
                    hitboxes.append((toggle_x, y0, x1, y1,
                                     lambda i=i, cid=item["id"]: self._dc_toggle(i, cid)))
        img.paste(area, (0, top))

    def _render_discord_servers(self, img, top, height, state, hitboxes):
        """Vista Server: colonna dei server a sinistra e canali vocali del
        server scelto, con le persone sempre elencate sotto, come in Discord."""
        S = self._S
        guilds = discord_guilds()
        g = self._discord_guild_index(guilds, state)
        if g < 0:
            self._render_center_message(img, ImageDraw.Draw(img), top, height,
                                        "Nessun canale vocale nei tuoi server")
            return
        on_list = self.discord_focus == "list"

        # colonna dei server
        rail_w, icon, step = S(64), S(44), S(52)
        rail = Image.new("RGB", (rail_w, height), OV_BG)
        rail_draw = ImageDraw.Draw(rail)
        # i preferiti in cima, separati dagli altri da una lineetta come in Discord
        favorites = sum(1 for guild in guilds if guild["favorite"])
        divider = S(12) if 0 < favorites < len(guilds) else 0

        def icon_top(i):
            return S(10) + i * step + (divider if i >= favorites else 0)

        self.discord_rail_scroll = rail_scroll = self._dc_scroll(
            icon_top(g) - S(6), icon_top(g) + icon + S(6), self.discord_rail_scroll,
            height, icon_top(len(guilds)))
        ix = (rail_w - icon) // 2
        if divider:
            dy = icon_top(favorites - 1) + icon + (step - icon + divider) // 2 - rail_scroll
            _ov_round(rail, (rail_w // 2 - S(16), dy - S(1), rail_w // 2 + S(16), dy + S(1)),
                      S(1), OV_BORDER)
        for i, guild in enumerate(guilds):
            iy = icon_top(i) - rail_scroll
            if iy + icon <= 0 or iy >= height:
                continue
            selected = i == g
            if selected:
                # barretta azzurra come la voce selezionata della tab Controlli
                _ov_round(rail, (0, iy + S(12), S(3), iy + icon - S(12)), S(2), OV_ACCENT)
                if self.discord_column == "rail" and on_list:
                    for r, color in ((S(4), OV_ACCENT), (S(2), OV_BG)):
                        _ov_round(rail, (ix - r, iy - r, ix + icon + r, iy + icon + r),
                                  S(16) + r, color)
            rail.paste(_discord_icon(guild, icon), (ix, iy),
                       _ov_mask(icon, icon, S(16) if selected else icon // 2))
            if guild["inside"]:  # qualcuno in vocale: pallino verde con l'altoparlante
                d, ring = S(18), S(3)
                dx, dy = ix + icon - d + S(4), iy + icon - d + S(4)
                _ov_round(rail, (dx - ring, dy - ring, dx + d + ring, dy + d + ring), d / 2 + ring,
                          OV_BG)
                _ov_round(rail, (dx, dy, dx + d, dy + d), d / 2, OV_CONNECTED)
                rail_draw.text((dx + d / 2, dy + d / 2), ICON_VOLUME, font=_ov_icon_font(S(9)),
                               fill=OV_TEXT, anchor="mm")
            y0, y1 = top + max(0, iy), top + min(height, iy + icon)
            if y1 > y0:
                hitboxes.append((ix, y0, ix + icon, y1,
                                 lambda gid=guild["id"]: self._dc_click_guild(gid),
                                 lambda guild=guild: self._discord_toggle_favorite(guild)))
        img.paste(rail, (0, top))
        img.paste(OV_BORDER, (rail_w - max(1, S(1)), top + S(8), rail_w, top + height - S(8)))

        # canali del server scelto
        guild = guilds[g]
        width = img.width - rail_w
        draw = ImageDraw.Draw(img)
        head_h = S(44)
        cy = top + head_h // 2
        small_font = _ov_font(S(12))
        right = img.width - S(14)
        if guild["inside"]:
            text = f"{guild['inside']} in vocale"
            draw.text((right, cy), text, font=small_font, fill=OV_MUTED, anchor="rm")
            right -= small_font.getlength(text) + S(10)
        head_font = _ov_font(S(15), "Semibold Text")
        draw.text((rail_w + S(14), cy), _ov_fit(guild["name"], head_font, right - rail_w - S(14)),
                  font=head_font, fill=OV_TEXT, anchor="lm")
        draw.rectangle((rail_w, top + head_h, img.width, top + head_h + max(1, S(1)) - 1),
                       fill=OV_BORDER)
        list_top = top + head_h + max(1, S(1))
        list_h = height - (list_top - top)

        channels = discord_guild_channels(guild["id"])
        index = self.discord_channel = min(self.discord_channel, max(0, len(channels) - 1))
        active = self.discord_column == "channels" and on_list
        label_h, row_h, member_h, gap = S(30), S(38), S(30), S(2)
        tops, heights, y = [], [], label_h
        for channel in channels:
            h = row_h + len(channel["members"]) * member_h
            tops.append(y)
            heights.append(h)
            y += h + gap
        total = y
        scroll = self.discord_scroll
        if active and channels:
            # basta che si veda la riga del canale, le persone possono restare sotto
            scroll = self._dc_scroll(tops[index] if index else 0, tops[index] + row_h, scroll,
                                     list_h, total)
        self.discord_scroll = scroll = max(0, min(scroll, total - list_h))

        area = Image.new("RGB", (width, list_h), OV_BG)
        adraw = ImageDraw.Draw(area)
        adraw.text((S(14), label_h - S(10) - scroll), "CANALI VOCALI",
                   font=_ov_font(S(11.5), "Semibold Text"), fill=OV_MUTED, anchor="lm")
        name_font = _ov_font(S(14.5))
        speaking = discord_speaking()
        you = (state.get("user") or {}).get("id")
        x0, x1 = S(6), width - S(8)
        for i, channel in enumerate(channels):
            y, h = tops[i] - scroll, heights[i]
            if y + h <= 0 or y >= list_h:
                continue
            selected = active and i == index
            if selected:
                _ov_round(area, (x0, y, x1, y + row_h), S(8), OV_SELECTED)
            cy = y + row_h // 2
            full = bool(channel["members"])
            color = OV_TEXT if selected else (OV_TEXT if full else OV_MUTED)
            adraw.text((x0 + S(12), cy), ICON_VOLUME, font=_ov_icon_font(S(14)),
                       fill=OV_TEXT if full or selected else OV_SUBTLE, anchor="lm")
            right = x1 - S(10)
            if channel["here"]:
                adraw.text((right, cy), "SEI QUI", font=_ov_font(S(11), "Bold Text"),
                           fill=OV_CONNECTED, anchor="rm")
                right -= S(56)
            elif selected:
                hint_font = _ov_font(S(12))
                adraw.text((right, cy), "Entra", font=hint_font, fill=OV_TEXT, anchor="rm")
                right -= hint_font.getlength("Entra") + S(6) + self._pad_button_width("A")
                self._pad_button(area, adraw, right, cy, "A")
                right -= S(6)
            nx = x0 + S(36)
            adraw.text((nx, cy), _ov_fit(channel["name"], name_font, right - nx), font=name_font,
                       fill=color, anchor="lm")
            my = y + row_h
            for person in channel["members"]:
                self._dc_member(area, adraw, person, x0 + S(36), my, x1 - S(10), member_h, OV_BG,
                                speaking if channel["here"] else (), you)
                my += member_h
            y0, y1 = list_top + max(0, y), list_top + min(list_h, y + row_h)
            if y1 > y0:
                hitboxes.append((rail_w + x0, y0, rail_w + x1, y1,
                                 lambda i=i: self._dc_click_channel(i)))
        img.paste(area, (rail_w, list_top))

    def _render_discord_bar(self, img, draw, y, h, state, hitboxes):
        """Scheda in basso (come la barra del brano di Spotify): avatar e nome,
        "Connesso in <canale>", muto (L3), silenzia, schermo ed esci."""
        S = self._S
        x0, x1 = S(14), img.width - S(14)
        _ov_round(img, (x0, y, x1, y + h), S(8), OV_SURFACE)
        cy = y + h // 2
        user = state.get("user") or {"name": "Tu", "initials": "TU", "image_url": None, "id": ""}
        current = state["current"]
        size, x = S(34), x0 + S(8)
        img.paste(_discord_avatar(user, size), (x, cy - size // 2), _ov_mask(size, size, size // 2))
        if current:  # pallino verde: in vocale
            d, ring = S(11), S(3)
            dx, dy = x + size - d + S(2), cy + size // 2 - d + S(2)
            _ov_round(img, (dx - ring, dy - ring, dx + d + ring, dy + d + ring), d / 2 + ring,
                      OV_SURFACE)
            _ov_round(img, (dx, dy, dx + d, dy + d), d / 2, OV_CONNECTED)

        buttons = self._discord_commands(state)
        bsize, gap = S(36), S(6)
        bx = x1 - S(8) - len(buttons) * bsize - (len(buttons) - 1) * gap
        tx = x + size + S(10)
        tw = bx - S(10) - tx
        name_font = _ov_font(S(13), "Semibold Text")
        small_font = _ov_font(S(11.5))
        draw.text((tx, cy - S(8)), _ov_fit(user["name"], name_font, tw), font=name_font,
                  fill=OV_TEXT, anchor="lm")
        status = get_discord_status()
        if status:  # per pochi secondi: entrata in corso o errore
            subtitle, color = status[0], OV_DANGER if status[1] else OV_MUTED
        elif current:
            subtitle, color = f"Connesso in {current['name']}", OV_CONNECTED
        else:
            subtitle, color = "Non connesso", OV_MUTED
        draw.text((tx, cy + S(9)), _ov_fit(subtitle, small_font, tw), font=small_font,
                  fill=color, anchor="lm")

        icon_font = _ov_icon_font(S(15))
        for i, button in enumerate(buttons):
            box = (bx, cy - bsize / 2, bx + bsize, cy + bsize / 2)
            if self.discord_focus == "bar" and i == self.discord_command:
                for r, ring_color in ((S(4), OV_ACCENT), (S(2), OV_SURFACE)):
                    _ov_round(img, (bx - r, cy - bsize / 2 - r, bx + bsize + r, cy + bsize / 2 + r),
                              bsize / 2 + r, ring_color)
            if button["label"] == "Esci" and button["enabled"]:
                fill, color = OV_DANGER, OV_TEXT
            elif button["on"]:  # muto / silenziato attivi: icona rossa
                fill, color = OV_BG, OV_DANGER
            else:
                fill, color = OV_BG, OV_TEXT if button["enabled"] else OV_SUBTLE
            _ov_round(img, box, bsize / 2, fill)
            draw.text((bx + bsize / 2, cy), button["icon"], font=icon_font, fill=color, anchor="mm")
            if i == 0:  # il muto si attiva anche con L3, da qualsiasi tab
                key_font = _ov_font(S(9), "Semibold Text")
                kw, kh = key_font.getlength("L3") + S(8), S(14)
                kx, ky = bx + bsize - kw + S(6), cy + bsize / 2 - kh + S(5)
                _ov_round(img, (kx - S(2), ky - S(2), kx + kw + S(2), ky + kh + S(2)), S(6),
                          OV_SURFACE)
                _ov_round(img, (kx, ky, kx + kw, ky + kh), S(4), OV_BORDER)
                draw.text((kx + kw / 2, ky + kh / 2), "L3", font=key_font, fill=OV_TEXT,
                          anchor="mm")
            hitboxes.append((*box, lambda i=i: self._dc_click_button(i)))
            bx += bsize + gap

    # --- mouse nella tab Discord ---

    def _dc_click_recent(self, i):
        self.discord_focus = "list"
        self.discord_recent = i
        self._redraw()
        self._discord_command("a")

    def _dc_toggle(self, i, channel_id):
        self.discord_focus = "list"
        self.discord_recent = i
        self.discord_expanded ^= {channel_id}
        self._redraw()

    def _dc_click_guild(self, guild_id):
        self.discord_focus = "list"
        self.discord_column = "rail"
        self._discord_select_guild(guild_id)
        self._redraw()

    def _dc_click_channel(self, i):
        self.discord_focus = "list"
        self.discord_column = "channels"
        self.discord_channel = i
        self._redraw()
        self._discord_command("a")

    def _dc_click_button(self, i):
        self.discord_focus = "bar"
        self.discord_command = i
        self._redraw()
        self._discord_command("a")

    def _render_filter(self, img, draw, cy, filters, current, on_select, hint, hitboxes):
        """Selettore a pillole (es. Recenti / Playlist) a sinistra e suggerimento
        dei tasti a destra. hint = (pulsanti del pad, testo)."""
        S = self._S
        pad = S(14)
        font = _ov_font(S(12.5), "Semibold Text")
        h, inset = S(30), S(3)
        widths = [font.getlength(label) + S(32) for _, label in filters]
        x = pad
        _ov_round(img, (x, cy - h / 2, x + sum(widths) + 2 * inset, cy + h / 2), h / 2, OV_SEGMENT_BG)
        x += inset
        for i, ((_, label), w) in enumerate(zip(filters, widths)):
            box = (x, cy - h / 2 + inset, x + w, cy + h / 2 - inset)
            if i == current:
                _ov_round(img, box, (h - 2 * inset) / 2, OV_ACCENT)
            draw.text((x + w / 2, cy), label, font=font,
                      fill=OV_BG if i == current else OV_MUTED, anchor="mm")
            hitboxes.append((*box, lambda i=i: on_select(i)))
            x += w
        buttons, text = hint
        hint_font = _ov_font(S(12))
        right = img.width - pad - S(4)
        draw.text((right, cy), text, font=hint_font, fill=OV_MUTED, anchor="rm")
        x = right - hint_font.getlength(text) - S(6) \
            - sum(self._pad_button_width(b) for b in buttons) - S(3) * (len(buttons) - 1)
        for button in buttons:
            x = self._pad_button(img, draw, x, cy, button) + S(3)

    def _render_list(self, img, top, height, items, index, scroll, image_for, badge_for,
                     on_click, hitboxes):
        """Righe grandi (immagine, titolo, sottotitolo) con scorrimento, nella
        fascia [top, top + height). index None = nessuna riga selezionata.
        image_for(elemento, lato) da' l'immagine, badge_for(elemento) il glifo
        del pulsante tondo sulla riga selezionata (o None). Gli elementi con
        "here" hanno il sottotitolo verde. Ritorna il nuovo scorrimento."""
        S = self._S
        width, pad = img.width, S(14)
        row_h, gap, cover = S(66), S(2), S(52)
        if index is not None:
            # la riga selezionata deve essere tutta visibile
            row_top = index * (row_h + gap)
            if row_top < scroll:
                scroll = row_top
            elif row_top + row_h > scroll + height:
                scroll = row_top + row_h - height

        # su un'immagine a parte, cosi' le righe parziali vengono tagliate
        area = Image.new("RGB", (width, height), OV_BG)
        draw = ImageDraw.Draw(area)
        title_font = _ov_font(S(15), "Semibold Text")
        subtitle_font = _ov_font(S(12.5))
        first = scroll // (row_h + gap)
        last = min(len(items), (scroll + height) // (row_h + gap) + 1)
        for i in range(first, last):
            item = items[i]
            y = i * (row_h + gap) - scroll
            selected = i == index
            if selected:
                _ov_round(area, (pad, y, width - pad, y + row_h), S(8), OV_SELECTED)
            cx, cy = pad + S(8), y + (row_h - cover) // 2
            area.paste(image_for(item, cover), (cx, cy), _ov_mask(cover, cover, S(5)))
            badge = badge_for(item) if selected else None
            tx = cx + cover + S(14)
            tw = width - pad - S(12) - tx - (S(44) if badge else 0)
            draw.text((tx, y + row_h // 2 - S(10)), _ov_fit(item["name"], title_font, tw),
                      font=title_font, fill=OV_TEXT, anchor="lm")
            draw.text((tx, y + row_h // 2 + S(12)), _ov_fit(item["subtitle"], subtitle_font, tw),
                      font=subtitle_font, fill=OV_PAD_COLORS["A"] if item.get("here") else OV_MUTED,
                      anchor="lm")
            if badge:
                px, py = width - pad - S(28), y + row_h // 2
                _ov_round(area, (px - S(16), py - S(16), px + S(16), py + S(16)), S(16), OV_ACCENT)
                # il triangolo del play sembra centrato solo spostato un po' a destra
                draw.text((px + (S(1) if badge == ICON_PLAY else 0), py), badge,
                          font=_ov_icon_font(S(13)), fill=OV_BG, anchor="mm")
            if y + row_h > 0 and y < height:
                hitboxes.append((pad, top + max(0, y), width - pad, top + min(height, y + row_h),
                                 lambda i=i: on_click(i)))
        img.paste(area, (0, top))
        return scroll

    def _render_now_playing(self, img, draw, y, hitboxes):
        """Barra compatta con il brano in corso (stessa fonte della scheda media)."""
        S = self._S
        width, pad = img.width, S(14)
        x0, x1, h = pad, width - pad, S(46)
        _ov_round(img, (x0, y, x1, y + h), S(8), OV_SURFACE)
        media = get_media_state()
        if media is None:
            draw.text((x0 + S(24), y + h // 2), ICON_MUSIC, font=_ov_icon_font(S(15)),
                      fill=OV_SUBTLE, anchor="mm")
            draw.text((x0 + S(46), y + h // 2), "Niente in riproduzione", font=_ov_font(S(13)),
                      fill=OV_MUTED, anchor="lm")
            return
        size = h - S(12)
        cover = self._cover(media["cover"], size)
        if cover is not None:
            img.paste(cover, (x0 + S(6), y + S(6)), _ov_mask(size, size, S(4)))
        else:
            img.paste(_spotify_placeholder(size, size), (x0 + S(6), y + S(6)),
                      _ov_mask(size, size, S(4)))
        hitboxes.append((x0, y, x1, y + h, lambda: media_command("play_pause")))
        play_x = x1 - S(24)
        _ov_round(img, (play_x - S(14), y + h // 2 - S(14), play_x + S(14), y + h // 2 + S(14)),
                  S(14), OV_ACCENT)
        draw.text((play_x, y + h // 2), ICON_PAUSE if media["playing"] else ICON_PLAY,
                  font=_ov_icon_font(S(12)), fill=OV_BG, anchor="mm")
        text_right = play_x - S(22)
        if isinstance(get_spotify_state(), dict):
            text_right = self._render_shuffle(img, draw, play_x - S(34), y + h // 2, hitboxes,
                                              with_key=True) - S(8)

        tx = x0 + S(6) + size + S(10)
        tw = text_right - tx
        draw.text((tx, y + S(16)), _ov_fit(media["title"], _ov_font(S(13), "Semibold Text"), tw),
                  font=_ov_font(S(13), "Semibold Text"), fill=OV_TEXT, anchor="lm")
        subtitle = " · ".join(p for p in (media["artist"], media["app"]) if p)
        draw.text((tx, y + S(32)), _ov_fit(subtitle, _ov_font(S(11)), tw),
                  font=_ov_font(S(11)), fill=OV_MUTED, anchor="lm")

    def _render_shuffle(self, img, draw, cx, cy, hitboxes, with_key=False):
        """Icona della riproduzione casuale di Spotify centrata in cx: azzurra
        con un puntino sotto se attiva. Ritorna la x del bordo sinistro."""
        S = self._S
        active = bool(_spotify_shuffle)
        draw.text((cx, cy - (S(2) if active else 0)), ICON_SHUFFLE, font=_ov_icon_font(S(15)),
                  fill=OV_ACCENT if active else OV_MUTED, anchor="mm")
        if active:
            _ov_round(img, (cx - S(2), cy + S(9), cx + S(2), cy + S(13)), S(2), OV_ACCENT)
        left = cx - S(12)
        if with_key:
            font = _ov_font(S(10), "Semibold Text")
            w = font.getlength("R3") + S(10)
            _ov_round(img, (left - w, cy - S(8), left, cy + S(8)), S(4), OV_BORDER)
            draw.text((left - w / 2, cy), "R3", font=font, fill=OV_TEXT, anchor="mm")
            left -= w
        hitboxes.append((left, cy - S(16), cx + S(12), cy + S(16), spotify_toggle_shuffle))
        return left

    def _render_media(self, img, draw, y, pad, width, hitboxes, S):
        index = len(OVERLAY_ITEMS) - 1
        media = get_media_state()
        x0, x1 = pad, width - pad
        if media is None:
            h = S(48)
            _ov_round(img, (x0, y, x1, y + h), S(8), OV_SURFACE)
            draw.text((x0 + S(24), y + h // 2), ICON_MUSIC, font=_ov_icon_font(S(16)),
                      fill=OV_SUBTLE, anchor="mm")
            draw.text((x0 + S(46), y + h // 2), "Niente in riproduzione",
                      font=_ov_font(S(13)), fill=OV_MUTED, anchor="lm")
            return y + h

        h = S(100)
        if index == self.index:
            _ov_round(img, (x0 - S(2), y - S(2), x1 + S(2), y + h + S(2)), S(10), OV_ACCENT)
        _ov_round(img, (x0, y, x1, y + h), S(8), OV_SURFACE)

        size = h - S(24)
        cover_x, cover_y = x0 + S(12), y + S(12)
        cover = self._cover(media["cover"], size)
        if cover is not None:
            img.paste(cover, (cover_x, cover_y), _ov_mask(size, size, S(6)))
        else:
            _ov_round(img, (cover_x, cover_y, cover_x + size, cover_y + size), S(6), OV_SELECTED)
            draw.text((cover_x + size // 2, cover_y + size // 2), ICON_MUSIC,
                      font=_ov_icon_font(S(28)), fill=OV_MUTED, anchor="mm")

        tx = cover_x + size + S(14)
        tw = x1 - S(12) - tx
        title_font = _ov_font(S(14), "Semibold Text")
        draw.text((tx, y + S(22)), _ov_fit(media["title"], title_font, tw),
                  font=title_font, fill=OV_TEXT, anchor="lm")
        subtitle = " · ".join(p for p in (media["artist"], media["app"]) if p)
        small_font = _ov_font(S(12))
        draw.text((tx, y + S(41)), _ov_fit(subtitle, small_font, tw),
                  font=small_font, fill=OV_MUTED, anchor="lm")

        # comandi: precedente, play/pausa, successivo
        by = y + S(74)
        control_font = _ov_icon_font(S(14))
        draw.text((tx + S(12), by), ICON_PREV, font=control_font, fill=OV_TEXT, anchor="mm")
        play_x = tx + S(50)
        _ov_round(img, (play_x - S(16), by - S(16), play_x + S(16), by + S(16)), S(16), OV_ACCENT)
        draw.text((play_x, by), ICON_PAUSE if media["playing"] else ICON_PLAY,
                  font=control_font, fill=OV_BG, anchor="mm")
        draw.text((play_x + S(38), by), ICON_NEXT, font=control_font, fill=OV_TEXT, anchor="mm")

        hitboxes.append((x0, y, x1, y + h, lambda: (self._select(index), self._confirm())))
        if isinstance(get_spotify_state(), dict):
            self._render_shuffle(img, draw, play_x + S(78), by, hitboxes)
        return y + h

    def _cover(self, cover, size):
        """Copertina ritagliata al centro e ridotta; tiene in cache l'ultima."""
        if cover is None:
            return None
        source, cached_size, cached = self._cover_cache
        if source is cover and cached_size == size:
            return cached
        result = _ov_crop_to(cover, size, size)
        self._cover_cache = (cover, size, result)
        return result

    def _keycap(self, img, draw, right, cy, text):
        s = self.scale
        font = _ov_font(round(11 * s), "Semibold Text")
        w, h = font.getlength(text) + round(14 * s), round(20 * s)
        box = (right - w, cy - h / 2, right, cy + h / 2)
        _ov_round(img, box, round(4 * s), OV_BORDER)
        _ov_round(img, (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1), round(4 * s), OV_SURFACE)
        draw.text((right - w / 2, cy), text, font=font, fill=OV_MUTED, anchor="mm")

    def _switch(self, img, draw, right, cy, on, selected):
        # interruttore in stile Windows 11: pieno quando e' acceso
        s = self.scale
        w, h = round(40 * s), round(20 * s)
        box = (right - w, cy - h // 2, right, cy + h // 2)
        knob = round(6 * s)
        if on:
            _ov_round(img, box, h // 2, OV_ACCENT)
            kx, color = box[2] - h // 2, OV_BG
        else:
            _ov_round(img, box, h // 2, OV_MUTED)
            inner = OV_SELECTED if selected else OV_BG
            _ov_round(img, (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1), h // 2 - 1, inner)
            kx, color = box[0] + h // 2, OV_MUTED
        _ov_round(img, (kx - knob, cy - knob, kx + knob, cy + knob), knob, color)

    def _pad_button(self, img, draw, x, cy, name):
        """Disegna un pulsante del pad (legenda, tab); ritorna la x finale."""
        s = self.scale
        if name in OV_PAD_COLORS:
            d = round(18 * s)
            _ov_round(img, (x, cy - d / 2, x + d, cy + d / 2), d // 2, OV_PAD_COLORS[name])
            draw.text((x + d / 2, cy), name, font=_ov_font(round(11 * s), "Bold Text"),
                      fill=OV_BG, anchor="mm")
            return x + d
        if name in PAD_ARROWS:  # sinistra/destra del D-pad: tasto quadrato con la freccia
            d = round(18 * s)
            _ov_round(img, (x, cy - d / 2, x + d, cy + d / 2), round(5 * s), OV_BORDER)
            draw.text((x + d / 2, cy), PAD_ARROWS[name], font=_ov_icon_font(round(9 * s)),
                      fill=OV_TEXT, anchor="mm")
            return x + d
        font = _ov_font(round(10 * s), "Semibold Text")
        w, h = font.getlength(name) + round(12 * s), round(18 * s)
        _ov_round(img, (x, cy - h / 2, x + w, cy + h / 2), round(5 * s), OV_BORDER)
        draw.text((x + w / 2, cy), name, font=font, fill=OV_TEXT, anchor="mm")
        return x + w

    def _pad_button_width(self, name):
        s = self.scale
        if name in OV_PAD_COLORS or name in PAD_ARROWS:
            return round(18 * s)
        return _ov_font(round(10 * s), "Semibold Text").getlength(name) + round(12 * s)


def _overlay_loop():
    if VOLUME_SUPPORT:
        comtypes.CoInitialize()  # il volume usa COM da questo thread
    try:
        overlay = _Overlay()
    except Exception as e:
        print(f"[AppUtili] Impossibile creare l'overlay: {e}")
        return
    overlay.root.mainloop()
    # Gli oggetti Tk vanno liberati da questo thread: gli hitbox (lambda che
    # puntano all'overlay) creano riferimenti circolari che altrimenti
    # verrebbero raccolti piu' tardi dal thread principale, e Tcl lo vieta
    overlay.hitboxes = []
    overlay.photo = None
    del overlay
    gc.collect()


def start_overlay_thread():
    global _overlay_thread
    if not OVERLAY_SUPPORT:
        print("[AppUtili] tkinter non disponibile: overlay disattivato.")
        return
    _overlay_thread = threading.Thread(target=_overlay_loop, daemon=True)
    _overlay_thread.start()
    start_media_thread()


def stop_overlay_thread():
    _overlay_queue.put("quit")
    if _overlay_thread is not None:
        _overlay_thread.join(timeout=1)


def toggle_overlay(icon=None, item=None):
    _overlay_queue.put("toggle")


# ---------------------------------------------------------------------------
# AVVIO CON WINDOWS
# ---------------------------------------------------------------------------
#
# Niente cartella "Esecuzione automatica" ne' chiave di registro Run: Windows
# salta in silenzio all'accesso i programmi che chiedono i privilegi di
# amministratore. Usiamo invece un'attivita' pianificata "all'accesso" con
# "Esegui con i privilegi piu' elevati", che parte gia' elevata senza UAC.

STARTUP_TASK_NAME = APP_NAME
CREATE_NO_WINDOW = 0x08000000

_startup_enabled = False  # cache: evita di interrogare schtasks a ogni menu


def _schtasks(*args):
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )


def _startup_command():
    """(eseguibile, argomenti) da lanciare all'accesso."""
    if getattr(sys, "frozen", False):
        # exe compilato con PyInstaller. Ha UIAccess (vedi certificato.ps1),
        # e Windows lo concede solo ai programmi avviati tramite la shell
        # (ShellExecute): l'Utilita' di pianificazione li avvia con
        # CreateProcess, che fallisce con ERROR_ELEVATION_REQUIRED. Quindi si
        # passa da ShellExec_RunDLL, che non apre finestre; l'attivita' e' gia'
        # elevata, percio' non compare nemmeno la richiesta UAC.
        rundll32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "rundll32.exe"
        return str(rundll32), f'shell32.dll,ShellExec_RunDLL "{sys.executable}"'
    # da sorgente: pythonw.exe per non aprire la finestra della console
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return str(exe), f'"{Path(__file__).resolve()}"'


def _startup_task_xml():
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    command, arguments = _startup_command()
    workdir = str(Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent)
    # ExecutionTimeLimit PT0S: senza, Windows chiuderebbe l'app dopo 72 ore.
    # Delay: qualche secondo per lasciar partire tray e driver del controller.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Avvia {APP_NAME} all'accesso a Windows</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{xml_escape(user)}</UserId>
      <Delay>PT10S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{xml_escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml_escape(command)}</Command>
      <Arguments>{xml_escape(arguments)}</Arguments>
      <WorkingDirectory>{xml_escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def is_startup_task_installed():
    try:
        return _schtasks("/Query", "/TN", STARTUP_TASK_NAME).returncode == 0
    except Exception:
        return False


def _enable_startup():
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        with open(xml_path, "w", encoding="utf-16") as f:
            f.write(_startup_task_xml())
        result = _schtasks("/Create", "/TN", STARTUP_TASK_NAME, "/XML", xml_path, "/F")
    finally:
        os.remove(xml_path)
    if result.returncode != 0:
        print(f"[AppUtili] Errore creazione attivita' di avvio: {result.stderr.strip()}")
    return result.returncode == 0


def _disable_startup():
    result = _schtasks("/Delete", "/TN", STARTUP_TASK_NAME, "/F")
    if result.returncode != 0:
        print(f"[AppUtili] Errore rimozione attivita' di avvio: {result.stderr.strip()}")
    return result.returncode == 0


def toggle_startup(icon, item):
    global _startup_enabled
    if _startup_enabled:
        if _disable_startup():
            _startup_enabled = False
            notify("Avvio con Windows disattivato")
        else:
            notify("⚠ Impossibile disattivare l'avvio con Windows")
    else:
        if _enable_startup():
            _startup_enabled = True
            notify("✔ Avvio con Windows attivato")
        else:
            notify("⚠ Impossibile attivare l'avvio con Windows "
                   "(serve avviare l'app come amministratore)")


def startup_checked(item):
    return _startup_enabled


# ---------------------------------------------------------------------------
# TRAY ICON / MENU
# ---------------------------------------------------------------------------


# Senza profili monitor (o per l'icona dell'exe): blu con la "M" di sempre
ICON_DEFAULT_STYLE = (MONITOR_COLORS[0], "M")
BADGE_ON = (16, 185, 80)    # pallino verde: controller attivo
BADGE_OFF = (140, 140, 140)  # pallino grigio: controller disattivato


def _load_icon_font(size):
    # Segoe UI e' il font di sistema di Windows: c'e' sempre
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def create_image(style=None, controller_enabled=True, size=64, badge=True):
    """Icona generata al volo: style = (colore, testo) del profilo monitor
    attuale (monitor_style), il pallino in basso a destra lo stato del
    controller. Le misure sono pensate su 64px e scalate per le altre dimensioni."""
    s = size / 64
    bg, text = style or ICON_DEFAULT_STYLE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((2 * s, 2 * s, size - 2 * s, size - 2 * s),
                           radius=14 * s, fill=bg + (255,))

    font = _load_icon_font(round((34 if len(text) == 1 else 28) * s))
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1] - 3 * s), text,
               fill=(255, 255, 255, 255), font=font)

    if badge and CONTROLLER_SUPPORT:
        color = BADGE_ON if controller_enabled else BADGE_OFF
        draw.ellipse((40 * s, 40 * s, 62 * s, 62 * s), fill=color + (255,),
                     outline=(255, 255, 255, 255), width=round(3 * s))
    return img


def save_app_ico(path):
    """Icona dell'exe (usata da build.bat): senza pallino del controller,
    che li' non avrebbe significato."""
    create_image(size=256, badge=False).save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )


def _tray_style():
    """Aspetto dell'icona: il profilo monitor caricato, se ci sono profili."""
    profile = monitor_current() if monitor_profiles() else ""
    return monitor_style(profile) if profile else None


def _tray_title():
    with config_lock:
        controller = config["controller_enabled"]
    profile = monitor_current() if monitor_profiles() else ""
    parts = [APP_NAME] + ([f"Monitor: {profile}"] if profile else [])
    if CONTROLLER_SUPPORT:
        parts.append(f"Controller: {'attivo' if controller else 'disattivato'}")
    return "\n".join(parts)


def refresh_tray_icon():
    if _tray_icon is None:
        return
    with config_lock:
        controller = config["controller_enabled"]
    try:
        _tray_icon.icon = create_image(_tray_style(), controller)
        _tray_icon.title = _tray_title()
        # su Windows il menu si ricostruisce da solo solo dopo un clic su
        # una sua voce: se il cambio arriva da controller/hotkey va forzato
        _tray_icon.update_menu()
    except Exception as e:
        print(f"[AppUtili] Errore aggiornamento icona: {e}")


def enable_dark_menus():
    """Fa seguire ai menu del tray il tema chiaro/scuro di Windows.
    Usa due funzioni non documentate di uxtheme.dll (le stesse usate da
    Esplora file): se non ci sono, i menu restano semplicemente chiari."""
    try:
        uxtheme = ctypes.WinDLL("uxtheme")
        set_preferred_app_mode = uxtheme[135]
        flush_menu_themes = uxtheme[136]
        set_preferred_app_mode(1)  # 1 = AllowDark: segue il tema di sistema
        flush_menu_themes()
    except Exception:
        pass


def _spotify_menu_status(item):
    if not spotify_client_id():
        return "Non configurato"
    if _spotify_connecting.is_set():
        return "Collegamento in corso…"
    return "Collegato" if spotify_connected() else "Non collegato"


def build_spotify_menu():
    # la finestra di configurazione e' Tk: la apre il thread dell'overlay
    return pystray.Menu(
        pystray.MenuItem(_spotify_menu_status, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Configura…", lambda icon, item: _overlay_queue.put("spotify_setup")),
        pystray.MenuItem("Collega account", lambda icon, item: spotify_connect(),
                          enabled=lambda item: bool(spotify_client_id())
                          and not _spotify_connecting.is_set()),
        pystray.MenuItem("Scollega", spotify_disconnect,
                          enabled=lambda item: spotify_connected()),
    )


def _discord_menu_status(item):
    if not discord_client_id() or not discord_client_secret():
        return "Non configurato"
    if _discord_connecting.is_set():
        return "Collegamento in corso…"
    state = _discord_state
    if state == "ready":
        return "Collegato"
    if state == "not_running":
        return "Discord non è aperto"
    if state == "not_connected":
        return "Non collegato"
    return "Errore di collegamento" if isinstance(state, str) else "Connessione…"


def build_discord_menu():
    return pystray.Menu(
        pystray.MenuItem(_discord_menu_status, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Configura…", lambda icon, item: _overlay_queue.put("discord_setup")),
        pystray.MenuItem("Collega account", lambda icon, item: discord_connect(),
                          enabled=lambda item: bool(discord_client_id())
                          and not _discord_connecting.is_set()),
        pystray.MenuItem("Scollega", discord_disconnect,
                          enabled=lambda item: _discord_load_token() is not None),
    )


def _monitor_menu_action(name):
    return lambda icon, item: switch_monitor(name)


def _monitor_menu_checked(name):
    return lambda item: monitor_current() == name


def _monitor_menu_items():
    """Sottomenu Monitor: i profili trovati, con le scorciatoie dei ruoli."""
    profiles = monitor_profiles()
    if not profiles:
        return [pystray.MenuItem("Nessun profilo: vedi Impostazioni → Info", None, enabled=False)]
    desk, tv = monitor_role("desk"), monitor_role("tv")
    return [pystray.MenuItem(name + ("  (Ctrl+Alt+↓)" if name == desk else
                                     "  (Ctrl+Alt+↑)" if name == tv else ""),
                             _monitor_menu_action(name), checked=_monitor_menu_checked(name),
                             radio=True)
            for name in profiles]


def build_menu():
    return pystray.Menu(
        pystray.MenuItem("🖥  Monitor Setup", pystray.Menu(_monitor_menu_items)),
        pystray.MenuItem(
            "🏠  Mappa tasto Home",
            pystray.Menu(
                pystray.MenuItem("Windows", set_home_mode("windows"),
                                  checked=home_mode_checked("windows"), radio=True),
                pystray.MenuItem("Alt+Tab", set_home_mode("alttab"),
                                  checked=home_mode_checked("alttab"), radio=True),
                pystray.MenuItem("Nessuno", set_home_mode("none"),
                                  checked=home_mode_checked("none"), radio=True),
            ),
        ),
        pystray.MenuItem("🎮  Controller", build_controller_menu()),
        # default: il clic sinistro sull'icona apre l'overlay
        pystray.MenuItem(f"☰  Overlay  ({HOTKEY_OVERLAY_LABEL})", toggle_overlay,
                          enabled=OVERLAY_SUPPORT, default=True),
        pystray.MenuItem("🎵  Spotify", build_spotify_menu()),
        pystray.MenuItem("🎧  Discord", build_discord_menu()),
        pystray.MenuItem("🔔  Notifiche", toggle_notifications,
                          checked=notifications_checked),
        pystray.MenuItem("🚀  Avvia con Windows", toggle_startup,
                          checked=startup_checked),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("✖  Esci", on_quit),
    )


def on_quit(icon, item):
    try:
        keyboard.unhook_all()
    except Exception:
        pass
    stop_home_hotkey_thread()
    stop_controller_thread()
    stop_overlay_thread()
    stop_discord_thread()
    icon.stop()


def main():
    if os.name != "nt":
        print("Questa applicazione funziona solo su Windows.")
        sys.exit(1)

    global _tray_icon, _startup_enabled

    # Usato da installa.bat: registra l'avvio con Windows ed esce subito
    if "--abilita-avvio" in sys.argv:
        sys.exit(0 if _enable_startup() else 1)

    # DPI aware per monitor: l'overlay resta nitido e si scala da solo sulla TV
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    enable_dark_menus()
    _startup_enabled = is_startup_task_installed()

    with config_lock:
        controller = config["controller_enabled"]
    _tray_icon = pystray.Icon(APP_NAME, create_image(_tray_style(), controller),
                              _tray_title(), menu=build_menu())

    start_home_hotkey_thread()
    start_controller_thread()
    start_overlay_thread()
    start_discord_thread()
    start_monitor_search()
    register_monitor_hotkeys()

    _tray_icon.run()


if __name__ == "__main__":
    main()
