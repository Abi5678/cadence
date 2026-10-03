/* Assign one task and play the agent's actions through Weaver. */
(() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tm = ts => ts ? new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
  const $ = s => document.querySelector(s);
  const avatar = $('[data-weaver-avatar]');
  let tasks = [], active = null, pose = 'welcome', seen = new Set();

  function poseFor(kind, status) {
    if (status === 'completed') return 'celebrating';
    if (status === 'failed' || status === 'cancelled') return 'resting';
    if (status === 'review' || status === 'waiting') return 'waiting';
    if (/approval\.confirmed|verified|task\.completed/.test(kind)) return 'complete';
    if (/approval\.prepared|missing_info/.test(kind)) return 'waiting';
    if (/voice|document/.test(kind)) return 'document';
    if (/tool_call|progress|planning/.test(kind)) return 'weaving';
    if (/created|model/.test(kind)) return 'thinking';
    return status === 'running' ? 'weaving' : 'welcome';
  }
  function showPose(next) {
    if (WeaverWork.greeting) next = 'greeting';
    else if (WeaverWork.celebrating) next = 'celebrating';
    pose = next;
    avatar.innerHTML = `<span class="weaver-pose" data-pose="${pose}" role="img" aria-label="The Weaver, ${pose}">${WeaverWork.markup()}</span>`;
  }
  function caption(ev, task) {
    if (!task) return ['Weaver is ready.', 'Give Cadence a task. The mascot follows each step the agent takes.'];
    if (task.status === 'completed') return ['Done.', ev?.summary || task.title];
    if (task.status === 'review') return ['Waiting for you.', ev?.summary || 'Something needs approval before it leaves the clinic.'];
    if (task.status === 'waiting') return ['Paused.', ev?.summary || 'Weaver is waiting on a person.'];
    if (ev) return [ev.summary, ev.kind.replaceAll('.', ' · ')];
    return ['Starting.', task.title];
  }
  function paint() {
    const task = tasks.find(t => t.id === active);
    const events = (task?.events || []).slice().reverse();
    const latest = events[0];
    const [title, detail] = caption(latest, task);
    $('#action').textContent = title;
    $('#detail').textContent = detail;
    showPose(task ? poseFor(latest?.kind || '', task.status) : 'welcome');
    $('#tasks').innerHTML = tasks.slice(0, 6).map(t => `<button type="button" data-id="${esc(t.id)}" class="${t.id === active ? 'on' : ''}">${esc(t.title)}<small>${esc(t.status)} · ${esc(t.id)}</small></button>`).join('');
    $('#feed').innerHTML = events.map(e => `<li><time>${tm(e.created_at)}</time><div><span class="k">${esc(e.kind)}</span><br>${esc(e.summary)}</div></li>`).join('')
      || '<li><time></time><div>No actions yet.</div></li>';
  }
  async function load() {
    const r = await fetch('/api/live/tasks');
    if (!r.ok) return;
    const data = await r.json();
    tasks = data.tasks || [];
    if (!active) active = tasks.find(t => t.status === 'running')?.id || tasks[0]?.id || null;
    paint();
  }
  function follow(ev) {
    if (!ev || ev.kind === 'device.reading') return;
    if (ev.task_id && (ev.task_id === active || !active)) {
      active = ev.task_id;
      const task = tasks.find(t => t.id === active) || { id: active, title: 'New task', status: 'running', events: [] };
      if (!tasks.includes(task)) tasks.unshift(task);
      task.events = task.events || [];
      if (!seen.has(ev.seq)) {
        seen.add(ev.seq);
        task.events.push(ev);
        if (/task\.completed|approval\.confirmed/.test(ev.kind)) WeaverWork.celebrate();
        else if (/tool_call|planning/.test(ev.kind)) WeaverWork.begin()();
      }
    }
    load();
  }
  $('#assign').addEventListener('submit', async e => {
    e.preventDefault();
    const brief = $('#brief').value.trim();
    if (!brief) return;
    $('#error').textContent = '';
    const button = e.target.querySelector('button');
    button.disabled = true;
    WeaverWork.greet();
    showPose('thinking');
    $('#action').textContent = 'Taking that on.';
    $('#detail').textContent = brief;
    try {
      const r = await fetch('/api/tasks', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: brief.slice(0, 90), brief, kind: 'coordinator' }) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || 'Could not assign the task');
      active = data.id;
      $('#brief').value = '';
      await load();
    } catch (err) {
      $('#error').textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
  document.querySelectorAll('[data-brief]').forEach(b => b.addEventListener('click', () => {
    $('#brief').value = b.dataset.brief;
    $('#brief').focus();
  }));
  $('#tasks').addEventListener('click', e => {
    const b = e.target.closest('[data-id]');
    if (!b) return;
    active = b.dataset.id;
    paint();
  });
  document.addEventListener('weaver-work-change', paint);
  const es = new EventSource('/api/events');
  es.onmessage = m => { try { follow(JSON.parse(m.data)); } catch {} };
  WeaverWork.greet();
  load();
})();
