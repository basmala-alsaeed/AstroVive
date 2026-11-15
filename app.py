import json, os, re, secrets, sqlite3, time, random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import diagnostic_engine as DE
from flask import Flask, render_template, request, jsonify, redirect, session, url_for, abort

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(BASE, 'data', 'astra_health_data.json'), encoding='utf-8'))
VS = {v['metric_id']: v for v in DATA['vital_signs']}
NAMES = {'V01': 'Heart Rate', 'V02': 'Systolic Pressure', 'V03': 'Diastolic Pressure', 'V04': 'Blood Oxygen (SpO2)',
         'V05': 'Body Temperature', 'V06': 'Respiration Rate', 'V07': 'Fasting Blood Glucose', 'V08': 'Carbon Dioxide (EtCO2)'}
DB = os.path.join(BASE, 'astra.db')
RANK = {'GREEN': 0, 'YELLOW': 1, 'RED': 2}
SANE = {'V01': (20, 250), 'V02': (40, 280), 'V03': (20, 200), 'V04': (50, 100), 'V05': (30, 45), 'V06': (4, 60), 'V07': (20, 600), 'V08': (5, 100)}
CREW = ['Aya Mounir - AI Officer', 'Alex - Mission Commander', 'John - Vehicle Engineer']

# Interactive body map: each clickable region and the data that belongs to it.
# vitals = vital-sign metric ids, wb = daily check-in rule ids, kb = knowledge-base condition ids.
# The mapping of metrics to body parts is a team assumption, not part of the data file.
REGIONS = {
    'head': {'name': 'Head', 'vitals': ['V05'], 'wb': ['B01', 'B03', 'B04', 'B05', 'B06', 'B07', 'B08', 'B09'],
             'kb': ['GRV-02', 'GRV-07', 'HCE-01', 'RAD-05', 'ISO-11'],
             'desc': 'Body temperature plus sleep, mood and cognitive check-ins.'},
    'chest': {'name': 'Chest & Lungs', 'vitals': ['V04', 'V06', 'V08'], 'wb': [],
              'kb': ['HCE-01', 'HCE-02', 'HCE-03', 'HCE-09', 'HCE-12', 'HCE-19', 'DFE-04'],
              'desc': 'Breathing, oxygen and carbon dioxide metrics.'},
    'heart': {'name': 'Heart', 'vitals': ['V01', 'V02', 'V03'], 'wb': [],
              'kb': ['GRV-04', 'GRV-05', 'DFE-04'],
              'desc': 'Heart rate and blood pressure metrics.'},
    'abdomen': {'name': 'Abdomen', 'vitals': ['V07'], 'wb': [],
                'kb': ['GRV-08', 'DFE-05', 'DFE-08'],
                'desc': 'Metabolic metrics (blood glucose).'},
    'right_arm': {'name': 'Right Arm', 'vitals': [], 'wb': [], 'kb': ['GRV-01', 'GRV-03', 'GRV-09', 'HCE-19', 'HCE-20'],
                  'desc': 'Musculoskeletal conditions.'},
    'left_arm': {'name': 'Left Arm', 'vitals': [], 'wb': [], 'kb': ['GRV-01', 'GRV-03', 'GRV-09', 'HCE-19', 'HCE-20'],
                 'desc': 'Musculoskeletal conditions.'},
    'right_leg': {'name': 'Right Leg', 'vitals': [], 'wb': [], 'kb': ['GRV-01', 'GRV-03', 'GRV-09', 'HCE-19'],
                  'desc': 'Musculoskeletal conditions.'},
    'left_leg': {'name': 'Left Leg', 'vitals': [], 'wb': [], 'kb': ['GRV-01', 'GRV-03', 'GRV-09', 'HCE-19'],
                 'desc': 'Musculoskeletal conditions.'},
}

app = Flask(__name__)
def _secret():
    """Session key: env var, else a random key generated once and kept in .secret_key (never hard-coded)."""
    if os.environ.get('ASTRA_SECRET'): return os.environ['ASTRA_SECRET']
    f = os.path.join(BASE, '.secret_key')
    if not os.path.exists(f): open(f, 'w').write(secrets.token_hex(32))
    return open(f).read().strip()
app.secret_key = _secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init():
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, crew TEXT,
                     metric_id TEXT, value REAL, symptom INTEGER, color TEXT, state TEXT, note TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS pain(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, crew TEXT,
                     region TEXT, level INTEGER, note TEXT)''')
init()

def evaluate(mid, val, sym=False, vs=None):
    """Same logic as status_logic in the data file."""
    v = (vs or VS).get(mid)
    try: val = float(val)
    except (TypeError, ValueError): return None
    if not v: return None
    lo_, hi_ = SANE[mid]
    if not lo_ <= val <= hi_: return None
    side = 'LOW' if val < v['normal_min'] else 'HIGH' if val > v['normal_max'] else 'NORMAL'
    cl, ch = v['critical_low'], v['critical_high']
    red = sym or (cl is not None and val < cl) or (ch is not None and val > ch)
    color = 'RED' if red else ('GREEN' if side == 'NORMAL' else 'YELLOW')
    state = 'GREEN' if color == 'GREEN' else ('RED_SYMPTOM' if color == 'RED' and side == 'NORMAL' else f'{color}_{side}')
    return {'color': color, 'state': state, 'guidance': v['states'].get(state)}

def crew():
    return session.get('crew', CREW[0])

PINS = {CREW[0]: '1111', CREW[1]: '2222', CREW[2]: '3333'}
DEMO_IDS = {CREW[0]: 'AV-001', CREW[1]: 'AV-002', CREW[2]: 'AV-003'}
HASHED = re.compile(r'^(scrypt|pbkdf2):[^$]*\$[^$]*\$[^$]+$')
BLOOD = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']

def init_accounts():
    """Individual account per astronaut: identity in `users`, medical 'Basic Information' in `basic_info`."""
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, pin TEXT)')
        have = {r['name'] for r in c.execute("PRAGMA table_info(users)")}
        for col in ('astronaut_id', 'email', 'role', 'created'):
            if col not in have: c.execute(f'ALTER TABLE users ADD COLUMN {col} TEXT')
        c.execute('''CREATE TABLE IF NOT EXISTS basic_info(crew TEXT PRIMARY KEY, blood_type TEXT, nationality TEXT, birth_date TEXT,
                     mission TEXT, emergency_name TEXT, emergency_phone TEXT, allergies TEXT, medications TEXT, notes TEXT)''')
        for n, pin in PINS.items():
            c.execute('INSERT OR IGNORE INTO users(name,pin) VALUES(?,?)', (n, pin))
        for r in c.execute('SELECT name,pin,astronaut_id,role FROM users').fetchall():       # one-time migration: plain codes -> hashes
            if not HASHED.match(r['pin'] or ''): c.execute('UPDATE users SET pin=? WHERE name=?', (generate_password_hash(r['pin']), r['name']))
            if r['name'] in DEMO_IDS and not r['astronaut_id']: c.execute('UPDATE users SET astronaut_id=? WHERE name=?', (DEMO_IDS[r['name']], r['name']))
            if r['name'] in CREW and not r['role']: c.execute('UPDATE users SET role=? WHERE name=?', (r['name'].split(' - ')[-1], r['name']))
        demo = {CREW[0]: ('O+', 'Egyptian', 'ASTRA Demo Mission'), CREW[1]: ('A+', 'American', 'ASTRA Demo Mission'), CREW[2]: ('B+', 'Canadian', 'ASTRA Demo Mission')}
        for n, (bt, nat, mis) in demo.items():   # fictional demo data
            c.execute('INSERT OR IGNORE INTO basic_info(crew,blood_type,nationality,mission,allergies,medications) VALUES(?,?,?,?,?,?)', (n, bt, nat, mis, 'None known', 'None'))
init_accounts()

def get_user(who):
    with db() as c:
        r = c.execute('SELECT name,astronaut_id,email,role FROM users WHERE name=?', (who,)).fetchone()
        b = c.execute('SELECT * FROM basic_info WHERE crew=?', (who,)).fetchone()
    return (dict(r) if r else {'name': who}), (dict(b) if b else {})

def clip(v, n=120): return re.sub(r'[\x00-\x1f<>]', '', str(v or '')).strip()[:n]


# ===== Configurable UI visibility (ui_config.json): hide pages / elements without touching code =====
UI_FILE = os.path.join(BASE, 'ui_config.json')
NEVER_HIDE = {'dashboard', 'login'}                      # the app cannot work without these
NAV = [('dashboard', 'Dashboard'), ('details', 'Details'), ('diagnose', 'Diagnose'), ('reports', 'Reports'),
       ('wellbeing', 'Wellbeing'), ('hazards', 'Hazards'), ('ground', 'Ground'), ('profile', 'Profile')]
PAGE_APIS = {'ground': {'api_send', 'api_flush', 'api_ground_state', 'api_ground_speed', 'api_ground_link'},
             'wellbeing': {'api_wb'}, 'diagnose': {'api_diagnose'},
             'hazards': {'api_env_state', 'api_env_measure'}}
_ui_cache = {'m': None, 'v': {'hidden_pages': set(), 'hidden_elements': set()}}

def ui_cfg():
    try: m = os.path.getmtime(UI_FILE)
    except OSError: return _ui_cache['v']
    if m != _ui_cache['m']:
        try:
            j = json.load(open(UI_FILE, encoding='utf-8'))
            _ui_cache.update(m=m, v={'hidden_pages': set(j.get('hidden_pages', [])) - NEVER_HIDE, 'hidden_elements': set(j.get('hidden_elements', []))})
        except (ValueError, OSError): pass                # a broken file never takes the app down
    return _ui_cache['v']

@app.context_processor
def _inject():
    cfg = ui_cfg()
    show = lambda k: k not in cfg['hidden_pages'] and k not in cfg['hidden_elements']
    return {'show': show, 'NAV': [(e, l) for e, l in NAV if show(e)]}


# ===== Profile: personal limits for each astronaut =====
with db() as _c:
    _c.execute('CREATE TABLE IF NOT EXISTS profile(crew TEXT PRIMARY KEY, age INTEGER, sex TEXT, weight REAL, height REAL, athlete INTEGER, bb INTEGER, htn INTEGER, dm INTEGER)')

def get_profile(who):
    with db() as c: r = c.execute('SELECT * FROM profile WHERE crew=?', (who,)).fetchone()
    return dict(r) if r else None

def personal_vs(who=None):
    """Copy of vital_signs with limits adjusted for the personal condition (team assumptions, not flight limits)."""
    p = get_profile(who or crew()); out = {k: dict(v) for k, v in VS.items()}
    if not p: return out
    if p['athlete']: out['V01']['normal_min'] = min(out['V01']['normal_min'], 40); out['V01']['critical_low'] = min(out['V01']['critical_low'], 40)
    if p['bb']: out['V01']['normal_min'] = min(out['V01']['normal_min'], 50); out['V01']['critical_low'] = min(out['V01']['critical_low'], 40)
    if p['htn']: out['V02']['normal_max'] = max(out['V02']['normal_max'], 129)
    if p['dm']: out['V07'].update(normal_min=80, normal_max=130, critical_high=180)
    return out

def recolor(who):
    """Re-evaluate the astronaut's old readings with the new personal limits."""
    pv = personal_vs(who)
    with db() as c:
        for r in c.execute('SELECT id,metric_id,value,symptom FROM logs WHERE crew=?', (who,)).fetchall():
            e = evaluate(r['metric_id'], r['value'], bool(r['symptom']), pv)
            if e: c.execute('UPDATE logs SET color=?,state=? WHERE id=?', (e['color'], e['state'], r['id']))

def side(pv, mid, x):
    v = pv[mid]; return 'LOW' if x < v['normal_min'] else 'HIGH' if x > v['normal_max'] else 'NORMAL'

def combos(who, pv, wbs):
    """Compound rules that combine several indicators (latest readings within 12 hours of the newest one)."""
    with db() as c:
        rows = c.execute('SELECT metric_id,value,ts FROM logs WHERE id IN (SELECT MAX(id) FROM logs WHERE crew=? GROUP BY metric_id)', (who,)).fetchall()
    if not rows: return []
    T = {r['metric_id']: datetime.strptime(r['ts'], '%Y-%m-%d %H:%M') for r in rows}; newest = max(T.values())
    S = {r['metric_id']: side(pv, r['metric_id'], r['value']) for r in rows if newest - T[r['metric_id']] <= timedelta(hours=12)}
    W = {w['id']: w['color'] for w in wbs}; out = []; g = S.get
    def add(col, txt): out.append({'color': col, 'text': '🔗 ' + txt})
    if g('V01') == 'HIGH' and g('V04') == 'LOW': add('RED', 'High heart rate together with low oxygen: possible hypoxia or cardiorespiratory strain. Repeat the measurement and notify Ground.')
    if g('V06') == 'HIGH' and g('V04') == 'LOW': add('RED', 'Fast breathing with low oxygen: check the air and breathing equipment, and notify Ground.')
    if g('V08') == 'HIGH' and g('V06') == 'HIGH': add('RED', 'High carbon dioxide with fast breathing: possible CO2 retention. Check cabin ventilation.')
    if g('V02') == 'LOW' and g('V01') == 'HIGH': add('RED', 'Low blood pressure with a fast heart rate: possible dehydration, fluid loss or fainting. Lie down and notify Ground.')
    if g('V07') == 'LOW' and g('V01') == 'HIGH': add('RED', 'Low blood glucose with a fast heart rate: possible hypoglycemia. Take a sugar source and measure again.')
    n = sum(g(m) == 'HIGH' for m in ('V05', 'V01', 'V06'))
    if n >= 2: add('RED' if n == 3 else 'YELLOW', f"{n} of 3 indicators (temperature / heart rate / respiration) are high together: may indicate inflammation or infection. Track temperature and notify Ground.")
    if (g('V02') == 'HIGH' or g('V03') == 'HIGH') and g('V01') == 'HIGH' and W.get('B04') == 'YELLOW': add('YELLOW', 'High blood pressure and heart rate with a stress warning: try to relax and measure again after 15 minutes.')
    if W.get('B01') == 'YELLOW' and any(W.get(k) == 'YELLOW' for k in ('B07', 'B08', 'B09')): add('YELLOW', 'Short sleep with reduced attention, memory or reaction time: fatigue risk that can affect critical tasks.')
    return out

@app.before_request
def guard():
    ep = request.endpoint
    if ep not in ('login', 'do_login', 'do_register', 'static') and 'crew' not in session:
        if request.path.startswith('/api/'): return jsonify(error='Session expired – please log in again'), 401
        return redirect('/')
    hid = ui_cfg()['hidden_pages']
    if ep in hid or any(ep in apis for pg, apis in PAGE_APIS.items() if pg in hid): abort(404)

@app.route('/')
def login():
    session.pop('crew', None)
    with db() as c: names = [r['name'] for r in c.execute('SELECT name FROM users')]
    return render_template('login.html', crew=names, err=request.args.get('err', ''))

@app.post('/login')
def do_login():
    n, p = request.form.get('crew', '').strip(), request.form.get('pin', '')
    r = None
    if n:
        with db() as c: rows = c.execute('SELECT name,pin FROM users').fetchall()
        nl = n.lower()
        for u in rows:                   # exact name first, then just the first name (for example: Aya)
            if u['name'].lower() == nl and check_password_hash(u['pin'], p): r = u; break
        if not r:
            for u in rows:
                if u['name'].lower().startswith(nl) and check_password_hash(u['pin'], p): r = u; break
    if not r: return redirect(url_for('login', err='Incorrect name or access code'))
    session.clear(); session['crew'] = r['name']; return redirect('/dashboard')

@app.post('/register')
def do_register():
    f = request.form; n, p = clip(f.get('crew'), 60), f.get('pin', '')
    aid, email, role = clip(f.get('astronaut_id'), 24), clip(f.get('email'), 80), clip(f.get('role'), 60)
    if not n or len(p) < 4: return redirect(url_for('login', err='A name is required and the code needs at least 4 characters'))
    if email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email): return redirect(url_for('login', err='Email address looks invalid'))
    with db() as c:
        if c.execute('SELECT 1 FROM users WHERE lower(name)=lower(?)', (n,)).fetchone(): return redirect(url_for('login', err='This name is already registered'))
        if aid and c.execute('SELECT 1 FROM users WHERE astronaut_id=?', (aid,)).fetchone(): return redirect(url_for('login', err='This astronaut ID is already registered'))
        c.execute('INSERT INTO users(name,pin,astronaut_id,email,role,created) VALUES(?,?,?,?,?,?)', (n, generate_password_hash(p), aid, email, role or 'Crew Member', datetime.now().strftime('%Y-%m-%d %H:%M')))
        c.execute('INSERT OR IGNORE INTO basic_info(crew) VALUES(?)', (n,))
    session.clear(); session['crew'] = n; return redirect('/profile')


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    who = crew(); msg = ''
    if request.method == 'POST' and request.form.get('section') == 'basic':
        f = request.form; bt = f.get('blood_type', '')
        if bt not in BLOOD + ['']: return redirect('/profile?err=Invalid+blood+type')
        bd = f.get('birth_date', '')
        if bd and not re.match(r'^\d{4}-\d{2}-\d{2}$', bd): return redirect('/profile?err=Invalid+birth+date')
        email = clip(f.get('email'), 80)
        if email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email): return redirect('/profile?err=Invalid+email')
        with db() as c:
            c.execute('UPDATE users SET email=?, role=? WHERE name=?', (email, clip(f.get('role'), 60), who))
            c.execute('INSERT OR IGNORE INTO basic_info(crew) VALUES(?)', (who,))
            c.execute('UPDATE basic_info SET blood_type=?,nationality=?,birth_date=?,mission=?,emergency_name=?,emergency_phone=?,allergies=?,medications=?,notes=? WHERE crew=?',
                      (bt, clip(f.get('nationality'), 40), bd, clip(f.get('mission'), 80), clip(f.get('emergency_name'), 60),
                       re.sub(r'[^0-9+() -]', '', f.get('emergency_phone', ''))[:24], clip(f.get('allergies'), 160), clip(f.get('medications'), 160), clip(f.get('notes'), 300), who))
        return redirect('/profile?saved=basic')
    if request.method == 'POST':
        f = request.form
        try:
            age, w, h = int(f.get('age') or 0), float(f.get('weight') or 0), float(f.get('height') or 0)
            assert 18 <= age <= 90 and 30 <= w <= 250 and 120 <= h <= 230
        except (ValueError, AssertionError):
            msg = 'Check the values: age 18–90, weight 30–250 kg, height 120–230 cm'
        else:
            sex = f.get('sex') if f.get('sex') in ('M', 'F') else ''
            with db() as c: c.execute('INSERT OR REPLACE INTO profile VALUES(?,?,?,?,?,?,?,?,?)', (who, age, sex, w, h, *(int(k in f) for k in ('athlete', 'bb', 'htn', 'dm'))))
            recolor(who); return redirect('/profile?ok=1')
    p = get_profile(who); pv = personal_vs(who)
    rows = [{'id': k, 'name': NAMES[k], 'unit': v['unit'], 'old': f"{VS[k]['normal_min']}–{VS[k]['normal_max']}", 'new': f"{v['normal_min']}–{v['normal_max']}",
             'ch': (v['normal_min'], v['normal_max'], v['critical_low'], v['critical_high']) != (VS[k]['normal_min'], VS[k]['normal_max'], VS[k]['critical_low'], VS[k]['critical_high'])} for k, v in pv.items()]
    bmi = round(p['weight'] / (p['height'] / 100) ** 2, 1) if p and p['weight'] and p['height'] else None
    me, bi = get_user(who)
    return render_template('profile.html', p=p, rows=rows, bmi=bmi, crew=who, ok=request.args.get('ok'), err=msg or request.args.get('err', ''),
                           me=me, bi=bi, blood=BLOOD, saved=request.args.get('saved'))


# ===== Interactive body map =====
def recent_pain(c, who):
    """Latest pain level per body region, only if reported in the last 24 hours and above zero."""
    cut = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')
    rows = c.execute('SELECT region,level,ts FROM pain WHERE id IN (SELECT MAX(id) FROM pain WHERE crew=? GROUP BY region)', (who,)).fetchall()
    return {r['region']: r['level'] for r in rows if r['ts'] >= cut and r['level'] > 0}

def region_status(who):
    with db() as c:
        last = {r['metric_id']: r['color'] for r in c.execute('SELECT metric_id,color FROM logs WHERE id IN (SELECT MAX(id) FROM logs WHERE crew=? GROUP BY metric_id)', (who,))}
        pain = recent_pain(c, who)
    return {k: {'color': max([last[m] for m in r['vitals'] if m in last], key=RANK.get, default='NONE'), 'pain': pain.get(k)} for k, r in REGIONS.items()}

def pain_label(level):
    return ('None', 'GREEN') if level == 0 else ('Mild', 'GREEN') if level <= 3 else ('Moderate', 'YELLOW') if level <= 6 else ('Severe', 'RED')

@app.route('/body/<rid>')
def body(rid):
    reg = REGIONS.get(rid)
    if not reg: abort(404)
    who = crew(); pv = personal_vs(who); metrics, logs = [], []
    with db() as c:
        for m in reg['vitals']:
            v = pv[m]
            rows = c.execute('SELECT ts,value,color,symptom,note FROM logs WHERE crew=? AND metric_id=? ORDER BY id DESC LIMIT 20', (who, m)).fetchall()
            last = rows[0] if rows else None
            g = evaluate(m, last['value'], bool(last['symptom']), pv)['guidance'] if last and last['color'] != 'GREEN' and evaluate(m, last['value'], bool(last['symptom']), pv) else None
            metrics.append({'name': NAMES[m], 'unit': v['unit'], 'val': last['value'] if last else None, 'color': last['color'] if last else 'NONE',
                            'range': f"{v['normal_min']}–{v['normal_max']}", 'vals': [r['value'] for r in rows][::-1], 'g': g, 'msg': ''})
            logs += [{'ts': r['ts'], 'name': NAMES[m], 'unit': v['unit'], 'value': r['value'], 'color': r['color'], 'symptom': r['symptom'], 'note': r['note']} for r in rows]
        wbs = {w['id']: w for w in wb_all(c, who)}
        for rid2 in reg['wb']:
            w = wbs.get(rid2)
            if not w: continue
            metrics.append({'name': w['name'], 'unit': WB[rid2][1], 'val': w['last'], 'color': w['color'], 'range': '', 'vals': w['vals'],
                            'g': {'what_it_means': w['msg'], 'first_aid_action': w['action'], 'contact_ground_when': w['contact']} if w['color'] == 'YELLOW' else None,
                            'msg': w['msg'] if w['color'] == 'NONE' else ''})
        pain = []
        for r in c.execute('SELECT ts,level,note FROM pain WHERE crew=? AND region=? ORDER BY id DESC LIMIT 15', (who, rid)):
            lab, col = pain_label(r['level']); pain.append({'ts': r['ts'], 'level': r['level'], 'note': r['note'], 'label': lab, 'col': col})
    logs.sort(key=lambda x: x['ts'], reverse=True)
    kb_ids = list(dict.fromkeys(reg['kb'] + [BR[r]['kb_id'] for r in reg['wb']]))
    kb = [KBX[i] for i in kb_ids if i in KBX]
    return render_template('body.html', region={'id': rid, **reg}, regions=[{'id': k, 'name': r['name']} for k, r in REGIONS.items()],
                           metrics=metrics, logs=logs[:12], pain=pain, kb=kb, crew=who)

@app.post('/api/pain')
def api_pain():
    j = request.get_json(force=True)
    try: level = int(j.get('level'))
    except (TypeError, ValueError): return jsonify(error='Pain level must be a number from 0 to 10'), 400
    rid = j.get('region')
    if rid not in REGIONS or not 0 <= level <= 10: return jsonify(error='Invalid body region or pain level'), 400
    with db() as c:
        c.execute('INSERT INTO pain(ts,crew,region,level,note) VALUES(?,?,?,?,?)',
                  (datetime.now().strftime('%Y-%m-%d %H:%M'), crew(), rid, level, str(j.get('note') or '')[:200]))
    return jsonify(ok=1)


@app.route('/dashboard')
def dashboard():
    with db() as c:
        rows = c.execute('SELECT * FROM logs WHERE id IN (SELECT MAX(id) FROM logs WHERE crew=? GROUP BY metric_id)', (crew(),)).fetchall()
    last = {r['metric_id']: r for r in rows}; pv = personal_vs()
    cards = [{'id': k, 'name': NAMES[k], 'unit': v['unit'], 'range': f"{v['normal_min']}–{v['normal_max']}",
              'val': last[k]['value'] if k in last else None, 'color': last[k]['color'] if k in last else 'NONE'}
             for k, v in pv.items()]
    worst = max((r['color'] for r in rows), key=RANK.get, default='NONE')
    col={'GREEN':'#10b981','YELLOW':'#f59e0b','RED':'#ef4444','NONE':'#475569'}
    G=[('Cardiovascular',['V01','V02','V03']),('Respiratory',['V04','V06','V08']),('Metabolic',['V07']),('Temperature',['V05'])]
    grp=[(n,col[max([last[i]['color'] for i in ids if i in last],key=RANK.get,default='NONE')]) for n,ids in G]
    me, bi = get_user(crew())
    return render_template('dashboard.html', me=me, bi=bi, cards={c['id']:c for c in cards}, worst=worst, crew=crew(), n=len(rows), grp=grp, ins=insights(crew()), zones=region_status(crew()))

@app.get('/api/history/<mid>')
def history(mid):
    with db() as c:
        r = c.execute('SELECT value FROM logs WHERE crew=? AND metric_id=? ORDER BY id DESC LIMIT 30', (crew(), mid)).fetchall()
    return jsonify([x['value'] for x in r][::-1])

@app.route('/details')
def details():
    return render_template('details.html', vs=list(personal_vs().values()), names=NAMES, crew=crew())

@app.route('/reports')
def reports():
    with db() as c:
        logs = c.execute('SELECT * FROM logs WHERE crew=? ORDER BY id DESC LIMIT 100', (crew(),)).fetchall()
    return render_template('reports.html', diag=DE.load_history(crew(), 50), logs=logs, names=NAMES, vs=personal_vs(), crew=crew(), meta=DATA['meta'], cnt={k:sum(1 for l in logs if l['color']==k) for k in RANK})

@app.post('/api/check')
def api_check():
    j = request.get_json(force=True)
    r = evaluate(j.get('metric_id'), j.get('value'), bool(j.get('symptom')), personal_vs())
    return jsonify(r or {'error': 'Implausible value for this measurement – please check it'})

@app.post('/api/log')
def api_log():
    j = request.get_json(force=True); out = []; pv = personal_vs()
    with db() as c:
        for x in j.get('readings', []):
            r = evaluate(x.get('metric_id'), x.get('value'), bool(j.get('symptom')), pv)
            if not r: continue
            c.execute('INSERT INTO logs(ts,crew,metric_id,value,symptom,color,state,note) VALUES(?,?,?,?,?,?,?,?)',
                      (datetime.now().strftime('%Y-%m-%d %H:%M'), crew(), x['metric_id'], float(x['value']),
                       int(bool(j.get('symptom'))), r['color'], r['state'], j.get('note', '')))
            out.append({'metric_id': x['metric_id'], 'color': r['color']})
    return jsonify({'saved': out})


BR = {r['rule_id']: r for r in DATA['baseline_rules']}
KBX = {k['id']: k for k in DATA['knowledge_base']}
WB = {'B01': ('Sleep Duration', 'hours/night'), 'B03': ('Sleep Efficiency', '%'), 'B04': ('Stress', '1–5'), 'B05': ('Mood', '1–5'),
      'B06': ('Mental Fatigue', '1–5'), 'B07': ('Attention Test', '0–100'), 'B08': ('Reaction Time', 'ms'), 'B09': ('Memory Test', '0–100')}
with db() as _c:
    _c.execute('CREATE TABLE IF NOT EXISTS wb(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, crew TEXT, rule_id TEXT, value REAL, color TEXT, msg TEXT)')
    _c.execute('CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, sent REAL, crew TEXT, delay REAL, down INTEGER, body TEXT)')

def wb_eval(rid, vals):
    """baseline_rules: absolute, or relative to the personal baseline (first two readings). TBD fields use team defaults (consecutive = 2)."""
    r = BR[rid]; n = r['consecutive_count'] or 2; cw = r['concern_when']
    if r['abs_threshold'] is not None:
        t = r['abs_threshold']; bad = [(x < t) if cw == 'Below' else (x > t) for x in vals]
    else:
        if len(vals) < 4: return 'NONE', f"Building personal baseline ({len(vals)}/4 readings)"
        b = sum(vals[:2]) / 2; p = r['rel_threshold_pct'] or 25
        pct = [(x - b) / b * 100 for x in vals]
        bad = [(q <= -p) if cw == 'Decrease' else (q >= p) if cw == 'Increase' else abs(q) >= p for q in pct]
    hit = len(vals) >= n and all(bad[-n:])
    cn = '2 consecutive readings' if n == 2 else f'{n} consecutive readings'
    if r['abs_threshold'] is not None:
        txt = f"{'Below' if cw == 'Below' else 'Above'} {r['abs_threshold']:g} for {cn}" if n > 1 else f"{'Below' if cw == 'Below' else 'Above'} {r['abs_threshold']:g}"
    else:
        txt = f"{'Decreased' if cw == 'Decrease' else 'Increased' if cw == 'Increase' else 'Deviated'} {p:g}% or more from your personal baseline for {cn}"
    return ('YELLOW' if hit else 'GREEN'), (txt if hit else 'Within limits')

def wb_all(c, who):
    out = []
    for rid in WB:
        vals = [x['value'] for x in c.execute('SELECT value FROM wb WHERE crew=? AND rule_id=? ORDER BY id', (who, rid))]
        if not vals: continue
        col, msg = wb_eval(rid, vals); k = KBX.get(BR[rid]['kb_id'], {})
        out.append({'id': rid, 'name': WB[rid][0], 'last': vals[-1], 'n': len(vals), 'color': col, 'msg': msg, 'vals': vals[-20:],
                    'action': k.get('recommended_action') if col == 'YELLOW' else '', 'contact': k.get('contact_ground_when') if col == 'YELLOW' else ''})
    return out

def insights(who):
    """Trend plus deviation from the personal baseline for vital signs (team assumptions: 3 consecutive readings, 10% deviation)."""
    res = []; pv = personal_vs(who)
    with db() as c:
        for mid, v in pv.items():
            x = [r['value'] for r in c.execute('SELECT value FROM logs WHERE crew=? AND metric_id=? ORDER BY id', (who, mid))][-12:]
            if len(x) >= 3 and (x[-3] < x[-2] < x[-1] or x[-3] > x[-2] > x[-1]):
                proj = x[-1] + (x[-1] - x[-2])
                if proj > v['normal_max'] or proj < v['normal_min']:
                    res.append({'color': 'YELLOW', 'text': f"{NAMES[mid]}: steady {'rising' if x[-1] > x[-3] else 'falling'} trend ({x[-3]:g}→{x[-2]:g}→{x[-1]:g}); the next expected reading ({proj:.3g}) is outside the normal range"})
            if len(x) >= 3:
                b = sum(x[:2]) / 2
                if b and abs(x[-1] - b) / b >= .10:
                    res.append({'color': 'YELLOW', 'text': f"{NAMES[mid]}: moved {abs(x[-1]-b)/b*100:.0f}% away from your personal baseline ({b:.3g})"})
        wbs = wb_all(c, who)
        res += [{'color': w['color'], 'text': f"{w['name']}: {w['msg']}"} for w in wbs if w['color'] == 'YELLOW']
    return combos(who, pv, wbs) + res

@app.route('/wellbeing')
def wellbeing():
    with db() as c: items = wb_all(c, crew())
    return render_template('wellbeing.html', WB=WB, items=items, crew=crew())

@app.post('/api/wb')
def api_wb():
    with db() as c:
        for rid, val in request.get_json(force=True).get('values', {}).items():
            if rid in WB and val not in ('', None) and 0 <= float(val) <= 1000 and (rid not in ('B04', 'B05', 'B06') or 1 <= float(val) <= 5) and (rid not in ('B03', 'B07', 'B09') or float(val) <= 100):
                c.execute('INSERT INTO wb(ts,crew,rule_id,value,color,msg) VALUES(?,?,?,?,?,?)', (datetime.now().strftime('%Y-%m-%d %H:%M'), crew(), rid, float(val), '', ''))
    return jsonify(ok=1)

# ===== Hazards: automatic cabin-environment measurement =====
# Simulated cabin sensors: one sample of every parameter every 2 minutes, stored in env_log. If nobody has the page open,
# the missed samples are filled in on the next visit (catch-up), so the history always covers the time that passed.
ENV_EVERY = 120                      # seconds between automatic measurements
ENV_KEEP = 240                       # samples kept per parameter
# limit_id: (nominal value, noise per step, lowest, highest) - values drift around the nominal value and sometimes cross a limit
ENV_SIM = {'E01': (2.0, .22, .5, 4.5), 'E02': (78, .6, 24, 82), 'E03': (101.3, .5, 33, 105), 'E04': (.4, .5, 0, 15),
           'E05': (22.5, .45, 16, 29), 'E06': (45, 2.2, 18, 82), 'E09': (.8, .25, 0, 3.6), 'E10': (.25, .08, 0, 1.2),
           'E11': (.08, .04, 0, .4), 'E12': (42, 1.8, 30, 56), 'E13': (32, 1.5, 25, 46), 'E14': (300, 22, 190, 400),
           'E15': (.8, .04, .55, 1), 'E16': (8, .7, 4, 12), 'E17': (.2, .03, 0, .4)}
ENV_ALIAS = {'E07': 'E05', 'E08': 'E06'}        # the same temperature / humidity sensor, checked against the performance-zone limits too
ENV_LIMITS = [e for e in DATA['environment_limits'] if e['limit_id'] in ENV_SIM or e['limit_id'] in ENV_ALIAS]
with db() as _c:
    _c.execute('CREATE TABLE IF NOT EXISTS env_log(id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL, limit_id TEXT, value REAL)')
    _c.execute('CREATE INDEX IF NOT EXISTS ix_env ON env_log(limit_id, t)')

def env_tick(force=False):
    """Take every measurement that is due (one per 2 minutes since the last one). force=True adds one more right now."""
    now = time.time()
    with db() as c:
        last = c.execute('SELECT MAX(t) FROM env_log').fetchone()[0]
        cur = {r['limit_id']: r['value'] for r in c.execute('SELECT limit_id, value FROM env_log WHERE id IN (SELECT MAX(id) FROM env_log GROUP BY limit_id)')}
        if last is None:
            times = [now - ENV_EVERY * i for i in range(14, -1, -1)]          # first start: 15 earlier samples so a trend is visible
        else:
            due = int((now - last) // ENV_EVERY)
            times = [last + ENV_EVERY * (due - n + i + 1) for n in [min(due, 30)] for i in range(n)]
            if not times and force and now - last >= 5: times = [now]
        for t in times:
            for k, (nom, noise, lo, hi) in ENV_SIM.items():
                v = cur.get(k, nom); v = v + .25 * (nom - v) + random.gauss(0, noise)
                cur[k] = round(min(hi, max(lo, v)), 3)
                c.execute('INSERT INTO env_log(t,limit_id,value) VALUES(?,?,?)', (t, k, cur[k]))
            for a, k in ENV_ALIAS.items(): c.execute('INSERT INTO env_log(t,limit_id,value) VALUES(?,?,?)', (t, a, cur[k]))
        if times: c.execute('DELETE FROM env_log WHERE id <= (SELECT MAX(id) FROM env_log) - ?', (ENV_KEEP * (len(ENV_SIM) + len(ENV_ALIAS)),))

def env_state():
    with db() as c: rows = c.execute('SELECT limit_id, t, value FROM env_log ORDER BY t, id').fetchall()
    hist = {}
    for r in rows: hist.setdefault(r['limit_id'], []).append([round(r['t'], 1), r['value']])
    last = max((h[-1][0] for h in hist.values()), default=None); now = time.time()
    return {'history': {k: v[-60:] for k, v in hist.items()}, 'next_in': max(1, round(ENV_EVERY - (now - last))) if last else ENV_EVERY, 'every': ENV_EVERY}

@app.route('/hazards')
def hazards():
    return render_template('hazards.html', env=ENV_LIMITS, crew=crew())

@app.get('/api/env')
def api_env_state():
    env_tick(); return jsonify(env_state())

@app.post('/api/env/measure')
def api_env_measure():
    env_tick(force=True); return jsonify(env_state())

# ===== Ground link: persistent simulation clock (speed ×1/×10/×60/×600) + link state =====
SPEEDS = (1, 10, 60, 600)
DELAYS = (0.05, 1.3, 5, 20)          # minutes one-way: ISS, Moon, HERA, Mars
with db() as _c:
    _c.execute('CREATE TABLE IF NOT EXISTS simclock(crew TEXT PRIMARY KEY, speed REAL, base_real REAL, base_sim REAL, link_down INTEGER)')

def clock(who):
    """Simulated time never jumps: changing the speed re-bases the clock, so past progress is kept."""
    with db() as c:
        r = c.execute('SELECT * FROM simclock WHERE crew=?', (who,)).fetchone()
        if not r:
            t = time.time(); c.execute('INSERT INTO simclock VALUES(?,?,?,?,?)', (who, 1.0, t, t, 0))
            r = c.execute('SELECT * FROM simclock WHERE crew=?', (who,)).fetchone()
    return dict(r)

def sim_now(r): return r['base_sim'] + (time.time() - r['base_real']) * r['speed']

def clock_state(who):
    r = clock(who); return {'sim_now': sim_now(r), 'speed': r['speed'], 'link_down': bool(r['link_down']), 'real_now': time.time()}

@app.route('/ground')
def ground():
    with db() as c: rows = c.execute('SELECT * FROM outbox WHERE crew=? ORDER BY id DESC LIMIT 20', (crew(),)).fetchall()
    return render_template('ground.html', rows=rows, crew=crew(), speeds=SPEEDS, st=clock_state(crew()))

@app.get('/api/ground/state')
def api_ground_state():
    return jsonify(clock_state(crew()))

@app.post('/api/ground/speed')
def api_ground_speed():
    if 'speed_sim' in ui_cfg()['hidden_elements']: return jsonify(error='Speed simulation is disabled'), 403
    try: sp = float((request.get_json(silent=True) or {}).get('speed'))
    except (TypeError, ValueError): return jsonify(error='Invalid speed'), 400
    if sp not in SPEEDS: return jsonify(error='Speed must be one of ' + ', '.join(f'x{x}' for x in SPEEDS)), 400
    r = clock(crew()); now = sim_now(r)
    with db() as c: c.execute('UPDATE simclock SET speed=?, base_real=?, base_sim=? WHERE crew=?', (sp, time.time(), now, crew()))
    return jsonify(clock_state(crew()))

@app.post('/api/ground/link')
def api_ground_link():
    """Link up/down. When the link returns, every stored report is transmitted (clock starts at that moment)."""
    down = bool((request.get_json(silent=True) or {}).get('down'))
    who = crew(); r = clock(who)
    with db() as c:
        c.execute('UPDATE simclock SET link_down=? WHERE crew=?', (int(down), who))
        if not down: c.execute('UPDATE outbox SET down=0, sent=? WHERE crew=? AND down=1', (sim_now(r), who))
    return jsonify(clock_state(who))

@app.post('/api/send')
def api_send():
    j = request.get_json(silent=True) or {}; who = crew(); L = [f"Health report – {who} – {datetime.now():%Y-%m-%d %H:%M}", '— Latest readings:']
    try: delay = float(j.get('delay', 5))
    except (TypeError, ValueError): return jsonify(error='Invalid delay'), 400
    if delay not in DELAYS: return jsonify(error='Unknown destination delay'), 400
    r = clock(who)
    with db() as c:
        for x in c.execute('SELECT * FROM logs WHERE id IN (SELECT MAX(id) FROM logs WHERE crew=? GROUP BY metric_id)', (who,)):
            L.append(f"  {NAMES[x['metric_id']]}: {x['value']:g} {VS[x['metric_id']]['unit']} [{x['color']}]")
        pain = recent_pain(c, who)
        if pain: L += ['— Pain reports (last 24 h):'] + [f"  {REGIONS[k]['name']}: {v}/10" for k, v in pain.items()]
        ins = insights(who); L.append('— Smart alerts:' if ins else '— No smart alerts.')
        L += ['  • ' + i['text'] for i in ins]
        d = c.execute('SELECT ts,hazard,severity FROM diagnostics WHERE crew=? ORDER BY id DESC LIMIT 1', (who,)).fetchone() if c.execute("SELECT 1 FROM sqlite_master WHERE name='diagnostics'").fetchone() else None
        if d: L += ['— Latest symptom diagnostic:', f"  {d['ts']}: {d['hazard']} [{d['severity']}]"]
        L.append('— Note: educational prototype; the decision rests with the flight surgeon.')
        c.execute('INSERT INTO outbox(sent,crew,delay,down,body) VALUES(?,?,?,?,?)', (sim_now(r), who, delay, int(bool(r['link_down'])), '\n'.join(L)))
    return jsonify(ok=1, queued=bool(r['link_down']))

def _restore_link():
    who = crew(); r = clock(who)
    with db() as c:
        c.execute('UPDATE simclock SET link_down=0 WHERE crew=?', (who,))
        c.execute('UPDATE outbox SET down=0, sent=? WHERE crew=? AND down=1', (sim_now(r), who))
    return jsonify(clock_state(who))

@app.post('/api/flush')
def api_flush():                       # kept for backward compatibility: same as "restore link"
    return _restore_link()

# ===== Symptom-based hazard diagnostic (shared engine: diagnostic_engine.py) =====
DE.init_diag_table()

def dashboard_vitals(who, hours=24):
    """Latest dashboard reading per vital sign, only if it is recent enough to be meaningful."""
    cut = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M')
    with db() as c:
        rows = c.execute('SELECT metric_id,value,ts FROM logs WHERE id IN (SELECT MAX(id) FROM logs WHERE crew=? GROUP BY metric_id)', (who,)).fetchall()
    return {r['metric_id']: r['value'] for r in rows if r['ts'] >= cut}, max((r['ts'] for r in rows if r['ts'] >= cut), default=None)

def run_diag(who, symptoms, readings, source, env_alert=False, feel_flags=None):
    dash, dts = dashboard_vitals(who)
    res = DE.diagnose(symptoms, readings, profile=get_profile(who), env_alert=env_alert, last_known=dash, feel_flags=feel_flags)
    res['dashboard_ts'] = dts
    res['id'] = DE.save_diagnosis(who, res, source=source)
    return res

@app.route('/diagnose')
def diagnose():
    dash, dts = dashboard_vitals(crew()); pv = personal_vs()
    lim = {k: f"{pv[k]['normal_min']}–{pv[k]['normal_max']} {pv[k]['unit']}" for k in ('V01', 'V04', 'V06', 'V05')}
    return render_template('diagnose.html', crew=crew(), symptoms=[(k, v[0], v[1]) for k, v in DE.SYMPTOMS.items()], dash=dash, dts=dts, lim=lim, names=NAMES,
                           history=DE.load_history(crew(), 8))

@app.post('/api/diagnose')
def api_diagnose():
    j = request.get_json(silent=True) or {}; text = str(j.get('text') or '')[:600]
    sym = [k for k in (j.get('symptoms') or []) if isinstance(k, str) and k in DE.SYMPTOMS] if isinstance(j.get('symptoms'), list) else []
    sym = list(dict.fromkeys(sym + DE.parse_symptoms(text)))
    rd = DE.parse_vitals(text); rd.update(DE.clean_readings(j.get('readings') if isinstance(j.get('readings'), dict) else {}))
    if not sym: return jsonify(error='Select or describe at least one symptom'), 400
    return jsonify(run_diag(crew(), sym, rd, 'form', env_alert=bool(j.get('env_alert'))))

@app.post('/api/seed')
def api_seed():
    """Fictional demo data for 10 days: heart rate rises, oxygen falls and sleep gets worse."""
    random.seed(7); base = {'V01': 68, 'V02': 108, 'V03': 70, 'V04': 98, 'V05': 36.7, 'V06': 15, 'V07': 85, 'V08': 40}
    drift = {'V01': 4.2, 'V04': -.7}; pv = personal_vs()
    with db() as c:
        for t in ('logs', 'wb'): c.execute(f'DELETE FROM {t} WHERE crew=?', (crew(),))
        for d in range(10):
            ts = (datetime.now() - timedelta(days=9 - d)).strftime('%Y-%m-%d %H:%M')
            for m, b in base.items():
                val = round(b + drift.get(m, 0) * d + random.uniform(-.02, .02) * b, 1); r = evaluate(m, val, False, pv)
                c.execute('INSERT INTO logs(ts,crew,metric_id,value,symptom,color,state,note) VALUES(?,?,?,?,?,?,?,?)', (ts, crew(), m, val, 0, r['color'], r['state'], 'Demo data'))
            w = {'B01': 8 - .35 * d, 'B03': 92 - 1.2 * d, 'B04': 2 + random.random(), 'B05': 4 - .2 * d, 'B06': 2 + .3 * d,
                 'B07': 90 - 1.5 * d, 'B08': 250 + 9 * d, 'B09': 85 - 1.2 * d}
            for k, v in w.items(): c.execute('INSERT INTO wb(ts,crew,rule_id,value,color,msg) VALUES(?,?,?,?,?,?)', (ts, crew(), k, round(v, 1), '', ''))
    return jsonify(ok=1)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
