/* 短线雷达 —— 前端逻辑。没有框架，没有构建，改完刷新就生效。 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const S = {
  status: {},
  result: null,
  polling: null,
  autoTimer: null,
  busy: false,
};

/* ---------------------------------------------------------------- 工具 */

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function num(v, fix = 2) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(fix);
}

/** 涨幅着色：正红负绿，A 股习惯。 */
function signed(v, fix = 2, suffix = '%') {
  if (v == null || isNaN(v)) return '<span class="flat">—</span>';
  const n = Number(v);
  const cls = n > 0 ? 'up' : (n < 0 ? 'dn' : 'flat');
  const s = (n > 0 ? '+' : '') + n.toFixed(fix) + suffix;
  return `<span class="${cls}">${s}</span>`;
}

function toast(msg, ms = 2200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('on'), ms);
}

async function api(path, opt = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opt,
    body: opt.body ? JSON.stringify(opt.body) : undefined,
  });
  const d = await r.json().catch(() => ({ error: '响应不是 JSON' }));
  if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}

/* ---------------------------------------------------------------- 时钟与状态 */

function tickClock() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  $('#clock').textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

const SESSION_BADGE = {
  trading: ['live', '交易中'],
  auction: ['auction', '集合竞价'],
  lunch: ['', '午间休市'],
  pre: ['', '盘前'],
  closed: ['', '休市'],
};

function paintStatus(st) {
  S.status = st;
  $('#ver').textContent = 'v' + (st.version || '1.0');
  const [cls, txt] = SESSION_BADGE[st.session] || ['', st.session_text || '—'];
  const b = $('#sessionBadge');
  b.className = 'badge ' + cls;
  b.textContent = txt + (st.session === 'trading' ? ' · ' + st.now.slice(11, 16) : '');

  $('#srcLabel').textContent = st.source_label || st.data_source || '—';
  $('#benchDate').textContent = st.bench_asof || '缺失';
  $('#atClock').textContent = st.now ? st.now.slice(11, 16) : '—';
  $('#holdCount').textContent = '持仓 ' + (st.holdings_count ?? 0) + ' 只';

  const w = [];
  if (st.bench_error) w.push('本地基准缺失：' + st.bench_error);
  if (st.source_ok === false && st.source_note) w.push(st.source_note);
  if (st.data_source === 'demo') w.push('当前是演示数据，不是真实行情。改 config.json 的 data_source 接真实源。');
  renderWarns(w);
}

function renderWarns(list) {
  $('#warns').innerHTML = (list || []).map((t) =>
    `<div class="warn-item">${esc(t).replace(/\n/g, '<br>')}</div>`).join('');
}

/* ---------------------------------------------------------------- 扫描 */

async function startScan() {
  if (S.busy) return;
  S.busy = true;
  $('#btnScan').disabled = true;
  $('#prog').classList.add('busy');
  $('#progText').textContent = '启动中…';

  try {
    const at = ($('#atInput').value || '').trim();
    await api('/api/scan', { method: 'POST', body: at ? { at } : {} });
    pollScan();
  } catch (e) {
    S.busy = false;
    $('#btnScan').disabled = false;
    $('#prog').classList.remove('busy');
    $('#progText').textContent = '失败';
    toast('扫描启动失败：' + e.message, 5000);
  }
}

function pollScan() {
  clearTimeout(S.polling);
  const loop = async () => {
    let st;
    try {
      st = await api('/api/scan/state');
    } catch (e) {
      $('#progText').textContent = '状态查询失败';
      S.busy = false; $('#btnScan').disabled = false;
      return;
    }

    const pct = Math.round((st.progress || 0) * 100);
    $('#progBar').style.width = pct + '%';
    $('#progText').textContent = st.running ? `${st.msg || st.stage} ${pct}%` : (st.error ? '出错' : '完成');

    if (st.running) {
      S.polling = setTimeout(loop, 400);
      return;
    }

    // 结束
    S.busy = false;
    $('#btnScan').disabled = false;
    $('#prog').classList.remove('busy');

    if (st.error) {
      $('#progText').textContent = '出错';
      renderWarns(['扫描失败：' + st.error]);
      toast('扫描失败', 4000);
      return;
    }
    if (st.result) {
      renderResult(st.result);
      const pass = (st.result.counts || {}).pass || 0;
      if (pass) {
        $('#progText').textContent = `完成 · 可打 ${pass} 只`;
        toast(`发现 ${pass} 只可打标的`, 4000);
      } else {
        $('#progText').textContent = '完成 · 无可打标的';
      }
    }
  };
  loop();
}

/* ---------------------------------------------------------------- 渲染结果 */

function renderResult(r) {
  S.result = r;
  renderFunnel(r.funnel, r.filters);
  renderGroups(r);
  renderWarns(r.warnings || []);
}

function renderFunnel(f, filters) {
  const pct = (a, b) => b ? Math.round((a / b) * 100) + '%' : '';
  const steps = [
    ['全市场', f.total, '', ''],
    [`涨幅≥${filters.min_gain_pct}%`, f.gain, pct(f.gain, f.total), ''],
    ['开盘进场区间', f.open_range, pct(f.open_range, f.gain), ''],
    [`量比<${filters.vr_max}`, f.vr_ok, pct(f.vr_ok, f.open_range), ''],
    ['全条件通过', f.clean, pct(f.clean, f.vr_ok), 'good'],
    ['其中已封板', f.sealed, '', 'hot'],
  ];
  $('#funnel').innerHTML = steps.map(([k, v, d, cls]) => `
    <div class="fstep">
      <div class="k">${esc(k)}</div>
      <div class="v ${cls === 'hot' ? 'hot' : ''}">${v}</div>
      <div class="d ${cls === 'good' ? 'good' : ''}">${d ? '上一步的 ' + d : ''}</div>
    </div>`).join('');
}

const GROUPS = [
  { key: 'pass', title: '可打', tip: '全条件通过且已封板 —— 挂涨停价排队，买不进就空仓' },
  { key: 'watch', title: '盯住', tip: '条件都够但还没封 —— 封不上就不买，别抢跑' },
  { key: 'reject', title: '剔除', tip: '差在哪一步，看最后一列' },
];

function verdictTags(x) {
  // 剔除的票只说「差在哪一步」，不再重复列已经通过的条件，否则一屏全是标签
  if (x.stage === 'reject' && x.rejects && x.rejects.length) {
    return `<div class="tags"><span class="tg bad">${esc(x.rejects.join('；'))}</span></div>`;
  }
  return `<div class="tags">${x.flags.map((f) =>
    `<span class="tg ${f.level}">${esc(f.text)}</span>`).join('')}</div>`;
}

function vrClass(v) {
  return v < 1 ? 'vr-ok' : (v < 2 ? 'vr-warn' : 'vr-bad');
}

function rowHtml(x) {
  const seal = x.seal_money ? Math.round(x.seal_money / 1e4).toLocaleString('zh-CN') : '—';
  return `<tr>
    <td class="l code">${esc(x.code)}</td>
    <td class="l name">${esc(x.name || '—')}</td>
    <td>${signed(x.gain)}</td>
    <td>${signed(x.open_gain)}</td>
    <td class="${vrClass(x.vr)}">${num(x.vr)}</td>
    <td>${esc(x.seal_time || '—')}</td>
    <td>${esc(x.board_text || (x.boards ? x.boards + '板' : '—'))}</td>
    <td>${seal}</td>
    <td class="dim">${x.to_limit_pct != null ? num(x.to_limit_pct) + '%' : '—'}</td>
    <td class="l">${verdictTags(x)}</td>
  </tr>`;
}

function renderGroups(r) {
  const box = $('#groups');
  if (!r.rows || !r.rows.length) {
    box.innerHTML = `<div class="card prose"><h3>这一次没有票进入候选</h3>
      <p>说明今天这个时点全市场没有「涨幅 ≥${r.filters.min_gain_pct}%」的票，
      或者行情还没更新。这是正常的 —— <b>没票就空仓</b>，平均每天也就 2~3 个机会。</p>
      <p class="dim">当前时点 ${esc(r.at)} · ${esc(r.session_text)} · 快照 ${r.funnel.total} 只</p></div>`;
    return;
  }

  const LIMIT = 12;
  box.innerHTML = GROUPS.map((g) => {
    const list = r.rows.filter((x) => x.stage === g.key);
    if (!list.length) return '';
    const rows = list.map(rowHtml).join('');
    const more = list.length > LIMIT
      ? `<div class="pad dim" style="text-align:center"><button class="ghost sm" data-expand="${g.key}">展开其余 ${list.length - LIMIT} 只</button></div>`
      : '';
    return `<section class="group ${g.key}">
      <h2 class="group-head">${g.title} <span class="n">${list.length} 只</span>
        <span class="tip">${esc(g.tip)}</span></h2>
      <div class="tw"><table>
        <thead><tr>
          <th>代码</th><th>名称</th><th>涨幅</th><th>开盘</th><th>量比</th>
          <th>首封</th><th>连板</th><th>封单/万</th><th>距涨停</th><th class="l">判定</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>${more}</section>`;
  }).join('');

  // 折叠超过 12 行的组
  $$('.group .tw tbody').forEach((tb) => {
    const rows = Array.from(tb.children);
    rows.forEach((tr, i) => { if (i >= LIMIT) tr.style.display = 'none'; });
  });
  $$('[data-expand]').forEach((b) => {
    b.onclick = () => {
      const g = b.closest('.group');
      g.querySelectorAll('tbody tr').forEach((tr) => { tr.style.display = ''; });
      b.parentElement.remove();
    };
  });
}

/* ---------------------------------------------------------------- 筛选条件 */

function fillFilters(f) {
  $('#f_vr').value = f.vr_max;
  $('#f_gain').value = f.min_gain_pct;
  $('#f_ogmin').value = f.open_gain_min_pct;
  $('#f_ogmax').value = f.open_gain_max_pct;
  $('#f_boards').value = f.max_boards;
  $('#f_st').checked = !!f.exclude_st;
  $('#f_bj').checked = !!f.exclude_bj;
  $('#f_sealed').checked = !!f.require_sealed;
}

async function saveFilters() {
  const filters = {
    vr_max: parseFloat($('#f_vr').value),
    min_gain_pct: parseFloat($('#f_gain').value),
    open_gain_min_pct: parseFloat($('#f_ogmin').value),
    open_gain_max_pct: parseFloat($('#f_ogmax').value),
    max_boards: parseInt($('#f_boards').value, 10),
    exclude_st: $('#f_st').checked,
    exclude_bj: $('#f_bj').checked,
    require_sealed: $('#f_sealed').checked,
  };
  try {
    const d = await api('/api/filters', { method: 'POST', body: { filters } });
    fillFilters(d.filters);
    toast('已保存，正在重扫');
    startScan();
  } catch (e) {
    toast('保存失败：' + e.message, 4000);
  }
}

/* ---------------------------------------------------------------- 持仓 */

async function loadHoldings() {
  try {
    const d = await api('/api/holdings');
    renderHoldings(d.items || []);
  } catch (e) {
    $('#holdList').innerHTML = `<p class="dim pad">读不到持仓：${esc(e.message)}</p>`;
  }
}

function renderHoldings(items) {
  const box = $('#holdList');
  if (!items.length) {
    box.innerHTML = '<p class="dim pad">还没有持仓。在下面记一笔。</p>';
    return;
  }
  box.innerHTML = items.map((h) => `
    <div class="sell-row">
      <div>
        <div class="name">${esc(h.name || h.code)}</div>
        <div class="lbl">${esc(h.code)} · 买于 ${esc(h.buy_date || '—')} · ${h.shares || 0} 股</div>
      </div>
      <div>
        <div class="lbl">挂单价（跌停）</div>
        <div class="floor">${h.floor_price != null ? num(h.floor_price) : '需手填昨收'}</div>
      </div>
      <div>
        <div class="lbl">昨收 / 现价</div>
        <div class="val">${num(h.prev_close)} / ${num(h.last_price)}</div>
      </div>
      <div>
        <div class="lbl">浮动</div>
        <div class="val">${signed(h.pnl_pct)}</div>
      </div>
      <div>
        <div class="act">${esc(h.action)}</div>
        <button class="btn-x" data-del="${esc(h.code)}">删除</button>
      </div>
    </div>`).join('');

  $$('[data-del]').forEach((b) => {
    b.onclick = async () => {
      try {
        const d = await api('/api/holdings?code=' + encodeURIComponent(b.dataset.del),
          { method: 'DELETE' });
        renderHoldings(d.items || []);
        refreshStatus();
        toast('已删除');
      } catch (e) { toast('删除失败：' + e.message, 4000); }
    };
  });
}

async function addHolding() {
  const code = ($('#h_code').value || '').trim();
  if (!code) { toast('先填代码'); return; }
  const body = {
    code,
    name: ($('#h_name').value || '').trim(),
    buy_price: parseFloat($('#h_price').value) || null,
    shares: parseInt($('#h_shares').value, 10) || 0,
    buy_date: $('#h_date').value || null,
  };
  try {
    const d = await api('/api/holdings', { method: 'POST', body });
    renderHoldings(d.items || []);
    ['#h_code', '#h_name', '#h_price', '#h_shares'].forEach((s) => { $(s).value = ''; });
    refreshStatus();
    toast('已记下');
  } catch (e) { toast('保存失败：' + e.message, 4000); }
}

/* ---------------------------------------------------------------- 自动刷新 */

function setupAuto() {
  const apply = () => {
    clearInterval(S.autoTimer);
    if (!$('#autoOn').checked) return;
    const sec = parseInt($('#autoSec').value, 10) || 120;
    S.autoTimer = setInterval(() => {
      if (!S.busy) startScan();
    }, sec * 1000);
  };
  $('#autoOn').onchange = apply;
  $('#autoSec').onchange = apply;
}

/* ---------------------------------------------------------------- 初始化 */

async function refreshStatus() {
  try { paintStatus(await api('/api/status')); }
  catch (e) { renderWarns(['读不到服务状态：' + e.message]); }
}

function setupTabs() {
  const activate = (name) => {
    $$('.tab').forEach((x) => x.classList.toggle('on', x.dataset.tab === name));
    $$('.pane').forEach((p) => p.classList.toggle('on', p.id === 'pane-' + name));
    if (name === 'hold') loadHoldings();
  };
  $$('.tab').forEach((t) => { t.onclick = () => activate(t.dataset.tab); });
  return activate;
}

async function init() {
  tickClock();
  setInterval(tickClock, 1000);

  // 心跳：告诉后端「页面还开着」。无窗口启动时没有「关掉黑窗口」这个动作，
  // 全靠它收尾 —— 所有标签页关掉后后端会自己退出，不在后台留进程。
  const heartbeat = () => fetch('/api/ping', { cache: 'no-store' }).catch(() => {});
  heartbeat();
  setInterval(heartbeat, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) heartbeat();
  });

  const activateTab = setupTabs();
  setupAuto();

  $('#btnScan').onclick = startScan;
  $('#btnFilter').onclick = () => {
    const box = $('#filterBox');
    box.hidden = !box.hidden;
  };
  $('#btnSaveFilter').onclick = saveFilters;
  $('#btnAddHold').onclick = addHolding;
  $('#h_date').value = new Date().toISOString().slice(0, 10);

  await refreshStatus();
  if (S.status.filters) fillFilters(S.status.filters);

  // 支持 ?tab=hold 直接打开某一页，方便收藏
  const want = new URLSearchParams(location.search).get('tab');
  if (want && ['scan', 'hold', 'help'].includes(want)) activateTab(want);
  else loadHoldings();

  // 页面打开就自动扫一次：交易时段直接扫，非交易时段只显示不扫
  if (S.status.session === 'trading' || S.status.session === 'auction') {
    startScan();
  } else {
    $('#progText').textContent = `${S.status.session_text || '休市'} · 点「开始扫描」可查看最近收盘数据`;
  }
  setInterval(refreshStatus, 60000);
}

init();
