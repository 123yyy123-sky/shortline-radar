# -*- coding: utf-8 -*-
"""实时价（腾讯 qt.gtimg.cn）。**只给界面看，不参与任何判定。**

R3 与 B线路 的判定全是收盘价口径：调仓、focus、破止损都只看收盘。
但界面上一排死数字没法用 —— 盘中你得知道「现价离止损还有多远」。
所以这里只做一件事：取现价，盖到展示层；取不到就退回收盘价，
**绝不拿实时价改判定结果**。

腾讯接口一次能问多只，格式：
  v_sh513120="1~港股创新药ETF广发~513120~1.206~1.200~1.198~...";
字段（~ 分隔）：1=名称 3=现价 4=昨收 30=时间 32=涨跌幅%

另附 daily()：腾讯前复权日线，只给记账算「持有几个交易日」「期间最高价」，
同样不参与任何判定。
"""
import json
import time
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
URL = 'https://qt.gtimg.cn/q=%s'

CACHE = {'at': 0.0, 'data': {}}
TTL = 15.0


def sym_of(code):
    """513120 → sh513120；159748 → sz159748（5 开头在上交所，其余在深交所）。"""
    c = str(code or '').strip().lower()
    for x in ('sh', 'sz', '.'):
        c = c.replace(x, '')
    c = ''.join(ch for ch in c if ch.isdigit())[:6]
    if len(c) != 6:
        return ''
    return ('sh' if c[0] == '5' else 'sz') + c


def realtime(codes, timeout=6, ttl=None):
    """返回 {code6: {price, prev_close, pct, name, time}}。失败返回 {}。"""
    ttl = TTL if ttl is None else ttl
    now = time.time()
    if CACHE['data'] and (now - CACHE['at']) < ttl:
        hit = CACHE['data']
        if all(c in hit for c in codes):
            return {c: hit[c] for c in codes}

    want = []
    for c in codes:
        s = sym_of(c)
        if s:
            want.append(s)
    want = list(dict.fromkeys(want))
    if not want:
        return {}

    out = {}
    try:
        req = urllib.request.Request(URL % ','.join(want),
                                     headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'})
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode('gbk', 'ignore')
        for line in raw.split(';'):
            line = line.strip()
            if '="' not in line:
                continue
            head, body = line.split('="', 1)
            body = body.rstrip('"')
            f = body.split('~')
            if len(f) < 33:
                continue
            code = f[2].strip()
            try:
                price = float(f[3])
                prev = float(f[4]) if f[4] else 0.0
                pct = float(f[32]) if f[32] else None
            except ValueError:
                continue
            if not price:
                continue
            out[code] = {'name': f[1], 'price': price, 'prev_close': prev,
                         'pct': pct, 'time': f[30] if len(f) > 30 else ''}
    except Exception:
        return dict(CACHE['data']) if CACHE['data'] else {}

    if out:
        CACHE.update(at=now, data=out)
    return out


KLINE = {'at': 0.0, 'data': {}}
KTTL = 600.0
KURL = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        '?param=%s,day,,,%d,qfq')


def daily(code, n=120, timeout=8, ttl=None):
    """日线（前复权）→ [{date, open, close, high, low, vol}]，升序。

    只给「持有交易日数」「期间最高价」这类记账用，不参与选股判定 ——
    判定还是靠工作区脚本自己的缓存。取不到就返回 []，调用方自己降级。
    """
    ttl = KTTL if ttl is None else ttl
    sym = sym_of(code)
    if not sym:
        return []
    now = time.time()
    hit = KLINE['data'].get(sym)
    if hit and (now - KLINE['at']) < ttl:
        return hit

    out = []
    try:
        req = urllib.request.Request(KURL % (sym, int(n)),
                                     headers={'User-Agent': UA,
                                              'Referer': 'https://gu.qq.com/'})
        j = json.loads(urllib.request.urlopen(req, timeout=timeout)
                       .read().decode('utf-8', 'ignore'))
        d = (j.get('data') or {}).get(sym) or {}
        bars = d.get('qfqday') or d.get('day') or []
        for b in bars:
            if len(b) < 5:
                continue
            try:
                out.append({'date': str(b[0]),
                            'open': float(b[1]), 'close': float(b[2]),
                            'high': float(b[3]), 'low': float(b[4]),
                            'vol': float(b[5]) if len(b) > 5 and b[5] else 0.0})
            except (TypeError, ValueError):
                continue
    except Exception:
        out = []

    if out:
        KLINE['at'] = now
        KLINE['data'][sym] = out
    elif hit:
        return hit
    return out


def overlay(rows, code_key='code'):
    """把实时价盖到列表上：加 rt_price / rt_pct，并重算距止损、距卖出价。"""
    codes = [r.get(code_key) for r in rows if r.get(code_key)]
    q = realtime(codes)
    for r in rows:
        m = q.get(str(r.get(code_key) or ''))
        if not m:
            continue
        r['rt_price'] = m['price']
        r['rt_pct'] = m['pct']
        base = r.get('close') or r.get('cost')
        if base:
            r['rt_vs_close_pct'] = round((m['price'] / base - 1) * 100, 2)
        stop = r.get('stop_price') or r.get('stop')
        if stop:
            r['rt_dist_stop_pct'] = round((m['price'] / stop - 1) * 100, 2)
            r['rt_broken'] = m['price'] <= stop
        tgt = r.get('target')
        if tgt:
            r['rt_to_target_pct'] = round((tgt / m['price'] - 1) * 100, 2)
    return q
