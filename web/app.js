/* 交易台 —— 前端逻辑。没有框架，没有构建，改完刷新就生效。 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const S = {
  status: {},
  result: null,
  polling: null,
  autoTimer: null,
  busy: false,
  tab: 'scan',
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
    const mode = $('#modeSel').value || 'dban';
    await api('/api/scan', { method: 'POST', body: { at: at || undefined, mode } });
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
  renderFunnel(r.funnel, r.filters, r.mode);
  renderGroups(r);
  renderWarns(r.warnings || []);
}

function renderFunnel(f, filters, mode) {
  const pct = (a, b) => b ? Math.round((a / b) * 100) + '%' : '';
  const steps = (mode === 'banlu') ? [
    ['全市场', f.total, '', ''],
    ['20cm（创业/科创）', f.cm20 ?? 0, pct(f.cm20 ?? 0, f.total), ''],
    ['开盘未越过触发价', f.open_ok ?? 0, pct(f.open_ok ?? 0, f.cm20 ?? 1), ''],
    ['已触发（涨过 5%）', f.touched ?? 0, pct(f.touched ?? 0, f.open_ok ?? 1), ''],
    [`高开>${filters.min_open_gap_pct ?? 2}%`, f.gap_ok ?? 0, pct(f.gap_ok ?? 0, f.touched ?? 1), ''],
    ['全条件通过', f.clean ?? 0, pct(f.clean ?? 0, f.gap_ok ?? 1), 'good'],
    ['其中已封板', f.sealed ?? 0, '', 'hot'],
  ] : [
    ['全市场', f.total, '', ''],
    [`涨幅≥${filters.min_gain_pct}%`, f.gain, pct(f.gain, f.total), ''],
    ['开盘进场区间', f.open_range, pct(f.open_range, f.gain), ''],
    [`价格≥${filters.min_price ?? 0}元`, f.price_ok, pct(f.price_ok, f.open_range), ''],
    ['全条件通过', f.clean, pct(f.clean, f.price_ok), 'good'],
    ['其中已封板', f.sealed, '', 'hot'],
  ];
  $('#funnel').innerHTML = steps.map(([k, v, d, cls]) => `
    <div class="fstep">
      <div class="k">${esc(k)}</div>
      <div class="v ${cls === 'hot' ? 'hot' : ''}">${v}</div>
      <div class="d ${cls === 'good' ? 'good' : ''}">${d ? '上一步的 ' + d : ''}</div>
    </div>`).join('');
}

function groupTips(mode) {
  return (mode === 'banlu') ? [
    { key: 'pass', title: '已触发', tip: '现价已越过触发价且全条件通过 —— 触发价附近买，14:30 后不接' },
    { key: 'watch', title: '盯住', tip: '条件都够但还没涨到 5% —— 触发了再买，别抢跑' },
    { key: 'reject', title: '剔除', tip: '差在哪一步，看最后一列' },
  ] : [
    { key: 'pass', title: '可打', tip: '全条件通过且已封板 —— 挂涨停价排队，买不进就空仓' },
    { key: 'watch', title: '盯住', tip: '条件都够但还没封 —— 封不上就不买，别抢跑' },
    { key: 'reject', title: '剔除', tip: '差在哪一步，看最后一列' },
  ];
}

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

function rowHtml(x, mode) {
  const seal = x.seal_money ? Math.round(x.seal_money / 1e4).toLocaleString('zh-CN') : '—';
  // data-label 给窄屏用：手机上表格变卡片，靠它显示字段名
  if (mode === 'banlu') {
    return `<tr>
    <td class="l code" data-label="代码">${esc(x.code)}</td>
    <td class="l name" data-label="名称">${esc(x.name || '—')}</td>
    <td data-label="涨幅">${signed(x.gain)}</td>
    <td data-label="开盘">${signed(x.open_gain)}</td>
    <td data-label="触发价">${num(x.trigger_price)}</td>
    <td data-label="距触发" class="${(x.to_trigger_pct ?? 9) <= 0 ? 'up' : ''}">${x.to_trigger_pct != null ? signed(x.to_trigger_pct) : '—'}</td>
    <td data-label="连板">${esc(x.board_text || (x.boards ? x.boards + '板' : '—'))}</td>
    <td class="dim" data-label="距涨停">${x.to_limit_pct != null ? num(x.to_limit_pct) + '%' : '—'}</td>
    <td class="l" data-label="判定">${verdictTags(x)}</td>
  </tr>`;
  }
  return `<tr>
    <td class="l code" data-label="代码">${esc(x.code)}</td>
    <td class="l name" data-label="名称">${esc(x.name || '—')}</td>
    <td data-label="涨幅">${signed(x.gain)}</td>
    <td data-label="开盘">${signed(x.open_gain)}</td>
    <td data-label="价格">${num(x.price)}</td>
    <td data-label="首封">${esc(x.seal_time || '—')}</td>
    <td data-label="连板">${esc(x.board_text || (x.boards ? x.boards + '板' : '—'))}</td>
    <td data-label="封单/万">${seal}</td>
    <td class="dim" data-label="距涨停">${x.to_limit_pct != null ? num(x.to_limit_pct) + '%' : '—'}</td>
    <td class="l" data-label="判定">${verdictTags(x)}</td>
  </tr>`;
}

function renderGroups(r) {
  const box = $('#groups');
  const mode = r.mode;
  const GROUPS = groupTips(mode);
  if (!r.rows || !r.rows.length) {
    const why = (mode === 'banlu')
      ? `没有 20cm 票同时满足「开盘未越过触发价 + 高开>${esc(r.filters.min_open_gap_pct ?? 2)}% + 首板」，或者还没涨到 5%。`
      : `说明今天这个时点全市场没有「涨幅 ≥${r.filters.min_gain_pct}%」的票，或者行情还没更新。`;
    box.innerHTML = `<div class="card prose"><h3>这一次没有票进入候选</h3>
      <p>${why} 这是正常的 —— <b>没票就空仓</b>。</p>
      <p class="dim">当前时点 ${esc(r.at)} · ${esc(r.session_text)} · 快照 ${r.funnel.total} 只</p></div>`;
    return;
  }

  const LIMIT = 12;
  const heads = (mode === 'banlu')
    ? '<th>代码</th><th>名称</th><th>涨幅</th><th>开盘</th><th>触发价</th><th>距触发</th><th>连板</th><th>距涨停</th><th class="l">判定</th>'
    : '<th>代码</th><th>名称</th><th>涨幅</th><th>开盘</th><th>价格</th><th>首封</th><th>连板</th><th>封单/万</th><th>距涨停</th><th class="l">判定</th>';
  box.innerHTML = GROUPS.map((g) => {
    const list = r.rows.filter((x) => x.stage === g.key);
    if (!list.length) return '';
    const rows = list.map((x) => rowHtml(x, mode)).join('');
    const more = list.length > LIMIT
      ? `<div class="pad dim" style="text-align:center"><button class="ghost sm" data-expand="${g.key}">展开其余 ${list.length - LIMIT} 只</button></div>`
      : '';
    return `<section class="group ${g.key}">
      <h2 class="group-head">${g.title} <span class="n">${list.length} 只</span>
        <span class="tip">${esc(g.tip)}</span></h2>
      <div class="tw"><table>
        <thead><tr>${heads}</tr></thead>
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
  $('#f_price').value = f.min_price ?? 0;
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
    min_price: parseFloat($('#f_price').value),
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

/* ---------------------------------------------------------------- R3 卫星轮动 */

const R3 = { data: null, timer: null, polling: null, asof: null };

async function loadR3(force) {
  const q = [];
  if (force) q.push('force=1');
  if (R3.asof) q.push('asof=' + encodeURIComponent(R3.asof));
  try {
    const d = await api('/api/r3' + (q.length ? '?' + q.join('&') : ''));
    if (!d.ok) {
      $('#r3bar').innerHTML = `<span class="tg bad">R3 取数失败：${esc(d.error || '未知错误')}</span>`;
      return;
    }
    R3.data = d;
    renderR3(d);
  } catch (e) {
    $('#r3bar').innerHTML = `<span class="tg bad">R3 读不到：${esc(e.message)}</span>`;
  }
}

function chip(k, v) {
  return `<span class="chip"><i>${esc(k)}</i><b>${v}</b></span>`;
}

function renderR3(d) {
  const sw = d.switch || {};
  const p = d.push || {};
  const modeTxt = d.is_rebalance_day
    ? '<span class="up">调仓日</span>'
    : `非调仓日（下次 ${esc(d.next_rebalance || '—')}${d.next_in_td != null ? `，还需 ${d.next_in_td} 个交易日` : ''}）`;
  const swTxt = sw.ok
    ? `<span class="up">不空仓</span>`
    : `<span class="dn">空仓</span>`;
  $('#r3bar').innerHTML = [
    chip('基准日', esc(d.as_of || '—')),
    chip('今天', modeTxt),
    chip('空仓开关', swTxt),
    chip('沪深300', sw.hs300_close != null
      ? `${num(sw.hs300_close)} / MA20 ${num(sw.hs300_ma20)}` : '—'),
    chip('距 MA20', sw.dev_pct != null ? signed(sw.dev_pct) : '—'),
    chip('止损口径', esc(d.stop_mode || '—')),
    chip('自动推送', (p.enabled && p.auto)
      ? `开 ${esc(p.after || '15:00')}~${esc(p.until || '22:00')}` : '<span class="flat">关</span>'),
    chip('K线到', esc(d.latest_date || '—')),
    chip('拉取', esc(d.fetched_at || '—')),
  ].join('');
  if (d.replay) {
    $('#r3bar').insertAdjacentHTML('beforeend',
      chip('模式', `<span class="up">回放 ${esc(d.as_of || '')}</span> <a href="#" id="r3Live" style="color:var(--acc)">回到最新</a>`));
    const a = $('#r3Live');
    if (a) a.onclick = (ev) => { ev.preventDefault(); R3.asof = null; loadR3(true); };
  }

  // 今天该做什么
  const bits = [];
  if (d.sell_all) bits.push('空仓开关触发 → 全部卖出');
  if (!d.is_rebalance_day) bits.push('非调仓日：清单只作预览，别提前动手');
  if (d.replay) bits.push('回放视图，不作为执行依据');
  if (!d.positions || !d.positions.length) bits.push('当前空仓，6,000 元放 1 天期逆回购');
  $('#r3Action').innerHTML =
    `<div class="big">${esc(d.action || '—')}</div>` +
    (d.note ? `<p class="dim" style="margin:6px 0 0">${esc(d.note)}</p>` : '') +
    (bits.length ? `<p class="dim" style="margin:6px 0 0">${bits.join(' · ')}</p>` : '');

  const al = d.alerts || [];
  $('#r3Alerts').innerHTML = al.map((t) => `<div class="r3-alert">⚠️ ${esc(t)}</div>`).join('');

  // 推送状态 / 更新状态
  $('#r3PushAt').innerHTML = p.at
    ? `上次推送 ${esc(p.at)}（${esc(p.label || '')}）`
    : '<span class="dim">还没推送过</span>';
  const u = d.update || {};
  if (u.running) {
    $('#btnR3Update').disabled = true;
    $('#btnR3Update').textContent = '更新中…';
    if (!R3.polling) R3.polling = setInterval(() => loadR3(false), 3000);
  } else {
    $('#btnR3Update').disabled = false;
    $('#btnR3Update').textContent = '更新数据';
    if (R3.polling) { clearInterval(R3.polling); R3.polling = null; loadR3(true); }
    if (u.ok === false && u.tail) $('#r3PushAt').innerHTML +=
      `<br><span class="dn">上次更新失败：${esc(u.tail)}</span>`;
  }

  renderR3Buy(d);
  renderR3Hold(d);
  renderR3Pool(d);
  renderR3Exclude(d);

  const sm = d.summary || {};
  $('#r3Summary').innerHTML = sm.positions
    ? `市值 ${num(sm.market_value, 0)} 元 · 浮盈 ${signed(sm.pnl_pct)}（${sm.pnl_amt > 0 ? '+' : ''}${num(sm.pnl_amt, 0)} 元）`
    : '空仓';
}

function renderR3Buy(d) {
  const list = d.buy || [];
  if (!list.length) {
    const why = d.sell_all ? '空仓开关触发，本次不买。'
      : (d.is_rebalance_day ? '本次没有要买的（目标已全部持有，或池内没有站上 MA20 的标的）。'
        : '非调仓日不产生买入清单，等下次调仓日。');
    $('#r3Buy').innerHTML = `<p class="dim pad">${why}</p>`;
    return;
  }
  const rows = list.map((x, i) => `<tr>
    <td>${i + 1}</td>
    <td class="l name">${esc(x.name)}</td>
    <td class="l code">${esc(x.code)}</td>
    ${edCell({ code: x.code, field: 'buy', value: x.suggest_price, custom: x.buy_custom })}
    <td class="dim">${num(x.limit_price, 3)}</td>
    ${edCell({ code: x.code, field: 'stop', value: x.stop_price, custom: x.stop_custom, cls: 'stopc' })}
    ${edCell({ code: x.code, field: 'target', value: x.target, custom: x.target_custom })}
    <td class="dim">${x.stop_dist_pct != null ? num(x.stop_dist_pct) + '%' : '—'}</td>
    <td>${x.shares}</td>
    <td>${num(x.amount, 0)}</td>
    <td><button class="btn-x" data-lvreset="${esc(x.code)}" title="清掉这只的自定义价">↺</button></td>
  </tr>`).join('');
  $('#r3Buy').innerHTML = `<div class="tw"><table>
    <thead><tr><th>优先</th><th class="l">名称</th><th class="l">代码</th><th>买入价</th>
    <th>挂单上限</th><th>止损价</th><th>卖出参考</th><th>止损距离</th><th>份数</th><th>金额/元</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="hint" style="padding:10px 16px 0">
      买入价默认 = 基准日收盘；止损默认 = 买入价 −1×N。明早按<b>开盘实际成交价</b>下单，成交后到下面「记一笔成交」填真实价格。
      点数字可改，改完自动存（<code>data/levels.json</code>）。</p>`;
}

function r3StateCell(x, bad) {
  if (bad) return '<span class="tg bad">已破止损 · 明日开盘卖该只</span>';
  if (x.note) return `<span class="tg warn">${esc(x.note)}</span>`;
  return '<span class="tg ok">持有</span>';
}

function renderR3Hold(d) {
  const list = d.positions || [];
  if (!list.length) {
    $('#r3Hold').innerHTML = '<p class="dim pad">空仓。买入成交后在下面记一笔，这里才有数。</p>';
    return;
  }
  const rows = list.map((x) => {
    const bad = x.rt_broken != null ? x.rt_broken : x.broken;
    const px = x.rt_price != null ? x.rt_price : x.close;
    const dist = x.rt_dist_stop_pct != null ? x.rt_dist_stop_pct : x.dist_stop_pct;
    return `<tr class="${bad ? 'badrow' : ''}">
      <td class="l name">${esc(x.name)}</td>
      <td class="l code">${esc(x.code)}</td>
      <td>${x.shares}</td>
      ${edCell({ code: x.code, field: 'cost', value: x.cost, custom: x.cost_custom })}
      <td>${px != null ? num(px, 3) : '—'}${x.rt_pct != null ? ' ' + signed(x.rt_pct) : ''}</td>
      <td>${signed(x.pnl_pct)}</td>
      ${edCell({ code: x.code, field: 'stop', value: x.stop_price, custom: x.stop_custom, cls: 'stopc' })}
      <td class="${bad ? 'dn' : ''}">${dist != null ? signed(dist) : '—'}</td>
      ${edCell({ code: x.code, field: 'target', value: x.target, custom: x.target_custom })}
      <td class="l">${r3StateCell(x, bad)}</td>
      <td><button class="btn-x" data-lvreset="${esc(x.code)}" title="清掉这只的自定义价">↺</button></td>
      <td><button class="btn-x" data-r3del="${esc(x.code)}">删除</button></td>
    </tr>`;
  }).join('');
  $('#r3Hold').innerHTML = `<div class="tw"><table>
    <thead><tr><th class="l">名称</th><th class="l">代码</th><th>份数</th><th>成本</th><th>现价</th>
    <th>浮盈</th><th>止损价</th><th>距止损</th><th>卖出参考</th><th class="l">状态</th><th></th><th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="hint" style="padding:10px 16px 0">止损价默认 = 该只 MA20（收盘破线 → 次日开盘卖这一只）。它随均线移动，可能高于成本 —— 那说明这只早就不该拿着。
      想用自己的止损位就点数字改；<b>改价格不重跑脚本，判定（是否调仓/买谁）仍按规则走。</b></p>`;

  $$('[data-r3del]').forEach((b) => {
    b.onclick = async () => {
      if (!confirm(`删除 ${b.dataset.r3del} 的持仓记录？`)) return;
      try {
        const r = await api('/api/r3/holdings?code=' + encodeURIComponent(b.dataset.r3del),
          { method: 'DELETE' });
        if (r.r3) { R3.data = r.r3; renderR3(r.r3); }
        toast('已删除');
      } catch (e) { toast('删除失败：' + e.message, 4000); }
    };
  });
}

function renderR3Pool(d) {
  const list = (d.ranked || []).slice(0, 5);
  $('#r3PoolInfo').innerHTML = d.pool_n != null
    ? `池内 ${d.pool_n} 只 · 基准日 ${esc(d.as_of || '')} 收盘` : '—';
  if (!list.length) { $('#r3Pool').innerHTML = '<p class="dim pad">—</p>'; return; }
  $('#r3Pool').innerHTML = `<div class="tw"><table>
    <thead><tr><th>#</th><th class="l">名称</th><th class="l">代码</th><th>收盘</th>
    <th>60 日涨幅</th><th>MA20</th></tr></thead><tbody>
    ${list.map((x, i) => `<tr>
      <td>${i + 1}</td>
      <td class="l name">${esc(x.name)}</td>
      <td class="l code">${esc(x.code)}</td>
      <td>${num(x.close, 3)}</td>
      <td>${signed(x.ret60)}</td>
      <td class="${x.above_ma20 ? 'up' : 'dn'}">${x.above_ma20 ? '站上' : '在下'}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

async function r3AddFill() {
  const code = ($('#r3_code').value || '').trim();
  if (!code) { toast('先填代码'); return; }
  const body = {
    code,
    name: ($('#r3_name').value || '').trim(),
    shares: parseInt($('#r3_shares').value, 10) || 0,
    cost: parseFloat($('#r3_cost').value) || 0,
    buy_date: $('#r3_date').value || null,
  };
  try {
    const r = await api('/api/r3/holdings', { method: 'POST', body });
    if (r.r3) { R3.data = r.r3; renderR3(r.r3); }
    ['#r3_code', '#r3_name', '#r3_shares', '#r3_cost'].forEach((s) => { $(s).value = ''; });
    toast('已记下，止损价按成本重算');
  } catch (e) { toast('保存失败：' + e.message, 5000); }
}

async function r3Clear() {
  if (!confirm('清空 R3 全部持仓记录？（真卖了才点）')) return;
  try {
    const r = await api('/api/r3/clear', { method: 'POST', body: {} });
    if (r.r3) { R3.data = r.r3; renderR3(r.r3); }
    toast('已清空');
  } catch (e) { toast('清空失败：' + e.message, 4000); }
}

async function r3PushNow() {
  try {
    const r = await api('/api/r3/push', { method: 'POST', body: {} });
    toast(r.sent ? '已推送到飞书 ETF 群' : '没推：' + r.msg, 5000);
    if (r.r3) { R3.data = r.r3; renderR3(r.r3); }
  } catch (e) { toast('推送失败：' + e.message, 5000); }
}

/* ---------------------------------------------------------------- 三价台账（点格子改） */

const FIELD_CN = { cost: '成本价', buy: '买入价', stop: '止损价', target: '卖出价', shares: '份数' };

/** 可编辑的价格单元格。custom=true 表示你手改过（蓝色 + 蓝点）。
 *  scope: levels（候选三价，默认）｜pos（B线路持仓台账） */
function edCell(o) {
  const v = o.value == null ? '' : (o.int ? String(Math.round(Number(o.value)))
    : Number(o.value).toFixed(3));
  const cls = ['ed', o.custom ? 'custom' : '', o.cls || ''].filter(Boolean).join(' ');
  return `<td class="${cls}" data-code="${esc(o.code)}" data-field="${o.field}"
    data-scope="${o.scope || 'levels'}"
    title="点击修改 · 回车存 · Esc 取消 · 留空=回默认">${v || '—'}<i class="pen">✎</i></td>`;
}

function setupCellEdit() {
  document.addEventListener('click', (ev) => {
    const t = ev.target;
    const reset = t.closest ? t.closest('[data-lvreset]') : null;
    if (reset) {
      api('/api/levels?code=' + encodeURIComponent(reset.dataset.lvreset), { method: 'DELETE' })
        .then(() => { toast('已恢复默认价'); refreshActive(true); })
        .catch((e) => toast('恢复失败：' + e.message, 4000));
      return;
    }
    const td = t.closest ? t.closest('td.ed') : null;
    if (!td || td.querySelector('input')) return;
    editCell(td);
  });
}

function editCell(td) {
  const old = td.textContent.replace('✎', '').trim();
  const inp = document.createElement('input');
  inp.className = 'cellin';
  inp.type = 'number';
  inp.step = '0.001';
  inp.min = '0';
  inp.value = old === '—' ? '' : old;
  td.textContent = '';
  td.appendChild(inp);
  inp.focus();
  inp.select();

  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const v = inp.value.trim();
    if (!save || v === old) { refreshActive(false); return; }
    const scope = td.dataset.scope || 'levels';
    const url = scope === 'pos' ? '/api/bline/positions' : '/api/levels';
    const body = { code: td.dataset.code, field: td.dataset.field, value: v === '' ? null : parseFloat(v) };
    try {
      await api(url, { method: 'POST', body });
      toast(v === '' ? `${FIELD_CN[body.field] || ''} 已回默认` : `${FIELD_CN[body.field] || ''} 已存 ${v}`);
    } catch (e) {
      toast('保存失败：' + e.message, 4000);
    }
    refreshActive(false);
  };

  inp.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
    else if (e.key === 'Escape') { e.preventDefault(); done = true; refreshActive(false); }
  };
  inp.onblur = () => finish(true);
}

/** 改完价之后重拉当前页：走后端缓存，不会重新扫描。 */
function refreshActive(force) {
  if (S.tab === 'bline') loadBline(false);
  else if (S.tab === 'r3') loadR3(false);
}

/* ---------------------------------------------------------------- B线路选股 */

const BL = { data: null, editing: false };

async function loadBline(force) {
  const q = [];
  if (force) q.push('force=1');
  const cb = $('#blLive');
  if (cb && cb.checked) q.push('live=1');
  try {
    const d = await api('/api/bline' + (q.length ? '?' + q.join('&') : ''));
    if (!d.ok) {
      $('#blBar').innerHTML = `<span class="tg bad">B线路取数失败：${esc(d.error || '未知错误')}</span>`;
      return;
    }
    BL.data = d;
    renderBline(d);
  } catch (e) {
    $('#blBar').innerHTML = `<span class="tg bad">B线路读不到：${esc(e.message)}</span>`;
  }
}

function blPrice(x) {
  return x.rt_price != null ? x.rt_price : x.close;
}

function blDist(x) {
  const v = x.rt_dist_stop_pct != null ? x.rt_dist_stop_pct : x.stop_dist_pct;
  return v == null ? '<span class="flat">—</span>' : signed(v);
}

function blRow(x, i) {
  const px = blPrice(x);
  const bad = x.rt_broken || (x.buy && x.stop && px != null && px <= x.stop);
  return `<tr class="${bad ? 'badrow' : ''}">
    ${i == null ? '' : `<td>${i + 1}</td>`}
    <td class="l name">${esc(x.name)}</td>
    <td class="l code">${esc(x.code)}</td>
    <td>${px != null ? num(px, 3) : '—'}${x.rt_pct != null ? ' ' + signed(x.rt_pct) : ''}</td>
    <td class="${(x.dist60 != null && x.dist60 <= 3) ? 'up' : ''}">${x.dist60 != null ? num(x.dist60) + '%' : '—'}</td>
    <td class="${(x.vol20 != null && x.vol20 > 2) ? 'dn' : ''}">${num(x.vol20)}</td>
    <td>${x.dev20 != null ? signed(x.dev20) : '—'}</td>
    ${edCell({ code: x.code, field: 'buy', value: x.buy, custom: x.buy_custom })}
    ${edCell({ code: x.code, field: 'stop', value: x.stop, custom: x.stop_custom, cls: 'stopc' })}
    ${edCell({ code: x.code, field: 'target', value: x.target, custom: x.target_custom })}
    <td class="${bad ? 'dn' : ''}">${blDist(x)}</td>
    <td>${x.shares != null ? x.shares : '—'}</td>
    <td class="l dim">${esc(x.focus ? (x.focus_note || 'focus 命中') : (x.focus_note || '未达标'))}</td>
    <td class="l">${x.held
      ? `<span class="tg ok">已持有 ${x.held} 份</span>`
      : `<button class="btn-x" data-bladd="${esc(x.code)}" title="把这只记成一笔买入">记</button>`}</td>
    <td class="nowrap">
      <button class="btn-x" data-lvreset="${esc(x.code)}" title="清掉这只的自定义价">↺</button>
      ${BL.editing ? `<button class="btn-x danger" data-poolrm="${esc(x.code)}"
        data-poolname="${esc(x.name || '')}" title="从观察池移除（物理删行）">出池</button>` : ''}
    </td>
  </tr>`;
}

function renderBline(d) {
  $('#blBar').innerHTML = [
    chip('基准日', esc(d.as_of || '—')),
    chip('档位', `<b class="${d.tier_code === 'A' ? 'up' : (d.tier_code === 'D' ? 'dn' : '')}">${esc(d.tier_code || '—')}</b>`),
    chip('站上 MA20', `${d.above_count != null ? d.above_count : '—'}/3`),
    chip('沪深300 距MA20', signed(d.hs300_dev)),
    chip('5 日', signed(d.hs300_5d)),
    chip('focus', d.focus_active
      ? `<span class="up">${d.focus_pool_n} 只</span>`
      : (d.focus_pool_n ? `${d.focus_pool_n} 只（不足 2 只→观察）` : '<span class="flat">0</span>')),
    chip('B线持仓', (d.bpos_summary && d.bpos_summary.positions)
      ? `${d.bpos_summary.positions} 只` +
        (d.bpos_summary.broken ? ` · <b class="dn">${d.bpos_summary.broken} 只破线</b>` : '')
      : '<span class="flat">空仓</span>'),
    chip('口径', d.live ? '<span class="up">盘中</span>' : '收盘'),
    chip('拉取', esc(d.fetched_at || '—') + (d.cached ? ' · 缓存' : '')),
  ].join('');
  $('#blAt').innerHTML = (d.levels_count
    ? `台账 ${d.levels_count} 只改过价 · ${esc(d.levels_updated || '')}`
    : '<span class="dim">三价还是默认值（点数字就能改）</span>');

  const cNote = (d.tier_code === 'D' && d.c_env)
    ? `<p class="dim" style="margin:6px 0 0">C 类环境成立（大盘 5 日 ${num(d.hs300_5d)}%）：
        深跌反弹候选 ${d.candidates_c && d.candidates_c.length ? esc(d.candidates_c.join('、')) : '无'} ——
        试探仓，止损＝成交价−1×N，回 MA20 / −1N / 15 日封顶出场。</p>`
    : (d.tier_code === 'D'
      ? '<p class="dim" style="margin:6px 0 0">C 类环境未成立（大盘 5 日跌幅未到 2%）→ 今日不开仓。</p>' : '');
  $('#blAction').innerHTML = `<div class="big">${esc(d.tier_action || '—')}</div>
    <p style="margin:6px 0 0">本档进场条件：${esc(d.tier_rule || '—')}</p>
    ${cNote}
    ${d.stale ? '<p class="dim" style="margin:6px 0 0">⚠️ K 线不是最新交易日，可到「R3卫星轮动」页点「更新数据」。</p>' : ''}`;

  const rows = d.rows || [];
  const focusRows = rows.filter((x) => x.focus);
  $('#blFocusInfo').innerHTML = d.focus_active
    ? `命中 ${focusRows.length} 只 · 按距 60 日高升序 · 尾盘复核后算数`
    : (d.focus_pool_n === 1 ? '仅 1 只 → 降级观察，不作为进场依据'
      : `0 只（${esc(d.tier_code || '')} 档）`);
  if (focusRows.length) {
    $('#blFocus').innerHTML = `<div class="tw"><table>
      <thead><tr><th>优先</th><th class="l">名称</th><th class="l">代码</th><th>现价</th><th>距60日高</th>
      <th>vol20</th><th>乖离20</th><th>买入价</th><th>止损价</th><th>卖出参考</th><th>距止损</th><th>份数</th>
      <th class="l">判定</th><th class="l">状态</th><th></th></tr></thead>
      <tbody>${focusRows.map((x, i) => blRow(x, i)).join('')}</tbody></table></div>`;
  } else {
    $('#blFocus').innerHTML = `<p class="dim pad">${d.focus_pool_n === 1
      ? '仅 1 只达标：孤军新高伪突破率高，只观察。'
      : '今日无命中，空仓等主线。'}</p>`;
  }

  renderBlPos(d);

  if (!rows.length) {
    $('#blPool').innerHTML = '<p class="dim pad">观察池为空（检查 watchlist.json）。</p>';
    return;
  }
  $('#blPool').innerHTML = `<div class="tw"><table>
    <thead><tr><th class="l">名称</th><th class="l">代码</th><th>现价</th><th>距60日高</th><th>vol20</th>
    <th>乖离20</th><th>买入价</th><th>止损价</th><th>卖出参考</th><th>距止损</th><th>份数</th>
    <th class="l">判定</th><th class="l">状态</th><th></th></tr></thead>
    <tbody>${rows.map((x) => blRow(x, null)).join('')}</tbody></table></div>`;
}

/* ---------------------------------------------------------------- B线路持仓（已买入台账） */

function blPosStatus(p) {
  if (p.action) return `<span class="tg bad">${esc(p.action)}</span>`;
  if (p.rt_broken) return '<span class="tg warn">盘中触线 · 等 30 分钟 / 收盘判</span>';
  return `<span class="tg ok">持有 · 还剩 ${p.days_left} 个交易日</span>`;
}

function blPosRow(p) {
  const px = p.rt_price != null ? p.rt_price : p.close;
  const pnl = p.rt_pnl_pct != null ? p.rt_pnl_pct : p.pnl_pct;
  const dist = p.rt_dist_stop_pct != null ? p.rt_dist_stop_pct : p.dist_stop_pct;
  const bad = p.rt_broken != null ? p.rt_broken : p.broken;
  return `<tr class="${bad ? 'badrow' : ''}">
    <td class="l name">${esc(p.name)}</td>
    <td class="l code">${esc(p.code)}</td>
    ${edCell({ code: p.code, field: 'shares', value: p.shares, scope: 'pos', int: true })}
    ${edCell({ code: p.code, field: 'cost', value: p.cost, scope: 'pos' })}
    <td>${px != null ? num(px, 3) : '—'}${p.rt_pct != null ? ' ' + signed(p.rt_pct) : ''}</td>
    <td class="${pnl > 0 ? 'up' : (pnl < 0 ? 'dn' : '')}">${signed(pnl)}</td>
    ${edCell({ code: p.code, field: 'stop', value: p.stop_price, scope: 'pos', cls: 'stopc',
               custom: p.stop_custom || p.stop_ladder })}
    <td class="${bad ? 'dn' : ''}">${dist != null ? signed(dist) : '—'}</td>
    ${edCell({ code: p.code, field: 'target', value: p.target, scope: 'pos',
               custom: p.target_custom })}
    <td class="dim" title="买入日 ${esc(p.buy_date)} · 已持有 ${p.hold_td} 个交易日">${p.hold_td}/${p.hold_limit}${p.c_class ? ' C类' : ''}</td>
    <td class="l">${blPosStatus(p)}</td>
    <td><button class="btn-x" data-bldel="${esc(p.code)}">删除</button></td>
  </tr>`;
}

function renderBlPos(d) {
  const pos = d.positions || [];
  const sm = d.bpos_summary || {};
  if (!pos.length) {
    $('#blPosInfo').innerHTML = '<span class="dim">空仓</span>';
    $('#blPos').innerHTML = '<p class="dim pad">还没有 B线路 持仓。候选成交后在下面「记一笔买入」，'
      + '这一笔就从候选变成持仓，止损按成交价重新写定。</p>';
    return;
  }
  $('#blPosInfo').innerHTML = `${pos.length} 只 · 市值 ${num(sm.market_value, 0)} / ${num(sm.budget, 0)}`
    + `（${num(sm.used_pct, 1)}%）· 浮盈 <b class="${sm.pnl_amt > 0 ? 'up' : (sm.pnl_amt < 0 ? 'dn' : '')}">`
    + `${signed(sm.pnl_pct)}</b>`;

  const notes = pos.filter((p) => p.ladder || p.stop_custom || p.stop_ladder)
    .map((p) => `<li><b>${esc(p.name)}</b> 写定止损 ${esc(p.stop_note)}`
      + `${p.ladder ? '；' + esc(p.ladder) : ''}</li>`).join('');
  const warn = (sm.notes || []).map((x) => `<li class="dn">${esc(x)}</li>`).join('');

  $('#blPos').innerHTML = `<div class="tw"><table>
    <thead><tr><th class="l">名称</th><th class="l">代码</th><th>份数</th><th>成本</th><th>现价</th>
    <th>浮盈</th><th>止损价</th><th>距止损</th><th>卖出参考</th><th>持有</th>
    <th class="l">状态</th><th></th></tr></thead>
    <tbody>${pos.map(blPosRow).join('')}</tbody></table></div>
    ${(notes || warn) ? `<ul class="hint" style="padding:10px 16px 0;margin:0">${warn}${notes}</ul>` : ''}
    <p class="hint" style="padding:8px 16px 0">
      三道闸门逐条算：<b>价格止损</b>（收盘 ≤ 止损 → 次日开盘清仓）· <b>回 MA20</b>（收盘 &lt; MA20 → 出场）·
      <b>时间止损</b>（普通仓满 20 个交易日、C 类 15 个 → 收盘离场）。
      <b>浮盈阶梯</b>会自动收紧止损（≥15% 取期间最高×0.92；曾达 +20% 抬到成本），只收盘判。
      止损价默认 = 成交价 − 1×N，点数字可改成你自己的写定价，清空回自动。</p>`;
}

async function blAddPos() {
  const v = (id) => ($(id) ? $(id).value.trim() : '');
  const body = {
    code: v('#bl_code'), name: v('#bl_name'),
    shares: parseInt(v('#bl_shares'), 10) || 0,
    cost: parseFloat(v('#bl_cost')) || 0,
    buy_date: v('#bl_date') || undefined,
  };
  if (!body.code) { toast('先填代码'); return; }
  if (!body.shares || !body.cost) { toast('份数和成交价都要填'); return; }
  const row = (BL.data && (BL.data.rows || []).find((x) => x.code === body.code));
  if (row && row.atr20) body.atr20 = row.atr20;
  if (row && row.c_class) body.c_class = true;
  try {
    const r = await api('/api/bline/positions', { method: 'POST', body });
    toast(`已记 ${r.item.name}：${r.item.shares} 份 @${num(r.item.cost, 3)}`);
    ['#bl_code', '#bl_name', '#bl_shares', '#bl_cost'].forEach((id) => { if ($(id)) $(id).value = ''; });
    loadBline(false);
  } catch (e) {
    toast('记账失败：' + e.message, 4000);
  }
}

async function blClearPos() {
  const n = (BL.data && (BL.data.positions || []).length) || 0;
  if (!n) { toast('本来就是空的'); return; }
  if (!confirm(`清空全部 ${n} 只 B线路 持仓记录？`)) return;
  try {
    await api('/api/bline/positions/clear', { method: 'POST' });
    toast('已清空');
    loadBline(false);
  } catch (e) { toast('清空失败：' + e.message, 4000); }
}

function setupBlPos() {
  const btn = $('#btnBlAdd');
  if (btn) btn.onclick = blAddPos;
  const clr = $('#btnBlClear');
  if (clr) clr.onclick = blClearPos;
  const d = $('#bl_date');
  if (d) d.value = new Date().toISOString().slice(0, 10);

  document.addEventListener('click', (ev) => {
    const t = ev.target;
    const add = t.closest ? t.closest('[data-bladd]') : null;
    if (add) {
      const code = add.dataset.bladd;
      const row = ((BL.data && BL.data.rows) || []).find((x) => x.code === code) || {};
      const px = row.rt_price != null ? row.rt_price : row.close;
      $('#bl_code').value = code;
      $('#bl_name').value = row.name || '';
      $('#bl_cost').value = px != null ? Number(px).toFixed(3) : '';
      const trial = (BL.data && BL.data.trial) || 750;
      $('#bl_shares').value = px ? Math.max(100, Math.round(trial / (px * 100)) * 100) : '';
      $('#bl_date').value = new Date().toISOString().slice(0, 10);
      $('#bl_cost').focus();
      $('#bl_cost').select();
      document.getElementById('bl_code').scrollIntoView({ behavior: 'smooth', block: 'center' });
      toast(`预填 ${row.name || code}：按现价算 ${$('#bl_shares').value} 份，成交后改成真实价`);
      return;
    }
    const del = t.closest ? t.closest('[data-bldel]') : null;
    if (del) {
      const code = del.dataset.bldel;
      if (!confirm(`删除 ${code} 的 B线路 持仓记录？`)) return;
      api('/api/bline/positions?code=' + encodeURIComponent(code), { method: 'DELETE' })
        .then(() => { toast('已删除'); loadBline(false); })
        .catch((e) => toast('删除失败：' + e.message, 4000));
    }
  });
}

/* ---------------------------------------------------------------- B线路观察池名单（增删） */

async function blPoolInfo() {
  try {
    const d = await api('/api/bline/pool');
    const log = (d.removed || []).slice(0, 2);
    const cut = (t, n) => (t || '').length > n ? (t || '').slice(0, n) + '…' : (t || '');
    $('#blPoolLog').innerHTML = (d.n != null ? `当前 <b>${d.n}</b> 只` : '')
      + (d.updated ? ` · 名单更新 ${esc(d.updated)}` : '')
      + (log.length ? '<br>最近出池：' + log.map((x) =>
          `${esc(x.date)} ${esc(x.name)}（${esc(cut(x.reason, 32))}）`).join('；')
        : '<br>还没有出池记录');
  } catch (e) {
    $('#blPoolLog').innerHTML = `<span class="dn">名单读取失败：${esc(e.message)}</span>`;
  }
}

async function blPoolAdd() {
  const code = ($('#bl_pool_code').value || '').trim();
  if (!code) { toast('先填代码'); return; }
  const btn = $('#btnBlPoolAdd');
  btn.disabled = true;
  try {
    const r = await api('/api/bline/pool', {
      method: 'POST',
      body: { code, name: ($('#bl_pool_name').value || '').trim(),
              note: ($('#bl_pool_note').value || '').trim() },
    });
    toast('已加入：' + (r.msg || code), 3500);
    ['#bl_pool_code', '#bl_pool_name', '#bl_pool_note'].forEach((id) => { $(id).value = ''; });
    blPoolInfo();
    toast('正在重扫名单…（约 20 秒）', 3000);
    loadBline(true);
  } catch (e) {
    toast('加池失败：' + e.message, 5000);
  } finally {
    btn.disabled = false;
  }
}

async function blPoolRemove(code, name) {
  const reason = prompt(`从观察池移除 ${name || ''} ${code}\n理由（写进 removed_log，可留空）：`,
    '人工出池（交易台）');
  if (reason === null) return;
  try {
    const r = await api('/api/bline/pool?code=' + encodeURIComponent(code)
      + '&reason=' + encodeURIComponent(reason), { method: 'DELETE' });
    toast('已出池：' + (r.msg || code), 3500);
    blPoolInfo();
    toast('正在重扫名单…（约 20 秒）', 3000);
    loadBline(true);
  } catch (e) {
    toast('出池失败：' + e.message, 5000);
  }
}

function setupBlPool() {
  const btn = $('#btnBlPoolEdit');
  if (!btn) return;
  // ?pooledit=1 直接展开编辑区（方便收藏/排查）
  const open = () => btn.click();
  btn.onclick = () => {
    BL.editing = !BL.editing;
    btn.textContent = BL.editing ? '收起编辑' : '编辑名单';
    btn.classList.toggle('primary', BL.editing);
    const box = $('#blPoolEdit');
    box.style.display = BL.editing ? '' : 'none';
    if (BL.editing) blPoolInfo();
    if (BL.data) renderBline(BL.data);
  };
  if ($('#btnBlPoolAdd')) $('#btnBlPoolAdd').onclick = blPoolAdd;
  if (new URLSearchParams(location.search).get('pooledit') === '1') setTimeout(open, 300);
  document.addEventListener('click', (ev) => {
    const t = ev.target;
    const b = t.closest ? t.closest('[data-poolrm]') : null;
    if (b) blPoolRemove(b.dataset.poolrm, b.dataset.poolname);
  });
}

/* ---------------------------------------------------------------- R3 排除名单 */

function renderR3Exclude(d) {
  const codes = d.exclude || [];
  if (!codes.length) {
    $('#r3ExList').innerHTML = '<span class="dim">没有排除任何标的（= 与回测口径一致）</span>';
    return;
  }
  $('#r3ExList').innerHTML = '已排除 ' + codes.length + ' 只 · <b>回测未覆盖</b>：'
    + codes.map((c) => `<span class="chip" style="margin:2px 4px 2px 0">${esc(c)}
        <button class="btn-x" data-exrm="${esc(c)}" title="取消排除">×</button></span>`).join('');
  document.querySelectorAll('[data-exrm]').forEach((b) => {
    b.onclick = async () => {
      try {
        const r = await api('/api/r3/exclude?code=' + encodeURIComponent(b.dataset.exrm),
          { method: 'DELETE' });
        toast('已取消排除 ' + b.dataset.exrm);
        if (r.r3) { R3.data = r.r3; renderR3(r.r3); }
      } catch (e) { toast('操作失败：' + e.message, 4000); }
    };
  });
}

async function r3AddExclude() {
  const code = ($('#r3_ex_code').value || '').trim();
  if (!code) { toast('先填代码'); return; }
  try {
    const r = await api('/api/r3/exclude', { method: 'POST', body: { code } });
    toast('已排除 ' + code);
    $('#r3_ex_code').value = '';
    if (r.r3) { R3.data = r.r3; renderR3(r.r3); }
  } catch (e) { toast('排除失败：' + e.message, 5000); }
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
    S.tab = name;
    $$('.tab').forEach((x) => x.classList.toggle('on', x.dataset.tab === name));
    $$('.pane').forEach((p) => p.classList.toggle('on', p.id === 'pane-' + name));
    if (name === 'hold') loadHoldings();
    if (name === 'r3') loadR3(false);
    if (name === 'bline') loadBline(false);
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
  // 半路板模式下不开放筛选编辑（条件来自回测口径，五条件缺一不做）
  $('#modeSel').onchange = () => {
    const banlu = $('#modeSel').value === 'banlu';
    $('#btnFilter').disabled = banlu;
    $('#btnFilter').title = banlu ? '半路板条件来自真源 §6.4，在 config.json 的 banlu_filters 里改' : '';
    if (banlu) $('#filterBox').hidden = true;
  };
  $('#btnFilter').onclick = () => {
    if ($('#modeSel').value === 'banlu') { toast('半路板条件来自真源 §6.4，在 config.json 的 banlu_filters 里改', 4000); return; }
    const box = $('#filterBox');
    box.hidden = !box.hidden;
  };
  $('#btnSaveFilter').onclick = saveFilters;
  $('#btnAddHold').onclick = addHolding;
  $('#h_date').value = new Date().toISOString().slice(0, 10);

  // R3 卫星轮动
  setupCellEdit();
  setupBlPos();
  setupBlPool();
  $('#btnR3ExAdd').onclick = r3AddExclude;
  $('#btnBlReload').onclick = () => loadBline(true);
  $('#blLive').onchange = () => loadBline(true);
  setInterval(() => { if (S.tab === 'bline') loadBline(false); }, 60000);

  $('#btnR3Reload').onclick = () => loadR3(true);
  $('#btnR3Update').onclick = async () => {
    try {
      const r = await api('/api/r3/update', { method: 'POST', body: {} });
      toast(r.started ? '开始更新 K 线，约 1~3 分钟' : '已经在更新了', 4000);
      loadR3(true);
    } catch (e) { toast('启动更新失败：' + e.message, 5000); }
  };
  $('#btnR3Push').onclick = r3PushNow;
  $('#btnR3Add').onclick = r3AddFill;
  $('#btnR3Clear').onclick = r3Clear;
  $('#r3_date').value = new Date().toISOString().slice(0, 10);
  // ?asof=2026-08-18 → 回放某天的调仓清单（只验证口径，不产生执行依据）
  R3.asof = new URLSearchParams(location.search).get('asof') || null;
  // R3 不用盯盘，2 分钟刷一次就够（真实动作发生在收盘后）
  setInterval(() => { if (S.tab === 'r3') loadR3(false); }, 120000);

  await refreshStatus();
  if (S.status.filters) fillFilters(S.status.filters);

  // 支持 ?tab=hold 直接打开某一页，方便收藏
  const want = new URLSearchParams(location.search).get('tab');
  if (want && ['scan', 'bline', 'hold', 'r3', 'help'].includes(want)) activateTab(want);
  else loadHoldings();

  // ?mode=banlu 直接切到半路板模式（截图 / 回放验证用）
  const wantMode = new URLSearchParams(location.search).get('mode');
  if (wantMode === 'banlu' || wantMode === 'dban') {
    $('#modeSel').value = wantMode;
    if ($('#modeSel').onchange) $('#modeSel').onchange();
  }

  // 页面打开就自动扫一次：交易时段直接扫，非交易时段只显示不扫
  // ?autorun=1 强制扫（截图 / 回放验证用，不改默认行为）
  const autorun = new URLSearchParams(location.search).get('autorun') === '1';
  if (autorun || S.status.session === 'trading' || S.status.session === 'auction') {
    startScan();
  } else {
    $('#progText').textContent = `${S.status.session_text || '休市'} · 点「开始扫描」可查看最近收盘数据`;
  }
  setInterval(refreshStatus, 60000);
}

init();
