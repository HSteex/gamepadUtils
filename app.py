"""
AppUtili - Utility tray per Windows
------------------------------------
Gruppi disponibili:
  1) Monitor Setup: lancia MonitorSwitcher.exe con il profilo Main o TV.
     Hotkey globali: Ctrl+Alt+Giu -> Main, Ctrl+Alt+Su -> TV
  2) Mappa tasto Home: intercetta il tasto multimediale "Browser Home"
     tramite RegisterHotKey (Windows) e lo rimappa su Windows, Alt+Tab
     oppure lo lascia disattivato (Nessuno = comportamento originale).
  3) Controller: toggle Attivo/Disattivo + due combinazioni fisse:
       Back + LB + D-pad Su  -> Monitor TV + apre Steam Big Picture
       Back + LB + D-pad Giu -> chiude Steam Big Picture + Monitor Main
       Back + click stick sx + click stick dx -> Win+D (mostra desktop)
       Back + LB + D-pad Sinistra/Destra -> Ctrl+è / Ctrl++ (cambio
       dispositivo audio, mappato in SteelSeries GG)
       Back + Start + LB + RB + LT + RT -> Alt+F4 (chiude la finestra attiva)
       Back + RB (al rilascio) -> overlay in alto a sinistra con tre tab
       (LT/RT per cambiare): "Controlli" con le azioni del tray, uscita
       audio di SteelSeries Sonar, volume, alimentazione e il brano in
       riproduzione; "Giochi" con la griglia dei giochi Steam (anche
       non-Steam aggiunti a Steam) dal piu' recente; "Spotify" con brani
       recenti e playlist dell'account collegato. D-pad/stick per
       navigare, A conferma/avvia, B chiude, LB/RB/X brani
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
import os
import queue
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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

MONITOR_SWITCHER_EXE = r"D:\AppUtili\MonitorProfileSwitcher\MonitorSwitcher.exe"
MONITOR_PROFILES_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "MonitorSwitcher" / "Profiles"
PROFILE_MAIN = str(MONITOR_PROFILES_DIR / "Main Setup.xml")
PROFILE_TV = str(MONITOR_PROFILES_DIR / "TV.xml")

HOTKEY_MAIN = "ctrl+alt+down"
HOTKEY_TV = "ctrl+alt+up"

# Combinazioni di pulsanti del controller, secondo la mappatura standard SDL
# "GameController" (la stessa numerazione che stampa rileva_controller.py):
#   4 = Back/Select, 7 = click stick sinistro, 8 = click stick destro,
#   9 = LB, 11 = D-pad Su, 12 = D-pad Giu, 13 = D-pad Sinistra,
#   14 = D-pad Destra
# Vale per qualsiasi marca (Xbox, PlayStation, 8BitDo, ecc.) perche' SDL
# traduce sempre sullo stesso schema.
COMBO_TV_BIGPICTURE = frozenset({4, 9, 11})
COMBO_MAIN_EXIT_BIGPICTURE = frozenset({4, 9, 12})
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
BUTTON_RIGHTSTICK = 8  # clic della levetta destra (R3)
BUTTON_BACK = 4
BUTTON_LB = 9
BUTTON_RB = 10
BUTTON_DPAD_UP = 11
BUTTON_DPAD_DOWN = 12
BUTTON_DPAD_LEFT = 13
BUTTON_DPAD_RIGHT = 14

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

# Attesa dopo il cambio profilo monitor prima di aprire Big Picture, cosi'
# Steam si apre sullo schermo giusto (la TV) e non su quello vecchio.
MONITOR_SETTLE_SECONDS = 3

DEFAULT_CONFIG = {
    "home_key_mode": "none",  # "windows" | "alttab" | "none"
    "controller_enabled": True,
    "notifications_enabled": True,
    "monitor_profile": "main",  # ultimo profilo lanciato: "main" | "tv"
    "spotify_client_id": "",  # dell'app creata su developer.spotify.com
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


def _launch_monitor_profile(path, profile):
    with config_lock:
        config["monitor_profile"] = profile
        save_config(config)
    refresh_tray_icon()
    try:
        return subprocess.Popen([MONITOR_SWITCHER_EXE, f'-load:{path}'])
    except Exception as e:
        print(f"[AppUtili] Errore avvio MonitorSwitcher: {e}")
        notify("⚠ Impossibile avviare MonitorSwitcher")
        return None


def switch_to_main(icon=None, item=None):
    _launch_monitor_profile(PROFILE_MAIN, "main")
    notify("🖥 Monitor: Main")


def switch_to_tv(icon=None, item=None):
    _launch_monitor_profile(PROFILE_TV, "tv")
    notify("📺 Monitor: TV")


def monitor_profile_checked(profile):
    def _checked(item):
        with config_lock:
            return config["monitor_profile"] == profile
    return _checked


def _open_steam_url(url):
    try:
        os.startfile(url)
    except Exception as e:
        print(f"[AppUtili] Errore apertura {url}: {e}")


def _tv_and_bigpicture():
    notify("📺 TV + Steam Big Picture")
    proc = _launch_monitor_profile(PROFILE_TV, "tv")
    if proc is not None:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    time.sleep(MONITOR_SETTLE_SECONDS)
    _open_steam_url(STEAM_OPEN_BIGPICTURE)


def _exit_bigpicture_and_main():
    _open_steam_url(STEAM_CLOSE_BIGPICTURE)
    _launch_monitor_profile(PROFILE_MAIN, "main")
    notify("🖥 Main, Big Picture chiuso")


def tv_and_bigpicture(icon=None, item=None):
    # In un thread a parte: contiene attese e non deve bloccare il chiamante
    threading.Thread(target=_tv_and_bigpicture, daemon=True).start()


def exit_bigpicture_and_main(icon=None, item=None):
    threading.Thread(target=_exit_bigpicture_and_main, daemon=True).start()


def register_monitor_hotkeys():
    try:
        keyboard.add_hotkey(HOTKEY_MAIN, switch_to_main)
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
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
HOTKEY_ID_HOME = 1

_home_stop_event = threading.Event()
_home_thread = None


def _home_hotkey_loop():
    """Gira in un thread dedicato: registra/deregistra l'hotkey in base alla
    configurazione corrente e processa i messaggi WM_HOTKEY."""
    registered = False
    msg = wintypes.MSG()

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
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_HOME:
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
    (COMBO_TV_BIGPICTURE, tv_and_bigpicture),
    (COMBO_MAIN_EXIT_BIGPICTURE, exit_bigpicture_and_main),
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

OVERLAY_BUTTONS = {
    BUTTON_DPAD_UP: "up",
    BUTTON_DPAD_DOWN: "down",
    BUTTON_DPAD_LEFT: "left",
    BUTTON_DPAD_RIGHT: "right",
    BUTTON_A: "a",
    BUTTON_B: "b",
    BUTTON_X: "play_pause",
    BUTTON_Y: "y",
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
    """Chiamata a ogni giro del ciclo controller: ripete su/giu' tenuti premuti."""
    if not _overlay_open.is_set():
        _nav_repeat.clear()
        return
    now = time.monotonic()
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
        pystray.MenuItem("📺  Back+LB+Su: TV + Big Picture", None, enabled=False),
        pystray.MenuItem("🖥  Back+LB+Giù: Main + esci da Big Picture", None, enabled=False),
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
# STEELSERIES SONAR (nome dell'uscita audio)
# ---------------------------------------------------------------------------
#
# Con Sonar l'uscita predefinita di Windows e' sempre il dispositivo virtuale
# "SteelSeries Sonar - Gaming": le scorciatoie Ctrl+è / Ctrl++ cambiano il
# dispositivo reale DENTRO Sonar. Per mostrarne il nome si legge l'API web
# locale di SteelSeries GG (non documentata, la stessa usata da vari progetti
# della community). Solo letture: il cambio resta affidato alle scorciatoie.
# Se GG non c'e' o l'API cambia, l'overlay mostra semplicemente le scorciatoie.

SONAR_CORE_PROPS = (Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
                    / "SteelSeries" / "GG" / "coreProps.json")
SONAR_REFRESH_DELAY = 0.7  # attesa dopo Ctrl+è / Ctrl++ prima di rileggere il nome

_sonar_base = None  # es. "http://127.0.0.1:61188", cambia a ogni avvio di GG
_audio_output = None  # (tipo, nome) dell'uscita attuale, es. ("Cuffie", "Arctis Nova Pro Wireless")
_audio_switching = False


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


def refresh_audio_output(delay=0):
    """Rilegge in un thread a parte il nome dell'uscita Sonar e ridisegna."""
    def _worker():
        global _audio_output, _audio_switching
        time.sleep(delay)
        name = _sonar_output_device_name()
        _audio_output = _split_device_name(name) if name else None
        _audio_switching = False
        _overlay_queue.put("redraw")
    threading.Thread(target=_worker, daemon=True).start()


def switch_audio_output(step):
    global _audio_switching
    _audio_switching = True
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


def _spotify_fetch_images(items):
    """Scarica le copertine in cache (una volta sola) e le collega agli
    elementi con "image_path"; ridisegna man mano che arrivano."""
    SPOTIFY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    jobs = []
    for item in items:
        url = item.get("image_url")
        if not url:
            continue
        path = SPOTIFY_CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
        if path.is_file():
            item["image_path"] = str(path)
        else:
            jobs.append((item, url, path))

    def fetch(job):
        item, url, path = job
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
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
            _spotify_fetch_images(state["recent"] + state["playlists"])
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

OV_DANGER = (220, 70, 70)  # pillola "Conferma?"
OV_SEGMENT_BG = (26, 26, 26)
OV_SEGMENT_ACTIVE = (68, 68, 68)

OVERLAY_TABS = ("Controlli", "Giochi", "Spotify")
TAB_CONTROLS, TAB_GAMES, TAB_SPOTIFY = 0, 1, 2
CONFIRM_TIMEOUT = 3.0  # secondi per la seconda pressione di A
OVERLAY_TICK_MS = 30000  # ridisegno periodico (orologio, batteria)
GRID_COLUMNS = 3  # griglia di copertine della tab Giochi
SPOTIFY_FILTERS = (("recent", "Recenti"), ("playlists", "Playlist"))  # Y per cambiare


def _monitor_is(profile):
    with config_lock:
        return config["monitor_profile"] == profile


def _notifications_on():
    with config_lock:
        return config["notifications_enabled"]


def _audio_icon():
    if _audio_output is None:
        return ICON_VOLUME
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
        return "Ctrl+è / Ctrl++"
    return _audio_output[1]


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
    {"section": "Schermo", "kind": "segmented", "icon": ICON_MONITOR, "label": "Monitor",
     "desc": "Profilo monitor: scrivania (Main) o TV.",
     "options": [
         {"label": "Main", "action": switch_to_main, "active": lambda: _monitor_is("main")},
         {"label": "TV", "action": switch_to_tv, "active": lambda: _monitor_is("tv")},
     ]},
    {"section": "Steam", "kind": "segmented", "icon": ICON_GAME, "label": "Big Picture",
     "desc": "Avvia su TV: passa alla TV e apre Big Picture. Esci: lo chiude e torna a Main.",
     "options": [
         {"label": "Avvia su TV", "action": tv_and_bigpicture},
         {"label": "Esci", "action": exit_bigpicture_and_main},
     ]},
    {"section": "Audio", "kind": "choice", "icon": _audio_icon, "label": "Uscita",
     "value": _audio_value, "step": switch_audio_output,
     "desc": "Sinistra / destra: cambia il dispositivo di uscita in SteelSeries Sonar."},
    {"kind": "slider", "icon": _volume_icon, "label": "Volume",
     "value": get_volume, "step": change_volume, "toggle": toggle_mute,
     "desc": "Sinistra / destra: volume (tieni premuto per andare veloce). A: muto."},
    {"section": "Finestre", "icon": ICON_DESKTOP, "label": "Mostra desktop",
     "desc": "Riduce a icona tutte le finestre (Win+D).",
     "action": show_desktop},
    {"icon": ICON_CLOSE, "label": "Chiudi finestra", "hint": "Alt+F4",
     "desc": "Chiude la finestra che era in primo piano prima dell'overlay.",
     "action": close_window},
    {"section": "Sistema", "kind": "toggle", "label": "Notifiche",
     "icon": lambda: ICON_BELL if _notifications_on() else ICON_BELL_OFF,
     "value": _notifications_on, "toggle": lambda: toggle_notifications(None, None),
     "desc": "Avvisi di Windows quando cambi profilo monitor."},
    {"kind": "segmented", "icon": ICON_POWER, "label": "Energia", "confirm": True,
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

OVERLAY_FOOTERS = {
    TAB_CONTROLS: [(("A",), "Conferma"), (("B",), "Chiudi"),
                   (("LB", "RB"), "Brano"), (("X",), "Play/pausa")],
    TAB_GAMES: [(("A",), "Avvia"), (("B",), "Chiudi"), (("LT", "RT"), "Cambia tab")],
    TAB_SPOTIFY: [(("A",), "Riproduci"), (("B",), "Chiudi"),
                  (("LB", "RB"), "Brano"), (("X",), "Pausa")],
}


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
        self.root.attributes("-topmost", True)
        bg = "#%02x%02x%02x" % OV_BG
        self.root.configure(bg=bg)

        # Tutto il pannello e' un'unica immagine disegnata con Pillow
        self.label = tk.Label(self.root, bd=0, highlightthickness=0, bg=bg)
        self.label.pack()
        self.label.bind("<Button-1>", self._click)

        # con il focus, anche la tastiera funziona
        for key, command in (("<Up>", "up"), ("<Down>", "down"), ("<Left>", "left"),
                             ("<Right>", "right"), ("<Return>", "a"), ("<Escape>", "b"),
                             ("<Prior>", "prev_tab"), ("<Next>", "next_tab")):
            self.root.bind(key, lambda e, c=command: self._handle(c))

        self.tab = TAB_CONTROLS
        self.index = 0  # voce selezionata nella tab Controlli
        self.segment = 0  # opzione sotto il cursore nelle righe "segmented"
        self.pending = None  # (voce, opzione, scadenza) in attesa della seconda A
        self.games_grid = _CoverGrid()
        self.spotify_filter = 0  # indice in SPOTIFY_FILTERS
        self.spotify_index = [0] * len(SPOTIFY_FILTERS)  # riga selezionata per filtro
        self.spotify_scroll = 0  # px gia' scorsi nella lista
        self.setup_window = None  # finestra di configurazione di Spotify, se aperta
        self.panel_height = None  # altezza della tab Controlli, usata da tutte le tab
        self.prev_hwnd = None
        self.scale = 1.0
        self.photo = None
        self.hitboxes = []  # (x0, y0, x1, y1, funzione) per i clic del mouse
        self._cover_cache = (None, 0, None)
        self._tick_id = None
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
        except Exception as e:
            print(f"[AppUtili] Errore overlay: {e}")
        self.root.after(15, self._poll)

    def _handle(self, command):
        if command == "toggle":
            if _overlay_open.is_set():
                self.hide()
            else:
                self.show()
        elif command == "spotify_setup":  # dal menu del tray, anche a overlay chiuso
            self._open_spotify_setup()
        elif not _overlay_open.is_set():
            return  # comandi arrivati mentre era chiuso: vecchi, si scartano
        elif command == "redraw":
            self._redraw()
        elif command == "b":
            self.hide()
        elif command in ("prev_tab", "next_tab"):
            self._set_tab(self.tab + (1 if command == "next_tab" else -1))
        elif command in MEDIA_KEYS:  # LB / RB / X: brani, da qualsiasi tab
            media_command(command)
        elif command == "shuffle":  # R3: riproduzione casuale di Spotify, da qualsiasi tab
            spotify_toggle_shuffle()
        elif self.tab == TAB_GAMES:
            self._games_command(command)
        elif self.tab == TAB_SPOTIFY:
            self._spotify_command(command)
        else:
            self._controls_command(command)

    def _set_tab(self, tab):
        self.tab = tab % len(OVERLAY_TABS)
        self.pending = None
        self._redraw()

    # --- tab Controlli -----------------------------------------------------

    def _controls_command(self, command):
        # "*_repeat" = sinistra/destra tenuti premuti: contano solo per gli slider
        repeat = command.endswith("_repeat")
        command = command.removesuffix("_repeat")
        if command in ("up", "down"):
            self._move(-1 if command == "up" else 1)
        elif command in ("left", "right"):
            if not repeat or OVERLAY_ITEMS[self.index].get("kind") == "slider":
                self._step(-1 if command == "left" else 1)
        elif command == "a":
            self._confirm()

    def _selectable(self, index):
        # la scheda media si seleziona solo se c'e' qualcosa in riproduzione
        return OVERLAY_ITEMS[index].get("kind") != "media" or get_media_state() is not None

    def _select(self, index):
        self.index = index
        self.pending = None
        item = OVERLAY_ITEMS[index]
        if item.get("kind") == "segmented":
            # il cursore parte dall'opzione attiva (es. il profilo monitor in uso)
            active = [i for i, o in enumerate(item["options"]) if o.get("active") and o["active"]()]
            self.segment = active[0] if active else 0

    def _move(self, delta):
        index = self.index
        for _ in OVERLAY_ITEMS:
            index = (index + delta) % len(OVERLAY_ITEMS)
            if self._selectable(index):
                break
        self._select(index)
        self._redraw()

    def _step(self, direction):
        item = OVERLAY_ITEMS[self.index]
        kind = item.get("kind", "action")
        if kind == "segmented":
            self.segment = min(len(item["options"]) - 1, max(0, self.segment + direction))
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
        item = OVERLAY_ITEMS[self.index]
        kind = item.get("kind", "action")
        if kind == "choice":
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
                self._run(item["options"][self.segment]["action"])
        else:
            self._run(item["action"])

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

    def _open_spotify_setup(self):
        """Finestra normale (mouse e tastiera) per incollare il Client ID: si
        fa una volta sola, poi resta salvato in config.json."""
        if self.setup_window is not None and self.setup_window.winfo_exists():
            self.setup_window.deiconify()
            self.setup_window.lift()
            self.setup_window.focus_force()
            return
        bg, surface, fg, muted = ("#%02x%02x%02x" % c for c in (OV_BG, OV_SURFACE, OV_TEXT, OV_MUTED))
        accent, danger = ("#%02x%02x%02x" % c for c in (OV_ACCENT, OV_DANGER))
        win = tk.Toplevel(self.root, bg=bg, padx=22, pady=18)
        self.setup_window = win
        win.title(f"{APP_NAME} – Spotify")
        win.icon = ImageTk.PhotoImage(create_image(size=32, badge=False), master=win)
        win.iconphoto(False, win.icon)  # icona di AppUtili invece della piuma di Tk
        win.resizable(False, False)
        win.attributes("-topmost", True)

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

        label("Collega Spotify", font=("Segoe UI", 15, "bold"))
        label("1. Apri il dashboard degli sviluppatori di Spotify e crea un'app\n"
              "    (nome e descrizione a piacere).\n"
              "2. Come Redirect URI inserisci l'indirizzo qui sotto e, tra le API,\n"
              "    seleziona \"Web API\". Salva.\n"
              "3. Nelle impostazioni dell'app copia il Client ID e incollalo qui.\n"
              "Serve Spotify Premium sull'account che crea l'app.",
              fg=muted, space=(8, 10))
        button(win, "Apri dashboard Spotify", lambda: webbrowser.open(SPOTIFY_DASHBOARD)).pack(anchor="w")

        label("Redirect URI", space=(12, 2))
        row = tk.Frame(win, bg=bg)
        row.pack(fill="x")
        uri = tk.Entry(row, font=("Consolas", 10), relief="flat", bg=surface, fg=fg,
                       readonlybackground=surface, width=40)
        uri.insert(0, SPOTIFY_REDIRECT_URI)
        uri.configure(state="readonly")
        uri.pack(side="left", ipady=4)

        def copy_uri():
            win.clipboard_clear()
            win.clipboard_append(SPOTIFY_REDIRECT_URI)
            copy.configure(text="Copiato")
        copy = button(row, "Copia", copy_uri)
        copy.pack(side="left", padx=(8, 0))

        label("Client ID", space=(12, 2))
        client_id = tk.Entry(win, font=("Consolas", 10), relief="flat", bg=surface, fg=fg,
                             insertbackground=fg, width=40)
        client_id.insert(0, spotify_client_id())
        client_id.pack(anchor="w", ipady=4)
        error = label("", fg=danger, space=(4, 0))

        def connect(event=None):
            value = client_id.get().strip().lower()
            if not SPOTIFY_CLIENT_ID_PATTERN.match(value):
                error.configure(text="Il Client ID è di 32 caratteri (cifre e lettere a-f).")
                return
            set_spotify_client_id(value)
            SPOTIFY_TOKEN_FILE.unlink(missing_ok=True)  # eventuale login con un altro Client ID
            win.destroy()
            spotify_connect()

        actions = tk.Frame(win, bg=bg)
        actions.pack(fill="x", pady=(6, 0))
        button(actions, "Collega", connect, primary=True).pack(side="right")
        button(actions, "Annulla", win.destroy).pack(side="right", padx=(0, 8))
        win.bind("<Return>", connect)
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        work, _ = _monitor_of(ov_user32.GetForegroundWindow())
        win.geometry(f"+{(work.left + work.right - win.winfo_width()) // 2}"
                     f"+{(work.top + work.bottom - win.winfo_height()) // 3}")
        _force_foreground(ov_user32.GetAncestor(win.winfo_id(), GA_ROOT))
        client_id.focus_force()

    # --- mostra / nascondi -------------------------------------------------

    def show(self):
        _overlay_open.set()
        self.prev_hwnd = ov_user32.GetForegroundWindow()
        self._select(0)
        self.games_grid.reset()
        self.spotify_index = [0] * len(SPOTIFY_FILTERS)
        self.spotify_scroll = 0
        _set_spotify_status(None)
        work, dpi = _monitor_of(self.prev_hwnd)
        self.scale = dpi / 96
        self.panel_height = None
        media_refresh()
        refresh_audio_output()
        refresh_games()
        refresh_spotify()
        self._redraw()
        margin = round(OVERLAY_MARGIN * self.scale)
        self.root.geometry(f"+{work.left + margin}+{work.top + margin}")
        self.root.deiconify()
        self.root.update_idletasks()

        hwnd = self._hwnd()
        style = ov_user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ov_user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                 (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        _round_window_corners(hwnd)
        self.root.attributes("-topmost", True)
        _force_foreground(hwnd)
        self.root.focus_force()
        self._tick_id = self.root.after(OVERLAY_TICK_MS, self._tick)

    def hide(self):
        # Prima si ridà il focus e poi si nasconde: nascondendo per primo,
        # Windows attiverebbe un'altra finestra e la restituzione verrebbe rifiutata
        prev = self.prev_hwnd
        self.prev_hwnd = None
        if prev and ov_user32.IsWindow(prev):
            _force_foreground(prev)
        self.root.withdraw()
        _overlay_open.clear()
        self.pending = None
        if self._tick_id is not None:
            self.root.after_cancel(self._tick_id)
            self._tick_id = None

    def _tick(self):
        self._redraw()  # orologio e batteria
        self._tick_id = self.root.after(OVERLAY_TICK_MS, self._tick)

    def _redraw(self):
        while not self._selectable(self.index):  # la musica si e' fermata
            self._select(self.index - 1)
        image, self.hitboxes = self.render()
        self.photo = ImageTk.PhotoImage(image, master=self.root)
        self.label.configure(image=self.photo)

    def _click(self, event):
        for x0, y0, x1, y1, action in reversed(self.hitboxes):
            if x0 <= event.x < x1 and y0 <= event.y < y1:
                action()
                return

    # --- disegno -----------------------------------------------------------

    def _S(self, v):
        return round(v * self.scale)

    def render(self):
        """Disegna il pannello. Ritorna (immagine, hitbox per il mouse)."""
        if self.tab == TAB_CONTROLS:
            image, hitboxes = self._render_controls()
            self.panel_height = image.height
            return image, hitboxes
        if self.panel_height is None:
            self.panel_height = self._render_controls()[0].height
        if self.tab == TAB_SPOTIFY:
            return self._render_spotify(self.panel_height)
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
        with config_lock:
            profile = config["monitor_profile"]
        chip_color = ICON_STYLES.get(profile, ICON_STYLES["main"])[0]
        chip_text = "TV" if profile == "tv" else "Main"
        chip_font = _ov_font(S(12), "Semibold Text")
        x0 = x - S(30) - chip_font.getlength(chip_text)
        _ov_round(img, (x0, cy - S(12), x, cy + S(12)), S(12), OV_SURFACE)
        _ov_round(img, (x0 + S(10), cy - S(4), x0 + S(18), cy + S(4)), S(4), chip_color)
        draw.text((x0 + S(23), cy), chip_text, font=chip_font, fill=OV_TEXT, anchor="lm")
        y += S(38)

        # tab: LT  Controlli  Giochi  RT
        cy = y + S(14)
        x = pad + S(4)
        x = self._pad_button(img, draw, x, cy, "LT") + S(12)
        tab_font = _ov_font(S(14), "Semibold Text")
        for i, name in enumerate(OVERLAY_TABS):
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

    def _render_footer(self, img, draw, y):
        S = self._S
        small_font = _ov_font(S(12))
        x = S(14) + S(6)
        cy = y + S(10)
        for buttons, text in OVERLAY_FOOTERS[self.tab]:
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

    def _render_controls(self):
        S = self._S
        img, draw = self._new_canvas(S(1600))
        width, pad = img.width, S(14)
        hitboxes = []
        y = self._render_header(img, draw, hitboxes)

        row_h = S(40)
        label_font = _ov_font(S(14))
        icon_font = _ov_icon_font(S(16))
        for index, item in enumerate(OVERLAY_ITEMS):
            kind = item.get("kind", "action")
            if kind == "media":
                continue
            if item.get("section"):
                y += S(8)
                draw.text((pad + S(12), y + S(9)), item["section"].upper(),
                          font=_ov_font(S(11), "Semibold Text"), fill=OV_MUTED, anchor="lm")
                y += S(20)
            selected = index == self.index
            x0, x1 = pad, width - pad
            cy = y + row_h // 2
            if selected:
                _ov_round(img, (x0, y, x1, y + row_h), S(6), OV_SELECTED)
                _ov_round(img, (x0, cy - S(8), x0 + S(3), cy + S(8)), S(2), OV_ACCENT)
            draw.text((x0 + S(24), cy), _value(item["icon"]), font=icon_font,
                      fill=OV_ACCENT if selected else OV_TEXT, anchor="mm")
            draw.text((x0 + S(46), cy), item["label"], font=label_font, fill=OV_TEXT, anchor="lm")
            hitboxes.append((x0, y, x1, y + row_h,
                             lambda i=index: (self._select(i), self._confirm())))

            right = x1 - S(10)
            if kind == "action":
                if item.get("hint"):
                    self._keycap(img, draw, right - S(4), cy, item["hint"])
            elif kind == "toggle":
                self._switch(img, draw, right - S(4), cy, _value(item["value"]), selected)
            elif kind == "choice":
                self._render_choice(img, draw, right - S(4), cy, item, selected)
            elif kind == "slider":
                self._render_slider(img, draw, right - S(4), cy, item, selected)
            elif kind == "segmented":
                self._render_segmented(img, draw, right, cy, index, item, selected, hitboxes)
            y += row_h + S(2)

        # scheda media (sempre l'ultima voce)
        y += S(12)
        y = self._render_media(img, draw, y, pad, width, hitboxes, S)

        y += S(12)
        if self.pending and self.pending[0] == self.index:
            option = OVERLAY_ITEMS[self.index]["options"][self.pending[1]]["label"]
            description = f"Premi di nuovo A per confermare: {option}."
        else:
            description = OVERLAY_ITEMS[self.index].get("desc", "")
        y = self._render_description(img, draw, y, description)
        y = self._render_footer(img, draw, y)
        return img.crop((0, 0, width, y)), hitboxes

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
        options = item["options"]
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
            self._render_spotify_filter(img, draw, body_top + filter_h // 2, hitboxes)
            items = self._spotify_items(state)
            list_top = body_top + filter_h
            if not items:
                empty = "Nessun brano ascoltato di recente" if self.spotify_filter == 0 \
                    else "Nessuna playlist"
                self._render_center_message(img, draw, list_top, body_h - filter_h, empty)
            else:
                self._render_spotify_list(img, list_top, body_h - filter_h, items, hitboxes)

        status = _spotify_status
        if status:
            draw.text((S(14) + S(8), height - footer_h - player_h - status_h // 2),
                      _ov_fit(status[0], _ov_font(S(12)), img.width - 2 * S(22)),
                      font=_ov_font(S(12)), fill=OV_DANGER if status[1] else OV_ACCENT,
                      anchor="lm")
        self._render_now_playing(img, draw, height - footer_h - player_h, hitboxes)
        self._render_footer(img, draw, height - footer_h)
        return img, hitboxes

    def _render_spotify_filter(self, img, draw, cy, hitboxes):
        """Selettore Recenti / Playlist a sinistra, suggerimento "Y Cambia" a destra."""
        S = self._S
        pad = S(14)
        font = _ov_font(S(12.5), "Semibold Text")
        h, inset = S(30), S(3)
        widths = [font.getlength(label) + S(32) for _, label in SPOTIFY_FILTERS]
        x = pad
        _ov_round(img, (x, cy - h / 2, x + sum(widths) + 2 * inset, cy + h / 2), h / 2, OV_SEGMENT_BG)
        x += inset
        for i, ((_, label), w) in enumerate(zip(SPOTIFY_FILTERS, widths)):
            box = (x, cy - h / 2 + inset, x + w, cy + h / 2 - inset)
            if i == self.spotify_filter:
                _ov_round(img, box, (h - 2 * inset) / 2, OV_ACCENT)
            draw.text((x + w / 2, cy), label, font=font,
                      fill=OV_BG if i == self.spotify_filter else OV_MUTED, anchor="mm")
            hitboxes.append((*box, lambda i=i: self._set_spotify_filter(i)))
            x += w
        hint_font = _ov_font(S(12))
        right = img.width - pad - S(4)
        hint_x = right - hint_font.getlength("Cambia")
        draw.text((right, cy), "Cambia", font=hint_font, fill=OV_MUTED, anchor="rm")
        self._pad_button(img, draw, hint_x - S(24), cy, "Y")

    def _render_spotify_list(self, img, top, height, items, hitboxes):
        """Righe grandi (copertina, titolo, sottotitolo), con scorrimento."""
        S = self._S
        width, pad = img.width, S(14)
        row_h, gap, cover = S(66), S(2), S(52)
        index = min(self.spotify_index[self.spotify_filter], len(items) - 1)
        self.spotify_index[self.spotify_filter] = index
        # scorrimento: la riga selezionata deve essere tutta visibile
        row_top = index * (row_h + gap)
        if row_top < self.spotify_scroll:
            self.spotify_scroll = row_top
        elif row_top + row_h > self.spotify_scroll + height:
            self.spotify_scroll = row_top + row_h - height

        # su un'immagine a parte, cosi' le righe parziali vengono tagliate
        area = Image.new("RGB", (width, height), OV_BG)
        draw = ImageDraw.Draw(area)
        title_font = _ov_font(S(15), "Semibold Text")
        subtitle_font = _ov_font(S(12.5))
        first = self.spotify_scroll // (row_h + gap)
        last = min(len(items), (self.spotify_scroll + height) // (row_h + gap) + 1)
        for i in range(first, last):
            item = items[i]
            y = i * (row_h + gap) - self.spotify_scroll
            selected = i == index
            if selected:
                _ov_round(area, (pad, y, width - pad, y + row_h), S(8), OV_SELECTED)
            cx, cy = pad + S(8), y + (row_h - cover) // 2
            path = item.get("image_path")
            image = _spotify_cover(path, cover, cover) if path else _spotify_placeholder(cover, cover)
            area.paste(image, (cx, cy), _ov_mask(cover, cover, S(5)))
            tx = cx + cover + S(14)
            tw = width - pad - S(12) - tx - (S(44) if selected else 0)
            draw.text((tx, y + row_h // 2 - S(10)), _ov_fit(item["name"], title_font, tw),
                      font=title_font, fill=OV_TEXT, anchor="lm")
            draw.text((tx, y + row_h // 2 + S(12)), _ov_fit(item["subtitle"], subtitle_font, tw),
                      font=subtitle_font, fill=OV_MUTED, anchor="lm")
            if selected:
                px, py = width - pad - S(28), y + row_h // 2
                _ov_round(area, (px - S(16), py - S(16), px + S(16), py + S(16)), S(16), OV_ACCENT)
                draw.text((px + S(1), py), ICON_PLAY, font=_ov_icon_font(S(13)), fill=OV_BG,
                          anchor="mm")
            if y + row_h > 0 and y < height:
                hitboxes.append((pad, top + max(0, y), width - pad, top + min(height, y + row_h),
                                 lambda i=i: (self.spotify_index.__setitem__(self.spotify_filter, i),
                                              self._spotify_command("a"))))
        img.paste(area, (0, top))

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
        font = _ov_font(round(10 * s), "Semibold Text")
        w, h = font.getlength(name) + round(12 * s), round(18 * s)
        _ov_round(img, (x, cy - h / 2, x + w, cy + h / 2), round(5 * s), OV_BORDER)
        draw.text((x + w / 2, cy), name, font=font, fill=OV_TEXT, anchor="mm")
        return x + w


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


# Aspetto dell'icona per ogni profilo monitor: (colore di sfondo, testo)
ICON_STYLES = {
    "main": ((0, 120, 215), "M"),   # blu
    "tv": ((136, 23, 152), "TV"),   # viola
}
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


def create_image(profile="main", controller_enabled=True, size=64, badge=True):
    """Icona generata al volo: il colore e la scritta mostrano il profilo
    monitor attuale, il pallino in basso a destra lo stato del controller.
    Le misure sono pensate su 64px e scalate per le altre dimensioni."""
    s = size / 64
    bg, text = ICON_STYLES.get(profile, ICON_STYLES["main"])
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


def _tray_title():
    with config_lock:
        profile = config["monitor_profile"]
        controller = config["controller_enabled"]
    parts = [APP_NAME, f"Monitor: {'TV' if profile == 'tv' else 'Main'}"]
    if CONTROLLER_SUPPORT:
        parts.append(f"Controller: {'attivo' if controller else 'disattivato'}")
    return "\n".join(parts)


def refresh_tray_icon():
    if _tray_icon is None:
        return
    with config_lock:
        profile = config["monitor_profile"]
        controller = config["controller_enabled"]
    try:
        _tray_icon.icon = create_image(profile, controller)
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


def build_menu():
    return pystray.Menu(
        pystray.MenuItem(
            "🖥  Monitor Setup",
            pystray.Menu(
                pystray.MenuItem("Main  (Ctrl+Alt+↓)", switch_to_main,
                                  checked=monitor_profile_checked("main"), radio=True),
                pystray.MenuItem("TV  (Ctrl+Alt+↑)", switch_to_tv,
                                  checked=monitor_profile_checked("tv"), radio=True),
            ),
        ),
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
        pystray.MenuItem("🎵  Spotify", build_spotify_menu()),
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
        profile = config["monitor_profile"]
        controller = config["controller_enabled"]
    _tray_icon = pystray.Icon(APP_NAME, create_image(profile, controller),
                              _tray_title(), menu=build_menu())

    start_home_hotkey_thread()
    start_controller_thread()
    start_overlay_thread()
    register_monitor_hotkeys()

    _tray_icon.run()


if __name__ == "__main__":
    main()
