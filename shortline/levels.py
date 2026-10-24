# -*- coding: utf-8 -*-
"""三价台账 —— 买入价 / 卖出价 / 止损价（外加成本价）。

为什么要单独一份文件：
  规则算出来的数（止损=MA20、买入价=收盘、卖出参考=MA20）只是**默认值**。
  真实交易里你总会想自己改一两个价：成本填错了、想用结构位止损、想挂个
  目标价。这些都不能污染规则脚本，所以统一落在这里。

存法：只存**改过的**字段。没改 = 用默认值；填 0 或空 = 回到默认值。
  data/levels.json
  {"items": {"513120": {"stop": 1.25, "target": 1.45, "updated": "..."}}}

谁读它：
  r3.payload()      → 持仓的 cost/stop/target、买入清单的 suggest/stop
  bline.payload()   → 候选的 buy/stop/target
两个入口都只调用本模块的 apply_*，不各写一套。
"""
import json
import os
import threading
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

FIELDS = ('cost', 'buy', 'stop', 'target')
FIELD_CN = {'cost': '成本价', 'buy': '买入价', 'stop': '止损价', 'target': '卖出价'}

LOCK = threading.Lock()


def _path(cfg):
    p = (cfg or {}).get('levels_file') or ''
    if not p:
        p = os.path.join(DATA, 'levels.json')
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)
    return p


def norm_code(s):
    """513120 / 513120.SH / sh513120 → 513120"""
    s = str(s or '').strip().lower()
    for x in ('sh', 'sz', '.', ' '):
        s = s.replace(x, '')
    return ''.join(ch for ch in s if ch.isdigit())[:6]


def load(cfg):
    p = _path(cfg)
    if not os.path.exists(p):
        return {'items': {}, 'updated': None}
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        return {'items': {}, 'updated': None}
    d.setdefault('items', {})
    return d


def _save(cfg, st):
    p = _path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    st['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def get(cfg, code):
    return (load(cfg)['items'].get(norm_code(code)) or {})


def all_items(cfg):
    return load(cfg)['items']


def set_field(cfg, code, field, value):
    """改一个价。value 为 None / '' / 0 → 删掉这个字段（回默认值）。

    返回 (item, cleared)。item 是这个代码剩下的自定义字段。
    """
    code = norm_code(code)
    if len(code) != 6:
        raise ValueError('代码要 6 位数字，例如 513120')
    field = str(field or '').strip()
    if field not in FIELDS:
        raise ValueError('只能改这四项：%s' % '、'.join(FIELDS))

    num = None
    if value not in (None, '', 'null'):
        try:
            num = round(float(value), 3)
        except (TypeError, ValueError):
            raise ValueError('%s 要填数字' % FIELD_CN[field])
        if num < 0:
            raise ValueError('%s 不能是负数' % FIELD_CN[field])
        if num == 0:
            num = None

    with LOCK:
        st = load(cfg)
        item = dict(st['items'].get(code) or {})
        cleared = num is None
        if cleared:
            item.pop(field, None)
        else:
            item[field] = num
        if item:
            item['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            st['items'][code] = item
        else:
            st['items'].pop(code, None)
        _save(cfg, st)
    return st['items'].get(code) or {}, cleared


def clear_code(cfg, code):
    code = norm_code(code)
    with LOCK:
        st = load(cfg)
        st['items'].pop(code, None)
        _save(cfg, st)
    return code


# ---------------------------------------------------------------- 叠加到结果

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def apply_r3(cfg, data):
    """把自定义价盖到 r3 的持仓/买入清单上，并重算受影响的派生数。

    只改展示层的数，**不动规则脚本的判定**：broken（破止损）按叠加后的
    止损价重算，其余（是否调仓、买谁）一律沿用脚本结论。
    """
    lv = all_items(cfg)
    if not lv:
        return data

    for x in data.get('positions') or []:
        v = lv.get(norm_code(x.get('code'))) or {}
        close = _f(x.get('close'))
        cost = _f(v.get('cost')) or _f(x.get('cost'))
        if v.get('cost'):
            x['cost'] = cost
            x['cost_custom'] = True
        if cost and close:
            sh = _f(x.get('shares')) or 0
            x['cost'] = cost
            x['pnl_pct'] = round((close / cost - 1) * 100, 2)
            x['pnl_amt'] = round((close - cost) * sh, 2)
            x['market_value'] = round(close * sh, 2)
        stop = _f(v.get('stop')) or _f(x.get('stop_price'))
        if v.get('stop'):
            x['stop_custom'] = True
        x['stop_price'] = stop
        if close and stop:
            x['dist_stop_pct'] = round((close / stop - 1) * 100, 2)
            x['broken'] = close <= stop
            if x['broken']:
                x['note'] = x.get('note') or '自定义止损已破'
        if v.get('target'):
            x['target'] = _f(v['target'])
            x['target_custom'] = True
            if close:
                x['to_target_pct'] = round((x['target'] / close - 1) * 100, 2)

    for x in data.get('buy') or []:
        v = lv.get(norm_code(x.get('code'))) or {}
        if v.get('buy'):
            x['suggest_price'] = _f(v['buy'])
            x['buy_custom'] = True
        stop = _f(v.get('stop')) or _f(x.get('stop_price'))
        if v.get('stop'):
            x['stop_custom'] = True
        x['stop_price'] = stop
        p = _f(x.get('suggest_price'))
        if p and stop:
            x['stop_dist_pct'] = round((p - stop) / p * 100, 2)
        if v.get('target'):
            x['target'] = _f(v['target'])
            x['target_custom'] = True

    # 汇总重算（成本/现价被改过之后，总浮盈必须跟着变）
    pos = data.get('positions') or []
    if pos:
        mv = sum(_f(x.get('market_value')) or 0 for x in pos)
        cb = sum((_f(x.get('cost')) or 0) * (_f(x.get('shares')) or 0) for x in pos)
        data['summary'] = {
            'positions': len(pos),
            'market_value': round(mv, 2),
            'cost': round(cb, 2),
            'pnl_amt': round(mv - cb, 2),
            'pnl_pct': round((mv / cb - 1) * 100, 2) if cb else None,
            'broken': sum(1 for x in pos if x.get('broken')),
        }

        # 警报是按**默认止损**算出来的，改过止损的那几只要重算，否则「警报说要卖、
        # 状态说还持有」两处打架。只处理自定义止损的代码，别人的照旧。
        custom = {norm_code(x.get('code')) for x in pos if x.get('stop_custom')}
        if custom:
            al = [a for a in (data.get('alerts') or [])
                  if not any(str(a).startswith(c) for c in custom)]
            for x in pos:
                if x.get('stop_custom') and x.get('broken'):
                    al.append('%s %s 现价 %s ≤ 自定义止损 %s → 次日开盘卖出该只，资金回现金等下次调仓'
                              % (x.get('code'), x.get('name'),
                                 x.get('close'), x.get('stop_price')))
            data['alerts'] = al
    return data


def apply_bline(cfg, data):
    """B线路候选：默认买入价=收盘、止损=收盘−1×N、卖出参考=MA20，可被自定义覆盖。"""
    lv = all_items(cfg)
    try:
        trial = float((cfg.get('bline') or {}).get('trial') or 750)
    except (TypeError, ValueError):
        trial = 750.0
    for x in data.get('rows') or []:
        v = lv.get(norm_code(x.get('code'))) or {}
        close, atr, ma20 = _f(x.get('close')), _f(x.get('atr20')), _f(x.get('ma20'))
        for k, src in (('buy', close), ('stop', (close - atr) if (close and atr) else None),
                       ('target', ma20)):
            val = _f(v.get(k))
            if val:
                x[k] = val
                x[k + '_custom'] = True
            else:
                x[k] = round(src, 3) if src else None
        price = _f(x.get('buy')) or close
        if price:
            x['shares'] = max(1, round(trial / (price * 100)))
            x['amount'] = round(x['shares'] * 100 * price, 2)
        if x.get('buy') and x.get('stop'):
            x['stop_dist_pct'] = round((x['buy'] - x['stop']) / x['buy'] * 100, 2)
        if x.get('buy') and x.get('target'):
            x['to_target_pct'] = round((x['target'] / x['buy'] - 1) * 100, 2)
    return data
