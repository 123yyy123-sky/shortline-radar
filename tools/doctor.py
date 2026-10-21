# -*- coding: utf-8 -*-
"""环境自检。扫描跑不起来时先跑它。

  python tools/doctor.py

它只读不写，依次查：Python、行情 CLI 能不能找到、找到的是哪一份、
CLI 能不能真的回话、本地基准是否齐全。任何一项不对，它会直接告诉你
该改哪个文件。
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OK, BAD, WARN = '[ OK ]', '[FAIL]', '[WARN]'


def line(tag, text):
    print(f'{tag} {text}')


def main():
    print('=' * 64)
    line(OK, f'Python {sys.version.split()[0]}  {sys.executable}')
    line(OK, f'程序目录 {ROOT}')

    # ---- 1. PATH 上有什么
    node = shutil.which('node')
    path_cli = shutil.which('hithink-finance.cmd') or shutil.which('hithink-finance')
    print('-' * 64)
    print('这一步查的是「从资源管理器双击时能不能看见行情工具」。')
    line(OK if node else WARN, f'PATH 里的 node       : {node or "没有"}')
    line(OK if path_cli else WARN, f'PATH 里的 hithink CLI : {path_cli or "没有"}')
    if not path_cli:
        print('      PATH 里没有是常态，不代表坏 —— 只要下面 vendor/ 那项是 OK 就行。')

    # ---- 2. 配置
    cfg_path = os.path.join(ROOT, 'config.json')
    if not os.path.isfile(cfg_path):
        line(BAD, f'缺配置文件 {cfg_path}')
        print('      解决：复制 config.example.json 为 config.json')
        return 2
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)
    line(OK, f'config.json           : data_source={cfg.get("data_source")}')

    # ---- 3. 数据源
    print('-' * 64)
    from shortline.datasource import SourceError, get_source
    try:
        src = get_source(cfg)
    except SourceError as e:
        line(BAD, '数据源不可用')
        print('      ' + str(e).replace('\n', '\n      '))
        return 2
    label = getattr(src, 'label', '?')
    cli = getattr(src, 'cli', None)
    line(OK, f'数据源               : {label}')
    if cli:
        line(OK, f'CLI 实际路径         : {cli}')
        origin = ('config.json 的 hithink.cli_path' if cfg.get('hithink', {}).get('cli_path')
                  else ('vendor/ 转发脚本' if 'vendor' in cli else '系统 PATH'))
        line(OK, f'来源                 : {origin}')
        try:
            r = subprocess.run([cli, '--version'], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=60)
            ver = (r.stdout or r.stderr or '').strip().splitlines()
            line(OK if r.returncode == 0 else BAD,
                 f'CLI 回话             : rc={r.returncode} {ver[0] if ver else ""}')
        except Exception as e:
            line(BAD, f'CLI 调不通           : {type(e).__name__}: {e}')
            return 2
    ok, note = src.health()
    line(OK if ok else BAD, f'数据源健康           : {note}')

    # ---- 4. 本地基准
    print('-' * 64)
    from shortline.scanner import load_benchmark, load_symbols
    try:
        b = load_benchmark()
        line(OK, f'基准数据             : {len(b.get("items") or {})} 只，截至 {b.get("asof")}')
        print('      asof 应该是上一个交易日。差太远就重建：')
        print('      python tools/build_benchmark.py')
    except SystemExit as e:
        line(BAD, f'基准数据缺失         : {e}')
    try:
        s = load_symbols()
        line(OK, f'证券简称表           : {len(s)} 条')
    except SystemExit as e:
        line(WARN, f'证券简称表缺失       : {e}')

    # ---- 5. 持仓
    hp = os.path.join(ROOT, cfg.get('holdings_file', 'data/holdings.json'))
    if os.path.isfile(hp):
        with open(hp, encoding='utf-8') as f:
            h = json.load(f) or []
        line(OK, f'持仓文件             : {len(h)} 只 {hp}')
    else:
        line(WARN, f'还没有持仓文件       : {hp}（不影响扫描）')

    print('=' * 64)
    print('自检结束。上面没有 [FAIL] 就能扫。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
