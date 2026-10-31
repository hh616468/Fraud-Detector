// Shared helpers for the wallet, judge console and reviewer pages.
// Everything coming from the API (names, notes, model text) is untrusted: it only ever
// reaches the page through textContent / createElement, never innerHTML.

// ---------- fetch ----------

export class ApiError extends Error {
  constructor(status, data, network = false) {
    const d = data && typeof data === 'object' ? data : {};
    super(d.error || (typeof d.detail === 'string' ? d.detail : '') || (network ? 'network' : `HTTP ${status}`));
    this.status = status;
    this.data = d;
    this.network = network;
    this.code = network ? 'network' : d.error || (status === 422 ? 'validation' : `http_${status}`);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * JSON request. GETs and requests carrying an Idempotency-Key are retried on network
 * failure with the same key, so a lost response never creates a second draft or debit.
 */
export async function api(method, path, body, { idempotencyKey, retries } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  const canRetry = method === 'GET' || Boolean(idempotencyKey);
  const attempts = 1 + (retries ?? (canRetry ? 2 : 0));
  for (let i = 0; i < attempts; i++) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: 'no-store',
      });
    } catch (err) {
      if (i < attempts - 1) {
        await sleep(500 * (i + 1));
        continue;
      }
      throw new ApiError(0, { detail: String(err) }, true);
    }
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: text };
      }
    }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }
  throw new ApiError(0, {}, true);
}

export function newKey() {
  if (globalThis.crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// ---------- error messages ----------

const AR_ERRORS = {
  network: 'ماكو اتصال بالخادم. تأكد إنه شغّال وجرّب مرة ثانية.',
  bad_pin: 'الرمز السري غلط. جرّب مرة ثانية.',
  proceed_locked: (e) => `استنى ${Math.ceil(Number(e.data.seconds) || 0)} ثانية بعد، وبعدين تگدر تكمل.`,
  stale_draft: 'التحويل تغيّر وانفحص من جديد. شوف التنبيه الجديد قبل لا تكمل.',
  insufficient_funds: 'رصيدك ما يكفي لهالتحويل.',
  already_committed: 'هالتحويل انرسل من قبل، وما راح ينسحب مرتين.',
  transfer_committed: 'هالتحويل انرسل من قبل.',
  transfer_cancelled: 'هالتحويل ملغي. سوّي تحويل جديد إذا تحتاج.',
  not_assessed: 'التحويل بعده ما انفحص. جرّب مرة ثانية.',
  concurrent_commit: 'صار تعارض بالتأكيد. حدّث الصفحة وجرّب مرة ثانية.',
  invalid_wallet: 'رقم المحفظة لازم يكون 11 رقم ويبدي بـ 07، مثل 07812345678.',
  invalid_amount: 'اكتب مبلغ صحيح بالدينار.',
  note_too_long: 'الملاحظة طويلة، أقصى شي 140 حرف.',
  name_too_long: 'الاسم طويل، أقصى شي 60 حرف.',
  unknown_reason: 'السبب اللي اخترته مو معروف.',
  self_transfer: 'ما تگدر تحوّل لنفس محفظتك.',
  nothing_to_change: 'ما غيّرت شي بالتحويل.',
  user_not_found: 'الحساب مو موجود.',
  transfer_not_found: 'التحويل مو موجود.',
  chat_finished: 'خلصت المحادثة.',
  no_coaching_at_this_tier: 'ماكو محادثة لهالتحويل.',
  choose_an_option: 'اختار وحدة من الخيارات.',
  turn_already_generated: 'هالرسالة وصلت من قبل.',
  validation: 'تأكد من المعلومات اللي كتبتها.',
};

const EN_ERRORS = {
  network: 'Cannot reach the server. Is it running?',
  bad_pin: 'Wrong PIN.',
  proceed_locked: (e) => `Proceed is locked for ${Math.ceil(Number(e.data.seconds) || 0)} more seconds.`,
  stale_draft: 'The draft changed; review the new assessment.',
  validation: 'Invalid input.',
};

function validationDetail(e) {
  const d = e.data && e.data.detail;
  if (!Array.isArray(d)) return '';
  return d.map((x) => `${(x.loc || []).filter((p) => p !== 'body').join('.')}: ${x.msg}`).join('; ');
}

export function errorAr(e) {
  if (!(e instanceof ApiError)) return 'صار خطأ غير متوقع. جرّب مرة ثانية.';
  const m = AR_ERRORS[e.code];
  if (typeof m === 'function') return m(e);
  if (m) return m;
  if (e.status >= 500) return 'صار خلل بالخادم. جرّب بعد شوية.';
  return 'ما گدرنا نكمل الطلب. جرّب مرة ثانية.';
}

export function errorEn(e) {
  if (!(e instanceof ApiError)) return String(e && e.message ? e.message : e);
  const m = EN_ERRORS[e.code];
  const base = typeof m === 'function' ? m(e) : m || e.code;
  const extra = e.code === 'validation' ? validationDetail(e) : typeof e.data.detail === 'string' ? e.data.detail : '';
  return `${e.status || 'network'} ${base}${extra && extra !== base ? ` (${extra})` : ''}`;
}

// ---------- formatting ----------

export function formatIQD(n) {
  const v = Math.round(Number(n) || 0);
  return `${v.toLocaleString('en-US')} د.ع`;
}

export function formatNumber(n) {
  return (Math.round(Number(n) || 0)).toLocaleString('en-US');
}

/** Arabic-Indic and Persian digits to ASCII, everything else dropped. */
export function toDigits(s) {
  return String(s || '')
    .replace(/[٠-٩]/g, (c) => String(c.charCodeAt(0) - 0x0660))
    .replace(/[۰-۹]/g, (c) => String(c.charCodeAt(0) - 0x06f0))
    .replace(/\D/g, '');
}

export function formatTime(iso) {
  if (!iso) return '';
  return String(iso).replace('T', ' ').slice(0, 16);
}

export function formatLatency(ms) {
  if (ms === null || ms === undefined) return '';
  const v = Number(ms);
  return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`;
}

export function compactJson(v) {
  if (v === null || v === undefined) return '';
  try {
    const s = JSON.stringify(v);
    return s === '{}' ? '' : s;
  } catch {
    return String(v);
  }
}

export const WALLET_RE = /^07[3-9]\d{8}$/;

// ---------- DOM ----------

const BLOCKED_PROPS = new Set(['innerHTML', 'outerHTML', 'srcdoc']);

/** createElement with safe props; string children become text nodes. */
export function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false || BLOCKED_PROPS.has(k)) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = String(v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k === 'value') node.value = v;
    else if (k === 'checked') node.checked = Boolean(v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function $(sel, root = document) {
  return root.querySelector(sel);
}

// ---------- shared widgets ----------

export const TIER_EN = { pass: 'pass', nudge: 'nudge', coach: 'coach' };

export function tierBadge(tier) {
  return el('span', { class: `tier-badge tier-${tier || 'none'}` }, tier || '—');
}

export const AR_TURN_LABELS = {
  concern: '',
  tactic: 'شنو ممكن يگولون بعدين',
  safe: 'الخطوة الآمنة',
  llm: 'Gemma',
  template: 'قالب مراجَع',
  you: 'انت',
};

export const EN_TURN_LABELS = {
  concern: 'Concern',
  tactic: 'What they may say next',
  safe: 'Safe step',
  llm: 'Gemma',
  template: 'Reviewed template',
  you: 'User tapped',
};

function sourceBadge(turn, labels) {
  const isLlm = turn.source === 'llm';
  const parts = [isLlm ? labels.llm : labels.template];
  const lat = formatLatency(turn.latency_ms);
  if (lat) parts.push(lat);
  return el(
    'span',
    {
      class: `src-badge ${isLlm ? 'src-llm' : 'src-template'}`,
      dir: 'ltr',
      title: (turn.errors || []).join(', ') || undefined,
    },
    parts.join(' · '),
  );
}

/**
 * One coaching turn as a chat bubble. `onOption(option)` may return a promise; options are
 * disabled while it runs and re-enabled if it rejects. Empty fields are not rendered.
 */
export function renderTurn(turn, { labels = AR_TURN_LABELS, onOption, debug = false } = {}) {
  const bubble = el('div', { class: 'bubble bubble-bot', dir: 'rtl', lang: 'ar' });
  const meta = el('div', { class: 'bubble-meta' }, sourceBadge(turn, labels));
  if (debug) {
    meta.append(el('span', { class: 'meta-item', dir: 'ltr' }, `turn ${turn.turn}${turn.final ? ' (final)' : ''}`));
    if (turn.cited_rules && turn.cited_rules.length) {
      meta.append(el('span', { class: 'meta-item', dir: 'ltr' }, `cites ${turn.cited_rules.join(', ')}`));
    }
    if (turn.errors && turn.errors.length) {
      meta.append(el('span', { class: 'meta-item meta-error', dir: 'ltr' }, `errors: ${turn.errors.join(', ')}`));
    }
  }
  bubble.append(meta);

  if (turn.concern) {
    append(bubble, [
      labels.concern ? el('span', { class: 'field-label', dir: 'auto' }, labels.concern) : null,
      el('p', { class: 'turn-concern' }, turn.concern),
    ]);
  }
  if (turn.next_tactic) {
    bubble.append(
      el('div', { class: 'turn-block turn-tactic' },
        el('span', { class: 'field-label', dir: 'auto' }, labels.tactic),
        el('p', {}, turn.next_tactic)),
    );
  }
  if (turn.next_step) {
    bubble.append(
      el('div', { class: 'turn-block turn-safe' },
        el('span', { class: 'field-label', dir: 'auto' }, labels.safe),
        el('p', {}, turn.next_step)),
    );
  }
  if (turn.question) bubble.append(el('p', { class: 'turn-question' }, turn.question));

  const options = turn.options || [];
  if (options.length) {
    const group = el('div', { class: 'turn-options', role: 'group', 'aria-label': turn.question || 'options' });
    const buttons = options.map((o) =>
      el('button', { type: 'button', class: 'option-btn', 'aria-pressed': 'false', dataset: { id: o.id } }, o.label),
    );
    const lock = (on) => buttons.forEach((b) => (b.disabled = on));
    buttons.forEach((b, i) => {
      b.addEventListener('click', async () => {
        if (!onOption) return;
        lock(true);
        b.setAttribute('aria-pressed', 'true');
        b.classList.add('chosen');
        try {
          await onOption(options[i]);
        } catch {
          b.setAttribute('aria-pressed', 'false');
          b.classList.remove('chosen');
          lock(false);
        }
      });
    });
    if (turn.chosen_option || !onOption) {
      lock(true);
      const chosen = buttons.find((b) => b.dataset.id === turn.chosen_option);
      if (chosen) {
        chosen.classList.add('chosen');
        chosen.setAttribute('aria-pressed', 'true');
      }
    }
    group.append(...buttons);
    bubble.append(group);
  }
  return bubble;
}

export function userBubble(text) {
  return el('div', { class: 'bubble bubble-user', dir: 'rtl', lang: 'ar' }, text);
}

export function typingBubble(label = 'يكتب…') {
  return el(
    'div',
    { class: 'bubble bubble-bot typing', role: 'status', 'aria-label': label },
    el('span', { class: 'dot' }),
    el('span', { class: 'dot' }),
    el('span', { class: 'dot' }),
  );
}

/** Rule trace table of every result, fired or not, including disabled rules. */
export function ruleTraceTable(results) {
  const table = el('table', { class: 'data-table trace-table' });
  table.append(
    el('thead', {},
      el('tr', {},
        el('th', { scope: 'col' }, 'Rule'),
        el('th', { scope: 'col' }, 'Description'),
        el('th', { scope: 'col', class: 'num' }, 'Points'),
        el('th', { scope: 'col', class: 'center' }, 'Fired'),
        el('th', { scope: 'col' }, 'Disabled'),
        el('th', { scope: 'col' }, 'Values'))),
  );
  const tbody = el('tbody');
  let total = 0;
  for (const r of results || []) {
    if (r.fired) total += r.points;
    tbody.append(
      el('tr', { class: r.fired ? 'row-fired' : r.disabled ? 'row-disabled' : '' },
        el('td', { class: 'mono' }, r.id),
        el('td', {}, r.en),
        el('td', { class: `num ${r.points < 0 ? 'credit' : ''}` }, r.points > 0 ? `+${r.points}` : String(r.points)),
        el('td', { class: 'center', 'aria-label': r.fired ? 'fired' : 'not fired' }, r.fired ? '✓' : '–'),
        el('td', {}, r.disabled || ''),
        el('td', { class: 'mono values', dir: 'auto' }, compactJson(r.values))),
    );
  }
  table.append(tbody);
  table.append(
    el('tfoot', {},
      el('tr', {},
        el('td', { colspan: '2' }, 'Sum of fired points (score floors at 0)'),
        el('td', { class: 'num' }, String(total)),
        el('td', { colspan: '3' }))),
  );
  return el('div', { class: 'table-wrap' }, table);
}

export function auditTable(events) {
  const rows = [...(events || [])].sort((a, b) => a.id - b.id);
  const table = el('table', { class: 'data-table audit-table' });
  table.append(
    el('thead', {},
      el('tr', {},
        el('th', { scope: 'col' }, '#'),
        el('th', { scope: 'col' }, 'Time'),
        el('th', { scope: 'col' }, 'Event'),
        el('th', { scope: 'col' }, 'User ref'),
        el('th', { scope: 'col' }, 'Payload'))),
  );
  const tbody = el('tbody');
  for (const e of rows) {
    tbody.append(
      el('tr', {},
        el('td', { class: 'mono' }, String(e.id)),
        el('td', { class: 'mono nowrap' }, formatTime(e.ts) + (e.ts ? String(e.ts).slice(16, 19) : '')),
        el('td', { class: 'mono' }, e.event),
        el('td', { class: 'mono' }, e.user_ref),
        el('td', { class: 'mono values', dir: 'auto' }, compactJson(e.payload))),
    );
  }
  if (!rows.length) tbody.append(el('tr', {}, el('td', { colspan: '5' }, 'No audit events.')));
  table.append(tbody);
  return el('div', { class: 'table-wrap' }, table);
}
