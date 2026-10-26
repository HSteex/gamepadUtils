"""
Script diagnostico: verifica che il controller sia riconosciuto da SDL
come "GameController" (mappatura standard A/B/X/Y/dorsali/stick/D-pad)
e stampa ogni pulsante premuto.

USO:
1. pip install pygame   (se non gia' installato)
2. Collega/accendi il controller PRIMA di avviare lo script
   (per 8BitDo: verifica che sia in modalita' XInput, di solito la "X"
   sullo switch sul retro - e' la piu' compatibile)
3. python rileva_controller.py
4. Premi i pulsanti sul controller e osserva l'output
5. CTRL+C per uscire
"""

import pygame
import pygame._sdl2.controller as sdl_controller

pygame.init()
sdl_controller.init()

count = sdl_controller.get_count()
print(f"Dispositivi joystick rilevati: {count}\n")

if count == 0:
    print("Nessun controller rilevato. Controlla che sia acceso/collegato e riprova.")
else:
    for i in range(count):
        is_ctrl = sdl_controller.is_controller(i)
        print(f"Indice {i}: riconosciuto come GameController = {is_ctrl}")
        if is_ctrl:
            c = sdl_controller.Controller(i)
            print(f"  Nome: {c.name}")
        else:
            print(
                "  ATTENZIONE: SDL non lo riconosce come GameController standard.\n"
                "  Prova a cambiare la modalita' del controller (es. switch XInput/DInput\n"
                "  sull'8BitDo) oppure aggiorna pygame."
            )

print("\nIn ascolto dei pulsanti... (CTRL+C per uscire)\n")

triggers_down = {}

try:
    while True:
        for event in pygame.event.get():
            if event.type == pygame.CONTROLLERBUTTONDOWN:
                print(f"Premuto: button={event.button}  instance_id={event.instance_id}")
            elif event.type == pygame.CONTROLLERAXISMOTION and event.axis in (
                pygame.CONTROLLER_AXIS_TRIGGERLEFT, pygame.CONTROLLER_AXIS_TRIGGERRIGHT
            ):
                # LT/RT sono assi analogici, non pulsanti: stampiamo solo
                # quando superano meta' corsa (come li considera app.py)
                name = "LT" if event.axis == pygame.CONTROLLER_AXIS_TRIGGERLEFT else "RT"
                pressed = event.value >= 16000
                if pressed != triggers_down.get(name, False):
                    triggers_down[name] = pressed
                    if pressed:
                        print(f"Premuto: {name} (grilletto)  instance_id={event.instance_id}")
            elif event.type == pygame.CONTROLLERDEVICEADDED:
                print(f"Controller collegato: device_index={event.device_index}")
            elif event.type == pygame.CONTROLLERDEVICEREMOVED:
                print(f"Controller scollegato: instance_id={event.instance_id}")
        pygame.time.wait(20)
except KeyboardInterrupt:
    print("\nUscita.")
