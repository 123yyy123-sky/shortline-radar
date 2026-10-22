# -*- coding: utf-8 -*-
"""交易规则与判定逻辑。

这里只有规则，不碰数据源，也不碰界面。改规则只改这一个文件。

三条规则来源（详见 docs/STRATEGY.md）：
  1. 涨停幅度按板块分档，ST 单独处理
  2. 量比 = 当日累计量 / 前 5 日均量。盘中拿不到「全天量比」，
     要用日内成交量占比曲线把当前推进率折成全天口径
  3. 开仓条件：涨幅 ≥7% + 开盘涨 3~9% + 全天量比 <1 + 首板 + 非 ST + 非北交所
"""
from bisect import bisect_left

# ---------------------------------------------------------------- 涨停幅度

def limit_pct(code, name=''):
    """按代码前缀和名称判断涨跌幅限制。

    顺序不能反：科创板 / 创业板的 ST 股仍是 20%，只有主板的 ST 才是 5%。
    """
    ticker = code.split('.')[0]
    if ticker.startswith(('688', '689')):     # 科创板
        return 0.20
    if ticker.startswith('30'):               # 创业板
        return 0.20
    if ticker.startswith(('8', '4', '92')):   # 北交所
        return 0.30
    if 'ST' in (name or '').upper():          # 主板 ST
        return 0.05
    return 0.10


def is_bj(code):
    return code.split('.')[0].startswith(('8', '4', '92'))


def limit_price(prev_close, code, name=''):
    """涨停价。快照里的价格已是真实价（非复权），所以这里能算准。"""
    if not prev_close:
        return None
    return round(prev_close * (1 + limit_pct(code, name)), 2)


# ---------------------------------------------------------------- 日内量占比

class ShareCurve:
    """把「当前累计量 / 前 5 日均量」折成「预估全天量比」。

    为什么需要折算：盘中成交量只是全天的一部分。10:00 时一只正常票
    已经成交了全天量的三成左右，直接拿「累计量/日均量」当量比会偏小，
    筛「量比<1」等于要求全天量比<0.34，会把绝大部分票误杀。

    曲线来源：单只股票 5 个交易日的分时实测，取值偏上（宁可放宽不筛空）。
    样本少，逐日波动大（10:00 实测占比在 23%~37% 之间）。
    换成你自己券商的实测值，或者积累足够多数据后重算，会准很多。
    """

    def __init__(self, points):
        pts = sorted((str(t), float(v)) for t, v in points)
        # 收敛到合法区间，防止配置写错导致量比爆炸
        pts = [(t, min(max(v, 0.01), 1.0)) for t, v in pts]
        # 保证单调不减
        for i in range(1, len(pts)):
            if pts[i][1] < pts[i - 1][1]:
                pts[i] = (pts[i][0], pts[i - 1][1])
        self.times = [t for t, _ in pts]
        self.vals = [v for _, v in pts]

    @staticmethod
    def _minutes(hhmm):
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])

    def at(self, hhmm):
        hhmm = (hhmm or '15:00')[:5]
        if any(not c.isdigit() and c != ':' for c in hhmm):
            return 1.0
        if not self.times:
            return 1.0
        # 开盘前按第一个点算，收盘后按最后一个点算
        if hhmm <= self.times[0]:
            return self.vals[0]
        if hhmm >= self.times[-1]:
            return self.vals[-1]
        i = bisect_left(self.times, hhmm)
        if self.times[i] == hhmm:
            return self.vals[i]
        t0, t1 = self.times[i - 1], self.times[i]
        v0, v1 = self.vals[i - 1], self.vals[i]
        m0, m1, m = self._minutes(t0), self._minutes(t1), self._minutes(hhmm)
        if m1 == m0:
            return v1
        return v0 + (v1 - v0) * (m - m0) / (m1 - m0)


# ---------------------------------------------------------------- 判定

# 三档结论
PASS = 'pass'      # 全条件通过、已封板 → 可以挂单
WATCH = 'watch'    # 全条件通过、还没封 → 盯着，封不上不买
REJECT = 'reject'  # 有硬伤 → 不碰


def evaluate(raw, f):
    """对一只票做全部判定。

    raw 需要：code, name, prev_close, open_price, last_price, volume, v5
    返回的 dict 直接给界面用，不再拼字符串。
    """
    code = raw['code']
    name = raw.get('name') or ''
    prev = float(raw['prev_close'])
    op = float(raw['open_price'])
    last = float(raw['last_price'])
    vol = float(raw['volume'])
    v5 = float(raw['v5'])

    gain = (last / prev - 1) * 100            # 当日涨幅（%）
    open_gain = (op / prev - 1) * 100         # 开盘涨幅（%）
    progress = vol / v5 if v5 > 0 else 0.0    # 推进率 = 累计量 / 前5日均量
    vr = progress / raw['share'] if raw['share'] > 0 else progress   # 预估全天量比

    lim = limit_pct(code, name)
    up = limit_price(prev, code, name)
    sealed = bool(up and abs(last - up) <= 0.011 and gain >= lim * 100 - 0.5)

    boards = raw.get('boards')
    is_st = bool(raw.get('is_st')) or ('ST' in name.upper())
    bj = is_bj(code)

    flags, rejects = [], []

    # —— 涨幅
    if gain < f['min_gain_pct']:
        rejects.append(f'涨幅 {gain:.2f}% 不到 {f["min_gain_pct"]:g}%')
    # —— 开盘幅度
    if open_gain < f['open_gain_min_pct']:
        rejects.append(f'开盘只涨 {open_gain:.2f}%，低于 {f["open_gain_min_pct"]:g}%')
    elif open_gain >= f['open_gain_max_pct']:
        rejects.append(f'开盘涨 {open_gain:.2f}%，跳空太贵（≥{f["open_gain_max_pct"]:g}%）')
    # —— 量比
    if vr >= 2.0:
        flags.append({'level': 'bad', 'text': f'量比 {vr:.2f} ≥2，历史期望为负'})
        rejects.append(f'量比 {vr:.2f} ≥2')
    elif vr >= f['vr_max']:
        flags.append({'level': 'warn', 'text': f'量比 {vr:.2f} ≥{f["vr_max"]:g}'})
        rejects.append(f'量比 {vr:.2f} 高于 {f["vr_max"]:g}')
    else:
        flags.append({'level': 'ok', 'text': f'量比 {vr:.2f} <{f["vr_max"]:g}'})
    # —— 连板
    if boards is not None and boards > f['max_boards']:
        flags.append({'level': 'bad', 'text': f'{boards} 连板'})
        rejects.append(f'{boards} 连板，炸板亏损翻倍')
    # —— ST / 北交所
    if is_st and f['exclude_st']:
        flags.append({'level': 'bad', 'text': 'ST'})
        rejects.append('ST 股')
    if bj and f['exclude_bj']:
        flags.append({'level': 'bad', 'text': '北交所'})
        rejects.append('北交所')

    if rejects:
        stage = REJECT
    elif sealed:
        stage = PASS
        flags.insert(0, {'level': 'ok', 'text': '已封板'})
    else:
        stage = WATCH
        flags.insert(0, {'level': 'warn', 'text': '未封板'})

    if f.get('require_sealed') and stage == WATCH:
        stage = REJECT
        rejects.append('未封板（当前设置只保留已封）')

    return {
        'code': code, 'name': name,
        'gain': round(gain, 2), 'open_gain': round(open_gain, 2),
        'vr': round(vr, 2), 'progress': round(progress, 2),
        'last': last, 'prev_close': prev, 'limit_price': up,
        'to_limit_pct': round((up / last - 1) * 100, 2) if up and last else None,
        'sealed': sealed, 'stage': stage,
        'boards': boards, 'board_text': raw.get('board_text') or '',
        'seal_time': raw.get('seal_time') or '',
        'seal_money': raw.get('seal_money'),
        'reason': raw.get('reason') or '',
        'is_st': is_st, 'is_bj': bj, 'limit_pct': lim,
        'flags': flags, 'rejects': rejects,
    }


# ---------------------------------------------------------------- 半路板（2026-10-22 新增）

# 回测来源：工作区 _bt_banluban.py（全A 5577 只，2016-10~2026-10，日线近似）。
# 最优配置 20cm T=5% 首板+量比≥1.5+情绪热 = +1.73%/61.0%，2020~2026 每年为正。
# ⚠️ 幸存者偏差未解（退市股不在回测库），数字只作方向性证据。
BANLU_DEFAULTS = {
    'trigger_pct': 5.0,        # T=5%，20cm 半板触发价 = round(昨收×1.05, 2)
    'vr_min': 1.5,             # 预估全天量比下限（第一强过滤，缺此条期望归零）
    'hot_limit_count': 50,     # 情绪热：当日全市场涨停家数 ≥ 50
    'exclude_st': True,
    'exclude_bj': True,
    'cutoff': '14:30',         # 时点规矩：14:30 后停止接新单（尾盘胜率全场最低）
}


def banlu_trigger_price(prev_close, trigger_pct=5.0):
    """半路板触发价 = round(昨收×(1+T), 2)，与回测口径逐分对齐。"""
    if not prev_close:
        return None
    return round(float(prev_close) * (1 + trigger_pct / 100.0), 2)


def evaluate_banlu(raw, f):
    """半路板判定。raw 同 evaluate，另需 market_limit_count（全市场涨停家数）。

    三档含义与打板模式不同：
      pass  = 已触发（现价越过触发价）且全条件通过 → 现在就能按触发价附近买
      watch = 条件都够但还没涨到 5% → 盯着，触发了再买
      reject = 有硬伤 → 不碰
    """
    code = raw['code']
    name = raw.get('name') or ''
    prev = float(raw['prev_close'])
    op = float(raw['open_price'])
    last = float(raw['last_price'])
    vol = float(raw['volume'])
    v5 = float(raw['v5'])

    gain = (last / prev - 1) * 100
    progress = vol / v5 if v5 > 0 else 0.0
    vr = progress / raw['share'] if raw['share'] > 0 else progress

    lim = limit_pct(code, name)
    up = limit_price(prev, code, name)
    trig = banlu_trigger_price(prev, f['trigger_pct'])
    touched = bool(trig and last >= trig)
    open_ok = bool(trig and op < trig)          # 开盘未越过触发价（一字/跳空排除）

    boards = raw.get('boards')
    is_st = bool(raw.get('is_st')) or ('ST' in name.upper())
    bj = is_bj(code)
    mkt = int(raw.get('market_limit_count') or 0)

    flags, rejects = [], []

    # —— 只做 20cm（创业板 / 科创板）
    if f.get('exclude_bj') and bj:
        flags.append({'level': 'bad', 'text': '北交所'})
        rejects.append('北交所')
    elif lim != 0.20:
        flags.append({'level': 'bad', 'text': f'{int(lim * 100)}cm 板，半路板只做 20cm'})
        rejects.append(f'{int(lim * 100)}cm 板，只做 20cm')
    # —— ST
    if is_st and f.get('exclude_st'):
        flags.append({'level': 'bad', 'text': 'ST'})
        rejects.append('ST 股')
    # —— 开盘未越过触发价
    if open_ok:
        flags.append({'level': 'ok', 'text': f'开盘未越过触发价 {trig:.2f}'})
    else:
        flags.append({'level': 'bad', 'text': f'开盘已越过触发价 {trig:.2f}（跳空/一字）'})
        rejects.append('开盘已越过触发价（跳空/一字）')
    # —— 量比 ≥ vr_min
    if vr >= f['vr_min']:
        flags.append({'level': 'ok', 'text': f'量比 {vr:.2f} ≥{f["vr_min"]:g}'})
    else:
        flags.append({'level': 'bad', 'text': f'量比 {vr:.2f} <{f["vr_min"]:g}，第一强过滤不过'})
        rejects.append(f'量比 {vr:.2f} 低于 {f["vr_min"]:g}')
    # —— 情绪热
    if mkt >= f['hot_limit_count']:
        flags.append({'level': 'ok', 'text': f'情绪热：全市场涨停 {mkt} 家'})
    else:
        flags.append({'level': 'bad', 'text': f'情绪冷：全市场涨停仅 {mkt} 家（<{f["hot_limit_count"]})'})
        rejects.append(f'情绪冷：涨停 {mkt} <{f["hot_limit_count"]} 家')
    # —— 连板（涨停池里标了 2 板以上 → 不是首板；昨日是否涨停拿不到，收盘人工复核）
    if boards is not None and boards > 1:
        flags.append({'level': 'bad', 'text': f'{boards} 连板，非首板'})
        rejects.append(f'{boards} 连板，非首板')
    else:
        flags.append({'level': 'warn', 'text': '首板按涨停池尽力判定，昨日板收盘人工核'})

    if rejects:
        stage = REJECT
    elif touched:
        stage = PASS
        flags.insert(0, {'level': 'ok', 'text': f'已触发（现价 ≥ {trig:.2f}）'})
    else:
        stage = WATCH
        flags.insert(0, {'level': 'warn', 'text': f'未触发（距触发价 {trig:.2f} 还差 '
                                                  f'{(trig / last - 1) * 100:.2f}%）'})

    return {
        'code': code, 'name': name,
        'gain': round(gain, 2),
        'vr': round(vr, 2), 'progress': round(progress, 2),
        'last': last, 'prev_close': prev,
        'limit_price': up, 'trigger_price': trig,
        'to_trigger_pct': round((trig / last - 1) * 100, 2) if trig and last else None,
        'to_limit_pct': round((up / last - 1) * 100, 2) if up and last else None,
        'touched': touched, 'sealed': bool(up and abs(last - up) <= 0.011 and gain >= lim * 100 - 0.5),
        'stage': stage,
        'boards': boards, 'board_text': raw.get('board_text') or '',
        'seal_time': raw.get('seal_time') or '',
        'seal_money': raw.get('seal_money'),
        'reason': raw.get('reason') or '',
        'is_st': is_st, 'is_bj': bj, 'limit_pct': lim,
        'market_limit_count': mkt,
        'open_gain': round((op / prev - 1) * 100, 2),
        'flags': flags, 'rejects': rejects,
    }
