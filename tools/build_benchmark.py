# -*- coding: utf-8 -*-
"""从本地日K面板构建扫描所需的两个数据文件。

产出：
  data/benchmark.json   每只票的「前 5 日均量」+ 昨收，用于算量比
  代码清单直接取自 benchmark 的键，不另存一份。

为什么不用数据库：
  扫描只需要「前 5 日均量」这一个数。把它压成 JSON 只有几百 KB，
  可以跟着仓库一起走，别人 clone 下来不用准备行情库也能跑。

基准口径（重要）：
  asof = 面板最后一个交易日 D。
  v5   = D 往前 5 个交易日（含 D）的成交量均值，也就是 D-4..D。
  适用 = D 之后的第一个交易日。因为 T 日盘中要的是「T-1 往前 5 日」，
         所以 D 就是 T-1 时的最新数据。

用法：
  python tools/build_benchmark.py --panel data/panel_10y.parquet
  python tools/build_benchmark.py --panel path.parquet --out data
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--panel', required=True, help='日K parquet，至少含 thscode/date/close/volume')
    ap.add_argument('--out', default=os.path.join(ROOT, 'data'))
    ap.add_argument('--days', type=int, default=5, help='均量天数，默认 5')
    args = ap.parse_args()

    try:
        import duckdb
    except ImportError:
        sys.exit('缺 duckdb：pip install duckdb')

    os.makedirs(args.out, exist_ok=True)
    con = duckdb.connect()
    n = args.days

    asof = con.execute(
        f"SELECT max(date) FROM read_parquet('{args.panel}')").fetchone()[0]
    print(f'面板最后交易日：{asof}')

    # 前 n 日均量 + 最后收盘
    # nd >= 3 而不是 nd = n：停牌一两天的票不该被整只排除，
    # 用剩下几天的均量当基准，分母偏小 → 量比偏大 → 更容易被筛掉，方向是保守的。
    rows = con.execute(f"""
        WITH r AS (
          SELECT thscode, date, close, volume,
                 row_number() OVER (PARTITION BY thscode ORDER BY date DESC) rn
          FROM read_parquet('{args.panel}')
          WHERE date <= DATE '{asof}'
        )
        SELECT thscode,
               max(CASE WHEN rn = 1 THEN close END) AS last_close,
               avg(CASE WHEN rn <= {n} THEN volume END) AS v5,
               count(*) AS nd
        FROM r WHERE rn <= {n}
        GROUP BY 1
        HAVING v5 IS NOT NULL AND v5 > 0 AND nd >= 3
    """).fetchall()

    items = {c: [round(float(v), 1), round(float(p), 2)] for c, p, v, _ in rows if c}
    bench = {
        'asof': str(asof),
        'days': n,
        'built_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(items),
        # 紧凑存储：值 = [前N日均量, 最后收盘]
        'items': items,
    }
    bench_path = os.path.join(args.out, 'benchmark.json')
    with open(bench_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(bench, f, ensure_ascii=False, separators=(',', ':'))
    print(f'benchmark.json {len(items)} 只  {os.path.getsize(bench_path)/1024:.0f} KB')
    print()
    print(f'基准截至 {asof}，适用于 {asof} 之后的第一个交易日。')
    print('注意：基准会随交易日衰减。跑之前先确认 asof 是不是「上一个交易日」。')


if __name__ == '__main__':
    main()
