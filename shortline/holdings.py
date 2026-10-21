# -*- coding: utf-8 -*-
"""持仓与卖出提醒。

卖出规则（回测结论，见 docs/STRATEGY.md §卖出）：
  买进去的票，T+1 早上集合竞价挂跌停价一次清光。
  次日跳空 +2.15%，次日日内 −0.28%，钱全在开盘那一刻。

持仓文件是一个纯 JSON 数组，可以直接用记事本改：
  [{"code":"002479.SZ","name":"富春环保","buy_price":6.61,
    "buy_date":"2026-10-20","shares":100,"note":""}]
"""
import json
import os
from datetime import date

from .config import ROOT
from .rules import limit_pct
from .scanner import load_symbols


def holdings_path(cfg):
    p = cfg.get('holdings_file') or 'data/holdings.json'
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def load_holdings(cfg):
    p = holdings_path(cfg)
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        return []
    return d if isinstance(d, list) else []


def save_holdings(cfg, items):
    p = holdings_path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return p


def upsert_holding(cfg, item):
    """按代码覆盖添加。"""
    code = (item.get('code') or '').strip().upper()
    if not code:
        raise ValueError('缺少代码')
    if '.' not in code:
        # 只填了 6 位数字时补齐交易所后缀
        if code.startswith(('6', '9')):
            code += '.SH'
        elif code.startswith(('4', '8', '92')):
            code += '.BJ'
        else:
            code += '.SZ'
    hs = [h for h in load_holdings(cfg) if h.get('code') != code]
    hs.append({
        'code': code,
        'name': item.get('name') or load_symbols().get(code, ''),
        'buy_price': _num(item.get('buy_price')),
        'buy_date': item.get('buy_date') or date.today().isoformat(),
        'shares': int(_num(item.get('shares')) or 0),
        'note': item.get('note') or '',
    })
    save_holdings(cfg, hs)
    return hs


def remove_holding(cfg, code):
    code = (code or '').strip().upper()
    hs = [h for h in load_holdings(cfg) if h.get('code') != code]
    save_holdings(cfg, hs)
    return hs


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sell_plan(cfg, snapshot_map=None):
    """算出每只持仓的挂单价（跌停价）。

    snapshot_map 是 {code: 快照}，用来取今天真实的昨收。
    取不到就退化成「需要手填昨收」，不要瞎猜。
    """
    hs = load_holdings(cfg)
    today = date.today()
    out = []

    for h in hs:
        code = h.get('code')
        if not code:
            continue
        s = (snapshot_map or {}).get(code) or {}
        prev = _num(s.get('prev_price'))
        name = h.get('name') or load_symbols().get(code, '')
        lim = limit_pct(code, name)
        floor = round(prev * (1 - lim), 2) if prev else None
        bp = _num(h.get('buy_price'))
        last = _num(s.get('last_price'))

        # T+1：买入日当天不能卖
        can_sell = True
        if h.get('buy_date'):
            try:
                can_sell = date.fromisoformat(h['buy_date']) < today
            except ValueError:
                pass

        pnl = None
        if bp and last:
            pnl = round((last / bp - 1) * 100, 2)

        out.append({
            'code': code, 'name': name,
            'buy_price': bp, 'buy_date': h.get('buy_date'),
            'shares': h.get('shares') or 0,
            'prev_close': prev, 'last_price': last,
            'floor_price': floor,
            'limit_pct': lim,
            'pnl_pct': pnl,
            'can_sell': can_sell,
            'note': h.get('note') or '',
            'action': ('集合竞价挂跌停价卖出' if can_sell
                       else '今天买入，T+1 才能卖'),
            'need_prev': prev is None,
        })
    return out
