"""
AstroVive – Symptom-Based Hazard & Diagnostic Tool (Streamlit version)

Run:   pip install streamlit werkzeug
       streamlit run diagnostic_streamlit.py

It uses the SAME engine (diagnostic_engine.py) and the SAME SQLite file (astra.db) as the Flask app,
so every session saved here also appears in the Flask "Reports" page (Diagnostic History), and the
personal thresholds come from the astronaut's Profile.
"""
import html
import secrets
import sqlite3
from datetime import datetime, timedelta

import streamlit as st
from werkzeug.security import check_password_hash

import diagnostic_engine as DE

COLORS = {'GREEN': '#10b981', 'YELLOW': '#f59e0b', 'RED': '#ef4444', 'BLACK': '#e5e7eb'}

st.set_page_config(page_title='AstroVive – Diagnostic Tool', page_icon='🩺', layout='wide')
st.markdown("""<style>
.dcard{border:2px solid var(--c);border-radius:20px;padding:18px;margin:12px 0;background:rgba(255,255,255,.04)}
.dcard.BLACK{background:#000;box-shadow:0 0 0 2px #ef4444}
.dcard h3{margin:0 0 4px 0}.dcard h5{margin:14px 0 4px;color:#3cc8ff;text-transform:uppercase;font-size:.78rem}
.dcard .sev{float:right;background:var(--c);color:#04101c;font-weight:800;border-radius:99px;padding:4px 14px}
.dcard li,.dcard p{font-size:.92rem;line-height:1.6}.dcard .why{background:rgba(255,255,255,.07);border-radius:8px;padding:4px 10px;margin:3px 0;font-size:.84rem}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ data helpers (shared SQLite file)
def conn():
    c = sqlite3.connect(DE.DB_PATH); c.row_factory = sqlite3.Row; return c


def list_astronauts():
    with conn() as c:
        return [r['name'] for r in c.execute('SELECT name FROM users ORDER BY name')]


def check_access(name, code):
    with conn() as c:
        r = c.execute('SELECT pin FROM users WHERE name=?', (name,)).fetchone()
    if not r: return False
    stored = r['pin'] or ''
    if stored.startswith(('scrypt:', 'pbkdf2:')): return check_password_hash(stored, code)
    return secrets.compare_digest(stored, code)        # legacy plain code (the Flask app hashes it on its first start)


def get_profile(name):
    with conn() as c:
        r = c.execute('SELECT * FROM profile WHERE crew=?', (name,)).fetchone()
    return dict(r) if r else {}


def dashboard_vitals(name, hours=24):
    """Latest Dashboard reading per vital sign (only recent ones are trusted)."""
    cut = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M')
    with conn() as c:
        rows = c.execute('SELECT metric_id,value,ts FROM logs WHERE id IN (SELECT MAX(id) FROM logs WHERE crew=? GROUP BY metric_id)', (name,)).fetchall()
    fresh = [r for r in rows if r['ts'] >= cut]
    return {r['metric_id']: r['value'] for r in fresh}, max((r['ts'] for r in fresh), default=None)


def run_diag(name, symptoms, readings, source, env_alert=False, feel_flags=None):
    """Diagnose with personalised thresholds + Dashboard vitals, then log the session to SQLite."""
    dash, _ = dashboard_vitals(name)
    res = DE.diagnose(symptoms, readings, profile=get_profile(name), env_alert=env_alert, last_known=dash, feel_flags=feel_flags)
    res['id'] = DE.save_diagnosis(name, res, source=f'streamlit-{source}')
    return res


# ------------------------------------------------------------------ Smart Card
def _val(a):
    return 'felt ' + html.escape(a['side'].lower()) if a['value'] is None else html.escape(f"{a['value']:g}") + ' ' + html.escape(a['unit'])


def card_html(res):
    e = html.escape
    rows = ''.join(
        f"<tr><td>{e(DE.NAMES[m])}</td><td><b>{_val(a)}</b></td><td>{e(a['range'])}</td>"
        f"<td>{'🟢' if a['color'] == 'GREEN' else '🟡' if a['color'] == 'YELLOW' else '🔴'}</td><td>{'Dashboard' if a['src'] == 'dashboard' else 'Reported'}</td></tr>"
        for m, a in res['vitals'].items())
    why = ''.join(f'<div class="why">🔗 {e(x)}</div>' for x in res.get('matched', []))
    plan = ''.join(f'<li>{e(x)}</li>' for x in res['plan'])
    return (f"<div class='dcard {res['severity']}' style='--c:{COLORS[res['severity']]}'>"
            f"<span class='sev'>{res['icon']} {e(res['severity_label'])}</span>"
            f"<small>State {res['state']} – {e(res['state_label'])}</small><h3>{res['icon']} {e(res['hazard'])}</h3>"
            f"<h5>Body impact</h5><p>{e(res['impact'])}</p>"
            + (f'<h5>Why this was flagged</h5>{why}' if why else '')
            + f"<h5>Action plan</h5><ol>{plan}</ol>"
            + (f"<h5>Vitals used</h5><table style='width:100%;font-size:.84rem'><tr><th align=left>Vital</th><th align=left>Value</th><th align=left>Your range</th><th></th><th align=left>Source</th></tr>{rows}</table>" if rows
               else '<h5>Vitals used</h5><p>No vital signs available.</p>')
            + "<p style='color:#9db4dd;font-size:.74rem'>Saved to the Reports history. Educational prototype – not NASA flight limits. The flight surgeon makes the final decision.</p></div>")


# ------------------------------------------------------------------ login (same access codes as the Flask app)
st.title('🩺 AstroVive – Symptom-Based Hazard & Diagnostic Tool')
if 'crew' not in st.session_state:
    with st.form('login'):
        who = st.selectbox('Astronaut', list_astronauts())
        code = st.text_input('Cabin access code', type='password')
        if st.form_submit_button('Enter'):
            if check_access(who, code): st.session_state.crew = who; st.rerun()
            else: st.error('Incorrect access code')
    st.stop()

crew = st.session_state.crew
dash, dts = dashboard_vitals(crew)
st.sidebar.markdown(f'**{crew}**')
if st.sidebar.button('Log out'): st.session_state.clear(); st.rerun()
st.sidebar.caption('Dashboard vitals' + (f' (last reading {dts})' if dts else ' – none in the last 24 h'))
for m in ('V01', 'V04', 'V06', 'V05'):
    st.sidebar.metric(DE.NAMES[m], f"{dash[m]:g}" if m in dash else '—')

tab_form, tab_hist = st.tabs(['📝 Symptom form', '📚 Reports history'])

# ------------------------------------------------------------------ Tab 1: form
with tab_form:
    labels = {k: f'{v[0]} · {v[1]}' for k, v in DE.SYMPTOMS.items()}
    chosen = st.multiselect('Symptoms right now', list(labels), format_func=labels.get, placeholder='Select one or more symptoms')
    extra = st.text_input('Anything else? (optional)', placeholder='e.g. "I feel dizzy, heart rate 110, oxygen 94"')
    st.caption('Vital signs – leave at 0 to use the latest Dashboard value.')
    cols = st.columns(4); vals = {}
    for col, (m, lab) in zip(cols, [('V01', 'Heart rate (bpm)'), ('V04', 'SpO2 (%)'), ('V06', 'Respiration (/min)'), ('V05', 'Temperature (°C)')]):
        vals[m] = col.number_input(lab, min_value=0.0, max_value=300.0, value=0.0, step=1.0, key=f'v_{m}')
    env = st.checkbox('A radiation / solar-event alert is active on board')
    if st.button('Run diagnostic', type='primary'):
        syms = list(dict.fromkeys(chosen + DE.parse_symptoms(extra)))
        rd = DE.parse_vitals(extra); rd.update(DE.clean_readings({m: v for m, v in vals.items() if v > 0}))
        if not syms: st.warning('Select or describe at least one symptom.')
        else: st.markdown(card_html(run_diag(crew, syms, rd, 'form', env_alert=env)), unsafe_allow_html=True)

# ------------------------------------------------------------------ Tab 2: history (same table the Flask Reports page reads)
with tab_hist:
    hist = DE.load_history(crew, 100)
    if not hist: st.info('No diagnostics yet.')
    for h in hist:
        with st.expander(f"{h['icon']} {h['ts']} – {h['hazard']} ({h['label']})"):
            st.write('**Symptoms:**', ', '.join(h['symptoms']) or '—')
            st.write('**Vitals:**', '; '.join(f"{DE.NAMES[m]} {v['value'] if v['value'] is not None else 'felt'} {v['unit']}" for m, v in h['vitals'].items()) or '—')
            st.write('**Body impact:**', h['impact'])
            st.write('**Action plan:**'); [st.write(f'{i}. {x}') for i, x in enumerate(h['plan'], 1)]
