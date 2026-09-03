<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-PRODUCTION-REPORTS banner" width="100%">
</p>

# 📈 HYDRA-UMC-PRODUCTION-REPORTS

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | 🇮🇹 <b>Italiano</b> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 📑 Motore di reportistica KPI e OEE automatizzato per i direttori di stabilimento

<p align="left">
  <img src="https://img.shields.io/badge/Licenza-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Metriche-OEE%20%2F%20KPI%20%2F%20Cycle--Time-green.svg" alt="Metrics">
  <img src="https://img.shields.io/badge/Esporta-PDF%20%2F%20CSV%20%2F%20JSON-blue.svg" alt="Export">
</p>

---

## 1. 🛠️ PANORAMICA TECNICA

**HYDRA-UMC-PRODUCTION-REPORTS** è il cervello analitico per la gestione della fabbrica. Elabora i dati grezzi dal Datalake per generare report automatizzati di alto livello sull'efficienza della produzione, la qualità e la disponibilità delle macchine.

Calcola l'**OEE (Overall Equipment Effectiveness)** dell'intero sciame, identificando i colli di bottiglia nella linea di produzione e fornendo la tracciabilità per ogni componente prodotto, dai profili di saldatura termica all'accuratezza del Pick-and-Place.

### Caratteristiche principali:
* 📈 **Calcolo OEE:** Metriche in tempo reale per disponibilità, prestazioni e qualità.
* 📑 **Reportistica automatizzata:** Riepiloghi PDF/CSV giornalieri, settimanali e mensili inviati ai manager.
* 🛠️ **Analisi dei colli di bottiglia:** Identifica quali robot o strumenti stanno causando ritardi nella coda delle missioni.
* 🌡️ **Tracciabilità della qualità:** Collega ogni prodotto finale ai suoi log di assemblaggio specifici (termici, visivi, meccanici).
* 🕐 **Confini Turno/Giorno:** `shift.py` fornisce a ogni report un'unica, vera fonte di verità su dove inizia e finisce un turno o un giorno di calendario - incluso un vero turno di notte che attraversa la mezzanotte. *(implementato)*
* 🧾 **Formule Versionate + Tracciabilità:** Ogni report porta il suo vero `formula_version` e un `input_fingerprint` sha256 calcolato sui dati esatti che lo hanno prodotto. *(implementato)*
* 📤 **Esportazione CSV Riproducibile:** `GET /reports/{oee,availability}/export` - output identico byte per byte per input identici, non solo "abbastanza vicino". *(implementato)*

---

## 2. 🔄 FLUSSO DI REPORTISTICA

```mermaid
flowchart LR
    LAKE["HYDRA-UMC-DATALAKE"] --> PROC["Report Processor Engine"]
    PROC --> OEE["Calcolo OEE & KPI"]
    OEE --> TPL["Report Templating (PDF/HTML)"]
    TPL --> DISP["Dashboard / Email / Esporta"]
```

---

## 3. 🧱 ARCHITETTURA E DECISIONI DI PROGETTAZIONE

* **Perché è fratello, non un sottomodulo, di HYDRA-UMC-DATALAKE.** La generazione di report è un lavoro di query+calcolo periodico e di sola lettura su telemetria già memorizzata - tenerla separata significa che una generazione di report non compete mai con le scritture in tempo reale proprie di HYDRA-UMC-TELEMETRY-COLLECTOR per le risorse dello stesso archivio. Questo progetto non ha un proprio database.
* **Integrazione HTTP reale, non una libreria condivisa.** `datalake_client.py` parla con un'istanza reale di HYDRA-UMC-DATALAKE tramite HTTP semplice (`GET /query`), e deliberatamente NON importa il pacchetto Python di DATALAKE direttamente anche se oggi entrambi convivono nello stesso ambiente di sviluppo - l'HTTP reale è il vero punto di integrazione disaccoppiato, coerente con come servizi/repository separati parlerebbero davvero tra loro in produzione.
* **Lo schema `production_event` è la convenzione propria v0 di questo progetto, non uno standard dell'ecosistema.** L'OEE deve sapere, per ogni ciclo completato, se il pezzo era buono e quanto è durato il ciclo. Questo progetto definisce ciò come un `Sample` DATALAKE per ciclo, `kind="production_event"`, con due campi scritti insieme allo stesso timestamp: `"good"` (1.0/0.0) e `"cycleTimeS"`. Nessun altro progetto scrive ancora questo kind - HYDRA-UMC-JOB-DISPATCHER sarebbe la vera fonte di produzione una volta collegato per riportare i completamenti dei cicli in questo modo (tracciato in `mejoras_futuras.txt`).
* **La disponibilità funziona contro QUALSIASI stream di telemetria esistente, di proposito.** A differenza dell'OEE, `availability.py` non ha affatto bisogno della convenzione `production_event` - prende qualsiasi serie di timestamp già presente in DATALAKE (es. campioni `motor_temp` che HYDRA-UMC-TELEMETRY-COLLECTOR già scrive) e segnala come vero downtime un intervallo tra campioni consecutivi maggiore di `expected_interval_ms x gap_factor`. Questo è esattamente ciò che oggi permette a questo progetto di "parlare" con i suoi fratelli di telemetria, prima ancora che qualsiasi progetto scriva dati `production_event` reali.
* **Performance e Availability sono limitati a `[0, 1]`.** Un ciclo reale può andare più veloce di una cifra "ideale" prudente, oppure il tempo operativo può superare una finestra pianificata mal configurata; riportare >100% sarebbe aritmeticamente difendibile ma fuorviante nella pratica, quindi entrambi sono limitati.
* **I campi `production_event` non abbinati vengono contati e segnalati, mai sostituiti silenziosamente con un valore predefinito.** Se una lettura `"good"` non ha un `"cycleTimeS"` corrispondente allo stesso timestamp esatto (una scrittura parziale/malformata), viene esclusa e il numero di letture escluse è incluso nell'errore/risposta invece di assumere un tempo di ciclo di 0s, il che corromperebbe la cifra di Performance.
* **Perché `shift.py` è un modulo separato, non matematica inline in `oee.py`/`availability.py`.** Due report che discordano silenziosamente su dove taglia un turno o un giorno di calendario producono due numeri diversi, entrambi difendibili, per la stessa finestra reale - esattamente la contraddizione segnalata dall'audit di promozione. Rendere i confini turno/giorno un modulo proprio, reale e testato (con un vero turno di notte che attraversa la mezzanotte, e una vera ricerca inversa timestamp-turno) dà a ogni report la stessa fonte di verità invece di far ri-derivare la propria a ogni chiamante.
* **Perché `formula_version` e `input_fingerprint` sono sul report, non solo nel CSV.** Un report consumato come JSON (da una dashboard, un altro servizio) merita la stessa tracciabilità di uno esportato su file - entrambi i campi sono additivi sulle risposte già esistenti di `GET /reports/oee`/`GET /reports/availability`, non qualcosa visibile solo nell'output di `export.py`.
* **Perché l'esportazione CSV è un nuovo endpoint, non un flag `?format=csv` sulle rotte esistenti.** Mantenere `GET /reports/oee`/`GET /reports/availability` intatti (ancora reali, ancora JSON, ancora esattamente ciò che `tests/test_api.py` già verificava) ha permesso di aggiungere la funzione di esportazione e dimostrarne la riproducibilità senza toccare nemmeno una risposta già testata.

---

## 📂 STRUTTURA DELLE CARTELLE

Servizio puramente software (generazione di report) - senza hardware, firmware o sistema operativo propri; tali cartelle sono omesse secondo la politica della struttura del repository.

```text
HYDRA-UMC-PRODUCTION-REPORTS/
├── src/hydra_umc_production_reports/
│   ├── __init__.py           # Versione del pacchetto
│   ├── datalake_client.py    # Client HTTP reale per l'API GET /query di HYDRA-UMC-DATALAKE
│   ├── oee.py                 # Vera formula OEE (Disponibilità x Performance x Qualità)
│   ├── availability.py        # Vero calcolo del downtime dagli intervalli di telemetria
│   ├── reports.py             # Orchestrazione: DatalakeClient -> oee.py / availability.py
│   ├── shift.py                # Vero calcolo dei confini turno/giorno (fonte di verità)
│   ├── export.py               # Vera esportazione CSV, riproducibile byte per byte
│   ├── api.py                  # API HTTP reale (GET /reports/oee, /reports/availability, /stats, /export)
│   └── main.py                 # Punto di ingresso - avvia il vero server HTTP
├── tests/                    # Test reali: matematica di OEE/disponibilità/turno/esportazione, round-trip contro un DATALAKE fittizio
├── docs/
│   └── API.md                 # Riferimento reale degli endpoint HTTP (richieste, risposte, codici di stato)
├── images/                   # Media e diagrammi
├── systemd/
│   └── hydra-umc-production-reports.service # Unità systemd della API locale di report sulla CM5
├── tools/
│   ├── build_test.py         # Controllo build senza versionamento
│   └── ci_validate.py        # Validazione manifest/CHANGELOG/docs usata dalla CI
├── pyproject.toml            # Metadati del pacchetto + extra [dev] (pytest)
├── bump_version.py           # Incremento di versione nativa stile contachilometri (eseguito dal build)
├── bump_manifest_version.py  # Sincronizza la versione di hydra-umc.project.json con quella nativa (--sync)
├── build.sh / build.bat      # Build reale: venv + installazione editable + suite di test reale
├── run.sh / run.bat          # Esecuzione reale: avvia l'API HTTP
└── README.md
```

Vedi [`docs/API.md`](docs/API.md) per il riferimento completo degli endpoint HTTP.

---

## 4. ⚙️ BUILD ED ESECUZIONE

Richiede Python >= 3.10.

```bash
# Linux/macOS
./build.sh && ./run.sh --datalake-url http://localhost:8095

# Windows
build.bat
run.bat --datalake-url http://localhost:8095
```

`build` crea/attiva un `.venv` locale, installa il pacchetto in modalità editable con gli extra di sviluppo, verifica l'import ed esegue la vera suite di test `pytest`. `run` avvia la vera API HTTP (porta predefinita `8099`) contro un'istanza HYDRA-UMC-DATALAKE su `--datalake-url` (predefinito `http://localhost:8095`).

```bash
# Vero report OEE per la fonte "robot-1" su una finestra di 5 secondi
curl "http://localhost:8099/reports/oee?sourceId=robot-1&start=0&end=5000&plannedTimeS=10.0&idealCycleTimeS=2.0"

# Vero report di disponibilità per lo stream motor_temp della stessa fonte
curl "http://localhost:8099/reports/availability?sourceId=robot-1&kind=motor_temp&field=value&start=0&end=10000&expectedIntervalMs=1000"
```

---

## 🚀 TABELLA DI MARCIA
* **Fatto (v0):** vero calcolo OEE e disponibilità, vera integrazione HTTP con HYDRA-UMC-DATALAKE, vera API HTTP.
* **Prossimo:** una vera fonte di dati `production_event` - collegare HYDRA-UMC-JOB-DISPATCHER affinché riporti i completamenti dei cicli con questo schema.
* **Prossimo:** report persistenti/pianificati (oggi ogni report viene calcolato in tempo reale, su richiesta).
* **Più avanti:** vera esportazione PDF/CSV e una dashboard, secondo la roadmap originale sotto.
* Analisi ROI guidata dall'IA per l'aggiornamento degli strumenti, collegata a questi stessi dati OEE una volta memorizzati i trend storici.

---

## 🔗 Progetti Correlati

Questo progetto fa parte dell'ecosistema robotico HYDRA-UMC dello stesso autore (JuanenRac / Electro Hobby 3D). Vale la pena conoscerlo, poiché una richiesta potrebbe in realtà riguardare uno di questi invece di questo repository.

**Progetto Padre**
- **[HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)** — vero archivio di serie temporali basato su sqlite3, con una vera API HTTP di ingestione/query; il genitore di cui questo repository è un servizio di analisi specifico, all'interno del proprio livello di dati e analisi.

**Progetti Fratelli** — gli altri servizi di analisi del livello di dati e analisi proprio di HYDRA-UMC-DATALAKE
- **[HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)** — vera pipeline di ingestione CAN/WebSocket verso DATALAKE, con deduplicazione per sequenza — oggi anche una vera fonte di dati: il proprio `availability.py` di questo report legge i suoi campioni `motor_temp` (e qualsiasi altra serie che scrive) direttamente da Datalake, senza bisogno della convenzione `production_event` per quel report.
- **[HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)** — vero rilevatore di anomalie FFT + baseline statistica, con monitoraggio della deriva.

**Direttamente Correlati**
- **[HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)** — vera coda di lavori basata su priorità con deduplicazione, su una vera API HTTP — la fonte reale prevista dei campioni `production_event` (la propria convenzione good/tempo-ciclo di OEE) una volta che i completamenti missione saranno collegati per scriverli; nulla scrive ancora quel tipo, tracciato onestamente come lavoro futuro anziché dichiarato completato.

**Fa Anche Parte dell'Ecosistema**

*Hardware e Piattaforma di Base*
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — la scheda madre fisica del braccio robotico: host CM5 + coprocessore STM32H745 dual-core, che coordina fino a 8 bracci utensile via CAN-OTA/SPI-OTA.
- **[HYDRA-UMC-OS](https://github.com/JuanenRac/HYDRA-UMC-OS)** — livello prodotto riproducibile su Raspberry Pi OS per il CM5: agente in sola lettura, config/profili validati, provisioning WiFi al primo contatto.
- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** — il contratto JSON-Schema condiviso e la barriera di sicurezza contro cui ogni bridge valida i propri comandi.

*Backend Centrale e Client*
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — il vero backend headless (REST/WebSocket) con cui parla davvero ogni client di controllo.
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — dashboard di controllo web con visualizzazione 3D multi-robot in tempo reale.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — centro di comando sciame desktop (PySide6) per più server contemporaneamente, pacchettizzato come eseguibile standalone.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — app di controllo nativa per Android con login biometrico e un companion Wear OS abbinato.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — app di controllo per iOS/iPadOS (Flutter) con sincronizzazione WebSocket in tempo reale.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — interfaccia touch nativa per il touchscreen DSI da 7" a bordo, incorporata direttamente nel CM5.
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — creatore/editor grafico desktop di URDF che invia i modelli finiti al catalogo di STUDIO.
- **[HYDRA-UMC-BRIDGE-AMR](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-AMR)** — barriera di coordinamento per flotte AGV/AMR tramite un publisher MQTT VDA 5050 reale.
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** — coordinatore ad alto livello per celle CNC con accesso reale a stato/byte di controllo GRBL.
- **[HYDRA-UMC-BRIDGE-DROIDS](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-DROIDS)** — barriera di coordinamento per droidi con zampe/umanoidi, con un vero mittente di comandi per Boston Dynamics Spot.
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** — coordinatore di sicurezza per celle laser che legge 3 salvaguardie GPIO reali di chiave/involucro/interblocco.
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** — coordinatore ad alto livello sicuro per il flusso schede del pick-and-place OpenPnP.
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** — barriera di coordinamento sicura per stampanti 3D Moonraker/Klipper, con comandi di lavoro reali e controllati.
- **[HYDRA-UMC-BRIDGE-ROS2](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-ROS2)** — coordinatore di sicurezza con un vero trasporto ROS 2 rclpy, importato in modo lazy.
- **[HYDRA-UMC-BRIDGE-UAV](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-UAV)** — barriera di coordinamento per UAV dotati di fotocamera, con un vero mittente di comandi MAVLink.

*Piattaforma Strumenti URTC*
- **[URTC](https://github.com/JuanenRac/URTC)** — firmware per la scheda fisica dell'Universal Robot Tool Controller, oltre 25 profili utensile su bus CAN.
- **[URTC-FLASHER](https://github.com/JuanenRac/URTC-FLASHER)** — strumento desktop con GUI per il flashing delle schede URTC, CAN-OTA più SWD/JTAG a chip intero.
- **[URTC-TESTER](https://github.com/JuanenRac/URTC-TESTER)** — strumento desktop di diagnostica CAN-bus dal vivo per schede URTC, un pannello per profilo utensile.
- **[URTC-WEB-STUDIO](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — alternativa basata su browser a URTC-TESTER tramite la Web Serial API, senza installazione locale.

*Nodo IA Visione (Hailo-8)*
- **[HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)** — hub di integrazione per la pipeline di visione Hailo-8, con un vero controllo di prontezza hardware per fase.
- **[HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)** — registro reale di modelli compilati con verifica di caricamento sicuro per architettura Hailo/checksum.
- **[HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)** — generatore reale di pipeline GStreamer + config MediaMTX, con una vera barriera di integrazione HailoRT.
- **[HYDRA-UMC-VISUAL-SERVOING-API](https://github.com/JuanenRac/HYDRA-UMC-VISUAL-SERVOING-API)** — vera legge di correzione Position-Based Visual Servoing, con cancello di sicurezza sullo stato di zona a monte.
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — vero controllo di violazione zona e richiesta E-STOP, con imposizione della freschezza di calibrazione.

*Nodo IA Cognitivo (Hailo-10)*
- **[HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)** — hub di integrazione per la pipeline cognitiva Hailo-10 (orchestrazione LLM/VLA/voce).
- **[HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)** — vera codifica/decodifica di token d'azione e generazione di traiettoria per un modello Vision-Language-Action.
- **[HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)** — vero front-end vocale (VAD + parser di intenti) con un relay verso Watch limitato e soggetto a conferma.
- **[HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)** — vera scomposizione dei task basata su regole e recupero semantico degli errori sui codici errore MCU.
- **[HYDRA-UMC-DOCS-QA](https://github.com/JuanenRac/HYDRA-UMC-DOCS-QA)** — vera ricerca documentale TF-IDF (solo libreria standard) sui documenti Markdown di questo ecosistema.

*Orchestrazione e Sciame*
- **[HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)** — hub di integrazione con un vero contratto di health-report gRPC/Protobuf e una macchina a stati di missione.
- **[HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)** — vero watchdog di salute della flotta basato su gRPC, con retry/backoff e rilevamento di discrepanza d'identità.
- **[HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)** — vero pianificatore di percorsi 3D basato su RRT, con vera validazione delle collisioni ostacolo/spazio di lavoro.
- **[HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)** — vera sincronizzazione di stato CRDT LWW-Element-Map, con property test per la convergenza multi-cella.

*Gemello Digitale e Simulazione*
- **[HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)** — hub di integrazione per il motore di gemello digitale, con un vero contratto di sincronizzazione per compatibilità di versione.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — vero interblocco di sicurezza hardware-in-the-loop che instrada i comandi tra simulazione e hardware reale.
- **[HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)** — vera cinematica diretta e validazione dei limiti articolari su un vero sottoinsieme URDF.
- **[HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)** — vero generatore procedurale di scene 2D con esportazione di annotazioni YOLO/COCO.

*Gateway Industriale*
- **[HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)** — hub di integrazione che inoltra ai protocolli industriali, con un vero livello di allowlist dei comandi/backpressure.
- **[HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)** — vero spazio di indirizzi OPC-UA, verificato con una vera sessione client del protocollo binario.
- **[HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)** — vero broker MQTT con autenticazione opzionale per client e ACL sui topic.
- **[HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)** — veri endpoint XML `/probe` e `/current` di MTConnect, con output in modalità degradata.

*Strumenti Complementari e Operazioni dell'Ecosistema*
- **[HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)** — pannelli Smart Summaries e Anomaly Highlighting su DATALAKE/ANOMALY-DETECTOR, con un fallback statistico onesto.
- **[HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)** — CLI di flotta con un vero e stabile contratto di exit-code, un client live reale della stessa API di HYDRA-UMC-SERVER.
- **[HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)** — app companion WearOS con avvisi aptici reali e un relay vocale verso il telefono abbinato.
- **[URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)** — firmware per un rack di montaggio schede con decodifica reale dell'ID utensile e logica di preriscaldamento Smart Idle.
- **[URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)** — firmware più un vero companion di visione Python per una testa utensile di ispezione termica/RGB.
- **[HYDRA-UMC-UPDATER](https://github.com/JuanenRac/HYDRA-UMC-UPDATER)** — strumento amministrativo desktop che scopre, clona e aggiorna ogni repository di questo ecosistema.


## 👤 AUTORE
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com
📺 [youtube.com/@electrohobby3d](https://youtube.com/@electrohobby3d)

## 📜 LICENZA
GPL-3.0 - Vedere LICENSE per i dettagli.
