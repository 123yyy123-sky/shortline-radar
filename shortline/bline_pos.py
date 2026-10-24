# -*- coding: utf-8 -*-
"""B线路「已买入」台账 —— 候选成交之后，这一笔归这里管。

为什么单独一份，不塞进 levels.json / R3 状态里：
  候选表的「买入价 / 止损价」写的是**打算怎么做**，这一份记的是**已经做了什么**。
  两者的止损口径还不一样 —— 候选默认 = 收盘 − 1×N（预估），
  真成交后必须按**实际成交价 − 1×N** 重新写定（未写定兜底 成本×0.95）。
  混在一份里就会出现「改候选止损顺手把持仓止损改了」这种事故。

存法：data/bline_positions.json
  {"items": [{"code","name","shares","cost","buy_date","atr20",
              "stop","stop_src","target","target_src","c_class","note",
              "created","updated"}], "updated": "..."}

  stop_src   auto_atr（成交价−1×N，默认）｜auto_pct（成交价×0.95 兜底）｜manual
  target_src auto_ma20（跟着该只 MA20 走）｜manual

算的是真源 [[ETF交易系统]] v1.24 §3B.1 那四道闸门 + focus 出场三选一：
  ① 价格硬止损：收盘 ≤ 写定止损 → 次日开盘无条件清仓，不摊平
  ② 回 MA20：收盘 < MA20 → 出场（跌回均线走人）
  ③ 浮盈阶梯：浮盈 ≥15% → 止损收紧到 max(写定, 期间最高×0.92)；
              曾达 +20% → 保本线抬到成本。
              三铁律：只收紧 / 只朝盈利移 / **只收盘判**
  ④ 时间止损：普通仓满 20 个交易日、C 类 15 个 → 收盘离场，不论盈亏

本模块只算「现在触到哪一条」，不下单、不预测、不替代你自己的判断。
浮盈阶梯与止损一律按**收盘价**判；实时价只用来显示「离止损还有多远」。
"""
import json
import os
import threading
from datetime import datetime, timedelta

from . import quotes
from .levels import norm_code

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

LOCK = threading.Lock()

STOP_SRC_CN = {'auto_atr': '成交价−1×N', 'auto_pct': '成本×0.95（兜底）', 'manual': '手动写定'}


# ---------------------------------------------------------------- 存取

def _path(cfg):
    p = (cfg or {}).get('bline_positions_file') or ''
    if not p:
        p = os.path.join(DATA, 'bline_positions.json')
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)
    return p


def load(cfg):
    p = _path(cfg)
    if not os.path.exists(p):
        return {'items': [], 'updated': None}
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        return {'items': [], 'updated': None}
    if isinstance(d.get('items'), dict):      # 兼容误写成 {code: {...}}
        d['items'] = list(d['items'].values())
    d.setdefault('items', [])
    return d


def _save(cfg, st):
    p = _path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    st['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def items(cfg):
    return load(cfg)['items']


def get(cfg, code):
    code = norm_code(code)
    for x in items(cfg):
        if norm_code(x.get('code')) == code:
            return x
    return {}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 写

def upsert(cfg, item):
    """记一笔买入（同一代码覆盖）。成本填**真实成交价**，不要填收盘价。"""
    code = norm_code(item.get('code'))
    if len(code) != 6:
        raise ValueError('代码要 6 位数字，例如 513120')
    shares = int(float(item.get('shares') or 0))
    cost = _f(item.get('cost'))
    if shares <= 0:
        raise ValueError('份数要大于 0')
    if not cost or cost <= 0:
        raise ValueError('成交价要大于 0')

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with LOCK:
        st = load(cfg)
        old = None
        for x in st['items']:
            if norm_code(x.get('code')) == code:
                old = x
                break
        rec = dict(old or {})
        rec.update({
            'code': code,
            'name': (item.get('name') or rec.get('name') or code),
            'shares': shares,
            'cost': round(cost, 3),
            'buy_date': (item.get('buy_date') or rec.get('buy_date')
                         or datetime.now().strftime('%Y-%m-%d')),
            'created': rec.get('created') or now,
            'updated': now,
        })
        atr = _f(item.get('atr20'))
        if atr and atr > 0:
            rec['atr20'] = round(atr, 3)
        if 'c_class' in item:
            rec['c_class'] = bool(item.get('c_class'))
        rec.setdefault('c_class', False)
        rec.setdefault('stop_src', 'auto_atr')
        rec.setdefault('target_src', 'auto_ma20')
        rec.setdefault('note', '')
        if old is None:
            # 新建时如果前端传了写定止损，就认它是手动写定
            sv = _f(item.get('stop'))
            if sv and sv > 0:
                rec['stop'] = round(sv, 3)
                rec['stop_src'] = 'manual'
            tv = _f(item.get('target'))
            if tv and tv > 0:
                rec['target'] = round(tv, 3)
                rec['target_src'] = 'manual'
        if old is None:
            st['items'].append(rec)
        _save(cfg, st)
    return rec


def patch(cfg, code, field, value):
    """改一个字段（界面上点格子）。stop/target 传 null = 回到自动口径。"""
    code = norm_code(code)
    field = str(field or '').strip()
    if field not in ('cost', 'stop', 'target', 'shares', 'buy_date'):
        raise ValueError('只能改这几项：cost / stop / target / shares / buy_date')

    num = None
    if field == 'buy_date':
        # 日期不是数字，单独校验，别让 float() 把它挡下来
        v = str(value or '').strip()
        if v and not (len(v) == 10 and v[4] == '-' and v[7] == '-'):
            raise ValueError('买入日要写成 YYYY-MM-DD')
    elif value not in (None, '', 'null'):
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError('要填数字')
        if num < 0:
            raise ValueError('不能是负数')

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with LOCK:
        st = load(cfg)
        rec = None
        for x in st['items']:
            if norm_code(x.get('code')) == code:
                rec = x
                break
        if rec is None:
            raise ValueError('持仓里没有 %s，先用「记买入」建一条' % code)

        if field == 'cost':
            if not num or num <= 0:
                raise ValueError('成本价要大于 0')
            rec['cost'] = round(num, 3)
        elif field == 'shares':
            if not num or num <= 0:
                raise ValueError('份数要大于 0')
            rec['shares'] = int(num)
        elif field == 'buy_date':
            rec['buy_date'] = str(value or '').strip() or rec.get('buy_date')
        elif field == 'stop':
            if num and num > 0:
                rec['stop'] = round(num, 3)
                rec['stop_src'] = 'manual'
            else:
                rec['stop_src'] = 'auto_atr' if _f(rec.get('atr20')) else 'auto_pct'
                rec.pop('stop', None)
        elif field == 'target':
            if num and num > 0:
                rec['target'] = round(num, 3)
                rec['target_src'] = 'manual'
            else:
                rec['target_src'] = 'auto_ma20'
                rec.pop('target', None)
        rec['updated'] = now
        _save(cfg, st)
    return rec


def remove(cfg, code):
    code = norm_code(code)
    with LOCK:
        st = load(cfg)
        before = len(st['items'])
        st['items'] = [x for x in st['items'] if norm_code(x.get('code')) != code]
        if len(st['items']) == before:
            raise ValueError('持仓里没有 %s' % code)
        _save(cfg, st)
    return code


def clear(cfg):
    with LOCK:
        st = load(cfg)
        st['items'] = []
        _save(cfg, st)
    return st


# ---------------------------------------------------------------- 算

def calc_stop(rec, atr_live=None):
    """写定止损：手动 > 成交价−1×N > 成本×0.95。返回 (价, 口径说明)。"""
    cost = _f(rec.get('cost')) or 0
    if rec.get('stop_src') == 'manual' and _f(rec.get('stop')):
        return round(_f(rec['stop']), 3), STOP_SRC_CN['manual']
    atr = _f(rec.get('atr20')) or _f(atr_live)
    if cost and atr and atr > 0:
        s = cost - atr
        if s > 0:
            return round(s, 3), STOP_SRC_CN['auto_atr']
    return round(cost * 0.95, 3), STOP_SRC_CN['auto_pct']


def _wdays(buy, end):
    """按工作日估算（K 线取不到时的降级口径），未扣法定节假日。"""
    try:
        d0 = datetime.strptime(str(buy)[:10], '%Y-%m-%d')
        d1 = datetime.strptime(str(end)[:10], '%Y-%m-%d')
    except Exception:
        return 0, False
    n, cur = 0, d0
    while cur < d1:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n, True


def _one(cfg, rec, row, as_of):
    code = norm_code(rec.get('code'))
    cost = _f(rec.get('cost')) or 0
    sh = int(rec.get('shares') or 0)
    close = _f((row or {}).get('close'))
    ma20 = _f((row or {}).get('ma20'))
    atr_live = _f((row or {}).get('atr20'))
    buy_date = str(rec.get('buy_date') or '')[:10] or as_of

    # 期间最高 / 持有交易日：用日线；取不到退工作日估算
    bars = quotes.daily(code, 180)
    # 该只已经出池、不在观察池里时，收盘与 MA20 用日线补，别留空白
    if not close and bars:
        close = bars[-1]['close']
    if not ma20 and len(bars) >= 20:
        ma20 = round(sum(b['close'] for b in bars[-20:]) / 20, 3)
    if not close:
        close = cost                      # 连行情都取不到：按成本占位，浮盈显示 0%

    stop, stop_note = calc_stop(rec, atr_live)

    peak = max([x for x in (cost, close) if x] or [0])
    if bars:
        after = [b for b in bars if b['date'] >= buy_date]
        if after:
            peak = max(peak, max(b['high'] for b in after))
        hold_td = sum(1 for b in bars if buy_date < b['date'] <= as_of)
        hold_src = '日线'
    else:
        hold_td, _ = _wdays(buy_date, as_of)
        hold_src = '按工作日估'

    limit = 15 if rec.get('c_class') else 20
    pnl = round((close / cost - 1) * 100, 2) if cost else None
    peak_pct = round((peak / cost - 1) * 100, 2) if (cost and peak) else None

    # 浮盈阶梯（只收紧、只朝盈利移、只收盘判）
    stop_eff, ladder = stop, ''
    if peak_pct is not None and peak_pct >= 20 and cost > stop_eff:
        stop_eff = round(cost, 3)
        ladder = '曾达 +%s%% → 止损抬到成本 %s（保本，只收紧不放宽）' % (
            num1(peak_pct), fmt3(cost))
    if pnl is not None and pnl >= 15:
        cand = round(peak * 0.92, 3)
        if cand > stop_eff:
            stop_eff = cand
            ladder = '浮盈 %+.2f%% ≥15%% → 止损收紧至 max(写定, 期间最高×0.92) = %s' % (
                pnl, fmt3(cand))

    broken = bool(close and close <= stop_eff)
    ma_break = bool(ma20 and close < ma20)
    time_out = hold_td >= limit
    if stop_eff > stop:
        stop_note = '%s → 阶梯抬到 %s' % (stop_note, fmt3(stop_eff))

    checks = [
        dict(k='价格止损', hit=broken, limit=fmt3(stop_eff),
             text='收盘 %s %s 止损 %s' % (fmt3(close), '≤' if broken else '>',
                                          fmt3(stop_eff))),
        dict(k='回MA20', hit=ma_break, limit=fmt3(ma20) if ma20 else None,
             text='收盘 %s %s MA20 %s' % (fmt3(close), '<' if ma_break else '≥',
                                          fmt3(ma20) if ma20 else '—')),
        dict(k='时间止损', hit=time_out, limit=limit,
             text='已持有 %d / %d 个交易日（%s）' % (hold_td, limit, hold_src)),
    ]
    if broken:
        action = '已破止损 %s → 次日开盘清仓该只，不摊平' % fmt3(stop_eff)
    elif ma_break:
        action = '收盘跌破 MA20 %s → 按出场规则离场' % fmt3(ma20)
    elif time_out:
        action = '满 %d 个交易日 → 收盘离场（不论盈亏）' % limit
    else:
        action = ''

    tgt = _f(rec.get('target')) if rec.get('target_src') == 'manual' else ma20
    return {
        'code': code, 'name': (rec.get('name') or (row or {}).get('name') or code),
        'shares': sh, 'cost': cost, 'cost_custom': True,
        'buy_date': buy_date,
        'close': close, 'ma20': ma20, 'atr20': _f(rec.get('atr20')) or atr_live,
        'stop_price': stop_eff, 'stop': stop_eff,
        'stop_base': stop, 'stop_note': stop_note,
        'stop_ladder': stop_eff > stop,
        'stop_custom': rec.get('stop_src') == 'manual',
        'target': round(tgt, 3) if tgt else None,
        'target_custom': rec.get('target_src') == 'manual',
        'pnl_pct': pnl, 'pnl_amt': round((close - cost) * sh, 2) if cost else None,
        'market_value': round(close * sh, 2),
        'dist_stop_pct': round((close / stop_eff - 1) * 100, 2) if stop_eff else None,
        'peak': round(peak, 3) if peak else None, 'peak_pct': peak_pct,
        'ladder': ladder, 'broken': broken, 'ma_break': ma_break,
        'time_out': time_out, 'hold_td': hold_td, 'hold_limit': limit,
        'days_left': max(0, limit - hold_td), 'hold_src': hold_src,
        'c_class': bool(rec.get('c_class')), 'checks': checks, 'action': action,
        'note': rec.get('note') or '',
        'to_target_pct': (round((tgt / close - 1) * 100, 2)
                          if (tgt and close) else None),
    }


def num1(v):
    return ('%.1f' % v).rstrip('0').rstrip('.')


def fmt3(v):
    return ('%.3f' % v).rstrip('0').rstrip('.') if isinstance(v, float) else str(v)


def enrich(cfg, data, as_of=None):
    """把持仓盖到 B线路 结果上：positions[] + bpos_summary + 给候选行打「已持有」标。"""
    its = items(cfg)
    as_of = as_of or data.get('as_of') or datetime.now().strftime('%Y-%m-%d')
    rows = {norm_code(x.get('code')): x for x in (data.get('rows') or [])}

    pos = []
    for rec in its:
        row = rows.get(norm_code(rec.get('code')))
        p = _one(cfg, rec, row, as_of)
        pos.append(p)
        if row is not None:
            row['held'] = p['shares']
            row['held_cost'] = p['cost']
            row['held_pnl_pct'] = p['pnl_pct']
            row['held_broken'] = p['broken']

    if pos:
        try:
            quotes.overlay(pos)
        except Exception:
            pass
        for p in pos:
            rp = p.get('rt_price')
            if rp and p.get('cost'):
                p['rt_pnl_pct'] = round((rp / p['cost'] - 1) * 100, 2)
                p['rt_pnl_amt'] = round((rp - p['cost']) * p['shares'], 2)
                p['rt_market_value'] = round(rp * p['shares'], 2)

    budget = _f((cfg.get('bline') or {}).get('budget')) or 6000.0
    mv = sum((p.get('rt_market_value') or p.get('market_value') or 0) for p in pos)
    cb = sum((p.get('cost') or 0) * (p.get('shares') or 0) for p in pos)
    notes = []
    if len(pos) > 2:
        notes.append('同主线已 %d 只，超过「≤2 只」上限' % len(pos))
    if mv > budget:
        notes.append('占用 %.0f 元，超过 B线资金 %.0f 元' % (mv, budget))

    data['positions'] = pos
    data['bpos_summary'] = {
        'positions': len(pos),
        'market_value': round(mv, 2),
        'cost': round(cb, 2),
        'pnl_amt': round(mv - cb, 2),
        'pnl_pct': round((mv / cb - 1) * 100, 2) if cb else None,
        'broken': sum(1 for p in pos if p.get('broken')),
        'budget': budget,
        'used_pct': round(mv / budget * 100, 1) if budget else None,
        'notes': notes,
        'updated': load(cfg).get('updated'),
    }
    return data
