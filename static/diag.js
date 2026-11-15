/* AstroVive – shared helpers for the Diagnose page and the Ground page. */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(msg) {
  const t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'alert'); t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 5000);
}

/* POST/GET JSON with real error handling: expired session, server errors and network failures are all reported. */
async function api(url, body) {
  let r;
  try {
    r = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) { toast('Cannot reach the AstroVive server. Check that app.py is running.'); throw e; }
  if (r.status === 401) { location = '/'; throw new Error('session'); }
  let j = {}; try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) { toast(j.error || 'Request failed (' + r.status + ')'); throw new Error(j.error || r.status); }
  return j;
}

/* Smart Card: Identified Hazard / Body Impact / Severity / Action Plan (+ why, + vitals used). */
function renderCard(res) {
  const sev = res.severity, vit = res.vitals || {};
  const rows = Object.entries(vit).map(([m, a]) =>
    '<tr><td>' + esc(NAMES[m] || m) + '</td><td><b>' + (a.value === null ? 'felt ' + esc(a.side.toLowerCase()) : esc(a.value) + ' ' + esc(a.unit)) + '</b></td><td>' + esc(a.range) + '</td><td>'
    + (a.color === 'GREEN' ? '🟢' : a.color === 'YELLOW' ? '🟡' : '🔴') + '</td><td>' + (a.src === 'dashboard' ? 'Dashboard' : 'Reported') + '</td></tr>').join('');
  const why = (res.matched || []).map(x => '<div class="why">🔗 ' + esc(x) + '</div>').join('');
  return '<div class="dcard ' + esc(sev) + '" role="region" aria-label="Diagnostic report">'
    + '<div class="hd"><span class="ic">' + esc(res.icon) + '</span><div><small style="color:var(--mut)">' + (res.state === 1 ? 'State 1 – ' : 'State 2 – ') + esc(res.state_label) + '</small><h3>' + esc(res.hazard) + '</h3></div>'
    + '<span class="sev">' + esc(res.icon) + ' ' + esc(res.severity_label) + '</span></div>'
    + '<h5>Body impact</h5><p>' + esc(res.impact) + '</p>'
    + (why ? '<h5>Why this was flagged</h5>' + why : '')
    + '<h5>Action plan</h5><ol>' + (res.plan || []).map(x => '<li>' + esc(x) + '</li>').join('') + '</ol>'
    + (rows ? '<h5>Vitals used</h5><div style="overflow-x:auto"><table><tr><th>Vital</th><th>Value</th><th>Your range</th><th></th><th>Source</th></tr>' + rows + '</table></div>'
      : '<h5>Vitals used</h5><p style="color:var(--mut)">No vital signs available. Enter them for a fuller check.</p>')
    + '<div class="disc">Saved to your Reports history. Educational prototype – Earth-based reference values, not NASA flight limits. The flight surgeon makes the final decision.</div></div>';
}
