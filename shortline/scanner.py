# -*- coding: utf-8 -*-
"""扫描主流程：拉数据 → 套规则 → 出结构化结果。

不拼字符串，返回 dict，谁要用谁自己渲染（命令行 / 网页 / 推送）。
"""
import json
import os
import time
from datetime import datetime, date

from .config import ROOT
from .datasource import get_source
from .rules import ShareCurve, evaluate, PASS, WATCH, REJECT

DATA = os.path.join(ROOT, 'data')

_BENCH = None
_SYMBOLS = None


# ---------------------------------------------------------------- 本地数据

def load_benchmark(force=False):
    """data/benchmark.json：{'asof':..,'items':{code:[前5日均量, 昨收]}}"""
    global _BENCH
    if _BENCH is not None and not force:
        return _BENCH
    p = os.path.join(DATA, 'benchmark.json')
    if not os.path.exists(p):
        raise SystemExit('缺少 data/benchmark.json。先跑 python tools/build_benchmark.py --panel <日K.parquet>')
    _BENCH = json.load(open(p, encoding='utf-8'))
    return _BENCH


def load_symbols(force=False):
    global _SYMBOLS
    if _SYMBOLS is not None and not force:
        return _SYMBOLS
    p = os.path.join(DATA, 'symbols.json')
    _SYMBOLS = json.load(open(p, encoding='utf-8')).get('items', {}) if os.path.exists(p) else {}
    return _SYMBOLS


# ---------------------------------------------------------------- 时段

def trade_session(hhmm, weekday=None):
    """返回 (代码, 人话)。

    weekday 0=周一。周末一律视为休市。
    """
    weekday = datetime.now().weekday() if weekday is None else weekday
    if weekday >= 5:
        return 'closed', '周末休市'
    if hhmm < '09:15':
        return 'pre', '盘前（未开盘）'
    if hhmm < '09:25':
        return 'auction', '集合竞价'
    if hhmm < '09:30':
        return 'auction', '集合竞价（已撮合，待开盘）'
    if hhmm <= '11:30':
        return 'trading', '上午连续竞价'
    if hhmm < '13:00':
        return 'lunch', '午间休市'
    if hhmm <= '15:00':
        return 'trading', '下午连续竞价'
    return 'closed', '已收盘'


def bench_age_note(bench):
    """基准是给「下一个交易日」用的。放久了要提醒。"""
    asof = bench.get('asof')
    if not asof:
        return True, ''
    try:
        d = date.fromisoformat(asof)
    except ValueError:
        return True, ''
    gap = (date.today() - d).days
    if gap <= 1:
        return False, ''
    if gap <= 5:
        return True, f'基准截至 {asof}，距今 {gap} 天，量比可能偏一点。'
    return True, (f'基准截至 {asof}，距今 {gap} 天，量比已经不准。'
                  f'跑 python tools/build_benchmark.py 重新生成。')


# ---------------------------------------------------------------- 扫描

def scan(cfg, at=None, progress=None):
    """跑一次完整扫描。

    progress(stage, done, total, msg) —— stage 是 'snapshot' 这种短标识，
    前端用它显示进度条。可以为 None。
    """
    t0 = time.time()
    f = cfg['filters']
    curve = ShareCurve(cfg.get('intraday_share') or [['15:00', 1.0]])

    now = datetime.now()
    hhmm = (at or now.strftime('%H:%M'))[:5]
    sess, sess_text = trade_session(hhmm, now.weekday())
    share = curve.at(hhmm)

    def report(stage, done, total, msg):
        if progress:
            progress(stage, done, total, msg)

    # 1) 本地基准
    report('bench', 0, 1, '载入本地基准')
    bench = load_benchmark()
    symbols = load_symbols()
    bmap = bench.get('items') or {}
    codes = list(bmap.keys())
    stale, stale_note = bench_age_note(bench)
    report('bench', 1, 1, f'基准 {len(codes)} 只，截至 {bench.get("asof")}')

    # 2) 实时快照
    src = get_source(cfg)
    report('snapshot', 0, 1, f'拉取实时快照（{src.label}）')

    def sp(done, total):
        report('snapshot', done, total, f'快照 {done}/{total} 批')

    snaps = src.snapshot(codes, progress=sp, share=share)

    # 3) 涨停池（拿首封时间、连板、封单）
    report('pool', 0, 1, '拉取涨停池')
    try:
        pool_rows = src.limit_pool()
    except Exception:
        pool_rows = []
    pool = {x['thscode']: x for x in pool_rows if x.get('thscode')}
    report('pool', 1, 1, f'涨停池 {len(pool)} 只')

    # 4) 逐只判定
    report('evaluate', 0, len(snaps), '逐只判定')
    rows, funnel = [], {
        'total': len(snaps), 'has_bench': 0, 'gain': 0,
        'open_range': 0, 'vr_ok': 0, 'clean': 0, 'sealed': 0,
    }
    zero_volume = 0

    for i, s in enumerate(snaps):
        if i % 500 == 0:
            report('evaluate', i, len(snaps), f'判定 {i}/{len(snaps)}')

        code = s.get('thscode')
        b = bmap.get(code)
        prev = s.get('prev_price')
        op, last, vol = s.get('open_price'), s.get('last_price'), s.get('volume')
        if not (code and b and prev and op and last and vol is not None):
            continue
        if vol == 0:
            zero_volume += 1
        funnel['has_bench'] += 1

        p = pool.get(code) or {}
        raw = {
            'code': code,
            'name': p.get('name') or symbols.get(code) or '',
            'prev_close': prev, 'open_price': op, 'last_price': last,
            'volume': vol, 'v5': b[0], 'share': share,
            'boards': p.get('continue_day_cnt'),
            'board_text': p.get('continue_day_text') or '',
            'seal_time': p.get('limit_up_time') or '',
            'seal_money': p.get('seal_money'),
            'reason': p.get('limit_up_reason') or '',
            'is_st': p.get('is_st'),
        }
        r = evaluate(raw, f)

        # 漏斗：每一道剩多少
        if r['gain'] >= f['min_gain_pct']:
            funnel['gain'] += 1
            in_open = f['open_gain_min_pct'] <= r['open_gain'] < f['open_gain_max_pct']
            if in_open:
                funnel['open_range'] += 1
                if r['vr'] < f['vr_max']:
                    funnel['vr_ok'] += 1
                    if not (r['boards'] and r['boards'] > f['max_boards']) \
                            and not (r['is_st'] and f['exclude_st']) \
                            and not (r['is_bj'] and f['exclude_bj']):
                        funnel['clean'] += 1
                        if r['sealed']:
                            funnel['sealed'] += 1
            # 涨幅达标的都进表，方便看「差在哪一步」
            rows.append(r)
        elif r['sealed'] and f.get('show_sealed_below_gain'):
            rows.append(r)

    zero_volume_ratio = zero_volume / max(funnel['has_bench'], 1)

    # 5) 排序：能做的排前面，同档按量比升序
    order = {PASS: 0, WATCH: 1, REJECT: 2}
    rows.sort(key=lambda x: (order.get(x['stage'], 9), x['vr']))

    counts = {PASS: 0, WATCH: 0, REJECT: 0}
    for r in rows:
        counts[r['stage']] = counts.get(r['stage'], 0) + 1

    warnings = []
    if stale and stale_note:
        warnings.append(stale_note)
    if sess not in ('trading', 'auction'):
        warnings.append(f'现在是「{sess_text}」，快照是最近一次收盘的数据。'
                        f'量比按 {hhmm} 折算，仅供回顾，不能当盘中信号。')
    if zero_volume_ratio > 0.5:
        warnings.append(f'{zero_volume_ratio:.0%} 的票成交量为 0，行情可能还没更新或今天休市。')
    if src.name == 'demo':
        warnings.append('当前是演示数据，不是真实行情。改 config.json 的 data_source 接真实源。')

    ok, src_note = src.health()
    if not ok:
        warnings.append(src_note)

    report('done', 1, 1, '扫描完成')

    return {
        'ok': True,
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
        'at': hhmm,
        'session': sess,
        'session_text': sess_text,
        'source': src.name,
        'source_label': src.label,
        'bench_asof': bench.get('asof'),
        'share': round(share, 4),
        'filters': f,
        'funnel': funnel,
        'counts': counts,
        'rows': rows,
        'warnings': warnings,
        'elapsed': round(time.time() - t0, 1),
    }
