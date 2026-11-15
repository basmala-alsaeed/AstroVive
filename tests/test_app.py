"""AstroVive tests. Run from the project folder:  python -m unittest discover -s tests -v   (pytest also works if installed)."""
import json, os, sys, tempfile, unittest

_TMP = tempfile.mkdtemp()
os.environ['ASTRA_DB'] = os.path.join(_TMP, 'test.db')          # never touch the real astra.db
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A                                                  # noqa: E402
import diagnostic_engine as DE                                   # noqa: E402


def conds(r): return [c['id'] for c in r['conditions']]


class Base(unittest.TestCase):
    def setUp(self):
        with A.db() as c:
            for t in ('logs', 'wb', 'pain', 'cabin', 'diagnostics', 'outbox'): c.execute(f'DELETE FROM {t}')
        self.c = A.app.test_client(); self.login('Aya', '1111')

    def login(self, who, pin): self.c.post('/login', data={'crew': who, 'pin': pin})

    def diag(self, **kw):
        r = self.c.post('/api/diagnose', json=kw); self.assertEqual(r.status_code, 200, r.get_data(as_text=True)); return r.get_json()


class RemovedHazardsPage(Base):                                    # (a)
    def test_route_is_gone(self):
        self.assertIn(self.c.get('/hazards').status_code, (404, 302, 308))
        self.assertEqual(self.c.get('/hazards', follow_redirects=False).status_code, 404)

    def test_not_in_menu_or_files(self):
        self.assertNotIn('hazards', [e for e, _ in A.NAV])
        html = self.c.get('/dashboard').get_data(as_text=True)
        self.assertNotIn('href="/hazards"', html)
        self.assertFalse(os.path.exists(os.path.join(A.BASE, 'templates', 'hazards.html')))
        self.assertNotIn('hazards', [k for k in json.load(open(os.path.join(A.BASE, 'ui_config.json')))['_pages_you_can_hide']])

    def test_diagnose_page_has_one_flow(self):
        html = self.c.get('/diagnose').get_data(as_text=True)
        for part in ('Symptoms right now', 'Situation', 'Measurements', 'Cabin readings', 'aria-pressed', 'aria-live'): self.assertIn(part, html)
        self.assertEqual(html.count('id="go"'), 1)


class CabinGuarantee(Base):
    SYM = ['headache', 'dizziness']

    def test_b_no_cabin_data_returns_hce02_and_check_the_cabin(self):
        r = self.diag(symptoms=self.SYM)
        self.assertEqual(conds(r)[0], 'HCE-02')
        self.assertEqual(r['cabin']['summary'], 'not_checked'); self.assertEqual(r['cabin']['summary_text'], 'Cabin not checked')
        self.assertTrue(r['cabin']['check_cabin'])                                   # the "Check the cabin" prompt
        k = DE.KB['HCE-02']
        self.assertTrue(any(x['metric'] == k['metric_to_monitor'] and x['measured'] == k['measured_in_space'] for x in r['cabin']['check_cabin']))
        self.assertEqual(r['hazard']['name'], DE.HCE_HAZARD)
        self.assertEqual(r['disclaimer'], DE.DATA['meta']['required_app_disclaimer'])

    def test_c_ppco2_out_of_limit_puts_hce01_first(self):
        r = self.diag(symptoms=self.SYM, cabin={'E01': 4.5})
        self.assertEqual(conds(r)[0], 'HCE-01')
        ev = [e for g in r['conditions'][0]['why'] for e in g['items'] if g['type'] == 'cabin']
        self.assertTrue(ev); self.assertEqual(ev[0]['label'], 'Cabin reading out of limit')
        self.assertEqual(ev[0]['verification'], DE.ENV['E01']['verification']); self.assertEqual(ev[0]['verification'], 'Verified in source')
        self.assertIn('Environmental cause first', r['cabin']['cause_first'])
        self.assertTrue(any(a['text'] == DE.KB['HCE-01']['recommended_action'] for a in r['actions']))   # data text, not paraphrased
        self.assertEqual(r['hazard']['name'], DE.HCE_HAZARD); self.assertFalse(r['cabin']['check_cabin'] and any('CO2 partial' in x['metric'] for x in r['cabin']['check_cabin']))

    def test_e_in_range_reading_raises_no_cabin_alert(self):
        r = self.diag(symptoms=self.SYM, cabin={'E01': 1.2})
        self.assertEqual(r['cabin']['out'], []); self.assertEqual(r['cabin']['summary'], 'within')
        self.assertFalse(any(g['type'] == 'cabin' for c in r['conditions'] for g in c['why']))
        self.assertTrue(r['cabin']['within_notes']); self.assertIn('team assumption', r['cabin']['within_notes'][0])
        self.assertNotIn('all clear', r['cabin']['summary_text'].lower())

    def test_unverified_limit_is_not_authoritative(self):
        r = self.diag(cabin={'E09': 5})                                              # dust limit: "Value not verified"
        c = r['conditions'][0]; self.assertEqual(c['id'], 'HCE-09'); self.assertFalse(c['strong']); self.assertEqual(r['severity'], 'YELLOW')
        item = [i for g in c['why'] if g['type'] == 'cabin' for i in g['items']][0]
        self.assertEqual(item['verification'], 'Value not verified'); self.assertEqual(item['vclass'], 'unverified')

    def test_cabin_mapping_matches_data_file(self):
        for lid, m in DE.CABIN_TO_KB.items():
            self.assertIn(lid, DE.ENV)
            for ids in m.values():
                for k in ids: self.assertEqual(DE.KB[k]['hazard_category'], DE.HCE_HAZARD, k)
        for ids in DE.SYMPTOM_TO_KB.values():
            for k in ids: self.assertIn(k, DE.KB)
        self.assertEqual(DE.CABIN_TO_KB['E01']['high'], ['HCE-01', 'HCE-08'])

    def test_every_limit_listed_in_the_form(self):
        html = self.c.get('/diagnose').get_data(as_text=True)
        for e in DE.DATA['environment_limits']: self.assertIn(e['verification'], html); self.assertIn(f'id="cab-{e["limit_id"]}"', html)


class UsesEverythingLogged(Base):
    def test_d_logged_vitals_and_yellow_checkin_used_without_reentry(self):
        self.c.post('/api/log', json={'readings': [{'metric_id': 'V04', 'value': 92}]})
        for _ in range(2): self.c.post('/api/wb', json={'values': {'B01': 5}})        # 2 nights under 7 h -> YELLOW check-in
        r = self.diag(symptoms=['fatigue'])
        self.assertEqual(r['vitals']['V04']['src'], 'logged'); self.assertEqual(r['vitals']['V04']['color'], 'YELLOW')
        types = {g['type'] for c in r['conditions'] for g in c['why']}
        self.assertTrue({'measurement', 'checkin', 'symptom'} <= types, types)
        iso = next(c for c in r['conditions'] if c['id'] == 'ISO-01'); self.assertIn('checkin', [g['type'] for g in iso['why']])
        self.assertIn('HCE-02', conds(r))                                              # low SpO2 -> hypoxia, from the logged value

    def test_entered_value_overrides_logged_one(self):
        self.c.post('/api/log', json={'readings': [{'metric_id': 'V04', 'value': 92}]})
        r = self.diag(symptoms=['headache'], readings={'V04': 98})
        self.assertEqual(r['vitals']['V04']['src'], 'entered'); self.assertEqual(r['vitals']['V04']['color'], 'GREEN')

    def test_pain_trend_and_combo_are_evidence(self):
        self.c.post('/api/pain', json={'region': 'chest', 'level': 6, 'note': ''})
        r = self.diag(symptoms=['breathless'])
        self.assertIn('pain', {g['type'] for c in r['conditions'] for g in c['why']})
        self.c.post('/api/seed'); r = self.diag(symptoms=['palpitations'])
        types = {g['type'] for c in r['conditions'] for g in c['why']}
        self.assertTrue({'trend', 'combo'} & types, types)

    def test_status_and_emergency_rules(self):
        r = self.diag(symptoms=['confusion', 'dizziness'], readings={'V04': 80})
        self.assertEqual(r['severity'], 'BLACK'); self.assertTrue(r['emergency']); self.assertEqual(r['legend']['response_time'], 'Immediately')
        self.assertEqual(r['actions'][0]['group'], 'Emergency / immediate')
        r = self.diag(symptoms=['nausea'])
        self.assertEqual(r['severity'], 'GREEN')

    def test_actions_are_deduplicated_data_text(self):
        r = self.diag(symptoms=self.SYM if hasattr(self, 'SYM') else ['headache', 'dizziness'], cabin={'E01': 4.5})
        texts = [a['text'].strip().lower() for a in r['actions']]; self.assertEqual(len(texts), len(set(texts)))
        allowed = {DE.KB[k][f] for k in DE.KB for f in ('recommended_action', 'contact_ground_when')}
        allowed |= {s[f] for v in DE.VS.values() for s in v['states'].values() for f in ('first_aid_action', 'contact_ground_when')}
        self.assertTrue(all(a['text'] in allowed for a in r['actions']))


class GroundReportAndScope(Base):
    def test_f_report_contains_diagnosis_and_out_of_limit_cabin(self):
        self.diag(symptoms=['headache', 'dizziness'], cabin={'E01': 4.5, 'E05': 22})
        self.assertEqual(self.c.post('/api/send', json={'delay': 5}).status_code, 200)
        with A.db() as c: body = c.execute('SELECT body FROM outbox ORDER BY id DESC').fetchone()['body']
        self.assertIn('Latest diagnosis', body); self.assertIn('closest hazard: ' + DE.HCE_HAZARD, body)
        self.assertIn('ppCO2, 1-hour average: 4.5 mmHg', body); self.assertIn('OUT OF LIMIT [Verified in source]', body)
        self.assertIn('Out of limit: ppCO2, 1-hour average', body); self.assertIn('Temperature (operating limit): 22 °C', body)

    def test_report_without_cabin_says_not_checked(self):
        self.diag(symptoms=['headache']); self.c.post('/api/send', json={'delay': 5})
        with A.db() as c: body = c.execute('SELECT body FROM outbox ORDER BY id DESC').fetchone()['body']
        self.assertIn('Cabin not checked', body)

    def test_everything_is_scoped_to_the_signed_in_astronaut(self):
        self.diag(symptoms=['headache'], cabin={'E01': 4.5})
        self.login('Alex', '2222')
        r = self.diag(symptoms=['headache'])
        self.assertEqual(r['cabin']['summary'], 'not_checked')
        self.c.post('/api/send', json={'delay': 5})
        with A.db() as c: rows = c.execute('SELECT crew,body FROM outbox').fetchall()
        self.assertTrue(rows and 'OUT OF LIMIT' not in rows[-1]['body'] and rows[-1]['crew'].startswith('Alex'))
        self.assertEqual(len(DE.load_history(A.CREW[1], 10)), 1)

    def test_history_is_saved_with_compact_summary(self):
        self.diag(symptoms=['headache'], cabin={'E01': 4.5})
        h = DE.load_history(A.CREW[0], 5)[0]; self.assertEqual(h['detail']['cabin_out'][0]['limit_id'], 'E01')
        with A.db() as c: self.assertEqual(c.execute('SELECT limit_id,value FROM cabin').fetchall()[0]['limit_id'], 'E01')
        self.assertEqual(self.c.get('/reports').status_code, 200)

    def test_ui_toggles(self):
        old = A.ui_cfg
        try:
            A.ui_cfg = lambda: {'hidden_pages': set(), 'hidden_elements': {'diagnose.cabin'}}
            r = self.diag(symptoms=['headache'], cabin={'E01': 4.5}); self.assertEqual(r['cabin']['summary'], 'not_checked')
            self.assertNotIn('Cabin readings', self.c.get('/diagnose').get_data(as_text=True))
        finally: A.ui_cfg = old


if __name__ == '__main__': unittest.main()
