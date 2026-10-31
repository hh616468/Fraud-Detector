// Judge console: build a scenario, run it, and show the full scorecard trace.
import {
  $, api, auditTable, clear, el, errorEn, EN_TURN_LABELS, formatIQD, formatLatency, newKey,
  renderTurn, ruleTraceTable, tierBadge, typingBubble, userBubble, WALLET_RE,
} from '/static/common.js';

const S = { users: [], user: null, catalogue: null, view: null, turns: [], busy: false, token: 0 };

const val = (id) => $(`#${id}`).value.trim();
const num = (id) => (val(id) === '' ? null : Number(val(id)));
const setVal = (id, v) => { $(`#${id}`).value = v === null || v === undefined ? '' : String(v); };
const setChk = (id, v) => { $(`#${id}`).checked = Boolean(v); };

function showError(sel, msg) {
  const box = $(sel);
  box.textContent = msg || '';
  box.hidden = !msg;
}

// ---------- init ----------

async function init() {
  try {
    const [catalogue, users] = await Promise.all([api('GET', '/api/catalogue'), api('GET', '/api/users')]);
    S.catalogue = catalogue;
    S.users = users;
  } catch (e) {
    showError('#run-error', errorEn(e));
    return;
  }
  const reason = $('#tx-reason');
  for (const r of S.catalogue.reasons || []) {
    reason.append(el('option', { value: r.code }, `${r.code}${r.cue ? ' (cue)' : ''}: ${r.en}`));
  }
  const sel = $('#user');
  for (const u of S.users) {
    sel.append(el('option', { value: u.id }, `${u.id} · ${u.first_name} ${u.last_name} (${u.segment})`));
  }
  sel.value = S.users.some((u) => u.id === 'demo_salaried') ? 'demo_salaried' : (S.users[0] || {}).id;
  sel.addEventListener('change', () => loadUser(sel.value));
  document.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => applyPreset(b.dataset.preset)));
  $('#scenario').addEventListener('submit', (e) => {
    e.preventDefault();
    run();
  });
  $('#clear-btn').addEventListener('click', clearForm);
  $('#coach-btn').addEventListener('click', () => requestTurn(null));
  $('#audit-btn').addEventListener('click', loadAudit);
  $('#tx-amount').addEventListener('input', amountHint);
  loadHealth();
  await loadUser(sel.value);
}

async function loadHealth() {
  try {
    const h = await api('GET', '/api/health');
    $('#health').textContent =
      `Rules ${h.rules_version} · fingerprint ${h.fingerprint} · LLM ${h.llm ? 'reachable' : 'unreachable (template fallback)'} · coach timeout ${h.coach_timeout_s}s`;
  } catch (e) {
    $('#health').textContent = errorEn(e);
  }
}

async function loadUser(id) {
  try {
    S.user = await api('GET', `/api/users/${encodeURIComponent(id)}`);
  } catch (e) {
    showError('#run-error', errorEn(e));
    return;
  }
  const u = S.user;
  $('#user-info').textContent = `Balance ${formatIQD(u.balance)} · wallet ${u.wallet} · ${u.city}`;
  const list = clear($('#contacts'));
  for (const c of u.contacts) list.append(el('li', {}, `${c.name} `, el('span', { class: 'ltr mono' }, c.wallet)));
  if (!u.contacts.length) list.append(el('li', {}, '—'));
  amountHint();
}

function amountHint() {
  const a = num('tx-amount');
  const b = S.user ? S.user.balance : 0;
  $('#tx-amount-hint').textContent = a ? `${formatIQD(a)} · ${b ? Math.round((a / b) * 100) : '—'}% of balance` : '';
}

// ---------- presets ----------

function randWallet() {
  const prefix = ['078', '077', '075', '079'][Math.floor(Math.random() * 4)];
  let s = prefix;
  for (let i = 0; i < 8; i++) s += Math.floor(Math.random() * 10);
  return s === S.user.wallet ? randWallet() : s;
}

const roundTo = (n, step) => Math.max(step, Math.round(n / step) * step);

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function usualAmount(wallet) {
  return median((S.user.recent || []).filter((r) => r.direction === 'out' && r.wallet === wallet).map((r) => r.amount));
}

/** A saved contact that reads like a person (not the landlord, savings group or generator). */
function personContact() {
  const cs = S.user.contacts || [];
  return cs.find((c) => !/[-–]|جمعية|المولدة|إيجار|ايجار/.test(c.name)) || cs[0] || null;
}

function clearForm() {
  ['dev-kind', 'rc-wallet', 'rc-display', 'rc-age', 'rc-senders', 'cr-wallet', 'cr-name', 'cr-amount',
    'tx-amount', 'tx-name', 'tx-note', 'tx-reason'].forEach((id) => setVal(id, ''));
  setVal('dev-min', 45);
  setVal('cr-min', 30);
  ['rc-reported', 'rc-biller', 'rc-business', 'tx-call'].forEach((id) => setChk(id, false));
  showError('#run-error', '');
  amountHint();
}

function presetStatus(text) {
  const p = $('#preset-status');
  p.textContent = text;
  p.hidden = !text;
}

function applyPreset(name) {
  if (!S.user) return;
  clearForm();
  const u = S.user;
  const bal = u.balance;
  let note = '';
  switch (name) {
    case 'safe_account': {
      const w = randWallet();
      setVal('dev-kind', 'sim_change');
      setVal('dev-min', 40);
      setVal('rc-wallet', w);
      setVal('rc-display', 'خدمة عملاء زين كاش');
      setVal('rc-age', 2);
      setVal('tx-name', 'خدمة عملاء زين كاش');
      setVal('tx-amount', roundTo(bal * 0.9, 1000));
      setChk('tx-call', true);
      note = 'New 2-day-old wallet posing as Zain Cash support, live call, recent SIM change, 90% of balance.';
      break;
    }
    case 'prize_fee': {
      setVal('rc-wallet', randWallet());
      setVal('tx-name', 'حسن جاسم');
      setVal('tx-amount', roundTo(Math.min(25000, bal * 0.5), 250));
      setVal('tx-note', 'رسوم استلام الجائزة');
      setVal('tx-reason', 'prize_fee');
      note = 'Small fee to a new wallet, prize note, user-picked reason prize_fee.';
      break;
    }
    case 'refund': {
      const w = randWallet();
      setVal('cr-wallet', w);
      setVal('cr-name', 'حيدر كريم');
      setVal('cr-amount', 250000);
      setVal('cr-min', 30);
      setVal('rc-wallet', w);
      setVal('tx-name', 'حيدر كريم');
      setVal('tx-amount', 250000);
      setVal('tx-note', 'رجعلي الفلوس حولتها غلط');
      note = 'A stranger credits 250,000 IQD 30 min ago, then asks for it back to the same wallet.';
      break;
    }
    case 'lookalike': {
      const c = personContact();
      if (!c) {
        presetStatus('This user has no saved contacts; pick another user.');
        return;
      }
      const usual = usualAmount(c.wallet) || 100000;
      setVal('rc-wallet', randWallet());
      setVal('tx-name', c.name);
      setVal('tx-amount', roundTo(Math.min(usual * 1.5, bal * 0.6), 5000));
      setVal('tx-note', 'هذا رقمي الجديد، دزلي ضروري');
      note = `New wallet using the saved contact's name "${c.name}" (the real contact is ${c.wallet}).`;
      break;
    }
    case 'eid_gift': {
      const c = personContact();
      if (!c) {
        presetStatus('This user has no saved contacts; pick another user.');
        return;
      }
      const usual = usualAmount(c.wallet) || 50000;
      setVal('rc-wallet', c.wallet);
      setVal('tx-name', c.name);
      setVal('tx-amount', roundTo(Math.min(Math.max(usual * 2.5, 100000), bal * 0.5), 5000));
      setVal('tx-note', 'عيدية');
      note = `Eid gift to the familiar contact "${c.name}", about 2.5x the usual ${formatIQD(usual)}.`;
      break;
    }
    case 'rent': {
      const cs = u.contacts || [];
      let c = cs.find((x) => /إيجار|ايجار/.test(x.name));
      let fallback = false;
      if (!c) {
        const counts = {};
        for (const r of u.recent || []) if (r.direction === 'out') counts[r.wallet] = (counts[r.wallet] || 0) + 1;
        c = [...cs].sort((a, b) => (counts[b.wallet] || 0) - (counts[a.wallet] || 0))[0];
        fallback = true;
      }
      if (!c) {
        presetStatus('This user has no saved contacts; pick another user.');
        return;
      }
      const usual = usualAmount(c.wallet) || (fallback ? 50000 : 400000);
      setVal('rc-wallet', c.wallet);
      setVal('tx-name', c.name);
      setVal('tx-amount', usual);
      setVal('tx-note', fallback ? '' : 'ايجار الشهر');
      note = fallback
        ? `No landlord contact for this user; using the most frequent payee "${c.name}" at the usual amount. demo_salaried has the landlord.`
        : `Monthly rent to the saved landlord "${c.name}" at the usual ${formatIQD(usual)}.`;
      break;
    }
    default:
      return;
  }
  amountHint();
  presetStatus(note);
}

// ---------- run ----------

function buildSetup() {
  const setup = { user_id: S.user.id };
  if (val('dev-kind')) setup.device_event = { kind: val('dev-kind'), minutes_ago: Math.max(0, Math.round(num('dev-min') ?? 30)) };
  const facts = val('rc-display') !== '' || val('rc-age') !== '' || (num('rc-senders') || 0) > 0
    || $('#rc-reported').checked || $('#rc-biller').checked || $('#rc-business').checked;
  if (facts) {
    setup.recipient = {
      wallet: val('rc-wallet'),
      display_name: val('rc-display'),
      age_days: num('rc-age'),
      reported: $('#rc-reported').checked,
      is_biller: $('#rc-biller').checked,
      is_business: $('#rc-business').checked,
      unrelated_senders_7d: Math.max(0, Math.round(num('rc-senders') || 0)),
    };
  }
  if (val('cr-wallet') || val('cr-amount')) {
    setup.incoming_credit = {
      from_wallet: val('cr-wallet'),
      from_name: val('cr-name'),
      amount: Math.round(num('cr-amount') || 0),
      minutes_ago: Math.max(0, Math.round(num('cr-min') ?? 20)),
    };
  }
  return setup;
}

function validate(setup) {
  if (!WALLET_RE.test(val('rc-wallet'))) return 'Recipient wallet must look like 07XXXXXXXXX (11 digits, third digit 3-9).';
  const amount = num('tx-amount');
  if (!Number.isInteger(amount) || amount <= 0) return 'Transfer amount must be a positive whole number of IQD.';
  if (setup.incoming_credit) {
    if (!WALLET_RE.test(setup.incoming_credit.from_wallet)) return 'Incoming credit wallet must look like 07XXXXXXXXX.';
    if (!(setup.incoming_credit.amount > 0)) return 'Incoming credit amount must be positive.';
  }
  return '';
}

async function run() {
  if (S.busy || !S.user) return;
  showError('#run-error', '');
  const setup = buildSetup();
  const problem = validate(setup);
  if (problem) {
    showError('#run-error', problem);
    return;
  }
  const btn = $('#run-btn');
  S.busy = true;
  btn.disabled = true;
  btn.textContent = 'Running…';
  let applied = [];
  try {
    if (setup.device_event || setup.recipient || setup.incoming_credit) {
      applied = (await api('POST', '/api/console/setup', setup)).applied || [];
    }
    const body = {
      user_id: S.user.id,
      recipient_wallet: val('rc-wallet'),
      amount: num('tx-amount'),
      recipient_name: val('tx-name'),
      note: val('tx-note'),
      reason_code: val('tx-reason') || null,
      call_active: $('#tx-call').checked,
    };
    const view = await api('POST', '/api/transfers', body, { idempotencyKey: newKey() });
    renderResult(view, applied);
    loadUser(S.user.id);
  } catch (e) {
    showError('#run-error', `${errorEn(e)}${applied.length ? ` (setup already applied: ${applied.join(', ')})` : ''}`);
  } finally {
    S.busy = false;
    btn.disabled = false;
    btn.textContent = 'Run';
  }
}

function stat(k, v) {
  return el('div', { class: 'stat' }, el('span', { class: 'k' }, k), el('span', { class: 'v' }, v));
}

function renderResult(view, applied) {
  S.view = view;
  S.turns = [];
  S.token++;
  const { transfer: t, assessment: a, warning } = view;
  $('#result-empty').hidden = true;
  $('#result').hidden = false;
  clear($('#stats')).append(
    stat('Transfer', `#${t.id} · v${t.draft_version} · ${t.status}`),
    stat('Score', String(a.score)),
    stat('Tier', tierBadge(a.tier)),
    stat('Override', a.override || '—'),
    stat('Family', `${a.family.en} (${a.family.id})`),
    stat('Rules version', a.rules_version),
    stat('Assess latency', formatLatency(a.latency_ms) || '—'),
    stat('Proceed lock', `${a.proceed_unlock_in_s}s`),
    stat('Amount', formatIQD(t.amount)),
  );
  $('#setup-applied').textContent = applied.length ? `Setup applied: ${applied.join(', ')}` : 'No setup applied (transfer only).';
  const w = clear($('#warning'));
  w.append(warning ? warning.text : el('span', { class: 'muted', dir: 'ltr', lang: 'en' }, 'Pass tier: no warning, straight to PIN.'));
  w.className = a.tier === 'pass' ? 'panel arabic-block' : `arabic-block warn-card warn-${a.tier}`;
  clear($('#trace')).append(ruleTraceTable(a.results));
  clear($('#chat'));
  clear($('#audit'));
  $('#audit-wrap').hidden = true;
  showError('#action-error', '');
  const coachBtn = $('#coach-btn');
  coachBtn.disabled = a.tier !== 'coach';
  coachBtn.textContent = a.tier === 'coach' ? 'Coach turn' : 'Coach turn (coach tier only)';
}

// ---------- coaching ----------

async function requestTurn(option) {
  if (!S.view || S.busy) return;
  const last = S.turns[S.turns.length - 1];
  if (!option && last) {
    if (last.final) {
      showError('#action-error', 'Chat finished (3 turns).');
      return;
    }
    showError('#action-error', 'Tap one of the options in the last turn to get the next turn.');
    return;
  }
  const token = S.token;
  const t = S.view.transfer;
  const chat = $('#chat');
  showError('#action-error', '');
  const mine = option ? userBubble(option.label) : null;
  if (mine) chat.append(mine);
  const typing = typingBubble('Coach is typing…');
  chat.append(typing);
  S.busy = true;
  $('#coach-btn').disabled = true;
  try {
    const turn = await api('POST', `/api/transfers/${t.id}/coach`, { draft_version: t.draft_version, chosen_option_id: option ? option.id : null });
    if (token !== S.token) return;
    typing.remove();
    S.turns.push(turn);
    chat.append(renderTurn(turn, { labels: EN_TURN_LABELS, debug: true, onOption: turn.final ? null : (o) => requestTurnFromOption(o) }));
    $('#coach-btn').textContent = turn.final ? 'Chat finished' : 'Coach turn';
  } catch (e) {
    typing.remove();
    if (mine) mine.remove();
    showError('#action-error', errorEn(e));
    if (option) throw e;
  } finally {
    S.busy = false;
    const done = S.turns.length && S.turns[S.turns.length - 1].final;
    $('#coach-btn').disabled = !S.view || S.view.assessment.tier !== 'coach' || Boolean(done);
  }
}

function requestTurnFromOption(option) {
  return requestTurn(option);
}

async function loadAudit() {
  if (!S.view) return;
  showError('#action-error', '');
  try {
    const full = await api('GET', `/api/transfers/${S.view.transfer.id}`);
    clear($('#audit')).append(auditTable(full.audit));
    $('#audit-wrap').hidden = false;
    $('#audit-wrap').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch (e) {
    showError('#action-error', errorEn(e));
  }
}

init();
