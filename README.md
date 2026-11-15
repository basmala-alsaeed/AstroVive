# 🛰️ AstroVive – Astronaut Health Monitoring

> **NASA Space Apps Challenge 2026** · *Create Health Monitoring Software for Astronauts on Space Missions*

AstroVive is a self-assessment health monitoring web app for astronauts on long-duration missions. It collects vital signs, sleep and mental-health check-ins, pain reports and spacecraft-environment readings, evaluates them against each astronaut's **own** limits, and tells the crew **what to do** and **when to contact Ground**, even when the link to Earth is slow or down.

> ⚠️ **Educational prototype.** Limits are Earth-based adult reference values, not NASA flight limits. Medical text is draft guidance, not crew medical orders. The flight surgeon makes the final decision. All demo data is fictional.

---

## Table of contents
1. [Quick start](#quick-start)
2. [Features](#features)
3. [Pages](#pages)
4. [How the status logic works](#how-the-status-logic-works)
5. [Knowledge base and data file](#knowledge-base-and-data-file)
6. [Personalization](#personalization)
7. [Diagnose engine (symptom-based)](#diagnose-engine-symptom-based)
8. [Hazards: spacecraft environment](#hazards-spacecraft-environment)
9. [Ground link (store and forward)](#ground-link-store-and-forward)
10. [Accounts and security](#accounts-and-security)
11. [Hiding pages and elements](#hiding-pages-and-elements)
12. [Streamlit version](#streamlit-version)
13. [Project layout](#project-layout)
14. [Team assumptions and limitations](#team-assumptions-and-limitations)
15. [License](#license)

---

## Quick start

Requires Python 3.9+.

```bash
git clone <your-repo-url>
cd astrovive
pip install -r requirements.txt
python app.py
```

Open <http://localhost:5000>. On Windows you can also double-click `run.bat`.

**Demo accounts** (access code): `Aya` **1111** · `Alex` **2222** · `John` **3333**, or register a new astronaut with **Join Mission**.

**Demo tip:** Dashboard → **Load Demo Data (10 days)** fills 10 days of fictional readings (heart rate rising, oxygen and sleep getting worse) so every alert, trend and chart has something to show.

The database `astra.db` and the session key `.secret_key` are created automatically on first run and are git-ignored.

---

## Features

- **8 vital signs** with green / yellow / red status: heart rate, systolic and diastolic pressure, SpO₂, body temperature, respiration rate, fasting glucose and EtCO₂.
- **Daily check-in** for sleep and mental health (sleep duration and efficiency, stress, mood, mental fatigue, attention, reaction time, memory).
- **Interactive body map:** click a body region to see its live metrics, trends, guidance and a **pain log (0–10)**.
- **Smart alerts:** trend detection, deviation from the personal baseline, and **compound rules** that combine several indicators.
- **Symptom diagnostic** (`/diagnose`): select or type symptoms (English or Arabic, free text) and get an identified hazard, body impact, severity and action plan.
- **Spacecraft environment check:** simulated cabin sensors (CO₂, pressure, temperature, humidity, dust, noise, light) checked against limits.
- **Ground link:** health reports sent with a simulated communication delay, with store-and-forward when the link is down.
- **Knowledge base:** 53 conditions across 5 NASA human-research hazards, with evidence levels and 36 sources.
- **Personal profile:** limits adjust for athletes, beta-blocker use, treated high blood pressure and diabetes.
- **Health record** (`/reports`): full history, filters and diagnostic history.
- **Individual accounts** with hashed access codes and basic medical information.
- **Configurable UI:** hide pages or elements from `ui_config.json` without touching code.
- **Works offline** (local Flask + SQLite, no external services).

---

## Pages

| Route | Page | What it does |
|---|---|---|
| `/` | Login / Join Mission | Sign in, register a new astronaut, recover a lost code |
| `/dashboard` | Dashboard | 8 vital-sign cards, overall status, body map, smart alerts, identity (role, ID, blood type), demo data |
| `/body/<region>` | Body region | Metrics for that body part, trends, normal range, guidance, recent logs, pain log, related conditions |
| `/details` | Details | Detailed vital-sign view and history charts |
| `/diagnose` | Diagnose | Symptom form plus vitals, hazard result card, diagnostic history |
| `/reports` | Reports (EHR Insight) | Vital history with filters, diagnostic history |
| `/wellbeing` | Wellbeing | Daily sleep and mental-health check-in |
| `/hazards` | Hazards | Automatic spacecraft-environment measurement |
| `/ground` | Ground | Send a report to Ground, simulation speed, link state, outbox |
| `/profile` | Profile | Body data, medical conditions, identity and basic information |

---

## How the status logic works

Every vital sign follows the `status_logic` defined in `data/astra_health_data.json`:

1. If the value is not a number (or is physically implausible), there is no result.
2. `side` = **LOW** if below `normal_min`, **HIGH** if above `normal_max`, otherwise NORMAL.
3. Colour is **RED** if a symptom override is set, or the value is below `critical_low`, or above `critical_high`.
4. Otherwise **GREEN** inside the normal range, **YELLOW** outside it.
5. The state key is `GREEN`, `RED_SYMPTOM`, or `<COLOR>_<SIDE>` (e.g. `YELLOW_HIGH`, `RED_LOW`), which selects the guidance text.

| Vital sign | Unit | Normal range |
|---|---|---|
| Heart rate | bpm | 60–100 |
| Systolic pressure | mmHg | 90–119 |
| Diastolic pressure | mmHg | 60–79 |
| Blood oxygen (SpO₂) | % | 95–100 |
| Body temperature | °C | 36.1–37.2 |
| Respiration rate | breaths/min | 12–20 |
| Fasting glucose | mg/dL | 70–99 |
| EtCO₂ | mmHg | 35–45 |

| Colour | Meaning | Action focus |
|---|---|---|
| 🟢 Green | Within range | Prevention, routine monitoring |
| 🟡 Yellow | Outside normal, not critical | Self-care, recheck, log and report |
| 🔴 Red | Critical value or symptom override | Emergency action, contact the flight surgeon |

Every status comes with **what it means**, **first-aid action** and **when to contact Ground**.

---

## Knowledge base and data file

`data/astra_health_data.json` is the single source of truth (exported from the team's evidence-reviewed workbook). It contains:

- `vital_signs` (8) with limits, states and guidance text
- `knowledge_base` (53 conditions): symptoms, recommended action, when to contact Ground, severity, source ids, evidence level
- `baseline_rules` (9) for the daily check-in
- `environment_limits` (19) for the cabin environment
- `etco2_review`, `status_logic`, `colors` and `sources` (36 references)

Conditions are grouped by the five NASA human-research hazards: **Hostile / Closed Environments** (20), **Isolation and Confinement** (11), **Gravity Fields** (9), **Distance from Earth** (8) and **Space Radiation** (5).

**Evidence levels:** **A** = checked against the cited NASA / peer-reviewed source · **B** = team-compiled, source identified but numbers not re-verified · **C** = team-proposed or Earth-based reference values, needs clinical review.

---

## Personalization

Vital-sign limits and alerts adapt to each astronaut.

**Profile** (`/profile`): age, sex, weight, height and these conditions:

| Condition | Adjusted limit |
|---|---|
| Trained athlete | Heart-rate lower limit 40 |
| Beta-blocker | Heart-rate lower limit 50 (critical low 40) |
| Treated high blood pressure | Systolic upper limit 129 |
| Diabetes | Fasting glucose 80–130, critical > 180 |

Old readings are re-evaluated when the profile is saved. Age, sex, weight and height are used only to compute BMI, because the data file has no age- or sex-specific limits.

**Smart alerts** (Dashboard):
- **Trend:** three consecutive rising or falling readings that predict leaving the normal range.
- **Personal baseline:** a reading that moved 10% or more away from the baseline (first two readings).
- **Compound rules** (marked 🔗):
  - high heart rate + low oxygen
  - fast breathing + low oxygen
  - high CO₂ + fast breathing
  - low pressure + high heart rate
  - low glucose + high heart rate
  - temperature / heart rate / respiration, 2 of 3 high together
  - high pressure and heart rate + stress warning
  - short sleep + reduced attention, memory or reaction time

**Daily check-in** rules (`baseline_rules`) work on absolute thresholds or relative to the astronaut's own baseline (first two readings, alerts from the fourth).

---

## Diagnose engine (symptom-based)

`diagnostic_engine.py` is pure Python (no Flask or Streamlit imports), so the web app and the Streamlit tool share exactly the same logic.

**Input:** 11 symptoms (dizziness, nausea, severe headache, eye strain, palpitations, insomnia, mental fatigue, shortness of breath, chest pain, fever / chills, confusion) by checkbox or **free text in English or Arabic** (for example "dizzy, SpO2 ninety two"), plus optional vitals. Dashboard values fill the gaps if they are less than 24 hours old.

**Dual-state engine:**
- **State 1: abnormal vitals + matching symptoms** → a space hazard, severity 🟡 / 🔴 / ⬛️ and an action plan.
- **State 2: normal vitals + mild symptoms** → no hazard alarm, 🟢 / 🟡 and wellness advice.
- Chest pain and confusion always escalate.

**Hazards recognised:** hypoxia / cardiorespiratory strain, elevated CO₂, space motion sickness, microgravity deconditioning, possible cardiac rhythm problem, possible infection / inflammation, radiation exposure (precaution) and low blood glucose.

**Output (Smart Card):** identified hazard · body impact · severity · action plan · why it was chosen · vitals used.

Every session is saved to the `diagnostics` table and listed in **Reports → Diagnostic History**. The latest diagnostic is included in the report sent to Ground.

---

## Hazards: spacecraft environment

The Hazards page simulates cabin sensors, one sample of every parameter every **2 minutes**, stored in `env_log`. If nobody has the page open, the missed samples are filled in on the next visit, so the history always covers the time that passed.

Monitored parameters (checked against `environment_limits`): ppCO₂, inert diluent gas, cabin pressure and its change rate, temperature, relative humidity (operating and crew-performance zones), total, respirable and lunar dust, work-area and sleep-area noise, daytime and pre-sleep light (melanopic EDI and DER).

---

## Ground link (store and forward)

- **Report:** the latest readings, pain reports (last 24 hours), smart alerts and the latest diagnostic, built into one message.
- **Simulated one-way delay** by destination: ISS 3 s · Moon 1.3 min · HERA 5 min · Mars 20 min (simulation only).
- **Simulation speed** ×1 / ×10 / ×60 / ×600 on a persistent server clock: no time jumps when the speed changes, and it survives a page reload.
- **Link state** (online / down) is saved. Reports sent while the link is down are stored and transmitted automatically when the link is restored.
- Network and session errors are reported to the user instead of failing silently.

---

## Accounts and security

- Each astronaut has an individual account: identity (role, astronaut ID, email) and **Basic Information** (blood type, nationality, date of birth, mission, emergency contact, allergies, medications, notes) on the Profile page.
- Access codes are **hashed** (Werkzeug). Old plain codes migrate automatically on first start.
- The session key is never hard-coded: set `ASTRA_SECRET`, or a random key is generated once into `.secret_key`.
- Session cookies are `HttpOnly` and `SameSite=Lax`. Text input is cleaned, numeric input is range-checked, and all API calls require a session (expired sessions return 401 and redirect to login).
- This is a local demo. For any real deployment, add HTTPS, rate limiting and a production server.

---

## Hiding pages and elements

Edit `ui_config.json`, save and refresh (no restart needed):

```json
{
  "hidden_pages": ["ground"],
  "hidden_elements": ["demo_data", "pain_log"]
}
```

- **Pages:** `details`, `diagnose`, `reports`, `wellbeing`, `hazards`, `ground`, `profile`
- **Elements:** `demo_data`, `smart_alerts`, `health_analysis`, `join_mission`, `forgot_code`, `speed_sim`, `link_toggle`, `pain_log`, `diag_history`

Hidden pages disappear from the menu, and their links and APIs return 404. `dashboard` and `login` can never be hidden. A broken config file never takes the app down.

---

## Streamlit version

A standalone diagnostic tool that uses the **same engine** and the **same `astra.db`**:

```bash
pip install streamlit werkzeug
streamlit run diagnostic_streamlit.py
```

Sessions saved there also appear in the Flask **Reports** page, and personal limits come from the astronaut's Profile.

---

## Project layout

```
astrovive/
├── app.py                    Flask app: routes, status logic, alerts, ground link, hazards
├── diagnostic_engine.py      Symptom / hazard engine (shared by Flask and Streamlit)
├── diagnostic_streamlit.py   Streamlit diagnostic tool
├── ui_config.json            Hide pages / elements without code changes
├── requirements.txt
├── run.bat                   One-click start on Windows
├── data/
│   └── astra_health_data.json   Vital signs, knowledge base, rules, limits, sources
├── templates/                base, login, dashboard, body, details, diagnose,
│                             reports, wellbeing, hazards, ground, profile
├── static/                   style.css, app.js, diag.js
├── tests/test_app.py         Unit tests (see limitations)
├── LICENSE                   MIT
└── .gitignore
```

**Stack:** Python · Flask · SQLite · vanilla JavaScript · Streamlit (optional).
**Database tables:** `users`, `basic_info`, `profile`, `logs`, `wb`, `pain`, `diagnostics`, `outbox`, `simclock`, `env_log`.

---

## Team assumptions and limitations

These are team decisions, **not** from the data file or NASA:

- Personal-limit adjustments and compound rules.
- Vital-sign alert settings: baseline = first two readings, alerts from the third reading, 10% deviation, trend from 3 consecutive readings.
- Which vitals belong to which body part on the body map. Arms and legs have no live sensor, so they show pain logs and reference conditions only. Left and right are from the astronaut's own point of view.
- Pain labels: mild 1–3, moderate 4–6, severe 7–10.
- Rule B02 is not enabled (its threshold is TBD in the data file).
- Medical guidance text is in English, as in the data file.
- Per the data audit, RED for fasting glucose ≥ 126 mg/dL and EtCO₂ > 50 mmHg are prototype displays, **not** medical-emergency criteria.
- Cabin sensor values are simulated.
- `tests/test_app.py` is out of date with the current code and needs updating before it can be used.

---

## License

MIT, see [`LICENSE`](LICENSE). Educational prototype, not a medical device.
