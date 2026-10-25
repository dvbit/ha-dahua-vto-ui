<p align="center"><img src="icon.png" width="128" alt="Dahua VTO UI"></p>

# Dahua VTO UI

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
![Versione](https://img.shields.io/badge/versione-2.0.0-blue)
![HA](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5)

🇬🇧 [Read in English](README.md)

Integrazione Home Assistant per **pulsantiere Dahua VTO e monitor interni VTH**,
configurabile interamente da interfaccia.

È un fork di [myhomeiot/DahuaVTO](https://github.com/myhomeiot/DahuaVTO): stesso
protocollo locale, ma le ricette YAML del README originale (lock template con timer,
automazioni del campanello, template dello stato porta, comandi di allarme del VTH)
ora sono entità native. La specifica completa è in [SPEC.md](SPEC.md).

## Funzioni

| VTO (pulsantiera) | VTH (monitor interno) |
|---|---|
| `event` campanello: `ring`, `answered`, `missed`, `voice_message` | `alarm_control_panel` sui profili del VTH |
| `lock` per relè porta (1–2), ri-blocco automatico | `sensor` ultima chiamata |
| `binary_sensor` stato porta | `button` riavvio |
| `event` controllo accessi (`authorized` / `denied`) | `sensor` connessione |
| `camera` (stream RTSP + istantanea) | |
| `sensor` ultima chiamata, `sensor` connessione | |
| `button` riavvio, `button` annulla chiamata | |

In più i servizi `open_door`, `send_command`, `send_instance_command` (ora con
risposta diretta) e l'evento `dahua_vto_ui` con gli stessi dati dell'originale.

Provata con VTO2000A e VTH5221DW; dovrebbero funzionare anche i modelli elencati
dall'integrazione originale.

## Installazione

1. HACS → ⋮ → **Repository personalizzati** → `https://github.com/dvbit/ha-dahua-vto-ui`,
   categoria **Integrazione**.
2. Installa **Dahua VTO UI** e riavvia Home Assistant.
3. **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → Dahua VTO UI**.

Coesiste con l'integrazione originale `dahua_vto`: puoi migrare un pezzo alla volta.

## Configurazione

| Campo | Descrizione |
|---|---|
| Nome | Opzionale; se vuoto si usa il modello |
| Host / Porta | Indirizzo del dispositivo, porta RPC (default 5000) |
| Nome utente / Password | Account web del dispositivo |

Il tipo (VTO o VTH) viene rilevato automaticamente. Gli entity ID sono creati in
inglese a partire dal nome, es. `lock.citofono_door_1`.

### Opzioni

| Opzione | Default | Dispositivo | Note |
|---|---|---|---|
| Timeout | 10 s | entrambi | Timeout comandi e attesa prima di riconnettersi |
| Relè porta | 1 | VTO | Numero di entità `lock` |
| Durata sblocco | 2 s | VTO | Allineala al "periodo di sblocco" del VTO |
| Numero breve | `HA` | VTO | Identificativo inviato con l'apertura |
| Porta RTSP / HTTP | 554 / 80 | VTO | Stream e istantanea della camera |
| Flusso video | main | VTO | `main` o `sub` |

## Esempi

### Notifica campanello con istantanea

```yaml
alias: Notifica campanello
triggers:
  - trigger: state
    entity_id: event.citofono_doorbell
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.citofono_doorbell
    attribute: event_type
    state: ring
actions:
  - action: camera.snapshot
    target:
      entity_id: camera.citofono_camera
    data:
      filename: /config/www/campanello.jpg
  - action: notify.mobile_app_telefono
    data:
      title: Campanello
      message: C'è qualcuno alla porta
      data:
        image: /local/campanello.jpg
mode: single
```

### Aprire il cancelletto

```yaml
action: lock.unlock
target:
  entity_id: lock.citofono_door_1
```

Oppure con il servizio (canale 2, numero breve personalizzato):

```yaml
action: dahua_vto_ui.open_door
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d
  channel: 2
  short_number: HA
```

### Notifica chiamata persa

```yaml
alias: Chiamata persa al citofono
triggers:
  - trigger: state
    entity_id: event.citofono_doorbell
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.citofono_doorbell
    attribute: event_type
    state: missed
actions:
  - action: notify.mobile_app_telefono
    data:
      message: Chiamata persa al citofono
mode: single
```

### Inserire l'allarme del VTH quando si esce

```yaml
alias: Allarme VTH fuori casa
triggers:
  - trigger: state
    entity_id: zone.home
    to: "0"
actions:
  - action: alarm_control_panel.alarm_arm_away
    target:
      entity_id: alarm_control_panel.monitor_alarm
mode: single
```

| Stato HA | Profilo VTH |
|---|---|
| `disarmed` | `AlarmEnable: false` |
| `armed_home` | `AtHome` |
| `armed_away` | `Outdoor` |
| `armed_night` | `Sleeping` |
| `armed_custom_bypass` | `Custom` |

### Comando grezzo con risposta

```yaml
actions:
  - action: dahua_vto_ui.send_command
    data:
      device_id: 1a2b3c4d5e6f7a8b9c0d
      method: configManager.getConfig
      params:
        name: AudioOutputVolume
      event: false
    response_variable: volume
  - action: persistent_notification.create
    data:
      title: Volume VTO
      message: "{{ volume.params.table }}"
```

Gli esempi di comandi del README originale funzionano allo stesso modo, con
`device_id` al posto di `entity_id` (es. `magicBox.listMethod`, `console.runCmd` con
`command: call 9901`, `RecordUpdater.clear` con `send_instance_command`).

### Eventi grezzi del dispositivo

```yaml
triggers:
  - trigger: event
    event_type: dahua_vto_ui
    event_data:
      Code: Invite
```

Ogni evento contiene `device_id`, `entry_id` ed `entity_id` (sensore di connessione).

## Migrazione da `dahua_vto`

| Originale | Dahua VTO UI |
|---|---|
| `sensor` + YAML | config flow |
| lock template + timer | `lock.<nome>_door_1` |
| automazione su `BackKeyLight` | `event.<nome>_doorbell` |
| template `DoorStatus` | `binary_sensor.<nome>_door_status` |
| `configManager.setConfig` CommGlobal | `alarm_control_panel.<nome>_alarm` |
| servizi `dahua_vto.*` con `entity_id` | `dahua_vto_ui.*` con `device_id` |
| evento `dahua_vto` | evento `dahua_vto_ui` |

## Debug

**Impostazioni → Dispositivi e servizi → Dahua VTO UI → Abilita log di debug**, oppure:

```yaml
logger:
  logs:
    custom_components.dahua_vto_ui: debug
```

I log di debug mostrano ogni frame scambiato (password oscurata). La diagnostica
include gli ultimi 20 eventi del dispositivo.

## Crediti e licenza

Basata su [myhomeiot/DahuaVTO](https://github.com/myhomeiot/DahuaVTO) di @myhomeiot,
con ringraziamenti a @elad-bar, @mcw0, @riogrande75 e @Antori91 per le note sul protocollo.
Licenza: [GPL-3.0](LICENSE).
