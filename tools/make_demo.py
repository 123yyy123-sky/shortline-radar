# -*- coding: utf-8 -*-
"""生成 data/demo_snapshot.json —— 没有行情 CLI 时用来看界面。

数据是构造的，不是真实行情。设计上刻意覆盖了每一种判定分支，
这样任何人 clone 下来都能立刻看到界面里各行会怎么显示。

用法：
  python tools/make_demo.py
"""
import json
import os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, 'data')

# (代码, 名称, 昨收, 开盘价, 最新价, 涨停价, 目标量比, 首封时刻, 连板, 封单万元, 理由)
# 量比 = 当日累计量 / 前5日均量。这里直接给「目标全天量比」，脚本反算成交量。
PLAN = [
    # ——— 命中：已封 + 量比<1 + 首板 + 开盘 3~9%
    ('002479.SZ', '富春环保', 6.01, 6.47, 6.61, 6.61, 0.80, '09:30', 1, 6371, '环保+危废处置'),
    ('603386.SH', '骏亚科技', 21.71, 22.64, 23.88, 23.88, 0.99, '09:36', 1, 4362, 'PCB+算力'),
    # ——— 量比过大：2 以上期望为负，1~2 只观察
    ('300750.SZ', '宁德时代', 258.00, 270.00, 283.80, 283.80, 2.60, '09:52', 1, 88000, '锂电'),
    ('600089.SH', '特变电工', 15.20, 16.00, 16.72, 16.72, 1.45, '10:05', 1, 15800, '特高压'),
    # ——— 连板：炸板亏得比首板多一倍
    ('002886.SZ', '沃特股份', 20.00, 21.00, 22.00, 22.00, 0.90, '09:41', 2, 9200, '新材料'),
    # ——— 排除项：ST / 北交所
    ('600800.SH', 'ST磁卡', 3.00, 3.10, 3.15, 3.15, 0.70, '09:33', 1, 2100, 'ST股'),
    ('920050.BJ', '爱伦医疗', 10.00, 10.60, 13.00, 13.00, 0.85, '09:44', 1, 860, '北交所'),
    # ——— 盯住：条件都够但还没封板
    ('002123.SZ', '梦网科技', 10.00, 10.50, 10.85, 11.00, 0.72, None, 0, 0, ''),
    # ——— 硬条件不达标：涨幅 / 开盘幅度
    ('600410.SH', '华胜天成', 8.00, 8.28, 8.40, 8.80, 0.65, None, 0, 0, ''),
    ('300496.SZ', '中科创达', 20.00, 21.92, 22.00, 22.00, 0.95, '09:31', 1, 3300, 'AI应用'),
    ('002415.SZ', '海康威视', 30.00, 30.60, 33.00, 33.00, 0.78, '10:20', 1, 12000, '安防'),
]


def main():
    bench = json.load(open(os.path.join(DATA, 'benchmark.json'), encoding='utf-8'))
    bmap = bench['items']

    snap, pool = [], []
    for code, name, prev, op, last, up, vr, t, boards, seal_w, reason in PLAN:
        v5 = (bmap.get(code) or [0])[0] or 2_000_000
        vol = int(v5 * vr)                       # 全天成交量（share=1 时的值）
        snap.append({
            'thscode': code, 'ticker': code.split('.')[0], 'volume': vol,
            'turnover': int(vol * (op + last) / 2),
            'last_price': last, 'price_change': round(last - prev, 2),
            'price_change_ratio_pct': round((last / prev - 1) * 100, 4),
            'open_price': op, 'high_price': max(up, last), 'low_price': round(min(op, last) * 0.985, 2),
            'prev_price': prev,
            # 下划线开头是内部字段：DemoSource 靠这两个值把「全天量比」折成
            # 「任意时点的累计量」，保证界面上任何时刻看到的量比都和目标一致。
            '_v5': round(v5, 1), '_vr': vr,
        })
        if t:                                     # 有封板时刻 = 进过涨停池
            pool.append({
                'thscode': code, 'ticker': code.split('.')[0], 'name': name,
                'is_st': name.upper().startswith('ST'), 'is_new': False,
                'last_price': last,
                'price_change_ratio_pct': round((last / prev - 1) * 100, 4),
                'limit_up_time': t, 'limit_up_reason': reason,
                'continue_day_text': '首板' if boards <= 1 else f'{boards}连板',
                'continue_day_cnt': boards,
                'seal_money': seal_w * 10000, 'max_seal_money': seal_w * 10000 * 3,
            })

    # 未进涨停池的票，名称从 symbols.json 补（真实运行时也是这么兜的）
    sym = json.load(open(os.path.join(DATA, 'symbols.json'), encoding='utf-8'))['items']
    names = {p['thscode']: p['name'] for p in pool}
    for s in snap:
        names.setdefault(s['thscode'], sym.get(s['thscode'], ''))

    out = {
        'meta': {
            'generated_at': date.today().isoformat(),
            'bench_asof': bench['asof'],
            'note': '构造的演示数据，不是真实行情。覆盖了命中/量比过大/连板/ST/北交所/未封/不达标七种分支。',
        },
        'names': names,
        'snapshot': snap,
        'limit_pool': pool,
    }
    p = os.path.join(DATA, 'demo_snapshot.json')
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'{p}  {len(snap)} 只快照 / {len(pool)} 只涨停池  {os.path.getsize(p)/1024:.0f} KB')


if __name__ == '__main__':
    main()
