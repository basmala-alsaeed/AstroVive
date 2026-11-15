"""
AstroVive – Symptom-Based Hazard & Diagnostic engine.

Pure Python (no Flask / Streamlit imports) so the same logic is used by:
  * the Flask page  /diagnose   (app.py)
  * the standalone Streamlit page (diagnostic_streamlit.py)

Educational prototype: thresholds are Earth-based reference values, not NASA flight limits.
The flight surgeon makes the final decision.
"""
import json
import os
import re
import sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'astra.db')
DATA = json.load(open(os.path.join(BASE, 'data', 'astra_health_data.json'), encoding='utf-8'))
VS = {v['metric_id']: v for v in DATA['vital_signs']}
NAMES = {'V01': 'Heart Rate', 'V02': 'Systolic Pressure', 'V03': 'Diastolic Pressure', 'V04': 'Blood Oxygen (SpO2)',
         'V05': 'Body Temperature', 'V06': 'Respiration Rate', 'V07': 'Fasting Blood Glucose', 'V08': 'Carbon Dioxide (EtCO2)'}
SANE = {'V01': (20, 250), 'V02': (40, 280), 'V03': (20, 200), 'V04': (50, 100), 'V05': (30, 45),
        'V06': (4, 60), 'V07': (20, 600), 'V08': (5, 100)}

# ---------------------------------------------------------------- severity
SEVERITY = {
    'GREEN':  {'rank': 0, 'icon': '🟢', 'label': 'Safe'},
    'YELLOW': {'rank': 1, 'icon': '🟡', 'label': 'Warning'},
    'RED':    {'rank': 2, 'icon': '🔴', 'label': 'Critical'},
    'BLACK':  {'rank': 3, 'icon': '⬛️', 'label': 'Emergency'},
}

# ---------------------------------------------------------------- symptoms
# key -> (English label, Arabic label, keywords in English + Arabic)
SYMPTOMS = {
    'dizziness':  ('Dizziness', 'دوخة', ['dizz', 'light-headed', 'lightheaded', 'vertigo', 'spinning', 'faint', 'دوخ', 'دوار', 'دايخ', 'دايخة']),
    'nausea':     ('Nausea', 'غثيان', ['nause', 'vomit', 'sick to my stomach', 'queasy', 'غثيان', 'استفراغ', 'ترجيع', 'مغص']),
    'headache':   ('Severe Headache', 'صداع شديد', ['headache', 'head ache', 'head hurts', 'migraine', 'صداع', 'راسي بتوجعني', 'رأسي']),
    'eye_strain': ('Eye Strain', 'إجهاد العين', ['eye strain', 'eyes hurt', 'blurry', 'blurred', 'vision', 'eye pain', 'عيني', 'عيون', 'نظر', 'زغللة']),
    'palpitations': ('Sudden Palpitations', 'خفقان مفاجئ', ['palpitat', 'heart racing', 'heart pounding', 'racing heart', 'fluttering', 'خفقان', 'قلبي بيدق', 'دقات']),
    'insomnia':   ('Insomnia', 'أرق', ['insomnia', "can't sleep", 'cannot sleep', 'not sleeping', 'sleepless', 'أرق', 'مش عارف انام', 'مش بنام', 'سهر']),
    'fatigue':    ('Mental Fatigue', 'إرهاق ذهني', ['fatigue', 'tired', 'exhaust', 'brain fog', 'foggy', 'can\'t focus', 'cannot focus', 'stress', 'إرهاق', 'تعب', 'مجهد', 'مرهق', 'تركيز', 'توتر']),
    'breathless': ('Shortness of Breath', 'ضيق تنفس', ['short of breath', 'shortness of breath', 'breathless', "can't breathe", 'hard to breathe', 'ضيق في التنفس', 'ضيق تنفس', 'نهجان', 'مش قادر اتنفس']),
    'chest_pain': ('Chest Pain', 'ألم في الصدر', ['chest pain', 'chest tight', 'tight chest', 'ألم في الصدر', 'وجع في صدري', 'صدري']),
    'fever_chills': ('Fever / Chills', 'حرارة / رعشة', ['fever', 'chills', 'feverish', 'shiver', 'حمى', 'سخونية', 'سخونه', 'رعشة']),
    'confusion':  ('Confusion', 'تشوش', ['confus', 'disoriented', 'cannot think', 'تشوش', 'مش مركز', 'ارتباك']),
}

# ---------------------------------------------------------------- number words (EN + AR) for free-text parsing
_EN_UNITS = {'zero': 0, 'oh': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8,
             'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
             'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19}
_EN_TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
_AR_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩٫٬', '0123456789..')


def words_to_numbers(text):
    """Convert 'ninety seven' / 'one hundred and ten' to digits, and Arabic-Indic digits to ASCII."""
    t = text.translate(_AR_DIGITS).lower()
    toks = re.findall(r"[a-z]+\d*|\d+(?:\.\d+)?|[^\sa-z\d]+", t)
    out, i = [], 0
    while i < len(toks):
        tk, total, used, cur, seen = toks[i], 0, 0, 0, False
        if tk in _EN_UNITS or tk in _EN_TENS or tk == 'hundred':
            j = i
            while j < len(toks):
                w = toks[j]
                if w in _EN_TENS:
                    cur = cur * 100 + _EN_TENS[w] if cur in (1, 2) and j > i and toks[j - 1] in _EN_UNITS else cur + _EN_TENS[w]   # "one twenty" = 120
                    seen = True
                elif w in _EN_UNITS: cur += _EN_UNITS[w]; seen = True
                elif w == 'hundred': cur = max(cur, 1) * 100; seen = True
                elif w == 'and' and seen and j + 1 < len(toks) and (toks[j + 1] in _EN_UNITS or toks[j + 1] in _EN_TENS): pass
                else: break
                j += 1
            out.append(str(cur)); i = j
        else:
            out.append(tk); i += 1
    res = ' '.join(out)
    # "38 point 5" -> "38.5"   (also "98 point 6 2" -> "98.62")
    return re.sub(r'(\d+)\s+point\s+((?:\d\s?)+)', lambda m: m.group(1) + '.' + m.group(2).replace(' ', ''), res)


# ---------------------------------------------------------------- vitals keywords (EN + AR)
VITAL_KW = {
    'V01': ['heart rate', 'pulse', 'heart', 'bpm', 'نبض', 'ضربات القلب', 'معدل القلب'],
    'V04': ['spo2', 'oxygen', 'saturation', 'sats', 'أكسجين', 'اكسجين', 'الأكسجين', 'الاكسجين'],
    'V06': ['respiration', 'respiratory', 'breathing rate', 'breaths', 'breathing', 'تنفس', 'التنفس'],
    'V05': ['temperature', 'temp', 'حرارة', 'الحرارة', 'درجة حرارتي'],
    'V07': ['glucose', 'sugar', 'سكر', 'السكر'],
    'V08': ['carbon dioxide', 'co2', 'etco2', 'ثاني أكسيد'],
}
_NUM = r'(\d+(?:\.\d+)?)'


def parse_vitals(text):
    """Extract vital readings from free text. Returns {metric_id: float}."""
    t = words_to_numbers(text or '')
    found = {}
    # blood pressure "120 over 80", "120/80", "ضغطي 120 على 80"
    m = re.search(r'(?:blood pressure|pressure|bp|ضغط\w*)\D{0,12}' + _NUM + r'\s*(?:over|/|on|على|\\)\s*' + _NUM, t)
    if not m:
        m = re.search(_NUM + r'\s*(?:over|/|على)\s*' + _NUM, t)
    if m:
        found['V02'], found['V03'] = float(m.group(1)), float(m.group(2))
    for mid, kws in VITAL_KW.items():
        for k in sorted(kws, key=len, reverse=True):
            # number after keyword ("heart rate 85") ...
            m = re.search(re.escape(k) + r'\D{0,18}?' + _NUM, t)
            # ... or before it ("85 bpm", "97 percent oxygen")
            if not m and k in ('bpm',):
                m = re.search(_NUM + r'\s*' + re.escape(k), t)
            if m:
                found[mid] = float(m.group(1)); break
    for mid, x in list(found.items()):           # drop implausible readings (typos, mis-heard numbers)
        lo, hi = SANE[mid]
        if not lo <= x <= hi: found.pop(mid)
    return found


def parse_symptoms(text):
    """Extract symptom keys from free text (English + Arabic)."""
    t = (text or '').lower()
    return [k for k, (_, _, kws) in SYMPTOMS.items() if any(w in t for w in kws)]


# ---------------------------------------------------------------- personal thresholds
def personal_limits(profile=None):
    """Vital-sign limits adjusted for the astronaut's profile (same assumptions as app.personal_vs)."""
    out = {k: dict(v) for k, v in VS.items()}
    p = profile or {}
    if p.get('athlete'): out['V01']['normal_min'] = min(out['V01']['normal_min'], 40); out['V01']['critical_low'] = min(out['V01']['critical_low'], 40)
    if p.get('bb'): out['V01']['normal_min'] = min(out['V01']['normal_min'], 50); out['V01']['critical_low'] = min(out['V01']['critical_low'], 40)
    if p.get('htn'): out['V02']['normal_max'] = max(out['V02']['normal_max'], 129)
    if p.get('dm'): out['V07'].update(normal_min=80, normal_max=130, critical_high=180)
    return out


def vital_state(limits, mid, val):
    """-> ('LOW'|'HIGH'|'NORMAL', 'GREEN'|'YELLOW'|'RED')"""
    v = limits[mid]
    side = 'LOW' if val < v['normal_min'] else 'HIGH' if val > v['normal_max'] else 'NORMAL'
    cl, ch = v.get('critical_low'), v.get('critical_high')
    crit = (cl is not None and val < cl) or (ch is not None and val > ch)
    return side, 'RED' if crit else ('GREEN' if side == 'NORMAL' else 'YELLOW')


# ---------------------------------------------------------------- hazard knowledge
# Each rule: needs >=1 symptom from `symptoms` AND >=1 abnormal vital from `vitals` ({metric: side}).
# Links symptoms to a hazard from the project's own five hazard categories.
HAZARD_RULES = [
    {'id': 'hypoxia', 'name': 'Hypoxia / Cardiorespiratory Strain', 'category': 'Hostile / Closed Environment',
     'symptoms': {'dizziness', 'headache', 'breathless', 'confusion', 'palpitations', 'chest_pain'},
     'vitals': {'V04': 'LOW'}, 'min_sev': 'RED',
     'impact': 'Not enough oxygen is reaching your blood and brain. This causes dizziness, headache and confusion, and the heart speeds up to compensate.',
     'plan': ['Stop all activity and sit or float still. Breathe slowly and deeply.', 'Check the sensor placement, then repeat the SpO2 reading within 2 minutes.',
              'Check cabin air: CO2 level, ventilation and the oxygen supply.', 'Notify the Flight Surgeon / Ground immediately if SpO2 stays low or confusion increases.']},
    {'id': 'co2', 'name': 'Elevated CO₂ (Hypercapnia)', 'category': 'Hostile / Closed Environment',
     'symptoms': {'headache', 'dizziness', 'breathless', 'nausea', 'confusion', 'fatigue'},
     'vitals': {'V08': 'HIGH', 'V06': 'HIGH'}, 'min_sev': 'RED',
     'impact': 'Carbon dioxide is building up in your body or in the cabin air. Typical effects are headache, fast breathing, dizziness and poor concentration.',
     'plan': ['Move to a well-ventilated module and check the cabin CO2 monitor.', 'Breathe slowly and avoid heavy exercise.', 'Report the cabin air quality to Ground.']},
    {'id': 'smsi', 'name': 'Space Motion Sickness', 'category': 'Altered Gravity',
     'symptoms': {'nausea', 'dizziness', 'headache'}, 'vitals': {'V01': 'HIGH', 'V06': 'HIGH', 'V05': 'HIGH'}, 'min_sev': 'YELLOW',
     'impact': 'In microgravity the balance organs of the inner ear send conflicting signals to the brain. This causes nausea, dizziness, cold sweat and a faster heart rate.',
     'plan': ['Limit sudden head movements and keep your eyes on a fixed reference point.', 'Rest in a position with your head stable. Take small sips of water.',
              'Use the anti-nausea medication from the onboard kit if approved by your medical plan.', 'Report to the Flight Surgeon if vomiting lasts more than 2 hours.']},
    {'id': 'decond', 'name': 'Microgravity Deconditioning', 'category': 'Altered Gravity',
     'symptoms': {'dizziness', 'fatigue', 'palpitations', 'eye_strain', 'headache'}, 'vitals': {'V01': 'HIGH', 'V02': 'LOW', 'V03': 'LOW'}, 'min_sev': 'YELLOW',
     'impact': 'Without gravity, body fluids shift upward and the heart and blood vessels lose conditioning. This can cause dizziness when you move, a fast resting heart rate and low blood pressure.',
     'plan': ['Sit or lie down until dizziness passes. Move slowly.', 'Drink fluids and electrolytes.', 'Re-measure heart rate and blood pressure in 15 minutes.', 'Review your exercise schedule with the Flight Surgeon.']},
    {'id': 'cardiac', 'name': 'Possible Cardiac Rhythm Problem', 'category': 'Altered Gravity',
     'symptoms': {'palpitations', 'chest_pain', 'dizziness', 'breathless'}, 'vitals': {'V01': 'ANY'}, 'min_sev': 'RED',
     'impact': 'Your heart rate is outside your personal range while you feel palpitations or chest discomfort. This may be an arrhythmia and needs medical review.',
     'plan': ['Stop activity and rest. Do not exercise.', 'Repeat the heart-rate reading and note how long the symptoms last.', 'Contact the Flight Surgeon now. Chest pain with a fast or very slow heart rate is urgent.']},
    {'id': 'infection', 'name': 'Possible Infection / Inflammation', 'category': 'Hostile / Closed Environment',
     'symptoms': {'fever_chills', 'fatigue', 'headache'}, 'vitals': {'V05': 'HIGH', 'V01': 'HIGH', 'V06': 'HIGH'}, 'min_sev': 'YELLOW', 'need': 2,
     'impact': 'Temperature, heart rate or breathing are raised together, which can be the body fighting an infection. Spaceflight can weaken the immune system.',
     'plan': ['Isolate from the crew if possible and keep hydrated.', 'Measure temperature every 4 hours.', 'Report to Ground if the fever lasts more than 24 hours.']},
    {'id': 'radiation', 'name': 'Radiation Exposure (precaution)', 'category': 'Space Radiation',
     'symptoms': {'nausea', 'fatigue', 'headache'}, 'vitals': {'V01': 'HIGH', 'V05': 'HIGH'}, 'min_sev': 'YELLOW', 'need_env': True,
     'impact': 'Nausea, fatigue and headache together with raised vital signs can appear after radiation exposure, for example during a solar particle event.',
     'plan': ['Move immediately to the radiation shelter or the best shielded area.', 'Check the radiation dashboard and any solar-event alerts.', 'Report the symptoms and the dose reading to Ground.']},
    {'id': 'hypoglycemia', 'name': 'Low Blood Glucose', 'category': 'Hostile / Closed Environment',
     'symptoms': {'dizziness', 'fatigue', 'palpitations', 'confusion'}, 'vitals': {'V07': 'LOW'}, 'min_sev': 'RED',
     'impact': 'Your blood sugar is low, so the brain and muscles lack fuel. This causes shakiness, dizziness, a racing heart and confusion.',
     'plan': ['Take a fast sugar source now (glucose gel or juice).', 'Re-measure glucose after 15 minutes.', 'Notify Ground if it stays low.']},
]


def _rank(c): return SEVERITY[c]['rank']


def _worst(cols): return max(cols, key=_rank, default='GREEN')


# ---------------------------------------------------------------- wellbeing advice (State 2)
WELLBEING_TIPS = {
    'fatigue': 'Take a 10–15 minute break away from screens; hydrate and have a light snack; schedule a short guided-relaxation or breathing session.',
    'insomnia': 'Dim cabin lighting 1–2 hours before the sleep period, avoid caffeine and screens, and keep the sleep-station temperature cool and consistent.',
    'headache': 'Drink a full glass of water, rest your eyes, and check the cabin lighting and CO₂ level. If it lasts more than 4 hours, check in with the flight surgeon.',
    'eye_strain': 'Use the 20-20-20 rule (every 20 minutes look at something far for 20 seconds), reduce screen brightness and warm the cabin lighting.',
    'dizziness': 'Move slowly, avoid sudden head movements and rest for 10 minutes. Re-measure your vitals if it continues.',
    'nausea': 'Sip water slowly, keep your head steady and avoid heavy meals for an hour.',
    'palpitations': 'Sit calmly and take slow breaths (4 s in, 6 s out). Re-measure your heart rate in 10 minutes. If it returns, tell the flight surgeon.',
    'breathless': 'Stop activity and breathe slowly. Re-measure SpO2. If it feels worse, contact the flight surgeon.',
    'chest_pain': 'Stop activity and contact the flight surgeon even if your vitals look normal.',
    'fever_chills': 'Measure your temperature in 30 minutes and keep hydrated.',
    'confusion': 'Do not perform critical tasks. Ask a crewmate to stay with you and contact the flight surgeon.',
}
# symptoms that should never be treated as "just wellbeing", even if the vitals are normal
ALWAYS_ESCALATE = {'chest_pain', 'confusion'}


def _fmt(a):
    return f"{a['value']:g} {a['unit']}" if a.get('value') is not None else 'reported ' + a['side'].lower()


def _diagnose(symptoms, readings, profile=None, env_alert=False, last_known=None, feel_flags=None):
    """
    Dual-state diagnostic engine.

    symptoms  : list of symptom keys
    readings  : {metric_id: value}  vitals the astronaut just reported/entered
    profile   : astronaut profile dict (athlete / bb / htn / dm) -> personalized thresholds
    last_known: {metric_id: value} dashboard values used only when the astronaut gave no new reading
    Returns a dict ready to render as a smart card and save to SQLite.
    """
    limits = personal_limits(profile)
    sym = list(dict.fromkeys(symptoms or []))
    vit = dict(last_known or {})                     # Dashboard values fill in what the astronaut did not report
    src = {m: 'dashboard' for m in vit}
    for m, x in (readings or {}).items(): vit[m] = x; src[m] = 'reported'
    used_dashboard = any(v == 'dashboard' for v in src.values())

    analysis = {}                                    # metric -> {value, side, color}
    for mid, x in vit.items():
        if mid in limits:
            side, col = vital_state(limits, mid, x)
            analysis[mid] = {'value': x, 'side': side, 'color': col, 'range': f"{limits[mid]['normal_min']}–{limits[mid]['normal_max']}", 'unit': limits[mid]['unit'], 'src': src.get(mid, 'reported')}
    for mid, side in (feel_flags or []):            # qualitative 'my heart feels fast' (no number): treated as a mild abnormal
        if mid not in analysis and mid in limits:
            analysis[mid] = {'value': None, 'side': side, 'color': 'YELLOW', 'range': f"{limits[mid]['normal_min']}–{limits[mid]['normal_max']}", 'unit': limits[mid]['unit'], 'src': 'reported'}
    abnormal = {m: a for m, a in analysis.items() if a['side'] != 'NORMAL'}
    vit_color = _worst([a['color'] for a in analysis.values()])
    sym_names = [SYMPTOMS[s][0] for s in sym]

    res = {'symptoms': sym, 'symptom_names': sym_names, 'vitals': analysis, 'used_dashboard': used_dashboard,
           'no_vitals': not analysis, 'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    # ---- STATE 1: abnormal vitals AND matching symptoms -> link to a hazard
    best = None
    for r in HAZARD_RULES:
        hit_sym = [s for s in sym if s in r['symptoms']]
        hit_vit = {m: abnormal[m] for m, side in r['vitals'].items() if m in abnormal and (side == 'ANY' or abnormal[m]['side'] == side)}
        if not hit_sym or len(hit_vit) < r.get('need', 1): continue
        if r.get('need_env') and not env_alert: continue          # radiation is only linked when an alert is flagged
        sev = _worst([r['min_sev']] + [a['color'] for a in hit_vit.values()])
        score = (len(hit_sym) + len(hit_vit) * 2, _rank(sev))
        if not best or score > best[0]: best = (score, r, hit_sym, hit_vit, sev)
    if best:
        _, r, hit_sym, hit_vit, sev = best
        # extreme emergency: critical vitals in 2+ systems, or 3+ matching symptoms with a critical vital
        crit = [m for m, a in hit_vit.items() if a['color'] == 'RED']
        n_crit_all = sum(1 for a in abnormal.values() if a['color'] == 'RED')
        if n_crit_all >= 3 or (len(hit_sym) >= 3 and crit) or ('V04' in analysis and analysis['V04']['value'] is not None and analysis['V04']['value'] < 85 and any(s in sym for s in ('confusion', 'dizziness'))):
            sev = 'BLACK'
        res.update(state=1, state_label='Correlation Found', hazard=r['name'], category=r['category'], severity=sev,
                   impact=r['impact'], plan=r['plan'] + (['Send an urgent report to Ground now (Ground page).'] if sev in ('RED', 'BLACK') else []),
                   matched=[f"{SYMPTOMS[s][0]} ↔ {NAMES[m]} {_fmt(a)} ({a['side'].lower()})" for s in hit_sym for m, a in hit_vit.items()][:4])
        return res

    # ---- abnormal vitals but no matching symptom, or symptoms that always need escalation
    if abnormal:
        sev = _worst([a['color'] for a in abnormal.values()])
        res.update(state=1, state_label='Abnormal vitals', hazard='Vital signs outside your personal range', category='Monitoring', severity='YELLOW' if sev == 'GREEN' else sev,
                   impact='One or more vital signs are outside your personal normal range: ' + '; '.join(f"{NAMES[m]} {_fmt(a)} (normal {a['range']})" for m, a in abnormal.items())
                          + '. The symptoms you reported do not clearly match a specific space hazard, so no hazard is assigned.',
                   plan=['Rest for 5–10 minutes, then repeat the measurement.', 'Check the sensor placement.', 'Keep logging symptoms and vitals; report to Ground if the values stay abnormal.'], matched=[])
        return res
    if set(sym) & ALWAYS_ESCALATE:
        res.update(state=2, state_label='Symptom-only (needs review)', hazard='Symptom needs medical review', category='General', severity='YELLOW',
                   impact='Your vital signs are within range, but this symptom should not be ignored even when vitals look normal.',
                   plan=[WELLBEING_TIPS[s] for s in sym if s in ALWAYS_ESCALATE] + ['Check in with the flight surgeon.'], matched=[])
        return res

    # ---- STATE 2: normal vitals (or none reported) + mild symptoms -> NO hazard alarm
    sev = 'GREEN' if len(sym) <= 1 else 'YELLOW'
    note = ' Your vital signs are normal, so no space hazard is flagged.' if analysis else ' No vital signs were available, so no space hazard can be flagged. Enter your heart rate and SpO2 for a fuller check.'
    plan = [WELLBEING_TIPS[s] for s in sym if s in WELLBEING_TIPS] or ['No symptoms reported. Keep up your routine: hydration, sleep and exercise.']
    if sev == 'YELLOW': plan.append('Optional: check in with the flight surgeon if the symptoms last more than a day.')
    res.update(state=2, state_label='Symptom-Only / General Wellbeing', hazard='General wellbeing – no hazard detected', category='Wellbeing', severity=sev,
               impact='The symptoms you describe are common during spaceflight (stress, disrupted sleep, workload, lighting) and are not linked to a hazard by your readings.' + note,
               plan=plan, matched=[])
    return res


def diagnose(*a, **kw):
    """Public entry point: runs the engine and adds the display fields (icon, label)."""
    res = _diagnose(*a, **kw); m = SEVERITY[res['severity']]
    res['icon'], res['severity_label'] = m['icon'], m['label']
    return res


# ---------------------------------------------------------------- SQLite persistence
def _conn(path=None):
    c = sqlite3.connect(path or DB_PATH); c.row_factory = sqlite3.Row; return c


def init_diag_table(path=None):
    with _conn(path) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS diagnostics(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, crew TEXT,
                     vitals TEXT, symptoms TEXT, hazard TEXT, state INTEGER, severity TEXT, impact TEXT, plan TEXT, source TEXT)''')


def save_diagnosis(crew, res, source='form', path=None):
    """Persist one diagnostic session. Returns the new row id."""
    init_diag_table(path)
    vit = {m: {'value': a['value'], 'unit': a['unit'], 'status': a['color']} for m, a in res['vitals'].items()}
    with _conn(path) as c:
        cur = c.execute('INSERT INTO diagnostics(ts,crew,vitals,symptoms,hazard,state,severity,impact,plan,source) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (res['ts'], crew, json.dumps(vit), json.dumps(res['symptom_names']), res['hazard'], res['state'],
                         res['severity'], res['impact'], json.dumps(res['plan']), source))
        return cur.lastrowid


def load_history(crew, limit=50, path=None):
    init_diag_table(path)
    with _conn(path) as c:
        rows = c.execute('SELECT * FROM diagnostics WHERE crew=? ORDER BY id DESC LIMIT ?', (crew, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r); d['vitals'] = json.loads(d['vitals'] or '{}'); d['symptoms'] = json.loads(d['symptoms'] or '[]'); d['plan'] = json.loads(d['plan'] or '[]')
        d['icon'] = SEVERITY.get(d['severity'], SEVERITY['GREEN'])['icon']; d['label'] = SEVERITY.get(d['severity'], SEVERITY['GREEN'])['label']
        out.append(d)
    return out


def severity_meta(sev): return SEVERITY.get(sev, SEVERITY['GREEN'])


def clean_readings(d):
    """{metric: value} from user input -> only plausible numeric readings."""
    out = {}
    for m, x in (d or {}).items():
        if m not in SANE or x in ('', None): continue
        try: x = float(x)
        except (TypeError, ValueError): continue
        if SANE[m][0] <= x <= SANE[m][1]: out[m] = x
    return out
