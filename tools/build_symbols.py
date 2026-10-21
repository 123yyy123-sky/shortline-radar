# -*- coding: utf-8 -*-
"""生成 data/symbols.json（代码 → 名称）。

为什么需要它：
  实时快照只有 11 个字段，**不含股票名称**。涨停池里的票能拿到名字，
  但「涨了 7% 还没封板」的观察对象拿不到。
  有了这份目录，界面才能把代码显示成人看得懂的名字。

数据来自 hithink-finance 的标的目录（一次可拉 1 万条，全 A 股一次拉完）。

用法：
  python tools/build_symbols.py
  python tools/build_symbols.py --cli "C:/path/to/hithink-finance.cmd"
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_CLI = os.path.join(ROOT, 'tools', 'hithink-finance.cmd')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cli', default=os.environ.get('HITHINK_CLI') or DEFAULT_CLI,
                    help='hithink-finance 可执行文件路径（也可用环境变量 HITHINK_CLI）')
    ap.add_argument('--out', default=os.path.join(ROOT, 'data', 'symbols.json'))
    ap.add_argument('--exchange', default='SH,SZ,BJ')
    args = ap.parse_args()

    if not os.path.exists(args.cli):
        sys.exit(f'找不到 CLI：{args.cli}\n用 --cli 指定，或设置环境变量 HITHINK_CLI。')

    print(f'拉取标的目录（{args.exchange}）...')
    r = subprocess.run([args.cli, 'symbol', 'list', '--exchange', args.exchange,
                        '--limit', '10000', '--format', 'json'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        d = json.loads(r.stdout)
    except Exception:
        sys.exit(f'解析失败：{r.stdout[:300]} {r.stderr[:300]}')
    if not d.get('ok'):
        sys.exit(f'调用失败：{json.dumps(d.get("error"), ensure_ascii=False)[:300]}')

    items = d['data']['item']
    m = {x['thscode']: x.get('name') or '' for x in items if x.get('thscode')}
    out = {
        'built_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(m),
        'items': m,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f'{args.out}  {len(m)} 只  {os.path.getsize(args.out)/1024:.0f} KB')


if __name__ == '__main__':
    main()
