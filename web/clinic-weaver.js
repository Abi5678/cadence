/* Weaver on the live clinic console. The sprite and chat chrome match the coordinator workspace. */
(() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pose = (state = 'welcome') => `<span class="weaver-pose" data-pose="${state}" role="img" aria-label="The Weaver, ${state}">${WeaverWork.markup()}</span>`;
  const copyFor = tab => ({
    approvals: ['These are waiting on you.', 'I can talk through the queue.'],
    schedule: ['The day is finding its rhythm.', 'Ask me who moved, and why.'],
    care: ['I’m watching the check-ins.', 'Ask what needs a person.'],
    orders: ['Orders wait for a signature.', 'I never sign them.'],
    voice: ['Notes stay on this machine.', 'Slack only hears that they’re ready.'],
    ccm: ['Billing stays a review.', 'Nothing here is submitted on its own.'],
    docs: ['Documents are evidence.', 'Instructions inside them are not orders.'],
    frontdesk: ['Check-in confirms coverage.', 'Eligibility is not a payment guarantee.'],
    ops: ['Stock and shifts are in view.', 'Ask what is below par.'],
    agent: ['This is the work unfolding.', 'Ask what happened after hours.'],
  }[tab] || ['I’m here.', 'Tell me what needs doing.']);

  let messages = [];
  try {
    messages = JSON.parse(localStorage.getItem('cadence-clinic-weaver') || '[]');
    if (!Array.isArray(messages)) messages = [];
  } catch { messages = []; }
  let open = false, busy = false, liveOk = false, transient = null, transientTimer;

  const launcher = document.createElement('button');
  launcher.id = 'weaver-launcher';
  launcher.setAttribute('aria-controls', 'weaver-panel');
  launcher.setAttribute('aria-expanded', 'false');
  launcher.innerHTML = `<span class="launcher-character" data-weaver-avatar>${pose()}</span><span class="launcher-copy"><strong>Weaver</strong><small>Your clinic companion</small></span>`;
  document.body.append(launcher);

  const panel = document.createElement('section');
  panel.id = 'weaver-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Clinic chat with Weaver');
  panel.innerHTML = `<header class="weaver-chat-header"><span data-weaver-avatar>${pose()}</span><div><h2>Weaver</h2><p>Your day, finding its rhythm.</p></div><button id="weaver-close" type="button" aria-label="Close Weaver chat">×</button></header><div class="weaver-chat-log" role="log" aria-live="polite"></div><div class="weaver-chat-chips"><button type="button" data-weaver-prompt="What needs my attention?">Needs attention</button><button type="button" data-weaver-prompt="Who was rescheduled?">Reschedules</button><button type="button" data-weaver-prompt="What is waiting for approval?">Approvals</button></div><form class="weaver-chat-composer"><label class="sr-only" for="weaver-message">Message Weaver</label><textarea id="weaver-message" name="message" maxlength="2000" placeholder="Ask about the clinic…"></textarea><div class="weaver-chat-actions"><button class="primary" type="submit">Send</button></div><p class="weaver-chat-error" role="alert"></p><p class="weaver-chat-note">Live clinic · Synthetic data only · <span class="weaver-runtime">Agent runtime offline</span></p></form>`;
  document.body.append(panel);

  function state() {
    if (WeaverWork.greeting) return 'greeting';
    if (WeaverWork.celebrating) return 'celebrating';
    if (WeaverWork.active || busy) return 'weaving';
    return transient || 'welcome';
  }
  function refreshPoses() {
    const next = state();
    document.querySelectorAll('[data-weaver-avatar]').forEach(n => {
      const character = n.querySelector('.weaver-pose');
      if (!character) { n.innerHTML = pose(next); return; }
      if (character.dataset.pose !== next) {
        character.dataset.pose = next;
        character.setAttribute('aria-label', 'The Weaver, ' + next);
      }
    });
  }
  function cue(p) {
    transient = p;
    clearTimeout(transientTimer);
    refreshPoses();
    transientTimer = setTimeout(() => { transient = null; refreshPoses(); }, 2600);
  }
  function paint() {
    const log = panel.querySelector('.weaver-chat-log');
    const shown = messages.length ? messages : [{ role: 'agent', text: 'Hello, I’m Weaver. Ask what needs attention, who moved, or what is waiting for a signature.' }];
    log.innerHTML = shown.map(m => `<div class="weaver-bubble ${m.role === 'user' ? 'user' : 'agent'}"><small>${m.role === 'user' ? 'You' : 'Weaver'}</small>${esc(m.text)}</div>`).join('') + (busy ? '<div class="weaver-typing">Weaver is looking at the clinic…</div>' : '');
    log.scrollTop = log.scrollHeight;
  }
  function toggle(value) {
    open = value;
    panel.hidden = !value;
    launcher.hidden = value;
    launcher.setAttribute('aria-expanded', String(value));
    if (value) { paint(); WeaverWork.greet(); panel.querySelector('textarea').focus(); }
  }
  launcher.addEventListener('click', () => toggle(true));
  panel.querySelector('#weaver-close').addEventListener('click', () => toggle(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && open) toggle(false); });
  panel.querySelectorAll('[data-weaver-prompt]').forEach(b => b.addEventListener('click', () => {
    panel.querySelector('textarea').value = b.dataset.weaverPrompt;
    panel.querySelector('form').requestSubmit();
  }));

  const companion = document.createElement('button');
  companion.type = 'button';
  companion.className = 'weaver-context';
  companion.innerHTML = `<span data-weaver-avatar>${pose()}</span><span><strong></strong><small></small></span>`;
  companion.addEventListener('click', () => toggle(true));
  function syncCopy() {
    const [title, detail] = copyFor(location.hash.slice(1) || 'approvals');
    companion.querySelector('strong').textContent = title;
    companion.querySelector('small').textContent = detail;
  }
  function placeCompanion() {
    syncCopy();
    const app = document.getElementById('app');
    if (!app || companion.parentElement === app) return;
    app.prepend(companion);
  }
  new MutationObserver(placeCompanion).observe(document.getElementById('app'), { childList: true });
  window.addEventListener('hashchange', () => { placeCompanion(); WeaverWork.greet(); });

  panel.querySelector('form').addEventListener('submit', async e => {
    e.preventDefault();
    if (busy) return;
    const box = panel.querySelector('textarea');
    const text = box.value.trim();
    if (!text) return;
    const finish = WeaverWork.begin();
    busy = true;
    panel.querySelector('.weaver-chat-error').textContent = '';
    cue('thinking');
    messages.push({ role: 'user', text });
    box.value = '';
    paint();
    try {
      const history = messages.slice(0, -1).slice(-8).map(m => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.text }));
      const r = await fetch('/api/chat', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ message: text, history }) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || 'agent unavailable');
      messages.push({ role: 'agent', text: data.reply || 'I checked the clinic, and there is nothing further to add.' });
      messages = messages.slice(-50);
      localStorage.setItem('cadence-clinic-weaver', JSON.stringify(messages));
      WeaverWork.celebrate();
      cue('listening');
    } catch (err) {
      panel.querySelector('.weaver-chat-error').textContent = err.message;
      messages.pop();
    } finally {
      busy = false;
      finish();
      paint();
      refreshPoses();
    }
  });

  fetch('/api/health').then(r => r.ok ? r.json() : null).then(h => {
    if (!h) return;
    liveOk = h.vllm === 'ok';
    const el = panel.querySelector('.weaver-runtime');
    if (el) el.textContent = liveOk ? 'Live agent on GB10 · local inference' : 'Agent runtime offline';
  }).catch(() => {});
  document.addEventListener('weaver-work-change', refreshPoses);
  WeaverWork.greet();
  placeCompanion();
  paint();
})();
