# -*- coding: utf-8 -*-
"""交易规则与判定逻辑。

这里只有规则，不碰数据源，也不碰界面。改规则只改这一个文件。

三条规则来源（详见 docs/STRATEGY.md 与知识库 [[短线交易系统]]）：
  1. 涨停幅度按板块分档，ST 单独处理
  2. 量比 = 当日累计量 / 前 5 日均量。盘中拿不到「全天量比」，
     要用日内成交量占比曲线把当前推进率折成全天口径。
     ⚠️ 2026-10-24 起量比**不再作判据**（未来函数式口径，新回测未复现），只作展示
  3. 开仓条件（真源 §6.5）：首板 + 开盘 1~3% + 昨日量比<1 + 价格≥20 + 20cm 优先。
     程序只能算「首板 / 开盘幅度 / 价格 / 板型」，昨日量比靠人工核。
     ⚠️ 打板只到打平（−0.682% → +0.020%），这组条件压炸板率，不构成开仓理由
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
    # —— 量比（2026-10-24 起只作参考。全天量比口径与真源 §6.5 的「昨日量比」不是同一个数，
    #    旧「量比<1 → +2.33%」结论在新口径未复现，故不作否决）
    vr_max = f.get('vr_max')
    if vr_max is None:
        flags.append({'level': 'ok' if vr < 2 else 'warn',
                      'text': f'量比 {vr:.2f}（仅参考，不作判据）'})
    elif vr >= vr_max:
        flags.append({'level': 'warn', 'text': f'量比 {vr:.2f} ≥{vr_max:g}'})
        rejects.append(f'量比 {vr:.2f} 高于 {vr_max:g}')
    else:
        flags.append({'level': 'ok', 'text': f'量比 {vr:.2f} <{vr_max:g}'})
    # —— 价格 ≥ min_price（§6.5 条件④）
    min_price = f.get('min_price')
    if min_price:
        if prev < min_price:
            flags.append({'level': 'bad', 'text': f'价格 {prev:.2f} <{min_price:g} 元'})
            rejects.append(f'价格 {prev:.2f} 低于 {min_price:g} 元')
        else:
            flags.append({'level': 'ok', 'text': f'价格 {prev:.2f} ≥{min_price:g} 元'})
    # —— 板型（§6.5 条件⑤：20cm 炸板率 30.4%，10cm 35.4%。只打标，不否决）
    if f.get('prefer_20cm') and lim != 0.20:
        flags.append({'level': 'warn',
                      'text': f'{int(lim * 100)}cm，炸板率 35.4% 高于 20cm 的 30.4%'})
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
        'last': last, 'prev_close': prev, 'price': round(prev, 2), 'limit_price': up,
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

# 回测来源：真源 [[短线交易系统]] §6.4（2026-10-24 复核后重写）
#           + [[短线回测_11_打板前置条件与半路板可执行版]]。
# 可执行口径：20cm、T=5%、开盘涨幅 >2%、首板、次一交易日收盘卖 =
#   +1.381% / 胜率 50.1% / n=42,257，2020~2026 7/7 年正。
# ⚠️ 原「量比≥1.5」「情绪热≥50」是未来函数（用收盘才知道的数），已删 —— 去掉后 +1.73% 掉到 +0.25%；
#    原「未封板当日收盘卖」违反 T+1，已改次一交易日收盘卖。
# ⚠️ 必须打折：2024 单年 +4.172%（贡献过半），剔除后 +0.587%，前 5 天占 70.4%。
#    保守预期 +0.5~0.6% 毛、+0.35~0.45% 净。仅模拟盘或 ≤300 元验证。
BANLU_DEFAULTS = {
    'trigger_pct': 5.0,        # T=5%，20cm 半板触发价 = round(昨收×1.05, 2)
    'min_open_gap_pct': 2.0,   # §6.4 条件②：开盘涨幅 >2%（唯一有效的可执行过滤）
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
    open_gain = (op / prev - 1) * 100
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
    # —— 开盘涨幅 > min_open_gap_pct（§6.4 条件②：唯一有效的可执行过滤，+0.364% → +1.381%）
    min_gap = f.get('min_open_gap_pct')
    if min_gap:
        if open_gain > min_gap:
            flags.append({'level': 'ok', 'text': f'高开 {open_gain:.2f}% >{min_gap:g}%'})
        else:
            flags.append({'level': 'bad', 'text': f'高开 {open_gain:.2f}% 不足 {min_gap:g}%'})
            rejects.append(f'高开 {open_gain:.2f}% ≤{min_gap:g}%')
    # —— 量比（只作参考：原「量比≥1.5」用全天成交量，是未来函数，真源 §6.4 已删）
    flags.append({'level': 'ok' if vr >= 1.5 else 'warn',
                  'text': f'量比 {vr:.2f}（仅参考，不作判据）'})
    # —— 情绪（只作参考：原「情绪热≥50」用当日涨停家数，是未来函数，真源 §6.4 已删）
    flags.append({'level': 'ok' if mkt >= 50 else 'warn',
                  'text': f'全市场涨停 {mkt} 家（仅参考，不作判据）'})
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
        'last': last, 'prev_close': prev, 'price': round(prev, 2),
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
        'open_gain': round(open_gain, 2),
        'open_gap': round(open_gain, 2),
        'exit_rule': '次一交易日收盘卖',
        'flags': flags, 'rejects': rejects,
    }
