// Mock wallet: draft -> assessment -> (nudge | coaching chat) -> PIN confirm | cancel.
import {
  $, api, append, clear, el, errorAr, formatIQD, formatTime, newKey, renderTurn, toDigits,
  typingBubble, userBubble, WALLET_RE,
} from '/static/common.js';

const SEGMENT_AR = { retiree: 'متقاعدة', salaried: 'موظف', first_time: 'مستخدمة جديدة' };
const KIND_AR = { p2p: 'تحويل', bill: 'فاتورة', salary: 'راتب', pension: 'تقاعد', cash_in: 'شحن رصيد', topup: 'شحن', merchant: 'شراء' };
const STORE_KEY = 'wallet.persona';

const S = {
  users: [],
  user: null,
  reasons: [],
  reason: null, // selected reason chip code
  submitKey: null, // Idempotency-Key of the current form submission, kept for network retries
  submitFingerprint: null,
  view: null, // latest transfer view from the API
  turns: [],
  turnInFlight: false,
  unlockAt: 0,
  timer: null,
  confirmKey: null,
  token: 0, // bumps whenever the active draft changes; late async results for an old draft are dropped
  editing: false,
  busy: false,
};

const screens = ['form', 'review', 'pin', 'done'];
function show(name) {
  for (const s of screens) $(`#screen-${s}`).hidden = s !== name;
  const target = $(`#screen-${name}`);
  const heading = target.querySelector('h2:not(.visually-hidden)') || target;
  if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
  heading.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

function showError(sel, message, ...extra) {
  const box = $(sel);
  clear(box);
  if (!message) {
    box.hidden = true;
    return;
  }
  append(box, [el('span', {}, message), ...extra]);
  box.hidden = false;
}

function setNotice(sel, text) {
  const box = $(sel);
  box.textContent = text || '';
  box.hidden = !text;
}

// ---------- loading ----------

async function init() {
  try {
    const [catalogue, users] = await Promise.all([api('GET', '/api/catalogue'), api('GET', '/api/users')]);
    S.reasons = catalogue.reasons || [];
    S.users = users || [];
  } catch (e) {
    showError('#global-error', errorAr(e));
    return;
  }
  const sel = $('#persona');
  for (const u of S.users) {
    sel.append(el('option', { value: u.id }, `${u.first_name} ${u.last_name} · ${SEGMENT_AR[u.segment] || u.segment}`));
  }
  let saved = null;
  try {
    saved = localStorage.getItem(STORE_KEY);
  } catch { /* storage unavailable */ }
  const fromUrl = new URLSearchParams(location.search).get('user');
  const pick = [fromUrl, saved, 'demo_retiree'].find((id) => id && S.users.some((u) => u.id === id)) || (S.users[0] && S.users[0].id);
  if (!pick) {
    showError('#global-error', 'ماكو حسابات تجريبية. شغّل ./dev.sh seed أول.');
    return;
  }
  sel.value = pick;
  renderReasonChips();
  bindForm();
  bindReview();
  bindPin();
  await switchUser(pick);
}

async function switchUser(id) {
  try {
    localStorage.setItem(STORE_KEY, id);
  } catch { /* ignore */ }
  resetFlow();
  await loadUser(id);
  renderContacts();
  resetFormFields();
  show('form');
}

async function loadUser(id = S.user && S.user.id) {
  try {
    S.user = await api('GET', `/api/users/${encodeURIComponent(id)}`);
    showError('#global-error', '');
  } catch (e) {
    showError('#global-error', errorAr(e));
    return;
  }
  $('#balance').textContent = formatIQD(S.user.balance);
  $('#user-wallet').textContent = S.user.wallet;
  renderRecent();
}

function renderRecent() {
  const list = clear($('#recent-list'));
  for (const r of S.user.recent || []) {
    const incoming = r.direction === 'in';
    list.append(
      el('li', {},
        el('div', {},
          el('div', {}, r.name || r.wallet),
          el('div', { class: 'r-meta' },
            el('span', { class: 'ltr' }, formatTime(r.ts)), ' · ', KIND_AR[r.kind] || r.kind)),
        el('div', { class: incoming ? 'r-in' : 'r-out' },
          el('bdi', {}, `${incoming ? '+' : '−'} ${formatIQD(r.amount)}`))),
    );
  }
  if (!list.childElementCount) list.append(el('li', {}, 'ماكو حركات بعد.'));
}

// ---------- form ----------

function renderContacts() {
  const list = clear($('#contact-list'));
  const contacts = (S.user && S.user.contacts) || [];
  $('#no-contacts').hidden = contacts.length > 0;
  contacts.forEach((c, i) => {
    const id = `contact-${i}`;
    list.append(
      el('label', { class: 'contact-option', for: id },
        el('input', { type: 'radio', name: 'contact', id, value: c.wallet, dataset: { name: c.name } }),
        el('span', { class: 'avatar', 'aria-hidden': 'true' }, (c.name || '?').trim().charAt(0)),
        el('span', { class: 'c-name' }, c.name),
        el('span', { class: 'c-wallet ltr' }, c.wallet)),
    );
  });
  if (!contacts.length) setMode('new');
}

function renderReasonChips() {
  const box = clear($('#reason-chips'));
  for (const r of S.reasons) {
    const chip = el('button', { type: 'button', class: 'chip', 'aria-pressed': 'false', dataset: { code: r.code } }, r.ar);
    chip.addEventListener('click', () => {
      S.reason = S.reason === r.code ? null : r.code;
      syncReasonChips();
    });
    box.append(chip);
  }
}

function syncReasonChips() {
  for (const chip of $('#reason-chips').children) {
    chip.setAttribute('aria-pressed', String(chip.dataset.code === S.reason));
  }
}

function mode() {
  return document.querySelector('input[name="rmode"]:checked').value;
}

function setMode(m) {
  document.querySelector(`input[name="rmode"][value="${m}"]`).checked = true;
  $('#contact-pane').hidden = m !== 'contact';
  $('#new-pane').hidden = m !== 'new';
  updateReasonVisibility();
}

function isSavedWallet(wallet) {
  return ((S.user && S.user.contacts) || []).some((c) => c.wallet === wallet);
}

function updateReasonVisibility() {
  const saved = mode() === 'contact' || isSavedWallet(toDigits($('#r-wallet').value));
  $('#reason-block').hidden = saved;
}

function bindForm() {
  $('#persona').addEventListener('change', (e) => switchUser(e.target.value));
  for (const r of document.querySelectorAll('input[name="rmode"]')) {
    r.addEventListener('change', () => setMode(mode()));
  }
  $('#r-wallet').addEventListener('input', (e) => {
    const digits = toDigits(e.target.value).slice(0, 11);
    if (e.target.value !== digits) e.target.value = digits;
    e.target.removeAttribute('aria-invalid');
    updateReasonVisibility();
  });
  $('#amount').addEventListener('input', (e) => {
    const digits = toDigits(e.target.value).replace(/^0+/, '').slice(0, 12);
    if (e.target.value !== digits) e.target.value = digits;
    e.target.removeAttribute('aria-invalid');
    $('#amount-preview').textContent = digits ? formatIQD(Number(digits)) : '';
  });
  $('#transfer-form').addEventListener('submit', (e) => {
    e.preventDefault();
    submitForm();
  });
  $('#edit-back-btn').addEventListener('click', () => {
    exitEdit();
    returnToDecision();
  });
}

/** Reads and validates the form; returns the transfer fields or null after showing an error. */
function readForm() {
  showError('#form-error', '');
  let wallet;
  let name;
  if (mode() === 'contact') {
    const picked = document.querySelector('input[name="contact"]:checked');
    if (!picked) {
      showError('#form-error', 'اختار جهة اتصال، أو اكتب رقم جديد.');
      return null;
    }
    wallet = picked.value;
    name = picked.dataset.name || '';
  } else {
    wallet = toDigits($('#r-wallet').value);
    name = $('#r-name').value.trim();
    if (!WALLET_RE.test(wallet)) {
      $('#r-wallet').setAttribute('aria-invalid', 'true');
      showError('#form-error', 'رقم المحفظة لازم يكون 11 رقم ويبدي بـ 07، مثل 07812345678.');
      $('#r-wallet').focus();
      return null;
    }
  }
  const amount = Number(toDigits($('#amount').value));
  if (!Number.isSafeInteger(amount) || amount <= 0) {
    $('#amount').setAttribute('aria-invalid', 'true');
    showError('#form-error', 'اكتب المبلغ بالدينار.');
    $('#amount').focus();
    return null;
  }
  const saved = isSavedWallet(wallet);
  return {
    recipient_wallet: wallet,
    recipient_name: name,
    amount,
    note: $('#note').value.trim(),
    reason_code: saved ? null : S.reason,
    call_active: $('#call-active').checked,
  };
}

function setBusy(on, btn, busyText) {
  S.busy = on;
  if (btn) {
    if (on) {
      btn.dataset.label = btn.textContent;
      btn.textContent = busyText;
    } else if (btn.dataset.label) {
      btn.textContent = btn.dataset.label;
    }
    btn.disabled = on;
  }
}

async function submitForm() {
  if (S.busy) return;
  const f = readForm();
  if (!f) return;
  if (S.editing && S.view) return submitEdit(f);

  const fingerprint = JSON.stringify([S.user.id, f]);
  if (!S.submitKey || S.submitFingerprint !== fingerprint) {
    S.submitKey = newKey();
    S.submitFingerprint = fingerprint;
  }
  const btn = $('#submit-btn');
  setBusy(true, btn, 'جاري فحص التحويل…');
  try {
    const view = await api('POST', '/api/transfers', { user_id: S.user.id, ...f }, { idempotencyKey: S.submitKey });
    S.submitKey = null;
    S.submitFingerprint = null;
    handleView(view);
  } catch (e) {
    if (!e.network) {
      S.submitKey = null;
      S.submitFingerprint = null;
    }
    showError('#form-error', errorAr(e));
  } finally {
    setBusy(false, btn);
  }
}

async function submitEdit(f) {
  const t = S.view.transfer;
  const changes = {};
  if (f.amount !== t.amount) changes.amount = f.amount;
  if (f.recipient_wallet !== t.recipient_wallet) changes.recipient_wallet = f.recipient_wallet;
  if (f.recipient_name !== (t.recipient_name || '')) changes.recipient_name = f.recipient_name;
  if (f.note !== (t.note || '')) changes.note = f.note;
  // The API ignores null fields, so clearing a reason is sent as an empty string.
  if ((f.reason_code || null) !== (t.reason_code || null)) changes.reason_code = f.reason_code || '';
  if (f.call_active !== t.call_active) changes.call_active = f.call_active;
  if (!Object.keys(changes).length) {
    exitEdit();
    returnToDecision();
    return;
  }
  const btn = $('#submit-btn');
  setBusy(true, btn, 'جاري الفحص من جديد…');
  try {
    const view = await api('PATCH', `/api/transfers/${t.id}`, { draft_version: t.draft_version, ...changes });
    exitEdit();
    handleView(view, 'انفحص التحويل من جديد بعد التعديل.');
  } catch (e) {
    if (e.code === 'stale_draft') {
      showError('#form-error', errorAr(e));
      await refreshTransfer(true);
    } else if (e.code === 'transfer_cancelled' || e.code === 'transfer_committed') {
      exitEdit();
      showError('#form-error', errorAr(e));
      await refreshTransfer();
    } else {
      showError('#form-error', errorAr(e));
    }
  } finally {
    setBusy(false, btn);
  }
}

function startEdit() {
  const t = S.view && S.view.transfer;
  if (!t || t.status !== 'pending') return;
  S.editing = true;
  resetFormFields();
  const contact = (S.user.contacts || []).find((c) => c.wallet === t.recipient_wallet && c.name === t.recipient_name);
  if (contact) {
    setMode('contact');
    const radio = document.querySelector(`input[name="contact"][value="${CSS.escape(contact.wallet)}"]`);
    if (radio) radio.checked = true;
  } else {
    setMode('new');
    $('#r-wallet').value = t.recipient_wallet;
    $('#r-name').value = t.recipient_name || '';
  }
  $('#amount').value = String(t.amount);
  $('#amount-preview').textContent = formatIQD(t.amount);
  $('#note').value = t.note || '';
  $('#call-active').checked = Boolean(t.call_active);
  S.reason = t.reason_code || null;
  syncReasonChips();
  updateReasonVisibility();
  $('#edit-banner').hidden = false;
  $('#edit-back-btn').hidden = false;
  $('#submit-btn').textContent = 'افحص التحويل من جديد';
  show('form');
}

function exitEdit() {
  S.editing = false;
  $('#edit-banner').hidden = true;
  $('#edit-back-btn').hidden = true;
  $('#submit-btn').textContent = 'متابعة';
  showError('#form-error', '');
}

function resetFormFields() {
  $('#transfer-form').reset();
  $('#amount-preview').textContent = '';
  for (const i of document.querySelectorAll('#transfer-form [aria-invalid]')) i.removeAttribute('aria-invalid');
  S.reason = null;
  syncReasonChips();
  setMode((S.user && S.user.contacts && S.user.contacts.length) ? 'contact' : 'new');
}

function resetFlow() {
  S.token++;
  clearInterval(S.timer);
  S.view = null;
  S.turns = [];
  S.turnInFlight = false;
  S.submitKey = null;
  S.submitFingerprint = null;
  exitEdit();
  resetFormFields();
  for (const sel of ['#review-error', '#pin-error']) showError(sel, '');
}

// ---------- assessment result ----------

function handleView(view, notice = '') {
  S.view = view;
  S.token++;
  S.turns = view.coach_turns || [];
  S.turnInFlight = false;
  S.confirmKey = newKey();
  const a = view.assessment;
  S.unlockAt = Date.now() + (a ? Number(a.proceed_unlock_in_s) || 0 : 0) * 1000;
  const status = view.transfer.status;
  if (status === 'committed') return showReceipt(view);
  if (status === 'cancelled') return showCancelled();
  if (!a || a.tier === 'pass') {
    showPin(notice);
  } else {
    renderReview(notice);
    show('review');
  }
}

function returnToDecision() {
  if (!S.view) return show('form');
  const a = S.view.assessment;
  if (!a || a.tier === 'pass') showPin('');
  else show('review');
}

async function refreshTransfer(enterEdit = false) {
  if (!S.view) return;
  try {
    const view = await api('GET', `/api/transfers/${S.view.transfer.id}`);
    handleView(view, 'التحويل تغيّر وانفحص من جديد، وهذا آخر تنبيه إله.');
    if (enterEdit && view.transfer.status === 'pending') startEdit();
  } catch (e) {
    showError('#global-error', errorAr(e));
  }
}

function summaryCard(t) {
  return el('div', { class: 'summary-card' },
    el('div', { class: 's-amount' }, el('bdi', {}, formatIQD(t.amount))),
    el('div', {}, 'إلى ', el('strong', {}, t.recipient_name || 'بدون اسم'), ' ', el('span', { class: 'ltr muted' }, t.recipient_wallet)),
    t.note ? el('div', { class: 's-line' }, 'ملاحظة: ', el('bdi', {}, t.note)) : null,
    t.call_active ? el('div', { class: 's-line' }, '📞 على مكالمة (محاكاة)') : null);
}

function renderReview(notice) {
  const { transfer: t, assessment: a, warning } = S.view;
  setNotice('#review-notice', notice);
  showError('#review-error', '');
  clear($('#review-summary')).append(summaryCard(t));
  const card = $('#warn-card');
  card.className = `warn-card warn-${a.tier}`;
  $('#warn-title').textContent = a.tier === 'coach' ? '🛑 لحظة قبل ما تدز' : '⚠️ انتبه قبل لا تكمل';
  $('#warn-text').textContent = warning ? warning.text : '';
  $('#cancel-btn').disabled = false;

  const chat = clear($('#chat'));
  chat.hidden = a.tier !== 'coach';
  if (a.tier === 'coach') {
    S.turns.forEach((turn, i) => {
      const prev = S.turns[i - 1];
      if (prev && prev.chosen_option) {
        const opt = (prev.options || []).find((o) => o.id === prev.chosen_option);
        if (opt) chat.append(userBubble(opt.label));
      }
      const live = i === S.turns.length - 1 && !turn.chosen_option;
      chat.append(renderTurn(turn, { onOption: live ? chooseOption : null }));
    });
    const last = S.turns[S.turns.length - 1];
    if (!last) requestTurn(null);
    else if (last.chosen_option && !last.final && S.turns.length < 3) requestTurn(last.chosen_option);
  }
  startCountdown();
}

function startCountdown() {
  clearInterval(S.timer);
  const btn = $('#proceed-btn');
  const status = $('#countdown-status');
  const first = Math.ceil((S.unlockAt - Date.now()) / 1000);
  status.textContent = first > 0 ? `زر "أكمل التحويل" يتفعّل بعد ${first} ثانية.` : '';
  const tick = () => {
    const left = Math.ceil((S.unlockAt - Date.now()) / 1000);
    if (left > 0) {
      btn.disabled = true;
      btn.textContent = `أكمل التحويل (${left})`;
    } else {
      btn.disabled = false;
      btn.textContent = 'أكمل التحويل';
      clearInterval(S.timer);
      if (first > 0) status.textContent = 'تگدر هسه تكمل التحويل إذا انت متأكد.';
    }
  };
  tick();
  if (first > 0) S.timer = setInterval(tick, 250);
}

function chooseOption(option) {
  return requestTurn(option.id, option.label);
}

async function requestTurn(optionId, optionLabel) {
  if (!S.view || S.turnInFlight) return;
  const token = S.token;
  const t = S.view.transfer;
  const chat = $('#chat');
  showError('#review-error', '');
  S.turnInFlight = true;
  const mine = optionLabel ? userBubble(optionLabel) : null;
  if (mine) chat.append(mine);
  const typing = typingBubble('المساعد يكتب…');
  chat.append(typing);
  typing.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  try {
    const turn = await api('POST', `/api/transfers/${t.id}/coach`, {
      draft_version: t.draft_version,
      chosen_option_id: optionId || null,
    });
    if (token !== S.token) return;
    typing.remove();
    const last = S.turns[S.turns.length - 1];
    if (last && optionId) last.chosen_option = optionId;
    S.turns.push(turn);
    const bubble = renderTurn(turn, { onOption: turn.final ? null : chooseOption });
    chat.append(bubble);
    bubble.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch (e) {
    typing.remove();
    if (token !== S.token) return;
    if (mine) mine.remove();
    if (['stale_draft', 'chat_finished', 'turn_already_generated', 'transfer_cancelled', 'transfer_committed'].includes(e.code)) {
      S.turnInFlight = false;
      await refreshTransfer();
      return;
    }
    const retry = optionId
      ? null
      : el('button', { type: 'button', class: 'btn-secondary', style: 'margin-inline-start:.5rem', onclick: () => requestTurn(null) }, 'حاول مرة ثانية');
    showError('#review-error', `${errorAr(e)} التحذير فوق يبقى صالح.`, retry);
    if (optionId) throw e; // lets the option buttons re-enable
  } finally {
    if (token === S.token) S.turnInFlight = false;
  }
}

function bindReview() {
  $('#cancel-btn').addEventListener('click', cancelTransfer);
  $('#proceed-btn').addEventListener('click', () => {
    if (Date.now() < S.unlockAt) return;
    showPin('');
  });
  for (const b of document.querySelectorAll('.js-edit')) b.addEventListener('click', startEdit);
}

async function cancelTransfer() {
  if (!S.view || S.busy) return;
  const t = S.view.transfer;
  const btns = [$('#cancel-btn'), $('#pin-cancel')];
  btns.forEach((b) => (b.disabled = true));
  S.busy = true;
  try {
    await api('POST', `/api/transfers/${t.id}/cancel`, { draft_version: t.draft_version });
    S.token++;
    clearInterval(S.timer);
    showCancelled();
  } catch (e) {
    const box = $('#screen-pin').hidden ? '#review-error' : '#pin-error';
    showError(box, errorAr(e));
    if (e.code === 'transfer_committed') await refreshTransfer();
  } finally {
    S.busy = false;
    btns.forEach((b) => (b.disabled = false));
  }
}

// ---------- PIN ----------

function showPin(notice) {
  const t = S.view.transfer;
  const a = S.view.assessment;
  setNotice('#pin-notice', notice);
  clear($('#pin-summary')).append(summaryCard(t));
  showError('#pin-error', '');
  clear($('#pin-extra')).hidden = true;
  $('#pin').value = '';
  $('#pin-back').hidden = !a || a.tier === 'pass';
  show('pin');
  $('#pin').focus();
}

function bindPin() {
  $('#pin').addEventListener('input', (e) => {
    const d = toDigits(e.target.value).slice(0, 4);
    if (e.target.value !== d) e.target.value = d;
  });
  $('#pin-form').addEventListener('submit', (e) => {
    e.preventDefault();
    confirmTransfer();
  });
  $('#pin-cancel').addEventListener('click', cancelTransfer);
  $('#pin-back').addEventListener('click', () => show('review'));
  $('#new-transfer').addEventListener('click', () => {
    resetFlow();
    show('form');
  });
}

function pinExtra(...buttons) {
  const box = clear($('#pin-extra'));
  box.append(...buttons);
  box.hidden = buttons.length === 0;
}

async function confirmTransfer() {
  if (S.busy || !S.view) return;
  const pin = toDigits($('#pin').value);
  showError('#pin-error', '');
  pinExtra();
  if (pin.length !== 4) {
    showError('#pin-error', 'الرمز السري لازم يكون 4 أرقام.');
    $('#pin').focus();
    return;
  }
  const t = S.view.transfer;
  const btn = $('#pin-submit');
  setBusy(true, btn, 'جاري الإرسال…');
  try {
    const view = await api('POST', `/api/transfers/${t.id}/confirm`, { draft_version: t.draft_version, pin },
      { idempotencyKey: S.confirmKey });
    S.token++;
    await showReceipt(view);
  } catch (e) {
    $('#pin').value = '';
    showError('#pin-error', errorAr(e));
    if (e.code === 'bad_pin') {
      $('#pin').focus();
    } else if (e.code === 'proceed_locked') {
      S.unlockAt = Date.now() + (Number(e.data.seconds) || 0) * 1000;
      startCountdown();
      pinExtra(el('button', { type: 'button', class: 'btn-secondary', onclick: () => show('review') }, 'رجوع للتنبيه'));
    } else if (e.code === 'stale_draft' || e.code === 'not_assessed') {
      pinExtra(el('button', { type: 'button', onclick: () => refreshTransfer() }, 'شوف التنبيه الجديد'));
    } else if (e.code === 'insufficient_funds') {
      await loadUser();
      pinExtra(el('button', { type: 'button', class: 'btn-secondary', onclick: startEdit }, 'عدّل المبلغ'));
    } else if (e.code === 'already_committed') {
      pinExtra(el('button', { type: 'button', onclick: () => refreshTransfer() }, 'شوف الوصل'));
    } else if (e.code === 'transfer_cancelled') {
      pinExtra(el('button', { type: 'button', onclick: () => { resetFlow(); show('form'); } }, 'تحويل جديد'));
    }
  } finally {
    setBusy(false, btn);
  }
}

// ---------- outcome ----------

async function showReceipt(view) {
  clearInterval(S.timer);
  S.view = view;
  const t = view.transfer;
  $('#done-icon').textContent = '✓';
  $('#done-icon').className = 'tick';
  $('#done-title').textContent = 'تم التحويل';
  $('#done-text').textContent = view.replay ? 'هالتحويل انرسل من قبل، وما انسحب مرتين.' : 'انرسلت الفلوس بنجاح.';
  const dl = clear($('#done-details'));
  const row = (k, v) => [el('dt', {}, k), el('dd', {}, v)];
  append(dl, [
    row('المبلغ', el('bdi', {}, formatIQD(t.amount))),
    row('المستلم', t.recipient_name || '—'),
    row('رقم المحفظة', el('span', { class: 'ltr' }, t.recipient_wallet)),
    t.note ? row('الملاحظة', el('bdi', {}, t.note)) : [],
    row('الوقت', el('span', { class: 'ltr' }, formatTime(t.committed_at))),
    row('رقم العملية', el('span', { class: 'ltr' }, `#${t.id}`)),
  ]);
  show('done');
  await loadUser();
  if (S.user) append(dl, row('رصيدك هسه', el('bdi', {}, formatIQD(S.user.balance))));
}

function showCancelled() {
  $('#done-icon').textContent = '✕';
  $('#done-icon').className = 'tick cross';
  $('#done-title').textContent = 'انلغى التحويل';
  $('#done-text').textContent = 'انلغى التحويل، ما انسحب شي من رصيدك';
  clear($('#done-details'));
  show('done');
  loadUser();
}

init();
