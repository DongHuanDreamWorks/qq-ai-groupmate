/* QQ 群友控制台 —— 无框架前端 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '-';
const fmtNum = (n) => (n ?? 0).toLocaleString('en-US');

let STATE = { settings: null };

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) { location.href = '/login'; throw new Error('unauthorized'); }
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
  return data;
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 2600);
}

/* ------------------------------------------------------------ form engine */

const FIELDS = {
  persona: [
    { key: 'name', label: '名字', type: 'text', hint: '群里显示的名字，也是它自称的名字' },
    { key: 'identity', label: '身份设定', type: 'text', hint: '一句话：年龄/职业/性格底色' },
    { key: 'system_prompt', label: '系统提示词', type: 'textarea', rows: 12, hint: '支持 {name} {identity} 占位符。这是人设的核心' },
    { key: 'backstory', label: '背景故事', type: 'textarea', rows: 8, hint: '一行一条，会注入到系统提示里，让它在没上下文时也有东西可说' },
    { key: 'city', label: '常住城市', type: 'text', hint: '天气和本地新闻按这个城市来' },
    { key: 'lore_enabled', label: '每天自动更新生活碎片', type: 'bool' },
    { key: 'lore_hour', label: '生活碎片更新时间', type: 'text', hint: 'HH:MM，默认 12:00' },
    { key: 'lore_prompt', label: '生活碎片提示词（固定模板，它不能改）', type: 'textarea', rows: 10, hint: '占位符：{name} {city} {date} {weather} {news}' },
    { key: 'lore', label: '当前生活碎片（每天 12:00 覆盖，可手动改）', type: 'lines', rows: 10, hint: '一行一条。每次随机抽 3 条给它当话题素材' },
    { key: 'style', label: '说话风格', type: 'text' },
    { key: 'taboos', label: '禁忌话题', type: 'lines', hint: '一行一个' },
    { key: 'disclose_ai', label: 'AI 身份策略', type: 'text', hint: '例：被直接问才承认' },
  ],
  context: [
    { key: 'max_turns', label: '上下文条数', type: 'number', hint: '每次请求带多少条最近的群消息（建议 20-40）' },
    { key: 'max_chars', label: '上下文字符上限', type: 'number' },
    { key: 'summary_enabled', label: '启用长期摘要', type: 'bool', hint: '聊天过长时用摘要替代早期消息' },
    { key: 'summary_every', label: '每 N 条消息做一次摘要', type: 'number' },
    { key: 'keep_after_summary', label: '摘要后保留的最近条数', type: 'number' },
    { key: 'include_images', label: '把图片传给模型', type: 'bool' },
    { key: 'max_images', label: '每次最多带几张图', type: 'number' },
    { key: 'include_faces', label: '识别 QQ 表情', type: 'bool' },
    { key: 'history_window_min', label: '上下文回溯窗口（分钟）', type: 'number' },
    { key: 'rotate_enabled', label: '每天午夜归档上下文', type: 'bool', hint: '归档后清空聊天记录，留下一段总结给第二天' },
    { key: 'rotate_threshold', label: '超过多少条才归档', type: 'number' },
    { key: 'rotate_hour', label: '归档时间', type: 'text', hint: 'HH:MM，默认 00:00' },
  ],
  trigger: [
    { key: 'reply_to_at', label: '被 @ 时回复', type: 'bool' },
    { key: 'reply_to_private', label: '私聊时回复', type: 'bool' },
    { key: 'prefix', label: '唤醒前缀', type: 'text', hint: '留空表示不需要前缀' },
    { key: 'keywords', label: '触发关键词', type: 'lines', hint: '命中任意一个就回复' },
    { key: 'probability', label: '随机插话概率（没配时段时用）', type: 'range', min: 0, max: 1, step: 0.01, hint: '0.08 = 8% 的普通消息会被它接话' },
    { key: 'schedule', label: '分时段回复率（JSON）', type: 'json', rows: 8, hint: '默认 12-20 点 100%，8-12 点 50%，20-24 点 25%，其余 0%' },
    { key: 'batch_enabled', label: '合并回复（把连发的几条当一轮）', type: 'bool' },
    { key: 'batch_window', label: '合并窗口（秒）', type: 'number', hint: '这几秒内的消息合成一次回复' },
    { key: 'single_reply_ratio', label: '单条回复占比', type: 'number', hint: '0.25 = 25% 只回单条，其余合并回复' },
    { key: 'cooldown_sec', label: '同群冷却（秒）', type: 'number' },
    { key: 'max_per_10min', label: '每 10 分钟最多回复次数', type: 'number' },
    { key: 'min_chars', label: '最短触发字数', type: 'number' },
    { key: 'quiet_hours', label: '安静时段', type: 'text', hint: '如 23:30-08:00，留空表示不启用' },
    { key: 'whitelist_groups', label: '群白名单', type: 'lines', hint: '留空 = 所有群；填了只在这些群工作' },
    { key: 'blacklist_users', label: '用户黑名单', type: 'lines', hint: '填 uid，如 qq:123456' },
    { key: 'ignore_prefixes', label: '忽略的消息前缀', type: 'lines', hint: '如 / #' },
    { key: 'quote_reply', label: '引用回复', type: 'bool' },
    { key: 'typing_simulation', label: '模拟打字延迟', type: 'bool' },
    { key: 'ignore_url_only', label: '忽略纯链接消息', type: 'bool' },
  ],
  model: [
    { key: 'base_url', label: 'API 地址', type: 'text' },
    { key: 'model', label: '模型名', type: 'text', hint: 'deepseek-flash / deepseek-v4-pro' },
    { key: 'api_key', label: 'API Key', type: 'password', hint: '留空表示不修改' },
    { key: 'temperature', label: '温度', type: 'range', min: 0, max: 2, step: 0.05 },
    { key: 'max_tokens', label: '最大输出 tokens', type: 'number' },
    { key: 'thinking', label: '深度思考模式', type: 'bool', hint: '开会更慢更贵，群聊建议关' },
    { key: 'vision', label: '视觉能力', type: 'bool' },
    { key: 'timeout', label: '超时（秒）', type: 'number' },
  ],
  memory: [
    { key: 'enabled', label: '启用长期记忆', type: 'bool' },
    { key: 'extract_every', label: '每 N 条消息更新一次印象', type: 'number' },
    { key: 'max_facts', label: '每人最多记住多少条事实', type: 'number' },
    { key: 'recall_min_familiarity', label: '最低熟悉度才注入记忆', type: 'number' },
    { key: 'summary_max_chars', label: '印象摘要字数上限', type: 'number' },
  ],
  safety: [
    { key: 'blocked_words', label: '屏蔽词', type: 'lines' },
    { key: 'max_reply_chars', label: '单条回复字数上限', type: 'number' },
    { key: 'max_bubbles', label: '最多拆成几条消息', type: 'number' },
  ],
  tools: [
    { key: 'image_enabled', label: '允许自己画图', type: 'bool', hint: '开启后模型可调用 draw_image，生成图片发到群里' },
    { key: 'image_style', label: '默认画风', type: 'text', hint: '会拼在提示词后面，统一风格' },
    { key: 'ask_enabled', label: '允许找别家 AI 聊天', type: 'bool' },
    { key: 'max_calls_per_hour', label: '每小时最多调用几次工具', type: 'number' },
    { key: 'max_calls_per_day', label: '每天最多调用几次工具', type: 'number' },
    { key: 'max_image_calls_per_day', label: '每天最多画几张', type: 'number' },
  ],
  selfops: [
    { key: 'signature', label: 'QQ 签名', type: 'text' },
    { key: 'avatar_url', label: '头像图片地址', type: 'text', hint: 'http(s) 图片链接，保存后自动换头像' },
    { key: 'auto_qzone', label: '每天自动发一条说说', type: 'bool' },
    { key: 'qzone_time', label: '每天发布时间', type: 'text', hint: 'HH:MM，例如 20:30' },
    {
      key: 'qzone_visibility', label: '说说可见范围', type: 'select', numeric: true,
      options: [{ value: 1, label: '公开' }, { value: 4, label: '好友可见' }, { value: 64, label: '仅自己' }],
    },
  ],
  proactive: [
    { key: 'enabled', label: '启用主动发言', type: 'bool', hint: '按随机间隔自己找话题说话' },
    { key: 'min_minutes', label: '最短间隔（分钟）', type: 'number' },
    { key: 'max_minutes', label: '最长间隔（分钟）', type: 'number' },
    { key: 'idle_min_minutes', label: '群安静多少分钟后才开口', type: 'number' },
    { key: 'idle_max_minutes', label: '群安静超过多久就不打扰', type: 'number' },
    { key: 'quiet_hours', label: '免打扰时段', type: 'text', hint: '如 23:30-08:30' },
    { key: 'max_per_day', label: '每群每天最多几条', type: 'number' },
    { key: 'groups', label: '目标群（留空=自动）', type: 'lines', hint: '留空则用最近活跃的 QQ 群' },
    { key: 'prompt', label: '唤醒提示词（{time} {name} 可用）', type: 'textarea', rows: 12, hint: '到点时把这段发给模型，让它根据上下文自己说一句' },
  ],
  friend: [
    { key: 'enabled', label: '启用自动审批', type: 'bool', hint: '总开关；关闭后只记录不处理' },
    { key: 'auto_accept_friend', label: '自动同意好友申请', type: 'bool' },
    { key: 'friend_keyword', label: '好友验证词', type: 'text', hint: '申请留言包含这个词才通过，留空=全部通过' },
    { key: 'auto_accept_group', label: '自动同意入群邀请', type: 'bool' },
    { key: 'group_keyword', label: '入群验证词', type: 'text' },
    { key: 'welcome', label: '通过好友后的招呼语', type: 'textarea', rows: 3 },
    { key: 'whitelist', label: '只自动通过这些 QQ', type: 'lines', hint: '填了就变成严格模式：只通过列表里的号，其他一律拒绝' },
    { key: 'blacklist', label: '黑名单 QQ', type: 'lines' },
  ],
};

function fieldHTML(f, val) {
  const v = val ?? '';
  const key = `data-key="${f.key}"`;
  let inner = '';
  switch (f.type) {
    case 'textarea':
      inner = `<textarea ${key} data-type="text" rows="${f.rows || 6}">${esc(v)}</textarea>`;
      break;
    case 'json':
      inner = `<textarea ${key} data-type="json" rows="${f.rows || 6}" style="font-family:Consolas,monospace">${esc(
        typeof v === 'object' && v !== null ? JSON.stringify(v, null, 2) : v
      )}</textarea>`;
      break;
    case 'lines':
      inner = `<textarea ${key} data-type="lines" rows="${f.rows || 4}" placeholder="一行一个">${esc((v || []).join('\n'))}</textarea>`;
      break;
    case 'bool':
      inner = `<label class="switch"><input type="checkbox" ${key} data-type="bool" ${v ? 'checked' : ''}/> 启用</label>`;
      break;
    case 'number':
      inner = `<input type="number" ${key} data-type="number" value="${esc(v)}" />`;
      break;
    case 'range':
      inner = `<div class="row"><input type="range" ${key} data-type="number" min="${f.min}" max="${f.max}" step="${f.step}" value="${esc(v)}" style="flex:1" />
        <span class="badge" id="rv_${f.key}">${esc(v)}</span></div>`;
      break;
    case 'password':
      inner = `<input type="password" ${key} data-type="text" placeholder="不修改请留空" />`;
      break;
    case 'select':
      inner = `<select ${key} data-type="${f.numeric ? 'number' : 'text'}">${(f.options || [])
        .map((o) => `<option value="${esc(o.value)}" ${String(v) === String(o.value) ? 'selected' : ''}>${esc(o.label)}</option>`)
        .join('')}</select>`;
      break;
    default:
      inner = `<input type="text" ${key} data-type="text" value="${esc(v)}" />`;
  }
  return `<div class="field"><label>${esc(f.label)}</label>${inner}${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ''}</div>`;
}

function bindRangeLabels(root) {
  $$('input[type=range]', root).forEach((el) => {
    el.addEventListener('input', () => { const b = $('#rv_' + el.dataset.key); if (b) b.textContent = el.value; });
  });
}

function collectForm(root, fields) {
  const out = {};
  for (const f of fields) {
    const el = $(`[data-key="${f.key}"]`, root);
    if (!el) continue;
    const t = el.dataset.type || 'text';
    if (f.type === 'bool') out[f.key] = el.checked;
    else if (t === 'number') out[f.key] = Number(el.value || 0);
    else if (f.type === 'lines') out[f.key] = el.value.split('\n').map((s) => s.trim()).filter(Boolean);
    else if (t === 'json') { try { out[f.key] = JSON.parse(el.value); } catch { out[f.key] = el.value; } }
    else out[f.key] = el.value;
  }
  return out;
}

async function saveSection(section, root, fields) {
  const value = collectForm(root, fields);
  if (section === 'model' && !value.api_key) delete value.api_key;
  try {
    await api(`/api/settings/${section}`, { method: 'PUT', body: JSON.stringify({ value }) });
    toast('已保存');
    STATE.settings = await api('/api/settings');
  } catch (e) { toast(e.message, true); }
}

function sectionForm(section, fields) {
  const values = STATE.settings[section] || {};
  const html = `<div class="card"><div class="grid c2">${fields.map((f) => fieldHTML(f, values[f.key])).join('')}</div>
    <div class="row" style="margin-top:6px"><div class="spacer"></div><button class="btn primary" id="save_${section}">保存</button></div></div>`;
  return html;
}

function afterSection(section, fields) {
  const root = $('#view');
  bindRangeLabels(root);
  const btn = $('#save_' + section);
  if (btn) btn.onclick = () => saveSection(section, root, fields);
}

/* ------------------------------------------------------------ overview */

async function viewOverview() {
  const [ov, st, qq] = await Promise.all([api('/api/overview'), api('/api/status'), api('/api/qq/state')]);
  setStatus(st.online);
  const m = ov.messages || {}, l = ov.llm || {};
  $('#pageSub').textContent = `模型 ${ov.model?.model || '-'} · 人设 ${ov.persona || '-'}`;
  const qqDown = !qq.connected || qq.kicked;
  $('#view').innerHTML = `
    ${qqDown ? `<div class="card" style="border-color:#5a2b31;margin-bottom:14px">
      <div class="row"><h3 style="margin:0;color:#ff9aa3">QQ 离线${qq.kicked ? '（被强制下线）' : ''}</h3>
        <span class="spacer"></span>
        <button class="btn" id="qrRefresh">重新生成二维码</button></div>
      <div class="row" style="margin-top:12px;align-items:flex-start">
        ${qq.qr_url
          ? `<img id="qrImg" src="${esc(qq.qr_url)}" style="width:190px;height:190px;border-radius:12px;background:#fff" />`
          : '<div class="muted" style="width:190px">还没有二维码<br/>点右上角重启 NapCat</div>'}
        <div class="small muted">
          手机 QQ 扫码登录「小肥鱼」。<br/>
          当前二维码年龄：${qq.qr_age === null ? '-' : qq.qr_age + ' 秒'}<br/>
          二维码约每 2 分钟自动刷新，页面每 15 秒拉一次。<br/>
          <span id="qrMsg"></span>
        </div>
      </div>
    </div>` : ''}
    <div class="grid c4">
      <div class="card"><div class="muted small">机器人</div><div class="kpi">${st.online ? '在线' : '离线'}</div>
        <div class="small muted">心跳 ${st.last_heartbeat ? fmtTime(st.last_heartbeat) : '无'}</div></div>
      <div class="card"><div class="muted small">24h 消息</div><div class="kpi">${fmtNum(m.n)}</div>
        <div class="small muted">回复 ${fmtNum(m.replies)} · 群 ${m.groups || 0} · 人 ${m.users || 0}</div></div>
      <div class="card"><div class="muted small">24h 花费</div><div class="kpi">$${(l.cost || 0).toFixed(3)}</div>
        <div class="small muted">${fmtNum(l.pt)} in / ${fmtNum(l.ct)} out</div></div>
      <div class="card"><div class="muted small">平均延迟</div><div class="kpi">${Math.round(l.lat || 0)}<small>ms</small></div>
        <div class="small muted">失败 ${l.errs || 0} 次</div></div>
    </div>
    <div class="grid c2" style="margin-top:14px">
      <div class="card"><h3>活跃群</h3>${ov.groups.length ? `<table><tr><th>群</th><th>消息</th><th>最后活跃</th></tr>
        ${ov.groups.map((g) => `<tr><td>${esc(g.group_id)}</td><td>${g.n}</td><td>${fmtTime(g.last_ts)}</td></tr>`).join('')}</table>`
        : '<div class="muted">还没有群消息，扫码登录后就会出现在这里</div>'}</div>
      <div class="card"><h3>最近事件</h3><div class="timeline">
        ${(ov.events || []).slice(0, 12).map((e) => `<div><span class="muted">${fmtTime(e.ts)}</span> · [${esc(e.level)}] ${esc(e.message)}</div>`).join('') || '<div class="muted">暂无</div>'}
      </div></div>
    </div>`;

  if (window.__qrTimer) { clearInterval(window.__qrTimer); window.__qrTimer = null; }
  if (qqDown) {
    const btn = $('#qrRefresh');
    if (btn) btn.onclick = async () => {
      btn.disabled = true;
      try {
        await api('/api/qq/restart', { method: 'POST', body: '{}' });
        const msg = $('#qrMsg'); if (msg) msg.textContent = '已提交重启，约 30 秒后出码…';
      } catch (e) { toast(e.message, true); }
      setTimeout(() => go('overview'), 35000);
    };
    window.__qrTimer = setInterval(async () => {
      try {
        const s = await api('/api/qq/state');
        if (s.connected) { clearInterval(window.__qrTimer); go('overview'); return; }
        const img = $('#qrImg');
        if (img && s.qr_url) img.src = s.qr_url;
        const msg = $('#qrMsg');
        if (msg) msg.textContent = `二维码年龄 ${s.qr_age ?? '-'} 秒（自动刷新）`;
      } catch {}
    }, 15000);
  }
}

/* ------------------------------------------------------------ chat */

const chatLog = [];

function renderChat() {
  $('#view').innerHTML = `
    <div class="chat-layout">
      <div class="card" style="display:flex;flex-direction:column">
        <div class="chat-box" id="chatBox"></div>
        <div class="chat-input">
          <input type="text" id="chatInput" placeholder="在测试群里说点什么…（回车发送）" />
          <button class="btn primary" id="chatSend">发送</button>
        </div>
      </div>
      <div>
        <div class="card"><h3>模拟身份</h3>
          <div class="field"><label>群号</label><input type="text" id="simGroup" value="demo" /></div>
          <div class="field"><label>你的昵称</label><input type="text" id="simNick" value="测试群友" /></div>
          <div class="field"><label>你的 QQ</label><input type="text" id="simUid" value="10001" /></div>
          <div class="small muted">调试台不会发到真实 QQ，走的是同一套大脑逻辑。</div>
        </div>
        <div class="card" style="margin-top:14px"><h3>本轮诊断</h3><pre class="small" id="chatDebug" style="white-space:pre-wrap;color:#9fb0c9">-</pre></div>
      </div>
    </div>`;
  $('#chatSend').onclick = sendChat;
  $('#chatInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });
  drawChat();
}

function drawChat() {
  const box = $('#chatBox');
  if (!box) return;
  box.innerHTML = chatLog.map((m) => `<div class="msg ${m.role}"><span class="meta">${esc(m.who)} · ${m.time}</span>${esc(m.text)}</div>`).join('')
    + (chatLog.length ? '' : '<div class="muted" style="margin:auto">发条消息试试，比如「在吗」「你觉得这游戏怎么样」</div>');
  box.scrollTop = box.scrollHeight;
}

async function sendChat() {
  const input = $('#chatInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  chatLog.push({ role: 'user', who: $('#simNick').value || '测试群友', text, time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) });
  drawChat();
  try {
    const res = await api('/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        group_id: $('#simGroup').value || 'demo',
        user_id: $('#simUid').value || '10001',
        nickname: $('#simNick').value || '测试群友',
        text,
      }),
    });
    if (res.reply) {
      chatLog.push({ role: 'bot', who: STATE.settings?.persona?.name || '小语', text: res.reply, time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) });
    } else {
      chatLog.push({ role: 'bot', who: '系统', text: `（没有回复：${res.reason}）`, time: '' });
    }
    $('#chatDebug').textContent = JSON.stringify(res, null, 2);
    drawChat();
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ memory */

let memSelected = null;

async function viewMemory() {
  const data = await api('/api/users');
  const users = data.users || [];
  $('#pageSub').textContent = `${users.length} 个记住的人`;
  $('#view').innerHTML = `<div class="mem-layout">
    <div class="card"><div class="field"><input type="text" id="memSearch" placeholder="搜索昵称或 UID" /></div>
      <div id="memList">${users.map(userRow).join('') || '<div class="muted">还没有记忆，去测试台聊几句</div>'}</div></div>
    <div id="memDetail"><div class="card muted">选中左边一个人查看印象</div></div>
  </div>`;
  $('#memSearch').addEventListener('input', () => {
    const q = $('#memSearch').value.toLowerCase();
    $$('#memList .user-item').forEach((el) => {
      el.style.display = el.dataset.text.toLowerCase().includes(q) ? '' : 'none';
    });
  });
  $$('#memList .user-item').forEach((el) => el.onclick = () => selectUser(el.dataset.uid));
  if (memSelected) selectUser(memSelected);
}

function userRow(u) {
  const imp = u.impression || {};
  const dims = imp.dims || {};
  const fam = dims.familiarity ?? 0;
  return `<div class="user-item" data-uid="${esc(u.uid)}" data-text="${esc((u.nickname || '') + ' ' + u.uid)}">
    <div class="row"><b>${esc(u.display_name || u.nickname || '(无名)')}</b><span class="spacer"></span><span class="small muted">${u.msg_count} 条</span></div>
    <div class="small muted">${esc(imp.summary || '还没有印象')}</div>
    <div class="bar" style="margin-top:6px"><i style="width:${fam}%"></i></div>
  </div>`;
}

async function selectUser(uid) {
  memSelected = uid;
  $$('#memList .user-item').forEach((el) => el.classList.toggle('active', el.dataset.uid === uid));
  const data = await api('/api/users/' + encodeURIComponent(uid));
  const u = data.user, imp = u.impression || {}, dims = imp.dims || {};
  const dimDefs = (STATE.settings?.memory?.dimensions) || [];
  $('#memDetail').innerHTML = `<div class="card">
    <div class="row"><h3 style="margin:0">${esc(u.display_name || u.nickname)}</h3><span class="badge">${esc(u.uid)}</span><span class="spacer"></span>
      <span class="small muted">消息 ${u.msg_count} · 回复 ${u.reply_count} · 最近 ${fmtTime(u.last_seen)}</span></div>
    <div class="grid c2" style="margin-top:14px">
      <div>
        <div class="field"><label>显示名（覆盖群名片/昵称）</label>
          <input type="text" id="impName" value="${esc(u.display_name || '')}" placeholder="${esc(u.nickname || '')}" /></div>
        <div class="field"><label>一句话印象</label><textarea id="impSummary" rows="3">${esc(imp.summary || '')}</textarea></div>
        <div class="field"><label>说话风格</label><input type="text" id="impStyle" value="${esc(imp.style || '')}" /></div>
        <div class="field"><label>标签（逗号分隔）</label><input type="text" id="impTags" value="${esc((imp.tags || []).join('，'))}" /></div>
        <div class="field"><label>记得的事（一行一条）</label><textarea id="impFacts" rows="5">${esc((imp.facts || []).join('\n'))}</textarea></div>
      </div>
      <div>
        <h3>印象维度</h3>
        <div class="bars">${dimDefs.map((d) => `<div class="bar-row"><span>${esc(d.name)}</span>
          <input type="range" min="0" max="100" value="${dims[d.key] ?? 30}" data-dim="${esc(d.key)}" />
          <span class="badge" id="dimv_${esc(d.key)}">${dims[d.key] ?? 30}</span></div>`).join('')}</div>
        <div class="row" style="margin-top:14px"><button class="btn primary" id="saveImp">保存印象</button>
          <button class="btn" id="refreshImp">重新提炼</button>
          <button class="btn danger" id="delUser">删除这个人</button></div>
      </div>
    </div>
    <h3 style="margin-top:18px">最近发言</h3>
    <div class="timeline">${(data.messages || []).slice(-30).map((m) => `<div><span class="muted">${fmtTime(m.ts)}</span> ${esc(m.text)}</div>`).join('') || '<div class="muted">无</div>'}</div>
  </div>`;
  $$('[data-dim]').forEach((el) => el.addEventListener('input', () => {
    const b = $('#dimv_' + el.dataset.dim); if (b) b.textContent = el.value;
  }));
  $('#saveImp').onclick = async () => {
    const dimsNew = {};
    $$('[data-dim]').forEach((el) => { dimsNew[el.dataset.dim] = Number(el.value); });
    try {
      await api('/api/users/' + encodeURIComponent(uid), {
        method: 'PUT', body: JSON.stringify({
          summary: $('#impSummary').value, style: $('#impStyle').value,
          tags: $('#impTags').value.split(/[，,]/).map((s) => s.trim()).filter(Boolean),
          facts: $('#impFacts').value.split('\n').map((s) => s.trim()).filter(Boolean),
          dims: dimsNew,
          display_name: $('#impName').value,
        }),
      });
      toast('已保存');
      viewMemory();
    } catch (e) { toast(e.message, true); }
  };
  $('#refreshImp').onclick = async () => {
    try {
      await api('/api/users/' + encodeURIComponent(uid) + '/refresh', { method: 'POST', body: '{}' });
      toast('已提交，几秒后刷新');
      setTimeout(() => selectUser(uid), 4000);
    } catch (e) { toast(e.message, true); }
  };
  $('#delUser').onclick = async () => {
    if (!confirm('删除这个人的全部记忆和消息？')) return;
    await api('/api/users/' + encodeURIComponent(uid), { method: 'DELETE' });
    memSelected = null; viewMemory();
  };
}

/* ------------------------------------------------------------ context */

async function viewContext() {
  const [data, peopleData] = await Promise.all([api('/api/groups'), api('/api/people')]);
  const groups = data.groups || [];
  const people = peopleData.digests || [];
  $('#pageSub').textContent = `${groups.length} 个群有记录 · ${people.length} 份人物动态`;
  $('#view').innerHTML = `<div class="grid c2">
    <div class="card"><h3>群列表</h3>
      <table><tr><th>群号</th><th>消息</th><th>最后</th><th></th></tr>
      ${groups.map((g) => `<tr><td>${esc(g.group_id)}</td><td>${g.n}</td><td>${fmtTime(g.last_ts)}</td>
        <td><button class="btn sm" data-g="${esc(g.platform)}|${esc(g.group_id)}">查看</button></td></tr>`).join('') || '<tr><td colspan="4" class="muted">暂无</td></tr>'}
      </table></div>
    <div class="card" id="ctxDetail"><div class="muted">选一个群查看上下文</div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>人物动态（跨对话）</h3>
    <div class="small muted">聊天里提到某个人的名字时，会自动把 TA 的动态摘要带进上下文。</div>
    <table style="margin-top:10px"><tr><th>人</th><th>最近在干嘛</th><th>话题</th><th>更新</th></tr>
      ${people.map((p) => `<tr><td>${esc(p.name)}</td><td>${esc(p.summary)}</td>
        <td>${(p.topics || []).map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</td>
        <td class="muted">${fmtTime(p.updated_at)}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">还没有，聊几句就有了</td></tr>'}
    </table>
  </div>`;
  $$('[data-g]').forEach((btn) => btn.onclick = () => loadContext(...btn.dataset.g.split('|')));
}

async function loadContext(platform, groupId) {
  const data = await api(`/api/context?platform=${encodeURIComponent(platform)}&group_id=${encodeURIComponent(groupId)}&limit=100`);
  const rot = data.rotation;
  $('#ctxDetail').innerHTML = `<div class="row"><h3 style="margin:0">${esc(groupId)}</h3><span class="spacer"></span>
    <button class="btn sm" id="rotateNow">立即归档</button>
    <button class="btn sm" id="importHist">从 QQ 拉取最近 50 条</button>
    <button class="btn danger sm" id="clearCtx">清空上下文</button></div>
    ${rot ? `<div class="card" style="margin:10px 0;background:#171d2a">
      <div class="small muted">上一次归档 · ${fmtTime(rot.created_at)} · ${rot.msg_count} 条</div>
      <div style="margin-top:6px">${esc(rot.summary)}</div>
      ${rot.injection ? `<div class="small" style="margin-top:8px;color:#9fb0c9">留给自己的话：${esc(rot.injection)}</div>` : ''}
      ${(rot.important || []).length ? `<div class="small muted" style="margin-top:6px">记住：${esc(rot.important.join('；'))}</div>` : ''}
    </div>` : ''}
    ${data.summary ? `<div class="small muted" style="margin:8px 0">摘要：${esc(data.summary.summary)}</div>` : ''}
    <div class="timeline" style="max-height:60vh">${(data.messages || []).map((m) => `<div><span class="muted">${fmtTime(m.ts)}</span>
      <b>${esc(m.role === 'assistant' ? '机器人' : m.nickname)}</b>：${esc(m.text)}</div>`).join('') || '<div class="muted">空</div>'}</div>`;
  $('#rotateNow').onclick = async () => {
    if (!confirm('现在归档这个群的对话？会清空当前聊天记录')) return;
    try {
      const r = await api('/api/rotation/run', { method: 'POST', body: JSON.stringify({ group_id: groupId }) });
      toast(`归档了 ${r.rotated} 个群`);
      loadContext(platform, groupId);
    } catch (e) { toast(e.message, true); }
  };
  $('#importHist').onclick = async () => {
    try {
      const r = await api('/api/context/import', { method: 'POST', body: JSON.stringify({ group_id: groupId, count: 50 }) });
      toast(`导入 ${r.imported} 条`);
      loadContext(platform, groupId);
    } catch (e) { toast(e.message, true); }
  };
  $('#clearCtx').onclick = async () => {
    if (!confirm('清空这个群的上下文？长期记忆不受影响')) return;
    await api(`/api/context?platform=${encodeURIComponent(platform)}&group_id=${encodeURIComponent(groupId)}`, { method: 'DELETE' });
    toast('已清空'); loadContext(platform, groupId);
  };
}

/* ------------------------------------------------------------ emoji */

async function viewEmoji() {
  const emoji = STATE.settings.emoji || {};
  const map = emoji.map || {};
  const rows = Object.entries(map).sort((a, b) => Number(a[0]) - Number(b[0]));
  $('#pageSub').textContent = `${rows.length} 个 QQ 表情映射`;
  $('#view').innerHTML = `<div class="grid c2">
    <div class="card"><h3>行为</h3>
      <div class="field"><label>随机带表情概率</label>
        <div class="row"><input type="range" id="emojiProb" min="0" max="1" step="0.01" value="${emoji.send_probability ?? 0.15}" style="flex:1" />
        <span class="badge" id="emojiProbV">${emoji.send_probability ?? 0.15}</span></div></div>
      <div class="small muted">模型可以在回复里写 <code>[表情:笑哭]</code>，会被转换成真正的 QQ 表情发出去；映射表决定哪些名字可用。</div>
      <div class="row" style="margin-top:14px"><button class="btn primary" id="saveEmoji">保存</button></div>
    </div>
    <div class="card"><h3>新增 / 修改</h3>
      <div class="row"><input type="number" id="newFaceId" placeholder="ID" style="width:90px" />
      <input type="text" id="newFaceName" placeholder="名称，如 笑哭" style="flex:1" />
      <button class="btn" id="addFace">添加</button></div>
      <div class="timeline" style="margin-top:12px;max-height:52vh">
        ${rows.map(([id, name]) => `<div class="row"><span class="badge">${esc(id)}</span><span>${esc(name)}</span><span class="spacer"></span>
          <button class="btn sm danger" data-del="${esc(id)}">删</button></div>`).join('')}
      </div>
    </div>
  </div>`;
  $('#emojiProb').addEventListener('input', (e) => { $('#emojiProbV').textContent = e.target.value; });
  $('#addFace').onclick = async () => {
    const id = $('#newFaceId').value.trim(), name = $('#newFaceName').value.trim();
    if (!id || !name) return;
    map[id] = name; await saveEmojiMap(map, Number($('#emojiProb').value));
  };
  $$('[data-del]').forEach((b) => b.onclick = async () => { delete map[b.dataset.del]; await saveEmojiMap(map, Number($('#emojiProb').value)); });
  $('#saveEmoji').onclick = () => saveEmojiMap(map, Number($('#emojiProb').value));
}

async function saveEmojiMap(map, prob) {
  try {
    await api('/api/settings/emoji', { method: 'PUT', body: JSON.stringify({ value: { map, send_probability: prob } }) });
    STATE.settings = await api('/api/settings');
    toast('已保存'); viewEmoji();
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ external AI */

async function viewTools() {
  const t = STATE.settings.tools || {};
  const providers = (t.ask_providers || []).slice();
  $('#pageSub').textContent = `${providers.length} 个外部模型`;
  $('#view').innerHTML = sectionForm('tools', FIELDS.tools) + `
    <div class="card" style="margin-top:14px"><h3>外部模型</h3>
      <div class="small muted">模型可以用 ask_ai 向这些服务提问。endpoint 填完整的 chat completions 地址（如 https://api.deepseek.com/chat/completions）。</div>
      <table style="margin-top:10px"><tr><th>id</th><th>名称</th><th>endpoint</th><th>模型</th><th>Key</th><th>额外 Header (JSON)</th><th></th></tr>
        <tbody id="provRows">${providers.map((p, i) => `<tr>
          <td><input data-f="id" value="${esc(p.id || '')}" style="width:100px"/></td>
          <td><input data-f="name" value="${esc(p.name || '')}" style="width:110px"/></td>
          <td><input data-f="endpoint" value="${esc(p.endpoint || '')}" style="width:250px"/></td>
          <td><input data-f="model" value="${esc(p.model || '')}" style="width:120px"/></td>
          <td><input data-f="api_key" type="password" value="${esc(p.api_key || '')}" style="width:110px"/></td>
          <td><input data-f="headers" value="${esc(JSON.stringify(p.headers || {}))}" style="width:150px" placeholder='{"Instance-Id":""}'/></td>
          <td><button class="btn sm danger" data-rm="${i}">删</button></td></tr>`).join('')}</tbody>
      </table>
      <div class="row" style="margin-top:10px"><button class="btn" id="addProv">添加一行</button>
        <div class="spacer"></div><button class="btn primary" id="saveTools">保存工具配置</button></div>
    </div>`;
  afterSection('tools', FIELDS.tools);
  $('#addProv').onclick = () => {
    providers.push({ id: '', name: '', endpoint: '', model: '', api_key: '' });
    t.ask_providers = providers;
    viewTools();
  };
  $$('[data-rm]').forEach((b) => b.onclick = () => {
    providers.splice(Number(b.dataset.rm), 1);
    t.ask_providers = providers;
    viewTools();
  });
  $('#saveTools').onclick = async () => {
    const rows = $$('#provRows tr').map((tr) => {
      const o = {};
      $$('input', tr).forEach((inp) => { o[inp.dataset.f] = inp.value; });
      try { o.headers = o.headers ? JSON.parse(o.headers) : {}; } catch { o.headers = {}; }
      return o;
    }).filter((o) => o.id && o.endpoint);
    const value = collectForm($('#view'), FIELDS.tools);
    value.ask_providers = rows;
    try {
      await api('/api/settings/tools', { method: 'PUT', body: JSON.stringify({ value }) });
      STATE.settings = await api('/api/settings');
      toast('已保存');
      viewTools();
    } catch (e) { toast(e.message, true); }
  };
}

/* ------------------------------------------------------------ self ops */

async function viewSelf() {
  const data = await api('/api/self');
  const p = data.profile || {};
  const uin = p.user_id || '';
  const avatar = p.avatar_url || (uin ? `https://q1.qlogo.cn/g?b=qq&nk=${uin}&s=640` : '');
  const posts = data.posts || [];
  $('#pageSub').textContent = p.nickname ? `${p.nickname}（${uin}）` : '账号资料与空间';
  $('#view').innerHTML = `
    <div class="grid c2">
      <div class="card"><h3>账号资料</h3>
        <div class="row" style="margin-bottom:12px">
          <img src="${esc(avatar)}" onerror="this.style.display='none'" style="width:64px;height:64px;border-radius:16px;object-fit:cover;background:#222" />
          <div class="small muted">头像预览</div>
        </div>
        <div class="field"><label>昵称</label><input type="text" id="selfNick" value="${esc(p.nickname || '')}" /></div>
        <div class="field"><label>签名</label><input type="text" id="selfSign" value="${esc(p.signature || '')}" /></div>
        <div class="row"><button class="btn primary" id="saveProfile">保存资料</button></div>
        <div class="field" style="margin-top:14px"><label>换头像（图片链接）</label>
          <div class="row"><input type="text" id="selfAvatar" value="${esc(p.avatar_url || '')}" style="flex:1" />
          <button class="btn" id="saveAvatar">换头像</button></div></div>
      </div>
      <div class="card"><h3>发一条说说</h3>
        <div class="field"><textarea id="qzText" rows="5" placeholder="写点什么，或者点下面让 AI 生成"></textarea></div>
        <div class="row">
          <select id="qzVis" style="width:130px">
            <option value="1">公开</option><option value="4">好友可见</option><option value="64">仅自己</option>
          </select>
          <button class="btn" id="qzDraft">AI 生成</button>
          <button class="btn primary" id="qzPost">发布</button>
        </div>
        <pre class="small" id="qzOut" style="white-space:pre-wrap;color:#9fb0c9;margin-top:10px">-</pre>
      </div>
    </div>
    <div style="margin-top:14px">${sectionForm('selfops', FIELDS.selfops)}</div>
    <div class="card" style="margin-top:14px"><h3>空间记录</h3>
      <table><tr><th>时间</th><th>内容</th><th>可见</th><th>来源</th><th></th></tr>
      ${posts.map((x) => `<tr><td>${fmtTime(x.ts)}</td><td>${esc((x.content || '').slice(0, 60))}</td>
        <td>${x.visibility === 1 ? '公开' : x.visibility === 4 ? '好友' : '仅自己'}</td>
        <td>${x.auto ? '自动' : '手动'}</td>
        <td>${x.deleted ? '<span class="badge">已删</span>' : `<button class="btn sm danger" data-del="${esc(x.tid)}">删除</button>`}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">还没有发过</td></tr>'}
      </table>
      ${data.last_error ? `<div class="small" style="color:#ef5f6b;margin-top:8px">最近错误：${esc(data.last_error)}</div>` : ''}
    </div>`;
  afterSection('selfops', FIELDS.selfops);
  $('#saveProfile').onclick = async () => {
    try {
      await api('/api/self/profile', { method: 'POST', body: JSON.stringify({ nickname: $('#selfNick').value, signature: $('#selfSign').value }) });
      toast('资料已更新'); viewSelf();
    } catch (e) { toast(e.message, true); }
  };
  $('#saveAvatar').onclick = async () => {
    try {
      await api('/api/self/avatar', { method: 'POST', body: JSON.stringify({ url: $('#selfAvatar').value }) });
      toast('头像已更新'); setTimeout(viewSelf, 1500);
    } catch (e) { toast(e.message, true); }
  };
  $('#qzDraft').onclick = async () => {
    $('#qzOut').textContent = '生成中…';
    try {
      const r = await api('/api/self/qzone/draft', { method: 'POST', body: '{}' });
      $('#qzText').value = r.text; $('#qzOut').textContent = '已生成，可修改后发布';
    } catch (e) { $('#qzOut').textContent = '失败：' + e.message; }
  };
  $('#qzPost').onclick = async () => {
    const content = $('#qzText').value.trim();
    if (!content) { toast('内容不能为空', true); return; }
    $('#qzOut').textContent = '发布中…';
    try {
      const r = await api('/api/self/qzone', { method: 'POST', body: JSON.stringify({ content, visibility: Number($('#qzVis').value) }) });
      $('#qzOut').textContent = `已发布，tid=${r.tid}`; $('#qzText').value = '';
      setTimeout(viewSelf, 1200);
    } catch (e) { $('#qzOut').textContent = '失败：' + e.message; }
  };
  $$('[data-del]').forEach((b) => b.onclick = async () => {
    if (!confirm('删除这条说说？')) return;
    try { await api('/api/self/qzone/delete', { method: 'POST', body: JSON.stringify({ tid: b.dataset.del }) }); toast('已删除'); viewSelf(); }
    catch (e) { toast(e.message, true); }
  });
}

/* ------------------------------------------------------------ proactive */

async function viewProactive() {
  const st = await api('/api/proactive/status');
  const groups = st.groups || [];
  const known = (await api('/api/groups')).groups.filter((g) => g.platform === 'qq');
  $('#pageSub').textContent = st.enabled ? '运行中' : '未启用';
  $('#view').innerHTML = sectionForm('proactive', FIELDS.proactive) + `
    <div class="card" style="margin-top:14px"><h3>调度状态</h3>
      <div class="small muted">NapCat 通道：${st.napcat_connected ? '<span class="badge ok">已连接</span>' : '<span class="badge bad">未连接</span>'}
        ${st.last_error ? ' · 最近错误：' + esc(st.last_error) : ''}</div>
      <table style="margin-top:10px"><tr><th>群</th><th>下次</th><th>今天已发</th></tr>
        ${groups.map((g) => `<tr><td>${esc(g.group_id)}</td>
          <td>${g.next_at ? fmtTime(g.next_at) + ` <span class="muted small">(${Math.round((g.in_seconds || 0) / 60)} 分钟后)</span>` : '-'}</td>
          <td>${g.today}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">还没有目标群，先去群里说句话</td></tr>'}
      </table>
      <div class="row" style="margin-top:12px">
        <select id="trigGroup" style="width:220px">
          ${(known.length ? known : groups).map((g) => `<option value="${esc(g.group_id)}">${esc(g.group_id)}</option>`).join('')}
        </select>
        <button class="btn primary" id="trigBtn">立即发一条</button>
        <span class="small muted">测试用，不会影响每日计数之外的状态</span>
      </div>
      <pre class="small" id="trigOut" style="white-space:pre-wrap;color:#9fb0c9;margin-top:10px">-</pre>
    </div>`;
  afterSection('proactive', FIELDS.proactive);
  $('#trigBtn').onclick = async () => {
    const gid = $('#trigGroup').value;
    if (!gid) { toast('没有可选群', true); return; }
    $('#trigOut').textContent = '生成中…';
    try {
      const r = await api('/api/proactive/trigger', { method: 'POST', body: JSON.stringify({ group_id: gid }) });
      $('#trigOut').textContent = `已发送：\n${r.text}`;
    } catch (e) { $('#trigOut').textContent = '失败：' + e.message; }
  };
}

/* ------------------------------------------------------------ friend */

async function viewFriend() {
  let reqs = [];
  try { reqs = (await api('/api/requests')).requests || []; } catch {}
  $('#pageSub').textContent = `${reqs.length} 条最近申请`;
  $('#view').innerHTML = sectionForm('friend', FIELDS.friend) + `
    <div class="card" style="margin-top:14px"><h3>最近的好友 / 入群申请</h3>
      <table><tr><th>时间</th><th>类型</th><th>QQ</th><th>群</th><th>留言</th><th>结果</th><th>原因</th></tr>
      ${reqs.map((r) => `<tr><td>${fmtTime(r.ts)}</td><td>${r.kind === 'friend' ? '好友' : '入群'}</td>
        <td>${esc(r.user_id)}</td><td>${esc(r.group_id || '-')}</td><td>${esc(r.comment || '-')}</td>
        <td><span class="badge ${r.approved ? 'ok' : 'bad'}">${r.approved ? '已同意' : '未通过'}</span></td>
        <td class="muted">${esc(r.reason || '-')}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">暂无</td></tr>'}
      </table>
      <div class="small muted" style="margin-top:8px">提示：新号频繁同意好友/入群会显著提高风控概率，建议先只开验证词模式。</div>
    </div>`;
  afterSection('friend', FIELDS.friend);
}

/* ------------------------------------------------------------ model */

async function viewModel() {
  const m = STATE.settings.model || {};
  $('#pageSub').textContent = m.api_key_masked ? `Key ${m.api_key_masked}` : '未配置 Key';
  $('#view').innerHTML = sectionForm('model', FIELDS.model) + `
    <div class="card" style="margin-top:14px"><h3>连通性测试</h3>
      <div class="row"><input type="text" id="testPrompt" value="用一句话介绍一下你自己" style="flex:1" />
      <button class="btn primary" id="testBtn">测试</button></div>
      <pre class="small" id="testOut" style="white-space:pre-wrap;color:#9fb0c9;margin-top:10px">-</pre></div>`;
  afterSection('model', FIELDS.model);
  $('#testBtn').onclick = async () => {
    $('#testOut').textContent = '请求中…';
    try {
      const r = await api('/api/model/test', { method: 'POST', body: JSON.stringify({ prompt: $('#testPrompt').value }) });
      $('#testOut').textContent = `延迟 ${r.latency_ms}ms · 花费 $${(r.cost || 0).toFixed(5)}\n\n${r.text}`;
    } catch (e) { $('#testOut').textContent = '失败：' + e.message; }
  };
}

/* ------------------------------------------------------------ logs */

async function viewLogs() {
  const data = await api('/api/logs?limit=200');
  $('#pageSub').textContent = '最近 200 条事件 / 50 次模型调用';
  $('#view').innerHTML = `<div class="grid c2">
    <div class="card"><h3>事件</h3><div class="timeline" style="max-height:68vh">
      ${(data.events || []).map((e) => `<div><span class="muted">${fmtTime(e.ts)}</span> <span class="badge">${esc(e.level)}</span> ${esc(e.source)} · ${esc(e.message)}</div>`).join('') || '<div class="muted">暂无</div>'}
    </div></div>
    <div class="card"><h3>模型调用</h3><div class="timeline" style="max-height:68vh">
      ${(data.llm || []).map((c) => `<div><span class="muted">${fmtTime(c.ts)}</span> ${esc(c.kind)} · ${c.prompt_tokens}/${c.completion_tokens} tok · ${c.latency_ms}ms · $${(c.cost_usd || 0).toFixed(5)} ${c.ok ? '' : '<span class="badge bad">失败</span>'} ${esc(c.error || '')}</div>`).join('') || '<div class="muted">暂无</div>'}
    </div></div>
  </div>`;
}

/* ------------------------------------------------------------ settings */

async function viewSettings() {
  const info = await api('/api/system');
  $('#pageSub').textContent = '安全与备份';
  $('#view').innerHTML = `<div class="grid c2">
    <div class="card"><h3>修改密码</h3>
      <div class="field"><label>原密码</label><input type="password" id="pwOld" /></div>
      <div class="field"><label>新密码（至少 6 位）</label><input type="password" id="pwNew" /></div>
      <button class="btn primary" id="pwSave">修改</button>
    </div>
    <div class="card"><h3>备份</h3>
      <p class="small muted">导出包含配置、记忆、摘要，但不含 API Key 和密码。导入为合并模式。</p>
      <div class="row"><a class="btn" href="/api/export">导出 JSON</a>
        <label class="btn" style="cursor:pointer">导入 JSON<input type="file" id="importFile" accept="application/json" class="hidden" /></label></div>
    </div>
    <div class="card"><h3>插件接入</h3>
      <p class="small muted">NapCat 插件用这个地址和 token 调用大脑：</p>
      <div class="field"><label>地址</label><input type="text" readonly value="http://console:8088" /></div>
      <div class="field"><label>Token</label><input type="text" readonly value="${esc(info.brain_token || '')}" /></div>
      <div class="small muted">机器人标识：${esc(info.self_id || '未登录')} · 版本 ${esc(info.version || '')}</div>
    </div>
    <div class="card"><h3>危险操作</h3>
      <p class="small muted">清空全部聊天上下文（保留长期记忆）。</p>
      <button class="btn danger" id="clearAll">清空所有上下文</button>
    </div>
  </div>`;
  $('#pwSave').onclick = async () => {
    try {
      await api('/api/password', { method: 'POST', body: JSON.stringify({ old: $('#pwOld').value, new: $('#pwNew').value }) });
      toast('密码已修改');
    } catch (e) { toast(e.message, true); }
  };
  $('#importFile').onchange = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const payload = JSON.parse(await file.text());
    try { await api('/api/import', { method: 'POST', body: JSON.stringify({ payload }) }); toast('已导入'); }
    catch (err) { toast(err.message, true); }
  };
  $('#clearAll').onclick = async () => {
    if (!confirm('确认清空所有群的上下文？')) return;
    const groups = (await api('/api/groups')).groups || [];
    for (const g of groups) await api(`/api/context?platform=${encodeURIComponent(g.platform)}&group_id=${encodeURIComponent(g.group_id)}`, { method: 'DELETE' });
    toast('已清空');
  };
}

/* ------------------------------------------------------------ router */

const ROUTES = {
  overview: { title: '概览', fn: viewOverview },
  chat: { title: '对话调试', fn: renderChat },
  memory: { title: '长期记忆', fn: viewMemory },
  context: { title: '上下文', fn: viewContext },
  persona: { title: '人设', fn: viewPersona },
  trigger: { title: '触发策略', fn: async () => { $('#view').innerHTML = sectionForm('trigger', FIELDS.trigger); afterSection('trigger', FIELDS.trigger); } },
  proactive: { title: '主动发言', fn: viewProactive },
  self: { title: '自运营', fn: viewSelf },
  tools: { title: '外部 AI', fn: viewTools },
  emoji: { title: '表情', fn: viewEmoji },
  friend: { title: '好友 / 入群', fn: viewFriend },
  model: { title: '模型', fn: viewModel },
  logs: { title: '日志', fn: viewLogs },
  settings: { title: '设置', fn: viewSettings },
};

function setStatus(online) {
  const dot = $('#statusDot'); const txt = $('#statusText');
  if (!dot) return;
  dot.className = 'dot' + (online ? ' on' : '');
  txt.textContent = online ? '机器人在线' : '机器人离线';
}

async function viewPersona() {
  $('#view').innerHTML = sectionForm('persona', FIELDS.persona) + `
    <div class="card" style="margin-top:14px">
      <div class="row"><h3 style="margin:0">生活碎片</h3><span class="spacer"></span>
        <button class="btn" id="loreRefresh">立即按今天刷新</button></div>
      <div class="small muted" id="loreInfo" style="margin-top:8px">
        每天到点自动抓「常住城市天气 + 社会新闻」，生成 6~8 条碎片；提示词就是上面那段模板，它只能读、不能改。
      </div>
    </div>`;
  afterSection('persona', FIELDS.persona);
  const btn = $('#loreRefresh');
  if (!btn) return;
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = '正在抓取…';
    try {
      const r = await api('/api/lore/refresh', { method: 'POST', body: '{}' });
      if (!r.ok) throw new Error(r.error || '刷新失败');
      const ta = document.querySelector('[data-key="lore"]');
      if (ta) ta.value = (r.fragments || []).join('\n');
      const info = $('#loreInfo');
      if (info) info.textContent = `${r.weather} ｜ 新闻 ${r.news.length} 条 ｜ 生成 ${r.fragments.length} 条碎片`;
      toast('生活碎片已更新');
    } catch (e) { toast(e.message, true); }
    btn.disabled = false;
    btn.textContent = '立即按今天刷新';
  };
}

async function go(view) {
  const route = ROUTES[view] || ROUTES.overview;
  $('#pageTitle').textContent = route.title;
  $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  location.hash = view;
  try { await route.fn(); } catch (e) { toast(e.message, true); }
}

async function boot() {
  try {
    STATE.settings = await api('/api/settings');
  } catch { return; }
  $$('.nav-btn').forEach((b) => b.onclick = () => go(b.dataset.view));
  $('#btnLogout').onclick = async () => { await api('/api/logout', { method: 'POST' }); location.href = '/login'; };
  const view = (location.hash || '#overview').slice(1);
  await go(view);
  pollStatus();
  pollAlerts();
  setInterval(pollStatus, 20000);
  setInterval(pollAlerts, 30000);
}

async function pollStatus() {
  try { const st = await api('/api/status'); setStatus(st.online); } catch {}
}

async function pollAlerts() {
  const bar = $('#alertBar');
  if (!bar) return;
  try {
    const d = await api('/api/alerts?only_open=true');
    if (!d.alerts || !d.alerts.length) { bar.className = 'alert-bar hidden'; return; }
    const a = d.alerts[0];
    bar.className = 'alert-bar';
    bar.innerHTML = `<b>${esc(a.level)}</b><span>${esc(a.message)}</span><span class="spacer"></span>
      <button class="btn sm" id="ackAlerts">知道了</button>`;
    const btn = $('#ackAlerts');
    if (btn) btn.onclick = async () => {
      try { await api('/api/alerts/ack', { method: 'POST', body: '{}' }); } catch {}
      pollAlerts();
    };
  } catch {}
}

window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (ROUTES[v]) go(v); });
boot();
