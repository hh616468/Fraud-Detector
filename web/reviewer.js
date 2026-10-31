// Reviewer queue: pseudonymous transfer list with the full trace, coach turns and audit trail.
import {
  $, api, auditTable, clear, el, errorEn, EN_TURN_LABELS, formatIQD, formatLatency, formatTime,
  renderTurn, ruleTraceTable, tierBadge,
} from '/static/common.js';

const S = { rows: [], selected: null, timer: null, loading: false };

function showError(sel, msg) {
  const box = $(sel);
  box.textContent = msg || '';
  box.hidden = !msg;
}

function sourcesSummary(sources) {
  if (!sources || !sources.length) return '—';
  const counts = {};
  for (const s of sources) counts[s] = (counts[s] || 0) + 1;
  return Object.entries(counts).map(([s, n]) => `${s} ×${n}`).join(', ');
}

async function loadQueue() {
  if (S.loading) return;
  S.loading = true;
  const includePass = $('#include-pass').checked;
  try {
    S.rows = await api('GET', `/api/review?limit=200&include_pass=${includePass}`);
    showError('#queue-error', '');
    renderQueue();
    $('#updated').textContent = `Updated ${new Date().toLocaleTimeString('en-GB')}`;
  } catch (e) {
    showError('#queue-error', errorEn(e));
  } finally {
    S.loading = false;
  }
}

function renderQueue() {
  const tbody = clear($('#queue'));
  for (const r of S.rows) {
    const tr = el('tr', { class: r.transfer_id === S.selected ? 'selected' : '', dataset: { id: r.transfer_id } },
      el('td', {},
        el('button', { type: 'button', class: 'row-btn', 'aria-label': `Open transfer ${r.transfer_id}` }, `#${r.transfer_id}`)),
      el('td', { class: 'mono nowrap' }, formatTime(r.ts)),
      el('td', { class: 'mono' }, r.user_ref),
      el('td', { class: 'num nowrap' }, formatIQD(r.amount)),
      el('td', {}, tierBadge(r.tier), r.override ? el('span', { class: 'small muted' }, ` ${r.override}`) : null),
      el('td', { class: 'num' }, String(r.score)),
      el('td', { class: 'mono' }, (r.fired || []).join(' ') || '—'),
      el('td', { class: 'mono' }, r.family || '—'),
      el('td', { class: 'small' }, sourcesSummary(r.coach_sources)),
      el('td', { class: 'mono small' }, (r.taps || []).join(' → ') || '—'),
      el('td', {}, r.choice || '—'),
      el('td', {}, r.status));
    tr.addEventListener('click', () => openDetail(r.transfer_id, true));
    tbody.append(tr);
  }
  if (!S.rows.length) {
    tbody.append(el('tr', {}, el('td', { colspan: '12', class: 'muted' },
      $('#include-pass').checked ? 'No transfers yet.' : 'No nudge or coach transfers yet. Tick "Include pass tier" to see all.')));
  }
}

function stat(k, v) {
  return el('div', { class: 'stat' }, el('span', { class: 'k' }, k), el('span', { class: 'v' }, v));
}

async function openDetail(id, focus = false) {
  S.selected = id;
  for (const tr of $('#queue').children) tr.classList.toggle('selected', Number(tr.dataset.id) === id);
  $('#detail').hidden = false;
  $('#grid').classList.add('has-detail');
  $('#detail-title').textContent = `Transfer #${id}`;
  try {
    const v = await api('GET', `/api/review/${id}`);
    if (S.selected !== id) return;
    showError('#detail-error', '');
    renderDetail(v);
    if (focus) {
      $('#detail-title').focus({ preventScroll: true });
      $('#detail').scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
  } catch (e) {
    showError('#detail-error', errorEn(e));
  }
}

function renderDetail(v) {
  const body = clear($('#detail-body'));
  const t = v.transfer;
  const a = v.assessment;
  const row = S.rows.find((r) => r.transfer_id === t.id);
  // Pseudonymous view: the user appears only as user_ref; recipient details and notes are not shown.
  body.append(
    el('div', { class: 'stats' },
      stat('User ref', t.user_ref || (row ? row.user_ref : '—')),
      stat('Recipient ref', t.recipient_ref || '—'),
      stat('Amount', formatIQD(t.amount)),
      stat('Status', `${t.status} · v${t.draft_version}`),
      a ? stat('Tier', tierBadge(a.tier)) : null,
      a ? stat('Score', String(a.score)) : null,
      a ? stat('Override', a.override || '—') : null,
      a ? stat('Family', `${a.family.en} (${a.family.id})`) : null,
      a ? stat('Rules', `${a.rules_version} · ${formatLatency(a.latency_ms)}`) : null,
      stat('Call flag', t.call_active ? 'on' : 'off'),
      stat('Reason', t.reason_code || '—')),
  );
  if (v.warning) {
    body.append(el('h3', {}, 'Warning shown'),
      el('div', { class: `arabic-block warn-card warn-${v.warning.tier}`, lang: 'ar', dir: 'rtl' }, v.warning.text));
  }
  if (a) body.append(el('h3', {}, 'Rule trace'), ruleTraceTable(a.results));

  const turns = v.coach_turns || [];
  body.append(el('h3', { style: 'margin-top:1rem' }, `Coach turns (${turns.length})`));
  if (turns.length) {
    const chat = el('div', { class: 'chat' });
    for (const turn of turns) {
      chat.append(renderTurn(turn, { labels: EN_TURN_LABELS, debug: true, onOption: null }));
    }
    body.append(chat);
  } else {
    body.append(el('p', { class: 'muted' }, a && a.tier === 'coach' ? 'No turns requested.' : 'No coaching at this tier.'));
  }
  body.append(el('h3', { style: 'margin-top:1rem' }, 'Audit events'), auditTable(v.audit));
}

function setAutoRefresh(on) {
  clearInterval(S.timer);
  if (on) {
    S.timer = setInterval(async () => {
      await loadQueue();
      if (S.selected !== null && !$('#detail').hidden) openDetail(S.selected, false);
    }, 5000);
  }
}

function init() {
  $('#include-pass').addEventListener('change', loadQueue);
  $('#refresh-btn').addEventListener('click', loadQueue);
  $('#auto-refresh').addEventListener('change', (e) => setAutoRefresh(e.target.checked));
  $('#close-detail').addEventListener('click', () => {
    S.selected = null;
    $('#detail').hidden = true;
    $('#grid').classList.remove('has-detail');
    for (const tr of $('#queue').children) tr.classList.remove('selected');
  });
  loadQueue();
}

init();
