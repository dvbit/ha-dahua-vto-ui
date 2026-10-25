# Dahua VTO UI — specifica consolidata v2.0.0

Fork di [myhomeiot/DahuaVTO](https://github.com/myhomeiot/DahuaVTO) (GPL-3.0)
reso configurabile interamente da interfaccia, con le configurazioni suggerite
nel README originale trasformate in entità native.

Repository: https://github.com/dvbit/ha-dahua-vto-ui
Istanza di collaudo: generico, collaudo su Casa Bracciano (HA 2026.10.2).
Dispositivi di test: VTO2000A, VTH5221DW.

## 1. Contesto

- 1.1 Dominio `dahua_vto_ui`: coesiste con l'integrazione originale `dahua_vto` (YAML).
- 1.2 Solo UI: nessun supporto YAML, nessun import.
- 1.3 Protocollo: RPC JSON "DHIP" su TCP (porta 5000), lo stesso dell'originale.
- 1.4 Requisito minimo: Home Assistant 2026.9.0.
- 1.5 Licenza GPL-3.0 con credito all'autore originale.

## 2. Config flow

- 2.1 Step `user`: nome (opzionale, default = modello), host, porta (5000), username,
  password. Validazione con login reale e lettura di `magicBox.getSystemInfo`.
- 2.2 Una entry per dispositivo; `unique_id` = numero di serie. Se il dispositivo è già
  configurato, host e porta vengono aggiornati e il flusso si chiude.
- 2.3 Tipo VTO/VTH rilevato dal modello (`deviceType`); modelli non riconosciuti = VTO.
- 2.4 Reauth su credenziali rifiutate (username e password, stesso numero di serie).
- 2.5 Options flow con reload automatico:
  - `timeout` (5–60 s, default 10): timeout comandi e attesa prima della riconnessione;
  - solo VTO: `lock_channels` (1–2, default 1), `unlock_period` (1–30 s, default 2),
    `short_number` (default `HA`), `rtsp_port` (554), `http_port` (80),
    `stream` (`main`/`sub`, default `main`).

## 3. Connessione

- 3.1 Connessione persistente con keep-alive (`global.keepAlive`, intervallo fornito
  dal dispositivo, default 60 s).
- 3.2 Dopo il login: `eventManager.attach` (tutti i codici) e
  `configManager.attach` su `CommGlobal`.
- 3.3 Connessione persa → riconnessione dopo `timeout` secondi.
  Credenziali rifiutate → reauth, nessun nuovo tentativo.
- 3.4 Primo collegamento fallito al setup → `ConfigEntryNotReady`.
- 3.5 Più comandi possono essere in corso contemporaneamente (risposte associate per id).

## 4. Entità

Gli `entity_id` sono generati in inglese (`<dominio>.<nome>_<chiave>`); i nomi visualizzati
sono localizzati. Tutte le entità, tranne il sensore di connessione, sono non disponibili
quando il dispositivo è disconnesso.

### 4.1 Entrambi i tipi
| Chiave | Piattaforma | Comportamento |
|---|---|---|
| `connection` | `sensor` enum | `connected`/`disconnected`; attributi `device_type`, `serial_number`, `software_version`; diagnostica |
| `last_call` | `sensor` timestamp | Da evento `VideoTalkLog`: `BeginTime`; attributi `end_state`, `talk_time`, `local_number`, `missed`; ripristinato al riavvio |
| `reboot` | `button` restart | `magicBox.reboot` |

### 4.2 Solo VTO
| Chiave | Piattaforma | Comportamento |
|---|---|---|
| `doorbell` | `event` doorbell | Da `BackKeyLight.State`: 1/2 → `ring` (una volta per chiamata), 4 → `voice_message`, 5 → `answered`, 6 → `missed` |
| `access` | `event` | Da `AccessControl`: `Status` 1 → `authorized`, 0 → `denied`; attributi `card_no`, `method`, `name`, `user_id`, `reader_id`, `type` |
| `door_<n>` | `lock` | Sblocco = `accessControl.openDoor` sul canale n; stato sbloccato per `unlock_period`, poi bloccato. Anche `BackKeyLight` 8 e `AccessControl` autorizzato segnano sbloccato (tutti i canali). `lock` non invia comandi |
| `door_status` | `binary_sensor` door | Da `DoorStatus.Data.Status` (`Open`/`Close`); ripristinato al riavvio |
| `camera` | `camera` | Stream RTSP `cam/realmonitor` (main/sub) e snapshot HTTP `cgi-bin/snapshot.cgi` con autenticazione digest |
| `cancel_call` | `button` | `console.runCmd` `hc` |

### 4.3 Solo VTH
| Chiave | Piattaforma | Comportamento |
|---|---|---|
| `alarm` | `alarm_control_panel` | Tabella `CommGlobal`: `AlarmEnable` false → `disarmed`; true → `CurrentProfile`: `AtHome` → `armed_home`, `Outdoor` → `armed_away`, `Sleeping` → `armed_night`, `Custom` → `armed_custom_bypass`. I comandi rileggono la tabella e la riscrivono completa con `configManager.setConfig`. Nessun codice richiesto. Aggiornamento da `client.notifyConfigChange` |

## 5. Servizi (target: `device_id` di un dispositivo dell'integrazione)

| Servizio | Campi | Risposta |
|---|---|---|
| `open_door` | `channel` (1–2, default 1), `short_number` (default da opzioni) | — |
| `send_command` | `method`, `params`, `event` (default true), `tag`, `timeout` (default 5) | risposta grezza del dispositivo |
| `send_instance_command` | `method`, `instance_params`, `params`, `event`, `tag`, `timeout` | risposta grezza del metodo |

- 5.1 Dispositivo non appartenente all'integrazione → `ServiceValidationError` tradotto.
- 5.2 Dispositivo disconnesso, timeout o errore del dispositivo → `HomeAssistantError` tradotto.
- 5.3 `send_instance_command`: `<servizio>.factory.instance` → metodo → `<servizio>.destroy`
  (come l'originale).

## 6. Evento bus

`dahua_vto_ui`, stessi payload dell'originale (`client.notifyEventStream`,
`client.notifyConfigChange`, risultati dei comandi con `event: true`) più `device_id`,
`entry_id` ed `entity_id` del sensore di connessione.

## 7. Logging

- DEBUG: frame inviati/ricevuti (password oscurata), eventi ricevuti, keep-alive, snapshot.
- INFO: connesso/disconnesso, setup completato, comandi applicati, cambio stato allarme.
- WARNING: riconnessione fallita, stato `BackKeyLight` sconosciuto, snapshot non disponibile.
- ERROR: credenziali rifiutate, comando rifiutato dal dispositivo.

## 8. Diagnostica

Dati entry e opzioni con username, password e numero di serie oscurati, informazioni di
sistema, stato connessione, ultimi 20 eventi ricevuti.

## 9. Localizzazione

en / it / fr / es / de: form, opzioni, errori, entità, stati, tipi di evento, servizi, eccezioni.

## 10. Packaging

- Repository HACS, `custom_components/dahua_vto_ui/`, `hacs.json`, `manifest.json` 2.0.0
  (prima versione del fork: major sopra l'ultimo tag upstream 1.0.9).
- Icona `brand/` (HA 2026.3+) + `icon.png` in root.
- README EN e IT con esempi, questa specifica, licenza GPL-3.0.
- Workflow CI in `ci/` (hassfest, HACS, lint, test): da copiare in `.github/workflows/`.

## 11. Verifiche eseguite (v2.0.0)

- 22 test pytest su HA reale **2026.10.2** e **2026.9.0**, contro un finto dispositivo
  DHIP su TCP reale: passati (login, errori, comandi concorrenti, istanze, notifiche,
  config flow, reauth, opzioni VTO/VTH, entity_id inglesi con UI italiana, campanello,
  lock e ri-blocco, porta, accessi, ultima chiamata, eventi bus, servizi con risposta,
  pulsanti, allarme VTH, riconnessione, setup retry/unload, camera, diagnostica).
- hassfest (core 2026.10.2): nessun errore.
- ruff (E,F,W,I,UP,B,SIM,D) e ruff format su codice e test: pulito.
- Nessun avviso di deprecazione HA durante i test.
