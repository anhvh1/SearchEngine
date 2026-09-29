'use strict';
const $ = id => document.getElementById(id);
const PAGE = 50;
let token = '', tokenMode = false, offset = 0, lastText = '', caps = {}, recorder = null, recognizer = null;
const EXAMPLES = ['Chuyển động hôm nay', 'Camera mất kết nối tuần này', 'Nhận diện khuôn mặt hôm qua', 'Xâm nhập sau 22h tối qua', 'Cảnh báo 7 ngày qua'];

async function api(path, body, method) {
  const headers = token ? {Authorization: `Bearer ${token}`} : {};
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api${path}`, {method: method || (body === undefined ? 'GET' : 'POST'), headers,
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body)});
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = {detail: response.ok ? text : `Máy chủ gặp lỗi (HTTP ${response.status}).`}; }
  if (response.status === 401 && token && path !== '/session') { signOut(); throw new Error('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
function node(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
const notice = text => { $('notice').textContent = text; $('notice').classList.toggle('show', !!text); if (text) setTimeout(() => { if ($('notice').textContent === text) notice(''); }, 6000); };
// Vietnamese labels for common Milestone event names; unknown names are shown as Milestone sends them.
const VI = {'motion detected': 'Phát hiện chuyển động', 'motion stopped': 'Hết chuyển động', 'not responding': 'Mất kết nối',
  'responding': 'Kết nối lại', 'server not responding': 'Máy chủ mất kết nối', 'server responding': 'Máy chủ kết nối lại',
  'intruderhuman': 'Phát hiện người xâm nhập', 'intruder': 'Xâm nhập', 'registered face detection': 'Nhận diện khuôn mặt đã đăng ký',
  'database deleting recordings before set retention size': 'Ổ lưu trữ đầy – xóa bản ghi sớm', 'communication error': 'Lỗi kết nối',
  'input activated': 'Đầu vào kích hoạt', 'output activated': 'Đầu ra kích hoạt', 'tampering': 'Phá hoại camera'};
const eventLabel = name => VI[(name || '').trim().toLowerCase()] || name;
const when = value => new Date(value).toLocaleString('vi-VN', {hour: '2-digit', minute: '2-digit', second: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric'});

/* ---------- sign-in ---------- */
async function enter() {
  const me = await api('/me');
  $('identity').textContent = me.name;
  document.querySelectorAll('.admin-only').forEach(n => n.hidden = !me.roles.includes('admin'));
  $('login').hidden = true; $('shell').hidden = false; $('login-error').textContent = '';
  caps = await api('/capabilities').catch(() => ({}));
  setupMic(); $('photo').hidden = !caps.vision;
  await refreshStatus();
  $('ask-input').focus();
}
function signOut() {
  const old = token; token = '';
  if (old) fetch('/api/session', {method: 'DELETE', headers: {Authorization: `Bearer ${old}`}}).catch(() => {});
  $('shell').hidden = true; $('login').hidden = false; $('answer').hidden = true; $('results').replaceChildren(); $('detail').hidden = true;
  stopListening();
}
$('use-token').onclick = () => { tokenMode = !tokenMode; $('token-login').hidden = !tokenMode; $('password-login').hidden = tokenMode; $('use-token').textContent = tokenMode ? 'Dùng tài khoản Milestone' : 'Dùng mã truy cập'; };
$('login-form').onsubmit = async e => {
  e.preventDefault();
  try {
    if (tokenMode) token = $('token').value.trim();
    else { token = ''; token = (await api('/session', {username: $('username').value.trim(), password: $('password').value})).token; }
    $('password').value = ''; $('token').value = ''; await enter();
  } catch (err) { token = ''; $('login-error').textContent = err.message; }
};
if (window.chrome?.webview) {
  // Smart Client and Management Client pass the signed-in Milestone user's token; no second login.
  $('auto-login').hidden = false;
  window.chrome.webview.addEventListener('message', async e => {
    if (e.data?.type !== 'milestone-token') return;
    try { const wasOut = $('shell').hidden; token = ''; token = (await api('/session', {token: e.data.token})).token; if (wasOut) await enter(); }
    catch (err) { $('login-error').textContent = err.message; }
    finally { $('auto-login').hidden = true; }
  });
}
$('logout').onclick = signOut;

async function refreshStatus() {
  try {
    const s = await api('/summary');
    $('data-status').textContent = s.records ? `${s.records.toLocaleString('vi-VN')} sự kiện · mới nhất ${s.last_received ? new Date(s.last_received).toLocaleString('vi-VN', {hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit'}) : '—'}` : 'Chưa nhận sự kiện nào';
  } catch { /* status line is informative only */ }
}
setInterval(() => { if (token) refreshStatus(); }, 60000);

/* ---------- search ---------- */
$('examples').append(...EXAMPLES.map(text => { const b = node('button', text, 'example'); b.type = 'button'; b.onclick = () => { $('ask-input').value = text; ask(text); }; return b; }));
$('ask-input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('ask-form').requestSubmit(); } });
$('ask-input').addEventListener('input', () => { const t = $('ask-input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 160) + 'px'; });
$('ask-form').onsubmit = e => { e.preventDefault(); ask($('ask-input').value.trim()); };
$('more').onclick = () => ask(lastText, true);

async function ask(text, more = false) {
  offset = more ? offset + PAGE : 0; lastText = text;
  $('answer').hidden = false; $('examples').hidden = true;
  if (!more) { $('summary').textContent = 'Đang tìm…'; $('chips').replaceChildren(); $('results').replaceChildren(); }
  try {
    const data = await api('/ask', {text, tz_offset_minutes: -new Date().getTimezoneOffset(), limit: PAGE, offset});
    render(data, more);
  } catch (err) { $('summary').textContent = err.message; }
}
function render(data, more) {
  if (!more) {
    const parts = [`Tìm thấy ${data.total.toLocaleString('vi-VN')} lần xuất hiện`];
    const people = data.facets?.people?.slice(0, 3).map(f => `${f.name} (${f.count})`);
    if (people?.length) parts.push(people.join(', '));
    const top = people?.length ? null : data.facets?.event_types?.slice(0, 3).map(f => `${eventLabel(f.name)} (${f.count})`);
    const cams = data.facets?.sources?.length;
    if (top?.length) parts.push(top.join(', '));
    if (cams) parts.push(`${cams >= 8 ? '8+' : cams} camera/thiết bị`);
    $('summary').textContent = data.total ? parts.join(' · ') : 'Không có sự kiện phù hợp. Thử khoảng thời gian rộng hơn hoặc bớt điều kiện.';
    $('chips').replaceChildren(...data.understood.map(c => node('span', c.label, 'chip chip-' + c.type)));
    if (data.described) $('chips').prepend(node('span', `Ảnh: ${data.described}`, 'chip chip-muted'));
    if (data.ignored_words) $('chips').append(node('span', `Bỏ qua “${data.ignored_words}” vì không khớp sự kiện nào`, 'chip chip-muted'));
  }
  for (const item of data.items) $('results').append(row(item));
  $('more').hidden = offset + PAGE >= data.total;
}
function row(item) {
  const b = node('button', undefined, 'result'); b.type = 'button';
  const time = new Date(item.occurred_at);
  const t = node('div', undefined, 'result-time'); t.append(node('b', time.toLocaleTimeString('vi-VN', {hour: '2-digit', minute: '2-digit'})), node('small', time.toLocaleDateString('vi-VN')));
  const body = node('div', undefined, 'result-body');
  const title = node('div', eventLabel(item.event_label || item.event_name || item.message) || item.event_type, 'result-title');
  if (item.kind === 'alarm' || item.episode?.alarms) title.prepend(node('span', 'ALARM', 'pill'));
  const tags = node('div', undefined, 'result-tags');
  for (const name of item.facts?.persons || []) tags.append(node('span', name, 'tag tag-person'));
  for (const name of [...(item.facts?.plates || []), ...(item.facts?.watchlists || [])]) tags.append(node('span', name, 'tag tag-person'));
  if (item.facts?.identity_status === 'unknown') tags.append(node('span', 'Người lạ', 'tag'));
  for (const a of item.facts?.action || []) tags.append(node('span', a, 'tag'));
  if (item.episode?.count > 1) tags.append(node('span', `×${item.episode.count}` + ((new Date(item.episode.last) - new Date(item.episode.first)) >= 5000 ? ` trong ${span(item.episode)}` : ''), 'tag tag-count'));
  body.append(title, node('div', item.source_name || item.source_id, 'result-source'), tags);
  b.append(t);
  if (item.has_image || item.episode?.image) b.append(thumb(item.key, 'thumb'));
  b.append(body);
  b.onclick = () => { document.querySelectorAll('.result.selected').forEach(n => n.classList.remove('selected')); b.classList.add('selected'); detail(item); };
  return b;
}
function span(ep) { const s = Math.round((new Date(ep.last) - new Date(ep.first)) / 1000); return s < 60 ? `${s} giây` : `${Math.round(s / 60)} phút`; }
function thumb(key, className) {
  // Images need the bearer token, so they are fetched and shown as object URLs.
  const img = node('img', undefined, className); img.alt = 'Ảnh chụp sự kiện'; img.loading = 'lazy';
  fetch(`/api/records/${encodeURIComponent(key)}/image`, {headers: {Authorization: `Bearer ${token}`}})
    .then(r => r.ok ? r.blob() : Promise.reject()).then(b => { img.src = URL.createObjectURL(b); }).catch(() => img.remove());
  return img;
}
function detail(item) {
  const root = $('detail-body'); root.replaceChildren(node('p', item.kind === 'alarm' ? 'ALARM' : 'SỰ KIỆN', 'eyebrow'), node('h2', eventLabel(item.event_label || item.event_name || item.message) || item.event_type));
  if (item.has_image || item.episode?.image) root.append(thumb(item.key));
  const dl = node('dl');
  for (const [label, value] of [['Thời điểm', when(item.occurred_at)], ['Camera / thiết bị', item.source_name || item.source_id], ['Tên trong Milestone', item.event_name], ['Nội dung', item.message !== item.event_name ? item.message : ''],
    ['Người', (item.facts?.persons || []).join(', ')], ['Hành động', (item.facts?.action || []).join(', ')],
    ['Nhận diện', item.facts?.identity_status === 'unknown' ? 'Người lạ' : item.facts?.identity_status === 'known' ? 'Người đã đăng ký' : ''],
    ['Số lần lặp', item.episode?.count > 1 ? `${item.episode.count} lần, ${when(item.episode.first)} → ${when(item.episode.last)}` : ''],
    ['Mức ưu tiên', item.priority], ['Trạng thái', item.state], ['Địa điểm', item.location], ['Mô tả', item.description]]) {
    if (value) dl.append(node('dt', label), node('dd', value));
  }
  root.append(dl);
  const related = node('div', undefined, 'related');
  for (const name of item.facts?.persons || []) { const b = node('button', `Các lần khác của ${name}`); b.type = 'button'; b.onclick = () => { $('ask-input').value = name; ask(name); }; related.append(b); }
  if (related.children.length) root.append(related);
  if (item.camera_id && window.chrome?.webview) {
    const play = node('button', '▶ Xem video tại thời điểm này', 'primary'); play.type = 'button';
    play.onclick = () => window.chrome.webview.postMessage({action: 'playback', camera_id: item.camera_id, time: item.occurred_at});
    root.append(play);
  } else if (item.camera_id) root.append(node('p', 'Mở trong Smart Client để xem video tại thời điểm này.', 'muted'));
  const raw = node('details'); raw.append(node('summary', 'Dữ liệu gốc'), node('pre', JSON.stringify(item.payload, null, 2))); root.append(raw);
  $('detail').hidden = false;
}
$('detail-close').onclick = () => { $('detail').hidden = true; document.querySelectorAll('.result.selected').forEach(n => n.classList.remove('selected')); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('detail-close').click(); });

/* ---------- photo ---------- */
$('photo').onclick = () => $('photo-input').click();
$('photo-input').onchange = async () => {
  const file = $('photo-input').files[0]; $('photo-input').value = ''; if (!file) return;
  $('answer').hidden = false; $('examples').hidden = true; $('summary').textContent = 'Đang phân tích ảnh…'; $('results').replaceChildren(); $('chips').replaceChildren();
  try { const form = new FormData(); form.append('file', file); offset = 0; const data = await api(`/ask/image?tz_offset_minutes=${-new Date().getTimezoneOffset()}`, form); lastText = data.described; $('ask-input').value = data.described; render(data, false); }
  catch (err) { $('summary').textContent = err.message; }
};

/* ---------- voice ---------- */
const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
function setupMic() {
  $('mic').hidden = !(caps.voice || Speech);
}
function listening(on, text) { $('mic').classList.toggle('listening', on); $('voice-status').textContent = text || ''; }
function stopListening() { if (recorder?.state === 'recording') recorder.stop(); if (recognizer) recognizer.stop(); }
$('mic').onclick = async () => {
  if (recorder?.state === 'recording' || recognizer) { stopListening(); return; }
  try {
    if (caps.voice) await recordOnServer(); else listenInBrowser();
  } catch (err) { listening(false); notice(err.message); }
};
async function recordOnServer() {
  // Audio stays on the local server; transcription uses the configured on-premises model.
  const stream = await navigator.mediaDevices.getUserMedia({audio: true});
  const chunks = []; recorder = new MediaRecorder(stream);
  recorder.ondataavailable = e => chunks.push(e.data);
  recorder.onstop = async () => {
    stream.getTracks().forEach(t => t.stop()); listening(false, 'Đang chuyển giọng nói thành chữ…');
    try {
      const form = new FormData(); form.append('file', new Blob(chunks, {type: recorder.mimeType}), 'voice.webm');
      const data = await api('/transcribe', form); listening(false);
      $('ask-input').value = data.transcript; if (data.transcript.trim()) ask(data.transcript.trim());
    } catch (err) { listening(false); notice(err.message); }
  };
  recorder.start(); listening(true, 'Đang nghe… bấm micro lần nữa để dừng');
  setTimeout(() => { if (recorder?.state === 'recording') recorder.stop(); }, 30000);
}
function listenInBrowser() {
  recognizer = new Speech(); recognizer.lang = 'vi-VN'; recognizer.interimResults = true; recognizer.continuous = false;
  let finalText = '';
  recognizer.onresult = e => { const text = [...e.results].map(r => r[0].transcript).join(' '); $('ask-input').value = text; if (e.results[e.results.length - 1].isFinal) finalText = text; };
  recognizer.onerror = e => notice(e.error === 'not-allowed' ? 'Chưa được phép dùng micro.' : 'Không nhận được giọng nói. Thử lại.');
  recognizer.onend = () => { recognizer = null; listening(false); if (finalText.trim()) ask(finalText.trim()); };
  recognizer.start(); listening(true, 'Đang nghe… nói câu hỏi của bạn');
}

/* ---------- administration: one status page ---------- */
$('admin-toggle').onclick = async () => { $('search-page').hidden = true; $('admin-page').hidden = false; $('detail').hidden = true; await operations().catch(err => notice(err.message)); };
$('admin-close').onclick = () => { $('admin-page').hidden = true; $('search-page').hidden = false; };
function health(label, value, level) { const c = node('div', undefined, 'health-card' + (level ? ' ' + level : '')); c.append(node('span', label, 'muted'), node('b', value)); return c; }
async function operations() {
  const [ops, connections] = await Promise.all([api('/operations'), api('/settings/milestone')]);
  const last = ops.last_received ? new Date(ops.last_received) : null, stale = !last || Date.now() - last > 3600e3;
  $('health').replaceChildren(
    health('Sự kiện đã lưu', ops.records.toLocaleString('vi-VN')),
    health('Nhận gần nhất', last ? last.toLocaleString('vi-VN') : 'Chưa có', stale ? 'warn' : ''),
    health('Đang xử lý', String(ops.pending), ops.pending > 1000 ? 'warn' : ''),
    health('Lỗi', String(ops.failed + (ops.worker_error ? 1 : 0)), ops.failed || ops.worker_error ? 'bad' : ''),
    health('Tìm theo ngữ nghĩa', caps.embedding ? 'Bật' : 'Chưa cài model'),
    health('Nhận giọng nói', caps.voice ? 'Trên máy chủ' : (Speech ? 'Trình duyệt' : 'Không có')));
  if (stale) $('health').append(health('Cần kiểm tra', 'Hơn 1 giờ chưa nhận sự kiện mới. Kiểm tra plugin Event Server.', 'warn'));
  $('conn-site').replaceChildren(...connections.map(c => { const o = node('option', c.site_id); o.value = c.site_id; return o; }));
  const fill = () => { const c = connections.find(x => x.site_id === $('conn-site').value) || connections[0] || {};
    $('conn-url').value = c.url || ''; $('conn-user').value = c.username || ''; $('conn-verify').checked = !!c.verify_certificates;
    $('conn-status').textContent = c.has_credentials ? 'Đã kết nối.' : c.url ? 'Đã tự phát hiện máy chủ. Nhập tài khoản nếu cần nạp lịch sử alarm.' : ''; };
  $('conn-site').onchange = fill; fill();
  $('operations-output').textContent = JSON.stringify(ops, null, 2);
  $('rules-suggest').hidden = !caps.chat;
  await showRules();
}
const ROLE = {person: 'người', person_code: 'mã người', plate: 'biển số', place: 'vị trí', action: 'hành động', number: 'số',
  duration_minutes: 'số phút', vehicle: 'phương tiện', color: 'màu', gender: 'giới tính', age: 'tuổi', watchlist: 'watchlist',
  code: 'mã', container: 'container', card: 'thẻ', door: 'cửa', value: 'giá trị'};
async function showRules() {
  const rules = await api('/rules');
  $('rules').replaceChildren(...(rules.length ? [] : [node('p', 'Chưa có loại sự kiện nào cần tách giá trị.', 'muted')]));
  for (const r of rules) {
    let i = 0; const readable = r.template.replace(/\{\}/g, () => `[${ROLE[r.roles[i++]] || 'giá trị'}]`);
    const box = node('div', undefined, `rule ${r.status}`); const text = node('div');
    text.append(node('code', readable), node('p', `${r.source === 'llm' ? 'AI đề xuất' : 'Tự học'} · ví dụ: ${(r.samples || [])[0] || ''}`, 'muted'));
    const b = node('button', r.status === 'active' ? 'Tắt' : r.status === 'proposed' ? 'Duyệt' : 'Bật'); b.type = 'button';
    b.onclick = async () => { try { for (const m of r.members) await api('/rules/status', {...m, status: r.status === 'active' ? 'disabled' : 'active'}); await showRules(); } catch (err) { notice(err.message); } };
    box.append(text, b); $('rules').append(box);
  }
}
$('rules-learn').onclick = async () => { try { const r = await api('/rules/learn', {}); notice(r.changed.length ? `Đã học ${r.changed.length} loại sự kiện mới.` : 'Không có gì mới để học.'); await showRules(); } catch (err) { notice(err.message); } };
$('rules-suggest').onclick = async () => { try { notice('AI đang đọc các loại sự kiện…'); const r = await api('/rules/suggest', {}); notice(`AI đề xuất ${r.proposals.length} quy tắc. Duyệt nếu đúng.`); await showRules(); } catch (err) { notice(err.message); } };
$('connection-form').onsubmit = async e => {
  e.preventDefault(); $('conn-status').textContent = 'Đang kiểm tra kết nối…';
  try {
    await api('/settings/milestone', {site_id: $('conn-site').value || 'main', url: $('conn-url').value.trim(), username: $('conn-user').value.trim(),
      password: $('conn-password').value, verify_certificates: $('conn-verify').checked}, 'PUT');
    $('conn-password').value = ''; await operations(); $('conn-status').textContent = 'Đã lưu.';
  } catch (err) { $('conn-status').textContent = err.message; }
};
