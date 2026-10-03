/* Live adapter for the coordinator workspace: same interface as operations-model.js, backed by the Cadence
   service on the GB10 (tasks, agent progress, approvals, activity, doctor voice clips). Schedules and settings
   stay local. If the service is unreachable the simulated adapter keeps working, so the static preview still runs. */
(function (root) {
  const simulated = root.CadenceOperations.create;
  const SOURCES = { coordinator: 'Coordinator', chat: 'Coordinator chat', patient_reply: 'Patient text', monitoring: 'Remote monitoring',
    ccm: 'Chronic care', orders: 'Always-on sweep · orders', inventory: 'Always-on sweep · inventory', staffing: 'Always-on sweep · staffing',
    insurance: 'Always-on sweep · insurance', documents: 'Lab results', slack: 'Slack · Dr. Chen' };
  const BOT = new Set(['agent.planning', 'agent.progress', 'agent.note', 'agent.missing_info', 'agent.reply', 'approval.prepared',
    'approval.confirmed', 'agent.error', 'agent.unavailable']);

  // Synchronous request: the UI expects createTask to return the new task id immediately.
  function syncPost(url, body) {
    const x = new XMLHttpRequest();
    x.open('POST', url, false);
    x.setRequestHeader('content-type', 'application/json');
    x.send(JSON.stringify(body || {}));
    const data = JSON.parse(x.responseText || '{}');
    if (x.status >= 400) throw Error(data.detail || 'The Cadence service refused the request.');
    return data;
  }
  const post = (url, body) => fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(async r => { const d = await r.json().catch(() => ({})); if (!r.ok) throw Error(d.detail || 'Request failed'); return d; });

  function toTask(t) {
    const status = { running: 'running', waiting: 'waiting', review: 'review', completed: 'completed', cancelled: 'cancelled', failed: 'paused' }[t.status] || 'running';
    const messages = [{ role: 'user', text: t.brief || t.title }];
    for (const e of t.events) {
      if (e.kind === 'coordinator.reply') messages.push({ role: 'user', text: e.summary });
      else if (BOT.has(e.kind)) messages.push({ role: 'bot', text: e.kind === 'approval.prepared' ? 'Queued for your approval: ' + e.summary
        : e.kind === 'approval.confirmed' ? 'Done: ' + e.summary : e.summary });
    }
    const toolCalls = t.events.filter(e => e.kind === 'agent.tool_call').length;
    const pending = t.approvals.filter(a => a.state === 'prepared');
    return {
      id: t.id, title: t.title, prompt: t.brief || t.title, source: SOURCES[t.kind] || 'Cadence', kind: t.kind, created: t.created_at,
      status, stage: status === 'review' || status === 'completed' ? 2 : toolCalls ? 1 : 0, files: [], requiresDocument: false, messages,
      events: t.events.map(e => ({ text: e.summary, at: e.created_at, kind: e.kind })), approvals: t.approvals, live: true,
      output: pending.length ? { task: pending.map(a => a.summary).join(' · '), live: true } : null,
    };
  }

  root.CadenceOperations.create = function (saved) {
    const local = simulated(saved);
    const listeners = new Set();
    let live = null, timer = null;
    const emit = () => listeners.forEach(fn => fn(api.state));

    async function refresh() {
      try {
        const r = await fetch('/api/live/tasks');
        if (!r.ok) throw Error();
        const d = await r.json();
        live = {
          tasks: d.tasks.map(toTask),
          events: d.events.map(e => ({ id: String(e.seq), at: e.created_at, text: e.summary, taskId: e.task_id })),
          inbox: d.recordings.map(x => ({ id: x.id, taskId: '', text: (x.patient_id ? x.patient_id + ': ' : '') + (x.summary || 'Voice clip received'),
            files: [{ name: 'Voice clip ' + x.id, bytes: 0 }], at: x.created_at, author: 'Dr. Chen', recording: x.id })),
        };
      } catch { live = null; }
      emit();
    }
    function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 300); }
    try {
      const es = new EventSource('/api/events');
      es.onmessage = m => { try { const e = JSON.parse(m.data); if (e.kind !== 'device.reading') schedule(); } catch {} };
    } catch {}
    refresh();

    const api = {
      get state() { const s = local.state; return live ? { ...s, tasks: live.tasks, events: live.events, inbox: live.inbox.length ? live.inbox : s.inbox, live: true } : s; },
      get live() { return !!live; },
      subscribe(fn) { listeners.add(fn); const off = local.subscribe(fn); return () => { listeners.delete(fn); off(); }; },
      createTask(text, source = 'Coordinator', files = [], requiresDocument = false) {
        if (!live) return local.createTask(text, source, files, requiresDocument);
        if (!text.trim()) throw Error('Describe the task first.');
        const t = syncPost('/api/tasks', { title: text.trim().slice(0, 90), brief: text.trim(), kind: source.startsWith('Slack') ? 'slack' : 'coordinator' });
        refresh();
        return t.id;
      },
      advance(id) { if (!live) local.advance(id); },  // the real agent advances tasks itself
      reply(id, text, files = []) {
        if (!live) return local.reply(id, text, files);
        if (!text.trim()) throw Error('Add a message.');
        post(`/api/tasks/${id}/reply`, { text }).then(refresh).catch(e => alert(e.message));
      },
      action(id, action) {
        if (!live) return local.action(id, action);
        const call = action === 'approve' ? post(`/api/tasks/${id}/approve`)
          : post(`/api/tasks/${id}/status`, { status: { pause: 'waiting', resume: 'running', cancel: 'cancelled' }[action] });
        call.then(refresh).catch(e => alert(e.message));
      },
      receiveSlack(text, files = []) {
        if (!live) return local.receiveSlack(text, files);
        return api.createTask(text || 'Process the attached doctor document', 'Slack · Dr. Chen', files, false);
      },
      runSchedule(id) {
        if (!live) return local.runSchedule(id);
        const x = local.state.schedules.find(x => x.id === id);
        if (!x) throw Error('Schedule not found.');
        x.lastRun = new Date().toISOString();
        return api.createTask(x.prompt, 'Schedule · ' + x.name, [], x.requiresDocument);
      },
      saveSchedule: (d, id) => local.saveSchedule(d, id), toggleSchedule: id => local.toggleSchedule(id),
      deleteSchedule: id => local.deleteSchedule(id), settings: d => local.settings(d), reset: () => local.reset(),
      refresh,
    };
    return api;
  };
})(typeof window !== 'undefined' ? window : globalThis);
