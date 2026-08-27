<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-PRODUCTION-REPORTS banner" width="100%">
</p>

# 📈 HYDRA-UMC-PRODUCTION-REPORTS

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | 🇫🇷 <b>Français</b> | <a href="README_ita.md">🇮🇹 Italiano</a> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 📑 Moteur de rapports KPI et OEE automatisés pour les directeurs d'usine

<p align="left">
  <img src="https://img.shields.io/badge/Licence-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Métriques-OEE%20%2F%20KPI%20%2F%20Cycle--Time-green.svg" alt="Metrics">
  <img src="https://img.shields.io/badge/Exporter-PDF%20%2F%20CSV%20%2F%20JSON-blue.svg" alt="Export">
</p>

---

## 1. 🛠️ APERÇU TECHNIQUE

**HYDRA-UMC-PRODUCTION-REPORTS** est le cerveau analytique pour la gestion de l'usine. Il traite les données brutes du Datalake pour générer des rapports automatisés de haut niveau sur l'efficacité de la production, la qualité et la disponibilité des machines.

Il calcule l'**OEE (Overall Equipment Effectiveness - Taux de Rendement Global)** de l'ensemble de l'essaim, identifiant les goulots d'étranglement dans la ligne de production et assurant la traçabilité de chaque composant fabriqué, des profils de soudure thermique à la précision du Pick-and-Place.

### Caractéristiques principales :
* 📈 **Calcul de l'OEE :** Métriques en temps réel pour la disponibilité, la performance et la qualité.
* 📑 **Rapports automatisés :** Résumés quotidiens, hebdomadaires et mensuels aux formats PDF/CSV envoyés aux gestionnaires.
* 🛠️ **Analyse des goulots d'étranglement :** Identifie les robots ou outils qui causent des retards dans la file d'attente des missions.
* 🌡️ **Traçabilité de la qualité :** Lie chaque produit final à ses journaux d'assemblage spécifiques (thermiques, visuels, mécaniques).

---

## 2. 🔄 FLUX DE RAPPORTS

```mermaid
flowchart LR
    LAKE["HYDRA-UMC-DATALAKE"] --> PROC["Moteur de traitement des rapports"]
    PROC --> OEE["Calcul OEE & KPI"]
    OEE --> TPL["Modèles de rapports (PDF/HTML)"]
    TPL --> DISP["Tableau de bord / E-mail / Exportation"]
```

---

## 3. 🧱 ARCHITECTURE & DÉCISIONS DE CONCEPTION

* **Pourquoi c'est un frère, pas un sous-module, de HYDRA-UMC-DATALAKE.** La génération de rapports est un travail de requête+calcul périodique et en lecture seule sur de la télémétrie déjà stockée - la garder séparée signifie qu'une génération de rapport ne concurrence jamais les propres écritures temps réel de HYDRA-UMC-TELEMETRY-COLLECTOR pour les ressources du même entrepôt. Ce projet ne possède aucune base de données propre.
* **Une vraie intégration HTTP, pas une bibliothèque partagée.** `datalake_client.py` parle à une instance réelle de HYDRA-UMC-DATALAKE via du HTTP simple (`GET /query`), et n'importe délibérément PAS le paquet Python de DATALAKE directement, même si les deux cohabitent aujourd'hui dans le même environnement de dev - le vrai HTTP est le vrai point d'intégration découplé, celui qui correspond à la façon dont des dépôts/services séparés se parleraient réellement en production.
* **Le schéma `production_event` est la propre convention v0 de ce projet, pas un standard de l'écosystème.** L'OEE a besoin de savoir, pour chaque cycle terminé, si la pièce était bonne et combien de temps le cycle a pris. Ce projet définit cela comme un `Sample` DATALAKE par cycle, `kind="production_event"`, avec deux champs écrits ensemble au même timestamp : `"good"` (1.0/0.0) et `"cycleTimeS"`. Aucun autre projet n'écrit encore ce kind - HYDRA-UMC-JOB-DISPATCHER serait la vraie source de production une fois connecté pour rapporter les fins de cycle de cette façon (suivi dans `mejoras_futuras.txt`).
* **La disponibilité fonctionne contre N'IMPORTE QUEL flux de télémétrie existant, volontairement.** Contrairement à l'OEE, `availability.py` n'a pas du tout besoin de la convention `production_event` - il prend n'importe quelle série de timestamps déjà présente dans DATALAKE (par ex. des échantillons `motor_temp` que HYDRA-UMC-TELEMETRY-COLLECTOR écrit déjà) et signale comme véritable temps d'arrêt un écart entre échantillons consécutifs supérieur à `expected_interval_ms x gap_factor`. C'est précisément ce qui permet à ce projet de "parler" dès aujourd'hui avec ses frères de télémétrie, avant même qu'aucun projet n'écrive de vraies données `production_event`.
* **Performance et Availability sont plafonnés à `[0, 1]`.** Un vrai cycle peut aller plus vite qu'un chiffre "idéal" prudent, ou le temps opérationnel peut dépasser une fenêtre planifiée mal configurée ; rapporter >100% serait arithmétiquement défendable mais trompeur en pratique, donc les deux sont plafonnés.
* **Les champs `production_event` non appariés sont comptés et signalés, jamais silencieusement remplacés par défaut.** Si une lecture `"good"` n'a pas de `"cycleTimeS"` apparié au même timestamp exact (une écriture partielle/malformée), elle est exclue et le nombre de lectures exclues est inclus dans l'erreur/la réponse plutôt que de supposer un temps de cycle de 0s, ce qui corromprait le chiffre de Performance.

---

## 📂 STRUCTURE DES RÉPERTOIRES

Service purement logiciel (génération de rapports) - sans hardware/firmware/os propres, retirés du template (voir la convention de l'écosystème dans `SONNET/_papelera/`).

```text
HYDRA-UMC-PRODUCTION-REPORTS/
├── src/hydra_umc_production_reports/
│   ├── __init__.py           # Version du paquet
│   ├── datalake_client.py    # Client HTTP réel pour l'API GET /query de HYDRA-UMC-DATALAKE
│   ├── oee.py                 # Vraie formule OEE (Disponibilité x Performance x Qualité)
│   ├── availability.py        # Vrai calcul de downtime à partir des écarts de télémétrie
│   ├── reports.py             # Orchestration : DatalakeClient -> oee.py / availability.py
│   ├── api.py                  # API HTTP réelle (GET /reports/oee, /reports/availability, /stats)
│   └── main.py                 # Point d'entrée - démarre le vrai serveur HTTP
├── tests/                    # Vrais tests, incluant des allers-retours contre un faux DATALAKE
├── pyproject.toml            # Métadonnées du paquet + extras [dev] (pytest)
├── bump_version.py           # Incrément de version type compteur kilométrique (exécuté par le build)
├── build.sh / build.bat      # Build réel : venv + installation éditable + vraie suite de tests
├── run.sh / run.bat          # Exécution réelle : démarre l'API HTTP
└── README.md
```

---

## 4. ⚙️ BUILD ET EXÉCUTION

Nécessite Python >= 3.10.

```bash
# Linux/macOS
./build.sh && ./run.sh --datalake-url http://localhost:8095

# Windows
build.bat
run.bat --datalake-url http://localhost:8095
```

`build` crée/active un `.venv` local, installe le paquet en mode éditable avec les extras de dev, vérifie l'import et exécute la vraie suite de tests `pytest`. `run` démarre la vraie API HTTP (port par défaut `8099`) contre une instance HYDRA-UMC-DATALAKE à `--datalake-url` (par défaut `http://localhost:8095`).

```bash
# Vrai rapport OEE pour la source "robot-1" sur une fenêtre de 5 secondes
curl "http://localhost:8099/reports/oee?sourceId=robot-1&start=0&end=5000&plannedTimeS=10.0&idealCycleTimeS=2.0"

# Vrai rapport de disponibilité pour le flux motor_temp de la même source
curl "http://localhost:8099/reports/availability?sourceId=robot-1&kind=motor_temp&field=value&start=0&end=10000&expectedIntervalMs=1000"
```

---

## 🚀 ROADMAP
* **Fait (v0) :** vrai calcul d'OEE et de disponibilité, vraie intégration HTTP avec HYDRA-UMC-DATALAKE, vraie API HTTP.
* **Ensuite :** une vraie source de données `production_event` - connecter HYDRA-UMC-JOB-DISPATCHER pour qu'il rapporte les fins de cycle selon ce schéma.
* **Ensuite :** rapports persistants/planifiés (aujourd'hui chaque rapport est calculé en direct, à la demande).
* **Plus tard :** un vrai export PDF/CSV et un tableau de bord, selon la roadmap d'origine ci-dessous.
* Analyse du ROI pilotée par l'IA pour les mises à niveau d'outils, connectée à ces mêmes données OEE une fois les tendances historiques stockées.

---

## 🔗 Projets Liés

Ce projet fait partie d'un écosystème robotique plus large du même auteur (JuanenRac / Electro Hobby 3D), couvrant firmware, logiciel de contrôle, nœuds IA et outillage de flotte. Bon à savoir, car une demande pourrait en réalité concerner l'un de ces projets plutôt que ce dépôt.

### Famille

**Parent :** **[HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)** — le parent d'intégration sur la télémétrie stockée duquel ce projet fait rapport.

**Frères et sœurs :**
- **[HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)** — service d'analytique frère, même parent.
- **[HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)** — service d'analytique frère, même parent.

### Relation Directe (hors de la famille)

Ce projet n'a pas de relation directe hors de la famille Data & Analytics (selon la carte de relations de l'écosystème) - voir « Reste de l'Écosystème » ci-dessous pour tout le reste.

### Reste de l'Écosystème

**Plateforme HYDRA-UMC** — la cellule de micro-usine multi-robot
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — la carte mère CM5 + STM32H745 orchestrant jusqu'à 8 bras robotiques.
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — le backend Express/WebSocket auquel parle chaque client de contrôle.
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — tableau de bord de contrôle web, visualisation 3D multi-robot.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — application de contrôle Android via Wi-Fi/Bluetooth.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — application de contrôle iOS/iPadOS construite en Flutter.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — centre de commande d'essaim de bureau (Python/PySide6).
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — éditeur de modèles URDF de bureau pour le catalogue de robots.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — interface tactile native pour l'écran DSI embarqué.

**Plateforme URTC** — le contrôleur de tête d'outil que porte chaque bras HYDRA-UMC
- **[URTC](https://github.com/JuanenRac/URTC)** — contrôleur de tête d'outil sur bus CAN, 25 profils d'outil.
- **[URTC-FLASHER](https://github.com/JuanenRac/URTC-FLASHER)** — outil de bureau de flashage CAN-OTA + SWD/JTAG.
- **[URTC-TESTER](https://github.com/JuanenRac/URTC-TESTER)** — outil de bureau de diagnostic CAN en direct.
- **[URTC-WEB-STUDIO](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — alternative basée navigateur via l'API Web Serial.

**🎥 Vision AI Node (Hailo-8)**
- [HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)
- [HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)
- [HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)
- [HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)
- [HYDRA-UMC-VISUAL-SERVOING-API](https://github.com/JuanenRac/HYDRA-UMC-VISUAL-SERVOING-API)

**🧠 Cognitive AI Node (Hailo-10)**
- [HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)
- [HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)
- [HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)
- [HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)
- [HYDRA-UMC-DOCS-QA](https://github.com/JuanenRac/HYDRA-UMC-DOCS-QA)

**🐝 Orchestration & Swarm**
- [HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)
- [HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)
- [HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)
- [HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)
- [HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)

**🎮 Digital Twin & Simulation**
- [HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)
- [HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)
- [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)
- [HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)

**🏭 Industrial Gateway**
- [HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)
- [HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)
- [HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)
- [HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)

**🛠️ Complementary Tools**
- [URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)
- [URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)
- [HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)
- [HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)
- [HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)


## 👤 AUTEUR
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com

## 📜 LICENCE
GPL-3.0 - Voir le fichier LICENSE pour plus de détails.

## Projets associés

> Canonical public ecosystem relationship map.

**Direct integrations:**
[HYDRA-UMC-OS](https://github.com/JuanenRac/HYDRA-UMC-OS) · [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) · [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) · [URTC](https://github.com/JuanenRac/URTC) · [HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE) · [HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR) · [HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)

**Platform and contracts:**
[HYDRA-UMC-OS](https://github.com/JuanenRac/HYDRA-UMC-OS) · [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)

**Rest of the ecosystem:**
All remaining public repositories are grouped by the seven ecosystem layers in the [JuanenRac ecosystem dashboard](https://juanenrac.github.io/JuanenRac/).
