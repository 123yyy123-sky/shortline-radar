# -*- coding: utf-8 -*-
"""命令行模式。给定时任务 / 脚本调用，或者不想开界面的时候用。

  python -m shortline.cli scan              按当前时间扫一次（打板模式）
  python -m shortline.cli scan --at 10:15   指定时点（回放）
  python -m shortline.cli scan --mode banlu 半路板模式（20cm 触及 5% + 高开>2% + 首板，次日收盘卖）
  python -m shortline.cli scan --json       输出 JSON，方便管道处理
  python -m shortline.cli sell              今天要卖的票和挂单价
  python -m shortline.cli status            环境自检

退出码：0 正常 / 1 有可打标的（方便脚本判断）/ 2 出错
"""
import argparse
import json
import sys

from .config import load_config
from .scanner import scan
from .holdings import sell_plan
from .datasource import get_source


def cmd_scan(cfg, args):
    def prog(stage, done, total, msg):
        if not args.quiet:
            sys.stderr.write(f'\r  {msg}          ')
            sys.stderr.flush()

    r = scan(cfg, at=args.at, progress=prog, mode=getattr(args, 'mode', None) or 'dban')
    if not args.quiet:
        sys.stderr.write('\r' + ' ' * 60 + '\r')
        sys.stderr.flush()

    if args.codes:
        # 同花顺导入自选股接受「6 位代码，一行一个」，不要带后缀
        rows = [x for x in r['rows'] if x['stage'] in ('pass', 'watch')]
        if getattr(args, 'sealed_only', False):
            rows = [x for x in rows if x['stage'] == 'pass']
        rows.sort(key=lambda x: (x['stage'] != 'pass', x['vr']))
        for x in rows:
            print(x['code'].split('.')[0])
        if not args.quiet:
            sys.stderr.write(f'\r已输出 {len(rows)} 只（可打 '
                             f'{sum(1 for x in rows if x["stage"] == "pass")}）\n')
        return 1 if any(x['stage'] == 'pass' for x in rows) else 0

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"# 短线雷达 · {r['generated_at']} · 时点 {r['at']} · {r['session_text']}"
              f" · 模式 {'半路板' if r.get('mode') == 'banlu' else '打板'}")
        print()
        f = r['funnel']
        if r.get('mode') == 'banlu':
            print(f"全市场 {f['total']} → 20cm {f.get('cm20', 0)} → 开盘未越过触发价 {f.get('open_ok', 0)}"
                  f" → 已触发 {f.get('touched', 0)} → 量比达标 {f.get('vr_ok', 0)}"
                  f" → 全条件 {f.get('clean', 0)} → 其中已封板 {f.get('sealed', 0)}")
        else:
            print(f"全市场 {f['total']} → 涨幅达标 {f['gain']} → 开盘达标 {f['open_range']}"
                  f" → 量比达标 {f['vr_ok']} → 全条件 {f['clean']} → 已封板 {f['sealed']}")
        print()
        for w in r['warnings']:
            print(f'> ⚠ {w}')
        if r['warnings']:
            print()

        rows = [x for x in r['rows'] if x['stage'] in ('pass', 'watch')]
        if not rows:
            print('**这次没有符合条件。** 空仓，等下一个。')
        else:
            for x in rows:
                if r.get('mode') == 'banlu':
                    tag = '✅ 已触发' if x['stage'] == 'pass' else '👀 盯住'
                    print(f"{tag} {x['code']} {x['name']}  涨{x['gain']:+.2f}% "
                          f"触发价{x['trigger_price']:.2f} 量比{x['vr']:.2f}"
                          + (f" 距触发{x['to_trigger_pct']:+.2f}%" if x.get('to_trigger_pct') is not None else ''))
                else:
                    tag = '✅ 可打' if x['stage'] == 'pass' else '👀 盯住'
                    print(f"{tag} {x['code']} {x['name']}  涨{x['gain']:+.2f}% "
                          f"开{x['open_gain']:+.2f}% 量比{x['vr']:.2f}"
                          + (f" 首封{x['seal_time']}" if x['seal_time'] else ''))
            print()
            if r.get('mode') == 'banlu':
                if any(x['stage'] == 'pass' for x in rows):
                    print('> 触发价附近买。当日封板→次日开盘卖；未封→当日收盘卖；跌回触发价→次日开盘清仓。')
                print('> 14:30 后不接新单。20cm 攒够 20 笔模拟样本前不上真仓。')
            elif any(x['stage'] == 'pass' for x in rows):
                print('> 挂涨停价排队，买不进就空仓。T+1 早上集合竞价挂跌停价清光。')
    return 1 if any(x['stage'] == 'pass' for x in r['rows']) else 0


def cmd_sell(cfg, args):
    from .datasource import get_source as gs
    src = gs(cfg)
    items = sell_plan(cfg)
    if items:
        try:
            snaps = src.snapshot([i['code'] for i in items], share=1.0)
            items = sell_plan(cfg, {s['thscode']: s for s in snaps})
        except Exception as e:
            print(f'（快照取失败，跌停价留空：{e}）')
    if not items:
        print('**没有持仓。**')
        return 0
    print('| 代码 | 名称 | 买入日 | 买价 | 昨收 | **挂单价** | 浮动 | 动作 |')
    print('|---|---|---|---:|---:|---:|---:|---|')
    for h in items:
        print(f"| {h['code']} | {h['name']} | {h['buy_date'] or '—'} | "
              f"{h['buy_price'] or '—'} | {h['prev_close'] or '—'} | "
              f"**{h['floor_price'] if h['floor_price'] else '手填'}** | "
              f"{('%.2f%%' % h['pnl_pct']) if h['pnl_pct'] is not None else '—'} | {h['action']} |")
    print()
    print('> 9:20 前挂出去，9:25 集合竞价撮合。不改价、不等高点。')
    return 0


def cmd_status(cfg, args):
    print(f"数据源：{cfg['data_source']}")
    try:
        src = get_source(cfg)
        ok, note = src.health()
        print(f"  {src.label} · {'可用' if ok else '不可用'} · {note}")
    except Exception as e:
        print(f'  不可用：{e}')
    try:
        from .scanner import load_benchmark
        b = load_benchmark()
        print(f"基准：{b['asof']} · {len(b.get('items') or {})} 只")
    except SystemExit as e:
        print(f'基准缺失：{e}')
    f = cfg['filters']
    print(f"筛选：涨幅≥{f['min_gain_pct']}% 开盘{f['open_gain_min_pct']}~{f['open_gain_max_pct']}% "
          f"量比<{f['vr_max']} 最多{f['max_boards']}板"
          + (' 排除ST' if f['exclude_st'] else '')
          + (' 排除北交所' if f['exclude_bj'] else ''))
    return 0


def main():
    ap = argparse.ArgumentParser(prog='shortline', description='短线雷达命令行')
    sub = ap.add_subparsers(dest='cmd')

    s = sub.add_parser('scan', help='扫一次')
    s.add_argument('--at', help='HH:MM，默认当前时间')
    s.add_argument('--mode', choices=['dban', 'banlu'], default='dban',
                   help='dban=打板（默认），banlu=半路板')
    s.add_argument('--json', action='store_true')
    s.add_argument('--quiet', action='store_true', help='不显示进度')
    s.add_argument('--codes', action='store_true',
                   help='只输出 6 位代码，一行一个（贴进同花顺自选股导入）')
    s.add_argument('--sealed-only', action='store_true', help='--codes 时只输出已封板的')

    sub.add_parser('sell', help='今天要卖的票')
    sub.add_parser('status', help='环境自检')

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 2

    cfg = load_config()
    fn = {'scan': cmd_scan, 'sell': cmd_sell, 'status': cmd_status}[args.cmd]
    try:
        return fn(cfg, args)
    except Exception as e:
        print(f'出错：{type(e).__name__}: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
