# -*- coding: utf-8 -*-
"""B线路选股 —— 把工作区的 `scan_watchlist.py` 接进界面。

分工（和 R3 一个道理）：
  **判定只有一份**，在工作区的 scan_watchlist.py 里（真源 [[B线路操作手册]]
  / [[ETF交易系统]] §3A.1、§3A.3）。本模块绝不重算 focus、不重算档位，
  只做四件事：
    1. 跑脚本拿 JSON（缓存 cache_seconds 秒；盘中用 --live 拼半根K）
    2. 把「默认三价」算成一行一行的候选（买入=收盘、止损=收盘−1×N、卖出参考=MA20）
    3. 用 levels.py 的自定义价覆盖默认值
    4. 叠一次实时价，只给界面上「距止损多远」用

缓存里存的是**脚本原始结果**，不是算完的展示数据 —— 这样改一个价不用重跑
扫描（扫描要几秒到几十秒，改价是毫秒级的事）。

为什么止损默认是「收盘 − 1×N」而不是 MA20：这是 B 线四道闸门里写定的
突破式止损（进场价 −1×N，N=ATR20）。R3 用 MA20 是因为它的买入条件就是
站上 MA20，两条线不串口径。
"""
import copy
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

from . import bline_pos, levels, quotes

CACHE = {'at': 0.0, 'raw': None, 'err': None, 'live': None}
LOCK = threading.Lock()

TIER_RULES = {
    'A': '创 60 日新高 + 距 MA20 乖离 ≤10% + vol20 ≤2.0（量比不作硬性）',
    'B': '距 60 日高 ≤3% + vol20 ≤1.5（稳强型）',
    'C': '无进场条件 → 空仓等方向',
    'D': '仅深跌反弹 C 类：20 日乖离 ≤−12% 且 >−18% + 大盘 5 日跌 ≥2%（试探仓）',
}
TIER_ACTION = {
    'A': '✅ 正常开仓',
    'B': '⚠️ 只做稳强型试探仓',
    'C': '❌ 不开仓，等方向',
    'D': '❌ 默认不开仓（唯一例外=深跌反弹 C 类）',
}


def bcfg(cfg):
    return (cfg or {}).get('bline') or {}


def _trial(cfg):
    try:
        return float(bcfg(cfg).get('trial') or 750)
    except (TypeError, ValueError):
        return 750.0


def _budget(cfg):
    try:
        return float(bcfg(cfg).get('budget') or 6000)
    except (TypeError, ValueError):
        return 6000.0


def _workdir(cfg):
    b = bcfg(cfg)
    r3 = (cfg or {}).get('r3') or {}
    wd = (b.get('workdir') or r3.get('workdir') or '').strip()
    if not wd:
        raise RuntimeError('config.json 里没填 bline.workdir（或 r3.workdir）')
    return wd.replace('\\', '/').rstrip('/')


def _script(cfg):
    p = os.path.join(_workdir(cfg), bcfg(cfg).get('script') or 'scan_watchlist.py')
    if not os.path.isfile(p):
        raise RuntimeError('找不到选股脚本：%s' % p)
    return p


def _py(cfg):
    p = (bcfg(cfg).get('python') or '').strip()
    if p and os.path.isfile(p):
        return p
    return sys.executable or 'python'


def _env():
    e = dict(os.environ)
    e['PYTHONIOENCODING'] = 'utf-8'
    e['PYTHONUTF8'] = '1'
    return e


def _is_trading_time(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


def _stale_warn(run_date, data_date):
    """K 线是否比「今天」旧 —— 但周末/节假日不算旧。

    脚本给的 data_stale = (data_date != run_date)，周六跑必然是 True
    （K 线停在周五）。那不是数据过期，是今天本来不开盘，提示了只会误导。
    所以只在**今天是工作日**且 K 线还停在更早的日子时才提示。
    """
    if not data_date:
        return False
    try:
        d = datetime.strptime(str(run_date or '')[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        return bool(data_date != run_date)
    if d.weekday() >= 5:
        return False
    return bool(data_date != run_date)


def invalidate():
    with LOCK:
        CACHE.update(at=0.0, raw=None, err=None, live=None)


def _run(cfg, live, timeout=None):
    script = _script(cfg)
    args = [_py(cfg), script, '--json'] + (['--live'] if live else [])
    to = int(bcfg(cfg).get('timeout') or 240)
    p = subprocess.run(args, cwd=_workdir(cfg), capture_output=True,
                       timeout=timeout or to, env=_env())
    out = (p.stdout or b'').decode('utf-8', 'replace')
    err = (p.stderr or b'').decode('utf-8', 'replace')
    i = out.find('{')
    if i < 0:
        raise RuntimeError((err or out or '脚本没输出 JSON（退出码 %d）' % p.returncode).strip()[:400])
    return json.loads(out[i:])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row(x, focus_codes):
    t = x.get('tech') or {}
    etf = str(x.get('etf') or '')
    code = etf[2:] if len(etf) > 6 else etf
    dev20 = _num(x.get('dev20'))
    return {
        'code': code, 'etf': etf, 'name': x.get('name'),
        'close': _num(t.get('close')), 'ma20': _num(t.get('ma20')),
        'atr20': _num(t.get('atr20')), 'pos60': _num(t.get('pos60')),
        'dist60': _num(x.get('dist60')), 'vol20': _num(x.get('vol20')),
        'dev20': dev20, 'r1_excess': _num(x.get('r1_excess')),
        'status': x.get('status'),
        'focus': code in focus_codes or bool(x.get('focus_hit')),
        'focus_note': x.get('focus_note'), 'date': t.get('date'),
        'c_class': (dev20 is not None and -18 < dev20 <= -12),
    }


def _build(cfg, raw, live, cached=False):
    r = copy.deepcopy(raw)
    focus_list = r.get('focus_list') or []
    focus_codes = set()
    for x in focus_list:
        etf = str(x.get('etf') or '')
        focus_codes.add(etf[2:] if len(etf) > 6 else etf)

    rows = [_row(x, focus_codes) for x in (r.get('results') or [])]
    # 排序：focus 前，其余按距 60 日高升序（离新高越近越值得盯）
    rows.sort(key=lambda x: (0 if x['focus'] else 1,
                             x['dist60'] if x['dist60'] is not None else 999))

    focus = []
    for x in focus_list:
        etf = str(x.get('etf') or '')
        focus.append({
            'code': etf[2:] if len(etf) > 6 else etf, 'name': x.get('name'),
            'close': _num(x.get('close')), 'atr20': _num(x.get('atr20')),
            'ma20': _num(x.get('ma20')), 'dist60': _num(x.get('dist60')),
            'vol20': _num(x.get('vol20')), 'dev20': _num(x.get('dev20')),
            'note': x.get('note'),
        })

    tier = (r.get('tier_code') or '').upper()
    d5 = round(_num(r.get('hs300_5d')) or 0, 2)
    data = {
        'ok': True,
        'tier_code': tier,
        'market_tier': r.get('market_tier'),
        'tier_action': TIER_ACTION.get(tier, ''),
        'tier_rule': TIER_RULES.get(tier, ''),
        'tier_rules': TIER_RULES,
        'above_count': r.get('above_count'),
        'hs300_dev': round(_num(r.get('hs300_dev')) or 0, 2),
        'hs300_5d': d5,
        'c_env': d5 <= -2,
        'as_of': r.get('data_date') or r.get('date'),
        'run_date': r.get('run_date') or r.get('date'),
        'stale': _stale_warn(r.get('run_date') or r.get('date'), r.get('data_date')),
        'live': live,
        'focus_active': bool(r.get('focus_active')),
        'focus_pool_n': r.get('focus_pool_n') or len(focus_list),
        'focus': focus,
        'rows': rows,
        'pool_n': len(rows),
        'candidates_c': [x['code'] for x in rows if x['c_class']],
        'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'cached': cached,
    }
    # 三价：默认 → 自定义覆盖 → 实时价
    levels.apply_bline(cfg, data)
    st = levels.load(cfg)
    data['levels_updated'] = st.get('updated')
    data['levels_count'] = len(st.get('items') or {})
    try:
        quotes.overlay(data['rows'])
        quotes.overlay(data['focus'])
    except Exception:
        pass
    # 已买入台账：候选行的「已持有」标、持仓视图、B线资金占用
    data['trial'] = _trial(cfg)
    data['budget'] = _budget(cfg)
    try:
        bline_pos.enrich(cfg, data)
    except Exception:
        data['positions'] = []
        data['bpos_summary'] = {'positions': 0, 'notes': []}
    return data


def payload(cfg, force=False, live=None):
    b = bcfg(cfg)
    ttl = int(b.get('cache_seconds') or 180)
    if live is None:
        v = b.get('live')
        live = _is_trading_time() if v in (None, '', 'auto') else bool(v)
    live = bool(live)

    with LOCK:
        raw, at, was_live = CACHE['raw'], CACHE['at'], CACHE['live']
    if raw and not force and was_live == live and (time.time() - at) < ttl:
        return _build(cfg, raw, live, cached=True)

    try:
        raw = _run(cfg, live)
    except Exception as e:
        with LOCK:
            CACHE.update(at=time.time(), raw=None, err=str(e), live=live)
        return {'ok': False, 'error': str(e),
                'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'tier_rules': TIER_RULES, 'tier_action': TIER_ACTION}
    with LOCK:
        CACHE.update(at=time.time(), raw=raw, err=None, live=live)
    return _build(cfg, raw, live, cached=False)
