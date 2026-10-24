# -*- coding: utf-8 -*-
"""B线路观察池名单（watchlist.json）的增删 —— 程序只当"遥控器"。

为什么不在这里自己写 JSON：
  手册 [[B线路操作手册]] §五 定死了维护口径，而且已经有脚本在做：
    · 加池 → `trading/_add_watchlist.py`（本次新增，推断 sz/sh、secid、备份、复验）
    · 出池 → `trading/_prune_watchlist.py`（既有，物理删行 + 写 removed_log + 备份）
  两条硬规矩都写在脚本里，程序再写一遍就是两套口径：
    ① 出池必须**物理删行**，只改 status=drop 雷达照扫，等于没出池；
    ② 删前必须备份，明细必须进 removed_log。

所以本模块只做三件事：读名单给界面看、拼参数调脚本、成功后让选股缓存失效。
"""
import json
import os
import subprocess

from . import bline


def _wl(cfg):
    return os.path.join(bline._workdir(cfg), '观察池', 'watchlist.json')


def _script(cfg, name):
    p = os.path.join(bline._workdir(cfg), name)
    if not os.path.isfile(p):
        raise RuntimeError('找不到脚本：%s' % p)
    return p


def _code6(s):
    s = str(s or '').strip().lower()
    for x in ('sh', 'sz', '.', ' '):
        s = s.replace(x, '')
    d = ''.join(ch for ch in s if ch.isdigit())
    return d[:6]


def _run(cfg, script, args):
    p = subprocess.run([bline._py(cfg), _script(cfg, script)] + args,
                       cwd=bline._workdir(cfg), capture_output=True,
                       timeout=60, env=bline._env())
    out = (p.stdout or b'').decode('utf-8', 'replace').strip()
    err = (p.stderr or b'').decode('utf-8', 'replace').strip()
    if p.returncode != 0:
        raise ValueError((err or out or '脚本失败（退出码 %d）' % p.returncode).splitlines()[-1][:200])
    return out, err


def payload(cfg):
    p = _wl(cfg)
    if not os.path.exists(p):
        raise RuntimeError('找不到 watchlist.json：%s' % p)
    d = json.load(open(p, encoding='utf-8'))
    items = []
    for s in d.get('sectors') or []:
        items.append({
            'code': (s.get('etf') or '')[-6:],
            'name': s.get('name') or '',
            'etf': s.get('etf') or '',
            'secid': s.get('secid') or '',
            'status': s.get('status') or 'watch',
            'added': s.get('added') or '',
            'note': (s.get('note') or '')[:160],
        })
    removed = []
    for x in (d.get('removed_log') or [])[-6:][::-1]:
        removed.append({'date': x.get('date'), 'name': x.get('name'),
                        'code': (x.get('etf') or '')[-6:],
                        'reason': (x.get('reason') or '')[:120]})
    return {'ok': True, 'items': items, 'n': len(items),
            'target': 12, 'removed': removed,
            'updated': d.get('updated'), 'rule_version': d.get('rule_version')}


def add(cfg, code, name='', note=''):
    code = _code6(code)
    if len(code) != 6:
        raise ValueError('代码要 6 位数字，例如 512480')
    args = ['--code', code]
    if (name or '').strip():
        args += ['--name', name.strip()]
    if (note or '').strip():
        args += ['--note', note.strip()]
    out, _err = _run(cfg, '_add_watchlist.py', args)
    bline.invalidate()
    res = payload(cfg)
    res['msg'] = out.splitlines()[-1] if out else '已加入观察池'
    return res


def remove(cfg, code, reason=''):
    code = _code6(code)
    if len(code) != 6:
        raise ValueError('代码要 6 位数字，例如 512480')
    args = ['--code', code, '--reason', (reason or '').strip() or '人工出池（交易台）']
    out, _err = _run(cfg, '_prune_watchlist.py', args)
    bline.invalidate()
    res = payload(cfg)
    marks = [ln for ln in out.splitlines() if ln.strip().startswith('✓')]
    res['msg'] = marks[-1].strip() if marks else '已出池'
    return res
