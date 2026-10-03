/* Live clinic pages inside the coordinator workspace (the Weaver UI): same sidebar, layout and mascot.
   Views are shared with the receptionist console (clinic.js) and read the Cadence service on the GB10. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tm = ts => ts ? new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  const dt = ts => ts ? new Date(ts).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const money = n => n == null ? '—' : `$${Number(n).toFixed(2)}`;
  const tone = s => ({ confirmed: 'good', completed: 'good', closed: 'good', transmitted: 'good', sent_to_lab: 'good', submitted: 'good', active: 'good', checked_in: 'iris',
    prepared: 'warn', requested: 'warn', offered: 'warn', pending_approval: 'warn', booked: 'warn', sent: 'warn', review: 'warn', running: 'iris', waiting: 'warn', open: 'iris',
    awaiting_signature: 'warn', needs_release: 'warn', call_needed: 'bad', resulted: 'iris', released: 'good',
    rejected: 'bad', failed: 'bad', cancelled: 'bad', escalated: 'bad', inactive: 'bad', declined: 'bad' }[s] || '');
  const pill = s => `<span class="pill ${tone(s)}">${esc(String(s).replace(/_/g, ' '))}</span>`;

  let S = null, phonePatient = 'P-104', pending = null;
  async function call(path, body) {
    const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  }
  function toast(t) { const el = $('#toast'); el.textContent = t; el.classList.add('show'); clearTimeout(toast.h); toast.h = setTimeout(() => el.classList.remove('show'), 3200); }
  async function act(fn, ok) { try { await fn(); if (ok) toast(ok); await refresh(); } catch (e) { toast(e.message); } }
  const name = id => (S.patients.find(p => p.id === id) || {}).name || id || '—';

  const views = {
    approvals() {
      const pending = S.approvals.filter(a => a.state === 'prepared');
      const done = S.approvals.filter(a => a.state !== 'prepared').slice(0, 25);
      return `<h1>Approvals</h1><p class="lede">You are the check before anything leaves the clinic: pharmacy, lab, claims, vendor orders, free-text patient messages and shift changes.</p>
      <div class="grid"><section class="card"><h2>Waiting for you <small>${pending.length}</small></h2>
      ${pending.map(a => `<div class="approval"><div><div class="what">${esc(a.summary)}</div><div class="meta">${esc(a.action.replace(/_/g, ' '))} · ${tm(a.created_at)}${a.task_id ? ` · task ${esc(a.task_id)}` : ''}</div>
        ${a.payload.body ? `<pre>${esc(a.payload.body)}</pre>` : ''}</div>
        <div class="row-actions"><button class="approve" data-approve="${esc(a.id)}">Approve</button><button data-reject="${esc(a.id)}">Reject</button></div></div>`).join('') || '<p class="empty">Nothing waiting. The agent will queue items here.</p>'}
      </section><section class="card"><h2>Recent decisions</h2><table><tr><th>Action</th><th>State</th><th>Receipt</th></tr>
      ${done.map(a => `<tr><td>${esc(a.summary)}<br><small class="pill">${esc(a.decided_by || '')}</small></td><td>${pill(a.state)}</td><td><small>${esc(a.receipt || '')}</small></td></tr>`).join('')}</table></section></div>`;
    },
    schedule() {
      const appts = S.appointments.filter(a => a.status !== 'cancelled' || Date.now() - new Date(a.starts_at) < 864e5);
      return `<h1>Schedule &amp; waitlist</h1><p class="lede">Cadence asks patients to confirm visits in the next 48 hours, and offers any freed slot to the waitlist (30-minute hold, then the next person).</p>
      <div class="grid"><section class="card" style="grid-column:1/-1"><h2>Appointments</h2><table><tr><th>When</th><th>Patient</th><th>Provider</th><th>Status</th><th>Confirmation / offer</th><th></th></tr>
      ${appts.map(a => `<tr><td>${dt(a.starts_at)}</td><td>${a.patient_id ? `${esc(a.patient_name)}<br><small>${esc(a.reason || '')}</small>` : '<em>open slot</em>'}</td><td>${esc(a.provider_name)}</td><td>${pill(a.status)}</td>
        <td>${a.offered_to ? `offered to ${esc(name(a.offered_to))} until ${tm(a.offer_expires_at)}` : a.patient_id ? pill(a.confirmation) : ''}</td>
        <td class="row-actions">${['booked', 'confirmed'].includes(a.status) ? `<button data-checkin="${esc(a.id)}">Check in</button>` : ''}${a.status === 'checked_in' ? `<button data-complete="${esc(a.id)}">Complete visit</button>` : ''}${a.patient_id ? `<button data-phone="${esc(a.patient_id)}">Phone</button>` : ''}</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Waitlist</h2><table><tr><th>Patient</th><th>Reason</th><th>Priority</th><th>Status</th></tr>
      ${S.waitlist.map(w => `<tr><td>${esc(name(w.patient_id))}</td><td>${esc(w.reason)}</td><td>${esc(w.priority)}</td><td>${pill(w.status)}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">Empty</td></tr>'}</table></section></div>`;
    },
    care() {
      return `<h1>Aftercare &amp; monitoring</h1><p class="lede">Post-visit check-ins go out automatically. Replies with warning signs, and out-of-range vitals, go straight to the doctor. Cadence routes; it does not diagnose.</p>
      <div class="grid"><section class="card"><h2>Aftercare check-ins</h2><table><tr><th>Patient</th><th>Due</th><th>Status</th><th>Answer</th></tr>
      ${S.aftercare.map(c => `<tr><td>${esc(name(c.patient_id))}</td><td>${dt(c.due_at)}</td><td>${pill(c.status)}</td><td>${esc(c.answer || '')}</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Simulate a monitoring reading</h2><form class="inline" id="lv-vitals">
        <select name="patient_id">${S.patients.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select>
        <select name="kind"><option value="spo2">SpO₂ (%)</option><option value="heart_rate">Heart rate</option><option value="systolic_bp">Systolic BP</option><option value="temp_c">Temp °C</option><option value="glucose">Glucose</option></select>
        <input name="value" type="number" step="0.1" required placeholder="Value"><button class="primary">Record</button></form>
        <p class="footnote">Alert ranges: SpO₂ 92+, HR 50–110, SBP 90–160, temp 35.5–38.0, glucose 70–250 (demo values).</p></section>
      <section class="card"><h2>Doctor alerts</h2><table><tr><th>Sent</th><th>Alert</th></tr>
      ${S.messages.filter(m => m.channel.startsWith('slack')).map(m => `<tr><td>${tm(m.created_at)}<br>${pill(m.channel === 'slack' ? 'slack' : 'preview')}</td><td>${esc(m.body)}</td></tr>`).join('') || '<tr><td colspan="2" class="empty">None yet</td></tr>'}</table></section></div>`;
    },
    orders() {
      return `<h1>Orders, labs &amp; prescriptions</h1><p class="lede">Doctors prescribe and order tests in Slack by patient ID. Cadence drafts the order; it is signed only when the doctor replies <code>CONFIRM O-…</code>, which the service verifies against Slack. Signed orders wait for your approval, then go to the (mock) lab or pharmacy.</p>
      <div class="grid"><section class="card" style="grid-column:1/-1"><h2>Doctor orders</h2><table><tr><th>Order</th><th>Patient</th><th>Detail</th><th>Signed</th><th>Status</th></tr>
      ${S.orders.map(o => `<tr><td>${esc(o.id)}<br>${pill(o.kind)}</td><td>${esc(name(o.patient_id))}</td><td>${esc(o.detail)}</td><td>${o.signed_by ? esc(o.signed_by) : pill('unsigned')}</td><td>${pill(o.status)}</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Simulate a signed doctor order</h2><form class="inline" id="lv-order">
        <select name="kind"><option value="lab">Lab order</option><option value="rx">Prescription</option></select>
        <select name="patient_id">${S.patients.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select>
        <select name="provider_id"><option value="DR-CHEN">Dr. Chen</option><option value="DR-PATEL">Dr. Patel</option></select>
        <input name="detail" required placeholder="e.g. Lipid panel, fasting" class="full"><button class="primary">Sign &amp; submit</button></form></section>
      <section class="card"><h2>Doctor ⇄ Cadence in Slack</h2><ul class="feed">${S.messages.filter(m => m.channel.startsWith('slack')).map(m => `<li><time>${tm(m.created_at)}</time><div>${esc(m.body)}${m.channel === 'slack-preview' ? ' ' + pill('preview') : ''}</div></li>`).join('') || '<li class="empty">No Slack messages yet. DM the Cadence bot as the doctor.</li>'}</ul></section></div>`;
    },
    voice() {
      const recs = S.recordings || [];
      return `<h1>Voice visits</h1><p class="lede">Doctors send a Slack voice clip after a visit. On this GB10, NVIDIA Parakeet transcribes it and Sortformer separates the speakers; the local model drafts the note, orders and follow-up for the doctor to sign. Nothing is signed or sent automatically.</p>
      <form class="composer" id="lv-voice"><input type="file" name="file" accept="audio/*,video/*" required><input name="message" placeholder="Patient ID, e.g. P-104" style="max-width:220px"><button class="primary">Transcribe on GB10</button></form>
      ${recs.map(r => { const roles = (r.extraction || {}).roles || {}; return `<section class="card" style="margin-bottom:16px"><h2>${esc(r.patient_id || 'Unknown patient')} <small>${esc(r.id)} · ${tm(r.created_at)} · ${pill(r.status)} · ${r.duration_s ? esc(r.duration_s) + 's audio' : ''} ${r.timings && r.timings.asr_s ? `· ASR ${esc(r.timings.asr_s)}s · diarization ${esc(r.timings.diarization_s)}s · note ${esc(r.timings.llm_s)}s` : ''}</small></h2>
        ${(r.extraction || {}).summary ? `<p>${esc(r.extraction.summary)}</p>` : ''}
        <ul class="feed">${(r.segments || []).map(g => `<li><time>${Number(g.start).toFixed(1)}s</time><div><span class="k">${esc((roles[g.speaker] || g.speaker).toUpperCase())}</span><br>${esc(g.text)}</div></li>`).join('')}</ul></section>`; }).join('') || '<p class="empty">No recordings yet. Send a voice clip to the Cadence bot in Slack, or upload one above.</p>'}`;
    },
    ccm() {
      const c = S.ccm; if (!c) return '<p class="empty">Loading…</p>';
      const usd = n => '$' + Number(n || 0).toFixed(2);
      const ready = c.packets.filter(k => k.status === 'needs_review');
      return `<h1>Chronic care &amp; billing</h1><p class="lede">Cadence watches ${c.monitored.length} chronic-care patients around the clock and audits each month for CCM (99490/99439) and remote monitoring (99454/99457/99458). Only human staff time is billable; the agent's own work is logged and excluded. You review, the doctor attests in Slack, then the claim goes to Medicare (mock).</p>
      <div class="composer"><button class="primary" id="ccm-close">Run month-end close for ${esc(c.month)}</button><span class="pill iris">${ready.length} packets ready · ${usd(ready.reduce((a, k) => a + k.result.billed, 0))}</span></div>
      <div class="grid"><section class="card" style="grid-column:1/-1"><h2>Audit packets <small>${esc(c.month)}</small></h2><table><tr><th>Patient</th><th>Codes</th><th>Checks</th><th>Agent work (not billed)</th><th>Status</th><th></th></tr>
      ${c.packets.map(k => { const r = k.result; return `<tr><td>${esc(r.patient)}<br><small>${esc(k.patient_id)}</small></td>
        <td class="nowrap">${r.codes.map(x => `${esc(x.code)}${x.units > 1 ? '×' + x.units : ''}`).join(', ') || '—'}<br><strong>${usd(r.billed)}</strong></td>
        <td>${r.checks.map(x => `<span class="pill ${x.ok ? 'good' : 'bad'}" title="${esc(x.detail)}">${x.ok ? '✓' : '✗'} ${esc(x.check)}</span>`).join(' ')}</td>
        <td><small>${esc(r.agent.actions)} actions / ${esc(r.agent.minutes)} min</small></td><td>${pill(k.status)}</td>
        <td class="row-actions">${k.status === 'needs_review' ? `<button class="approve" data-kreview="${esc(k.id)}">Approve</button><button data-kreturn="${esc(k.id)}">Return</button>` : ''}${k.status === 'awaiting_attestation' ? `<small>waiting for doctor CONFIRM ${esc(k.id)}</small><button class="mini" data-kattest="${esc(k.id)}">Attest (demo)</button>` : ''}</td></tr>`; }).join('') || '<tr><td colspan="6" class="empty">No packets yet. Run month-end close.</td></tr>'}</table></section>
      <section class="card"><h2>Medicare claims <small>mock MAC</small></h2><table><tr><th>Claim</th><th>Billed</th><th>Status</th><th>Paid</th></tr>
      ${c.claims.map(x => `<tr><td>${esc(x.id)}<br><small>${esc(x.patient_id)} · ${esc(x.receipt)}</small></td><td>${usd(x.billed)}</td><td>${pill(x.status)}</td><td>${x.remit ? usd(JSON.parse(x.remit).paid) : ''}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">None yet</td></tr>'}</table></section>
      <section class="card"><h2>At risk this month</h2><table><tr><th>Patient</th><th>Needs</th></tr>
      ${c.gaps.slice(0, 12).map(g => `<tr><td>${esc(g.name)}<br><small>${esc(g.patient_id)}</small></td><td>${g.needs.map(esc).join('; ')}</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Log care time <small>human staff only</small></h2><form class="inline" id="lv-time">
        <select name="patient_id">${c.monitored.map(m => `<option value="${esc(m.patient_id)}">${esc(m.name)}</option>`).join('')}</select>
        <select name="staff_id"><option value="S-6">Jamie (CCM nurse)</option><option value="S-1">Sam (RN)</option><option value="S-2">Lee (RN)</option></select>
        <select name="program"><option value="ccm">CCM care management</option><option value="rpm">RPM interactive</option></select>
        <input name="minutes" type="number" min="1" max="120" value="10" required><input name="activity" class="full" required placeholder="e.g. Phone call: reviewed glucose log and meds"><button class="primary">Log time</button></form></section></div>`;
    },
    docs() {
      const docs = S.documents, cons = S.consents;
      const c = (pid, k) => (cons.find(x => x.patient_id === pid && x.kind === k) || {}).granted;
      return `<h1>Documents &amp; consent</h1><p class="lede">Visit summaries, prescription copies, statements and doctor-released lab results go to the patient portal after you approve. Nothing is sent without the patient's consent.</p>
      <div class="grid"><section class="card" style="grid-column:1/-1"><h2>Documents</h2><table><tr><th>Document</th><th>Patient</th><th>Created</th><th>Status</th><th></th></tr>
      ${docs.map(d => `<tr><td>${esc(d.title)}<br><small>${esc(d.body.slice(0, 120))}</small></td><td>${esc(name(d.patient_id))}</td><td>${tm(d.created_at)}</td><td>${pill(d.status)}</td>
        <td>${d.status === 'released' ? `<button class="mini" data-senddoc="${esc(d.id)}">Send to patient</button>` : d.status === 'needs_release' ? '<small>waiting for doctor RELEASE</small>' : ''}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">No documents yet</td></tr>'}</table></section>
      <section class="card"><h2>Patient consent</h2><table><tr><th>Patient</th><th>SMS reminders</th><th>Electronic documents</th></tr>
      ${S.patients.map(p => `<tr><td>${esc(p.name)}<br><small>${esc(p.id)}</small></td>${['sms', 'documents'].map(k => `<td><label class="check-label"><input type="checkbox" data-consent="${esc(p.id)}" data-kind="${k}" ${c(p.id, k) ? 'checked' : ''}> ${c(p.id, k) ? 'yes' : 'no'}</label></td>`).join('')}</tr>`).join('')}</table>
      <p class="footnote">Patients can text STOP to withdraw SMS consent at any time.</p></section></div>`;
    },
    frontdesk() {
      return `<h1>Front desk &amp; billing</h1><p class="lede">Check-in runs a payer eligibility check and posts the copay. Completing a visit drafts the claim for approval and schedules aftercare.</p>
      <div class="grid"><section class="card"><h2>Today and tomorrow</h2><table><tr><th>When</th><th>Patient</th><th>Status</th><th></th></tr>
      ${S.appointments.filter(a => a.patient_id && !['cancelled'].includes(a.status)).map(a => `<tr><td>${dt(a.starts_at)}</td><td>${esc(a.patient_name)}</td><td>${pill(a.status)}</td>
        <td class="row-actions">${['booked', 'confirmed'].includes(a.status) ? `<button data-checkin="${esc(a.id)}">Check in</button>` : ''}${a.status === 'checked_in' ? `<button data-complete="${esc(a.id)}">Complete</button>` : ''}</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Charges &amp; claims</h2><table><tr><th>Charge</th><th>Patient</th><th>Amount</th><th>Patient owes</th><th>Status</th></tr>
      ${S.charges.map(c => `<tr><td>${esc(c.description)}</td><td>${esc(name(c.patient_id))}</td><td>${money(c.amount)}</td><td>${money(c.patient_responsibility)}</td><td>${pill(c.status)}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">No charges yet</td></tr>'}</table></section></div>`;
    },
    ops() {
      const st = S.staffing;
      return `<h1>Inventory &amp; staff</h1><p class="lede">The always-on sweep drafts reorders for anything below par and proposes fills for open shifts within each person's weekly hour limit.</p>
      <div class="grid"><section class="card"><h2>Inventory</h2><table><tr><th>Item</th><th>On hand</th><th>Par</th><th></th></tr>
      ${S.inventory.map(i => `<tr><td>${esc(i.name)}<br><small>${esc(i.vendor)}</small></td><td>${esc(i.on_hand)}</td><td>${esc(i.par)}</td><td>${i.below_par ? pill('low') : pill('ok')}</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Staff hours</h2><table><tr><th>Staff</th><th>Role</th><th>Scheduled</th><th>Max</th></tr>
      ${st.staff.map(s => `<tr><td>${esc(s.name)}</td><td>${esc(s.role.replace(/_/g, ' '))}</td><td>${esc(s.scheduled_hours)} h</td><td>${esc(s.max_weekly_hours)} h</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Shifts</h2><table><tr><th>Shift</th><th>When</th><th>Assigned</th></tr>
      ${st.shifts.map(s => `<tr><td>${esc(s.role.replace(/_/g, ' '))}</td><td>${dt(s.starts_at)}–${tm(s.ends_at)}</td><td>${s.staff_name ? esc(s.staff_name) : pill('open')}</td></tr>`).join('')}</table></section></div>`;
    },
    agent() {
      return `<h1>Agent activity</h1><p class="lede">Every task, tool call and model call from the GB10 agent (backend: <strong>${esc(S.agent_backend)}</strong>). The scheduler sweeps every minute.</p>
      <form class="composer" id="lv-task"><input name="title" required maxlength="200" placeholder="Ask Cadence to do something, e.g. “Find who on the waitlist could take Dr. Chen’s open slot tomorrow”"><button class="primary">Assign</button><button type="button" id="sweep">Run sweep now</button></form>
      <div class="grid"><section class="card"><h2>Tasks</h2><table><tr><th>Task</th><th>Status</th></tr>
      ${S.tasks.map(t => `<tr><td>${esc(t.title)}<br><small>${esc(t.id)} · ${tm(t.created_at)}</small></td><td>${pill(t.status)}</td></tr>`).join('')}</table></section>
      <section class="card"><h2>Live events</h2><ul class="feed">${S.events.map(e => `<li><time>${tm(e.created_at)}</time><div><span class="k">${esc(e.kind)}</span><br>${esc(e.summary)}</div></li>`).join('')}</ul></section></div>`;
    },
  };

  function phoneCard() {
    const thread = S.messages.filter(m => m.party === phonePatient && m.channel === 'sms').slice().reverse();
    return `<section class="card phone-card" aria-label="Patient phone simulator"><h2>Patient phone <small>simulated SMS</small></h2>
      <select id="lv-phone-patient" aria-label="Patient">${S.patients.map(p => `<option value="${esc(p.id)}" ${p.id === phonePatient ? 'selected' : ''}>${esc(p.name)} (${esc(p.id)})</option>`).join('')}</select>
      <div class="phone-thread">${thread.map(m => `<div class="bubble ${m.direction === 'in' ? 'in' : 'out'}">${esc(m.body)}<time>${tm(m.created_at)}</time></div>`).join('') || '<p class="empty">No messages yet.</p>'}</div>
      <div class="phone-quick"><button data-reply="YES">YES</button><button data-reply="NO">NO</button><button data-reply="RESCHEDULE">RESCHEDULE</button></div>
      <form id="lv-phone" class="phone-form"><input name="body" placeholder="Reply as the patient…" autocomplete="off" maxlength="500"><button class="primary">Send</button></form></section>`;
  }

  // route -> [sidebar title, symbol, view]
  const PAGES = {
    'live-approvals': ['Approvals', '✓', () => views.approvals()],
    'live-schedule': ['Patients & schedule', '◴', () => `<div class="live-split"><div>${views.schedule()}</div>${phoneCard()}</div>`],
    'live-voice': ['Voice visits', '◉', () => views.voice()],
    'live-orders': ['Orders, labs & Rx', '℞', () => views.orders()],
    'live-care': ['Monitoring & aftercare', '♡', () => views.care()],
    'live-ccm': ['Chronic care & billing', '$', () => views.ccm()],
    'live-docs': ['Documents & consent', '✉', () => views.docs()],
    'live-frontdesk': ['Front desk & billing', '⌂', () => views.frontdesk()],
    'live-ops': ['Inventory & staff', '▤', () => views.ops()],
    'mission': ['Mission control', '◎', null],
  };
  for (const [r, [title, sym]] of Object.entries(PAGES)) { titles[r] = title; symbols[r] = sym; }  // coordinator.js globals

  function paint() {
    const route = location.hash.slice(1);
    const page = PAGES[route];
    if (!page) return;
    $('#context').textContent = 'Live clinic / ' + page[0];
    $('#app').className = 'ops-view live-page view-' + route;
    if (!S) { $('#app').innerHTML = '<p class="empty">Connecting to the GB10…</p>'; return; }
    const busy = document.activeElement && $('#app').contains(document.activeElement) && document.activeElement.closest('form');
    if (!busy) $('#app').innerHTML = page[2]();
    const nav = $('#navigation'), first = nav.querySelector('a[href="#live-approvals"]');
    if (first && !nav.querySelector('.nav-divider')) first.insertAdjacentHTML('beforebegin', '<span class="nav-divider">LIVE CLINIC · GB10</span>');
    const n = S.approvals.filter(a => a.state === 'prepared').length, a = nav.querySelector('a[href="#live-approvals"]');
    if (a && n) a.insertAdjacentHTML('beforeend', `<span class="nav-count">${n}</span>`);
  }

  const previous = render;  // coordinator's render (already wrapped by the Weaver)
  render = function (...args) {
    const route = location.hash.slice(1);
    if (route === 'mission') { location.href = 'mission.html'; return; }
    previous.apply(this, args);  // builds the sidebar (with our pages) and the coordinator's own views
    const nav = $('#navigation');
    if (nav && !nav.querySelector('.nav-divider')) {
      const first = nav.querySelector('a[href="#live-approvals"]');
      if (first) first.insertAdjacentHTML('beforebegin', '<span class="nav-divider">LIVE CLINIC · GB10</span>');
    }
    if (PAGES[route]) { paint(); refresh(); }
    if (route === 'connections') liveConnections();
  };

  async function refresh() {
    if (!PAGES[location.hash.slice(1)]) return;
    try {
      const [st, cc, rec] = await Promise.all([call('/api/state'), call('/api/ccm').catch(() => null), call('/api/recordings').catch(() => [])]);
      S = { ...st, ccm: cc, recordings: rec };
    } catch { S = null; }
    paint();
  }
  async function liveConnections() {
    try {
      const h = await call('/api/health');
      const state = { gb10: h.vllm === 'ok', slack: h.hermes === 'ok', local: true };
      document.querySelectorAll('.connection-card').forEach(c => {
        const kind = c.querySelector('[data-kind]')?.dataset.kind, b = c.querySelector('.badge');
        if (b && kind in state) b.textContent = state[kind] ? 'Connected' : 'Offline';
      });
    } catch {}
  }

  // Events: capture phase so these run before coordinator.js handlers, and only for our own controls.
  document.addEventListener('click', e => {
    if (!e.target.closest('.live-page')) return;
    const b = e.target.closest('button'); if (!b) return;
    const d = b.dataset;
    if (d.approve) act(() => call(`/api/approvals/${d.approve}/decide`, { approve: true }), 'Approved');
    else if (d.reject) act(() => call(`/api/approvals/${d.reject}/decide`, { approve: false }), 'Rejected');
    else if (d.checkin) act(() => call(`/api/visits/${d.checkin}/checkin`, {}), 'Checked in, eligibility verified');
    else if (d.complete) act(() => call(`/api/visits/${d.complete}/complete`, {}), 'Visit completed, claim drafted');
    else if (d.kreview) act(() => call(`/api/ccm/packets/${d.kreview}/review`, { approve: true }), 'Approved; doctor asked to attest in Slack');
    else if (d.kreturn) act(() => call(`/api/ccm/packets/${d.kreturn}/review`, { approve: false }), 'Returned for correction');
    else if (d.kattest) act(() => call(`/api/ccm/packets/${d.kattest}/attest`, {}), 'Attested; claim submitted');
    else if (b.id === 'ccm-close') act(() => call('/api/ccm/close', {}), 'Month-end close assigned to Cadence');
    else if (d.senddoc) act(() => call(`/api/documents/${d.senddoc}/send`, {}), 'Queued for approval');
    else if (d.phone) { phonePatient = d.phone; if (location.hash !== '#live-schedule') location.hash = 'live-schedule'; else paint(); }
    else if (d.reply) sendReply(d.reply);
    else return;
    e.stopPropagation();
  }, true);
  document.addEventListener('submit', e => {
    const f = e.target; if (!f.id || !f.id.startsWith('lv-')) return;
    e.preventDefault(); e.stopPropagation();
    const v = Object.fromEntries(new FormData(f));
    if (f.id === 'lv-vitals') act(() => call('/api/sim/vitals', { ...v, value: Number(v.value) }), 'Reading recorded');
    else if (f.id === 'lv-order') act(() => call('/api/sim/doctor-order', v), 'Signed order received');
    else if (f.id === 'lv-time') act(() => call('/api/ccm/time', { ...v, minutes: Number(v.minutes), interactive: v.program === 'rpm' }), 'Time logged');
    else if (f.id === 'lv-voice') {
      const fd = new FormData(f); toast('Transcribing on the GB10…');
      fetch('/api/sim/voice', { method: 'POST', body: fd }).then(r => r.json()).then(() => { toast('Visit draft ready'); refresh(); }).catch(err => toast(err.message));
      return;
    }
    else if (f.id === 'lv-phone') { if (v.body?.trim()) sendReply(v.body.trim()); }
    f.reset();
  }, true);
  document.addEventListener('change', e => {
    if (e.target.id === 'lv-phone-patient') { phonePatient = e.target.value; paint(); return; }
    const c = e.target.closest('.live-page input[data-consent]'); if (!c) return;
    act(() => call('/api/consents', { patient_id: c.dataset.consent, kind: c.dataset.kind, granted: c.checked }), 'Consent updated');
  }, true);
  function sendReply(body) { act(() => call('/api/sim/patient-reply', { patient_id: phonePatient, body }), 'Reply sent, Cadence is on it'); }

  try {
    const es = new EventSource('/api/events');
    es.onmessage = m => { try { if (JSON.parse(m.data).kind === 'device.reading') return; } catch {} clearTimeout(pending); pending = setTimeout(refresh, 300); };
  } catch {}
  render(true);
})();
