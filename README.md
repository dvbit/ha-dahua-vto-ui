<p align="center"><img src="icon.png" width="128" alt="Dahua VTO UI"></p>

# Dahua VTO UI

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
![Version](https://img.shields.io/badge/version-2.1.0-blue)
![HA](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-41BDF5)

🇮🇹 [Leggi in italiano](README.it.md)

Home Assistant integration for **Dahua VTO outdoor stations and VTH indoor monitors**,
configured entirely from the UI.

It is a fork of [myhomeiot/DahuaVTO](https://github.com/myhomeiot/DahuaVTO): same
local protocol, but the YAML recipes of the original README (template lock with
timer, doorbell automations, door status template, VTH alarm commands) are now
native entities. The full specification is in [SPEC.md](SPEC.md).

## Features

| VTO (outdoor station) | VTH (indoor monitor) |
|---|---|
| `event` doorbell: `ring`, `answered`, `missed`, `voice_message` | `alarm_control_panel` on the VTH profiles |
| `lock` per door relay (1–2), auto relock | `sensor` last call |
| `binary_sensor` door status | `button` reboot |
| `event` access control (`authorized` / `denied`) | `sensor` connection |
| `camera` (RTSP stream + snapshot) | |
| `sensor` last call, `sensor` connection | |
| `button` reboot, `button` cancel call | |

Plus the services `open_door`, `send_command`, `send_instance_command` (now with a
direct response) and the `dahua_vto_ui` event with the same payloads as the original.

Tested with VTO2000A and VTH5221DW; the models listed by the original integration
should work as well.

## New in 2.1.0

- **Disarm PIN** for the VTH alarm (options of the VTH entry): required only to disarm,
  arming stays without PIN.
- **Fingerprint ID** on access events (`fingerprint_id`) and **fingerprint → person**
  links (VTO options → *Fingerprints and people*). The VTO cannot list its fingerprints,
  so the form offers the IDs already seen; you can also type one.
- **Access log**: `sensor.<name>_access_log` and the `get_access_log` service keep the
  last 60 accesses and calls, also after a restart.

## Installation

1. HACS → ⋮ → **Custom repositories** → `https://github.com/dvbit/ha-dahua-vto-ui`,
   category **Integration**.
2. Install **Dahua VTO UI** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Dahua VTO UI**.

It coexists with the original `dahua_vto` integration, so you can migrate step by step.

## Configuration

| Field | Description |
|---|---|
| Name | Optional; the model is used when empty |
| Host / Port | Device address, RPC port (default 5000) |
| Username / Password | Device web account |

The device type (VTO or VTH) is detected automatically. Entity IDs are created in
English from the name, e.g. `lock.citofono_door_1`.

### Options

| Option | Default | Device | Notes |
|---|---|---|---|
| Timeout | 10 s | both | Command timeout and wait before reconnecting |
| Door relays | 1 | VTO | Number of `lock` entities |
| Unlock period | 2 s | VTO | Match the "unlock period" set on the VTO |
| Short number | `HA` | VTO | Identifier sent with open commands |
| RTSP / HTTP port | 554 / 80 | VTO | Camera stream and snapshot |
| Video stream | main | VTO | `main` or `sub` |

## Examples

### Doorbell notification with snapshot

```yaml
alias: Doorbell notification
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
      filename: /config/www/doorbell.jpg
  - action: notify.mobile_app_phone
    data:
      title: Doorbell
      message: Someone is at the door
      data:
        image: /local/doorbell.jpg
mode: single
```

### Open the gate

```yaml
action: lock.unlock
target:
  entity_id: lock.citofono_door_1
```

Or with the service (channel 2, custom short number):

```yaml
action: dahua_vto_ui.open_door
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d
  channel: 2
  short_number: HA
```

### Missed call notification

```yaml
alias: Missed intercom call
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
  - action: notify.mobile_app_phone
    data:
      message: Missed call at the intercom
mode: single
```

### Arm the VTH alarm when leaving

```yaml
alias: Arm VTH when away
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

| HA state | VTH profile |
|---|---|
| `disarmed` | `AlarmEnable: false` |
| `armed_home` | `AtHome` |
| `armed_away` | `Outdoor` |
| `armed_night` | `Sleeping` |
| `armed_custom_bypass` | `Custom` |

### Raw command with response

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
      title: VTO volume
      message: "{{ volume.params.table }}"
```

The command examples of the original README work the same way, using `device_id`
instead of `entity_id` (e.g. `magicBox.listMethod`, `console.runCmd` with
`command: call 9901`, `RecordUpdater.clear` with `send_instance_command`).

### Access log in a dashboard

```yaml
type: markdown
title: Intercom log
content: >-
  {% for e in state_attr('sensor.citofono_access_log', 'history')[:15] %}
  {{ as_timestamp(e.time) | timestamp_custom('%d/%m %H:%M') }} ·
  {% if e.type == 'access' %}🔓 {{ e.method_name }}
  {%- if e.person %} {{ state_attr(e.person, 'friendly_name') }}{% endif %}
  {%- else %}🔔 {{ e.event }} {{ e.number or '' }}{% endif %}

  {% endfor %}
```

### Welcome by fingerprint

```yaml
alias: Welcome home by fingerprint
triggers:
  - trigger: state
    entity_id: event.citofono_access
    not_from: unavailable
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.person is not none }}"
actions:
  - action: notify.mobile_app_phone
    data:
      message: >-
        {{ state_attr(trigger.to_state.attributes.person, 'friendly_name') }}
        opened the gate
mode: queued
```

### Last 10 entries in a script

```yaml
actions:
  - action: dahua_vto_ui.get_access_log
    data:
      device_id: 1a2b3c4d5e6f7a8b9c0d
      limit: 10
    response_variable: log
  - action: persistent_notification.create
    data:
      message: "{{ log.entries | map(attribute='type') | list }}"
```

### Raw device events

```yaml
triggers:
  - trigger: event
    event_type: dahua_vto_ui
    event_data:
      Code: Invite
```

Every event carries `device_id`, `entry_id` and `entity_id` (connection sensor).

## Migration from `dahua_vto`

| Original | Dahua VTO UI |
|---|---|
| `sensor` + YAML | config flow |
| template lock + timer | `lock.<name>_door_1` |
| automation on `BackKeyLight` | `event.<name>_doorbell` |
| template `DoorStatus` | `binary_sensor.<name>_door_status` |
| `configManager.setConfig` CommGlobal | `alarm_control_panel.<name>_alarm` |
| `dahua_vto.*` services with `entity_id` | `dahua_vto_ui.*` with `device_id` |
| event `dahua_vto` | event `dahua_vto_ui` |

## Debugging

**Settings → Devices & services → Dahua VTO UI → Enable debug logging**, or:

```yaml
logger:
  logs:
    custom_components.dahua_vto_ui: debug
```

Debug logs show every frame exchanged (password hidden). Diagnostics include the
last 20 device events.

**Note:** the VTO2000A closes the session on `system.listService`; the integration
reconnects automatically. Use `magicBox.listMethod` or `<Service>.listMethod` instead.

## Credits and license

Based on [myhomeiot/DahuaVTO](https://github.com/myhomeiot/DahuaVTO) by @myhomeiot,
with thanks to @elad-bar, @mcw0, @riogrande75 and @Antori91 for the protocol notes.
License: [GPL-3.0](LICENSE).
