# -*- coding: utf-8 -*-
"""问财口径对照诊断。

拿同一份实时快照，按不同口径各算一遍，看差别到底出在哪一条。
用途：同花顺问财选不出票时，先跑这个判断是「市场本来就没有」还是「口径不同」。

    python tools/diag_wencai.py            # 用当前时间
    python tools/diag_wencai.py --at 10:00 # 指定时点回放

输出四块：
  1. 单条件命中数 —— 拿去和问财单条件结果对，一句一个数
  2. 累积漏斗 —— 每加一条剩多少
  3. 三种口径对比 —— 软件口径 / 同花顺量比口径 / 把开盘涨幅换成日内涨幅
  4. 通过全条件的票，明细
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shortline.config import load_config                      # noqa: E402
from shortline.datasource import get_source                   # noqa: E402
from shortline.rules import ShareCurve, is_bj, limit_pct, limit_price, PASS  # noqa: E402
from shortline.scanner import load_benchmark, load_symbols     # noqa: E402


def traded_minutes(hhmm):
    """已交易分钟，午休不计。"""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    t = h * 60 + m
    if t <= 9 * 60 + 30:
        return 1
    if t <= 11 * 60 + 30:
        return t - (9 * 60 + 30)
    if t < 13 * 60:
        return 120
    if t <= 15 * 60:
        return 120 + (t - 13 * 60)
    return 240


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--at', default=None, help='当成几点，如 10:15')
    args = ap.parse_args()

    cfg = load_config()
    f = cfg['filters']
    hhmm = (args.at or datetime.now().strftime('%H:%M'))[:5]
    curve = ShareCurve(cfg.get('intraday_share') or [['15:00', 1.0]])
    share_soft = curve.at(hhmm)          # 软件用的：历史实测日内量占比
    mins = traded_minutes(hhmm)
    share_uni = min(mins / 240.0, 1.0)   # 同花顺量比隐含的：均匀分布

    bench = load_benchmark()
    symbols = load_symbols()
    bmap = bench['items']
    codes = list(bmap.keys())

    src = get_source(cfg)
    print(f'时点 {hhmm}  数据源 {src.label}')
    print(f'软件占比 share={share_soft:.3f}   均匀占比 share={share_uni:.3f}   '
          f'已交易 {mins} 分钟   （两者比值 {share_soft / share_uni:.2f}）')
    print(f'基准截至 {bench.get("asof")}')
    print()

    snaps = src.snapshot(codes)
    try:
        pool = {x['thscode']: x for x in src.limit_pool() if x.get('thscode')}
    except Exception:
        pool = {}
    print(f'快照 {len(snaps)} 只   涨停池 {len(pool)} 只\n')

    # 单条件命中计数
    c = dict(total=0, gain=0, open_soft=0, open_intraday=0, vr_soft=0, vr_ths=0,
             board=0, not_st=0, not_bj=0)
    # 累积漏斗
    acc = dict(soft=[], ths=[], no_open=[])
    # 差异明细
    flip_open, flip_vr = [], []

    for s in snaps:
        code = s.get('thscode')
        b = bmap.get(code)
        prev, op = s.get('prev_price'), s.get('open_price')
        last, vol = s.get('last_price'), s.get('volume')
        if not (code and b and prev and op and last and vol is not None):
            continue
        c['total'] += 1

        gain = (last / prev - 1) * 100
        open_gain = (op / prev - 1) * 100
        intraday = (last / op - 1) * 100 if op else 0.0
        v5 = b[0]
        progress = vol / v5 if v5 > 0 else 0.0
        vr_soft = progress / share_soft if share_soft else progress
        vr_ths = progress / share_uni if share_uni else progress

        p = pool.get(code) or {}
        name = p.get('name') or symbols.get(code) or ''
        boards = p.get('continue_day_cnt')
        is_st = bool(p.get('is_st')) or ('ST' in name.upper())
        bj = is_bj(code)

        if gain >= f['min_gain_pct']:
            c['gain'] += 1
            in_open_soft = f['open_gain_min_pct'] <= open_gain < f['open_gain_max_pct']
            in_open_intra = f['open_gain_min_pct'] <= intraday < f['open_gain_max_pct']
            if in_open_soft:
                c['open_soft'] += 1
            if in_open_intra:
                c['open_intraday'] += 1
            if vr_soft < f['vr_max']:
                c['vr_soft'] += 1
            if vr_ths < f['vr_max']:
                c['vr_ths'] += 1
            if not (boards and boards > f['max_boards']):
                c['board'] += 1
            if not is_st:
                c['not_st'] += 1
            if not bj:
                c['not_bj'] += 1

            if in_open_soft != in_open_intra:
                flip_open.append((code, name, gain, open_gain, intraday))
            if (vr_soft < f['vr_max']) != (vr_ths < f['vr_max']):
                flip_vr.append((code, name, gain, progress, vr_soft, vr_ths))

        # 累积
        base = gain >= f['min_gain_pct']
        clean = not (boards and boards > f['max_boards']) and not is_st and not bj
        acc['soft'].append(bool(base and f['open_gain_min_pct'] <= open_gain < f['open_gain_max_pct']
                               and vr_soft < f['vr_max'] and clean))
        acc['ths'].append(bool(base and f['open_gain_min_pct'] <= open_gain < f['open_gain_max_pct']
                              and vr_ths < f['vr_max'] and clean))
        acc['no_open'].append(bool(base and vr_soft < f['vr_max'] and clean))

    def line(label, n, note=''):
        print(f'  {label:<26}{n:>6}   {note}')

    print('=== 一、单条件命中数（拿去和问财逐句对）===')
    line('全市场（有基准）', c['total'])
    line(f'涨幅 ≥{f["min_gain_pct"]:g}%', c['gain'])
    line(f'　└ 开盘涨幅 {f["open_gain_min_pct"]:g}~{f["open_gain_max_pct"]:g}%（软件口径）', c['open_soft'])
    line('　└ 日内涨幅 3~9%（若问财按此理解）', c['open_intraday'], '注意这个数差很多')
    line(f'　└ 量比 <{f["vr_max"]:g}（软件口径）', c['vr_soft'])
    line('　└ 量比 <1（同花顺实时口径）', c['vr_ths'])
    line(f'　└ 连板 ≤{f["max_boards"]}', c['board'])
    line('　└ 非 ST', c['not_st'])
    line('　└ 非北交所', c['not_bj'])
    print()

    print('=== 二、累积漏斗（条件一条条加上去）===')
    line('最终结果（软件口径）', sum(acc['soft']))
    line('最终结果（同花顺量比口径）', sum(acc['ths']))
    line('最终结果（不要开盘条件）', sum(acc['no_open']))
    print()

    print('=== 三、两条会引起分歧的条件 ===')
    print(f'  开盘口径不同导致翻盘：{len(flip_open)} 只')
    for x in flip_open[:8]:
        print(f'    {x[0]} {x[1]:<6} 涨幅{x[2]:5.1f}%  开盘{x[3]:5.1f}%  日内{x[4]:5.1f}%')
    print(f'  量比口径不同导致翻盘：{len(flip_vr)} 只')
    for x in flip_vr[:8]:
        print(f'    {x[0]} {x[1]:<6} 涨幅{x[2]:5.1f}%  推进率{x[3]:.2f}  '
              f'软件量比{x[4]:.2f} → 同花顺{x[5]:.2f}')
    print()

    print('=== 四、通过全条件的票 ===')
    rows = []
    for s in snaps:
        code = s.get('thscode')
        b = bmap.get(code)
        prev, op = s.get('prev_price'), s.get('open_price')
        last, vol = s.get('last_price'), s.get('volume')
        if not (code and b and prev and op and last and vol is not None):
            continue
        gain = (last / prev - 1) * 100
        open_gain = (op / prev - 1) * 100
        v5 = b[0]
        progress = vol / v5 if v5 > 0 else 0.0
        vr_soft = progress / share_soft if share_soft else progress
        vr_ths = progress / share_uni if share_uni else progress
        p = pool.get(code) or {}
        name = p.get('name') or symbols.get(code) or ''
        boards = p.get('continue_day_cnt')
        is_st = bool(p.get('is_st')) or ('ST' in name.upper())
        clean = not (boards and boards > f['max_boards']) and not is_st and not is_bj(code)
        if gain >= f['min_gain_pct'] and f['open_gain_min_pct'] <= open_gain < f['open_gain_max_pct'] \
                and vr_soft < f['vr_max'] and clean:
            up = limit_price(prev, code, name)
            sealed = bool(up and abs(last - up) <= 0.011)
            rows.append((code, name, gain, open_gain, vr_soft, vr_ths, sealed,
                         p.get('limit_up_time') or '', boards))
    if not rows:
        print('  无。今天到此刻全市场没有满足全部条件的票。')
    for r in sorted(rows, key=lambda x: x[4]):
        print(f'  {r[0]} {r[1]:<6} 涨幅{r[2]:5.2f}% 开盘{r[3]:5.2f}% '
              f'量比{r[4]:.2f}(同花顺{r[5]:.2f}) {"已封" if r[6] else "未封"} '
              f'{r[7]} {r[8] or "?"}板')


if __name__ == '__main__':
    main()
