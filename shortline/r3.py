# -*- coding: utf-8 -*-
"""R3 卫星轮动 —— 调仓判断、持仓视图、飞书推送。

规则的实现只有一份：交易工作区的 `r3_rotation.py`（规则真源见知识库
[[ETF交易系统]] §三之二）。本模块只做四件事，不重写任何规则：

  1. 调它拿 JSON（结果在内存里缓存 cache_seconds 秒）
  2. 记实际成交 —— 写回 r3_state.json 的 holdings，成本用**真实成交价**
  3. 有真动作就推飞书（同一个动作当天只推一次，不刷屏）
  4. 更新 K 线（后台跑 _update_hist.py，跑完自动失效缓存）

为什么持仓记在 r3_state.json 而不是程序自己的 data/ 目录：
脚本下次判断要用「上次买的是什么」来算换仓，两边分家必然对不上。
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

CACHE = {'at': 0.0, 'data': None, 'err': None}
LOCK = threading.Lock()
UPDATE = {'running': False, 'msg': '', 'ok': None,
          'started': None, 'finished': None, 'tail': ''}

# 飞书推送要调的本机程序一律不写死（公开仓库不能带个人绝对路径与群 ID）。
# 优先级：config.json 的 r3.push.{node, run_js, cli, chat_id} → 环境变量 → PATH 里的命令。
NODE_CANDIDATES = [p for p in (
    os.environ.get('LARK_NODE', ''),
    os.environ.get('NODE_EXE', ''),
) if p]
RUN_JS = os.environ.get('LARK_RUN_JS', '')
LARK_CMD = os.environ.get('LARK_CMD', 'lark-cli')
DEFAULT_CHAT = os.environ.get('LARK_CHAT_ID', '')


# ---------------------------------------------------------------- 基础

def rcfg(cfg):
    return (cfg or {}).get('r3') or {}


def _env():
    """强制子进程用 UTF-8 输出，否则 Windows 下中文变乱码、JSON 也解析不了。"""
    e = dict(os.environ)
    e['PYTHONIOENCODING'] = 'utf-8'
    e['PYTHONUTF8'] = '1'
    return e


def _paths(cfg):
    r = rcfg(cfg)
    wd = (r.get('workdir') or '').replace('\\', '/').rstrip('/')
    if not wd:
        raise RuntimeError('config.json 里没填 r3.workdir')
    script = os.path.join(wd, r.get('script') or 'r3_rotation.py')
    if not os.path.isfile(script):
        raise RuntimeError('找不到 R3 脚本：%s' % script)
    return wd, script


def _py(cfg):
    p = (rcfg(cfg).get('python') or '').strip()
    if p and os.path.isfile(p):
        return p
    return sys.executable or 'python'


def _run(cfg, args, timeout=180):
    """跑 r3_rotation.py，返回 stdout。"""
    wd, script = _paths(cfg)
    p = subprocess.run([_py(cfg), script] + list(args), cwd=wd,
                       capture_output=True, timeout=timeout, env=_env())
    out = (p.stdout or b'').decode('utf-8', 'replace')
    err = (p.stderr or b'').decode('utf-8', 'replace')
    if p.returncode != 0:
        raise RuntimeError((err or out or '脚本退出码 %d' % p.returncode).strip()[:400])
    return out


def invalidate():
    with LOCK:
        CACHE.update(at=0.0, data=None, err=None)


# ---------------------------------------------------------------- 取数

# ---------------------------------------------------------------- 排除名单
# R3 没有「名单」这个概念：池 = 全 ETF 缓存里剔除宽基/QDII/债券后的结果，
# 所以能编辑的只有「排除哪些不想要」。默认空 = 与回测口径完全一致。

def _ex_path(cfg):
    p = ((cfg or {}).get('r3') or {}).get('exclude_file') or ''
    if not p:
        p = os.path.join(DATA, 'r3_exclude.json')
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)
    return p


def load_exclude(cfg):
    try:
        d = json.load(open(_ex_path(cfg), encoding='utf-8'))
        return [norm_code(c) for c in (d.get('codes') or []) if norm_code(c)]
    except Exception:
        return []


def _save_exclude(cfg, codes):
    p = _ex_path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    d = {'codes': codes, 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    invalidate()
    return codes


def set_exclude(cfg, code):
    code = norm_code(code)
    if len(code) != 6:
        raise ValueError('代码要 6 位数字，例如 512660')
    codes = load_exclude(cfg)
    if code not in codes:
        codes.append(code)
    return _save_exclude(cfg, codes)


def remove_exclude(cfg, code):
    code = norm_code(code)
    codes = [c for c in load_exclude(cfg) if c != code]
    return _save_exclude(cfg, codes)


def payload(cfg, force=False, asof=None):
    r = rcfg(cfg)
    ttl = int(r.get('cache_seconds') or 120)
    if asof:
        force = True          # 回放不走缓存，否则拿到的是最新那天的结果
    with LOCK:
        cached, at = CACHE['data'], CACHE['at']
    if cached and not force and (time.time() - at) < ttl:
        return _decorate(cfg, cached, cached=True)

    args = ['--json']
    mode = (r.get('stop_mode') or '').strip()
    if mode:
        args += ['--stop-mode', mode]
    ex = load_exclude(cfg)
    if ex:
        args += ['--exclude', ','.join(ex)]
    if asof:
        args += ['--asof', str(asof)]
    try:
        out = _run(cfg, args)
        i = out.find('{')
        if i < 0:
            raise RuntimeError('脚本没输出 JSON：%s' % out.strip()[:200])
        data = json.loads(out[i:])
        data['ok'] = True
        data['replay'] = bool(asof)
        data['fetched_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        with LOCK:
            CACHE.update(at=time.time(), data=None, err=str(e))
        return {'ok': False, 'error': str(e), 'update': dict(UPDATE),
                'push': push_state(cfg), 'stop_mode': mode or 'ma20'}

    data['cached'] = False
    if not asof:
        with LOCK:
            # 缓存**脚本原始结果**，不缓存叠加后的数 —— 这样改价不用重跑脚本
            CACHE.update(at=time.time(), data=data, err=None)
    return _decorate(cfg, data, cached=False)


def _decorate(cfg, raw, cached=False):
    """展示层：深拷贝原始结果 → 叠自定义三价 → 叠实时价。判定字段一概不动。"""
    data = copy.deepcopy(raw)
    data['cached'] = cached
    data['update'] = dict(UPDATE)
    data['push'] = push_state(cfg)
    data['exclude'] = load_exclude(cfg)
    try:
        from . import levels, quotes
        levels.apply_r3(cfg, data)
        st = levels.load(cfg)
        data['levels_updated'] = st.get('updated')
        data['levels_count'] = len(st.get('items') or {})
        quotes.overlay(data.get('positions') or [])
        quotes.overlay(data.get('buy') or [])
    except Exception:
        pass
    return data


# ---------------------------------------------------------------- 持仓

def state_file(cfg):
    wd, _ = _paths(cfg)
    return os.path.join(wd, 'r3_state.json')


def load_state(cfg):
    p = state_file(cfg)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return {}


def save_state(cfg, st):
    p = state_file(cfg)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def norm_code(s):
    """513120 / 513120.SH / sh513120 → 513120"""
    s = str(s or '').strip().lower()
    for x in ('sh', 'sz', '.', ' '):
        s = s.replace(x, '')
    digits = ''.join(ch for ch in s if ch.isdigit())
    return digits[:6]


def guess_sym(code):
    """ETF 代码 → 腾讯符号：5 开头在上交所，其余（159/16/18）在深交所。"""
    return ('sh' if code[:1] == '5' else 'sz') + code


def upsert_holding(cfg, item):
    """记一笔成交（同一代码覆盖）。成本填真实成交价，不要填收盘价。"""
    code = norm_code(item.get('code'))
    if len(code) != 6:
        raise ValueError('代码要 6 位数字，例如 513120')
    shares = int(float(item.get('shares') or 0))
    cost = float(item.get('cost') or 0)
    if shares <= 0:
        raise ValueError('份数要大于 0')
    if cost <= 0:
        raise ValueError('成交价要大于 0')

    st = load_state(cfg)
    hs = [h for h in (st.get('holdings') or []) if norm_code(h.get('code')) != code]
    hs.append(dict(sym=(item.get('sym') or guess_sym(code)), code=code,
                   name=(item.get('name') or code),
                   shares=shares, cost=round(cost, 3),
                   buy_date=(item.get('buy_date')
                             or datetime.now().strftime('%Y-%m-%d'))))
    st['holdings'] = hs
    st['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    st['rule_version'] = st.get('rule_version') or 'manual'
    save_state(cfg, st)
    invalidate()
    return st


def remove_holding(cfg, code):
    code = norm_code(code)
    st = load_state(cfg)
    before = len(st.get('holdings') or [])
    st['holdings'] = [h for h in (st.get('holdings') or [])
                      if norm_code(h.get('code')) != code]
    if len(st['holdings']) == before:
        raise ValueError('持仓里没有 %s' % code)
    st['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_state(cfg, st)
    invalidate()
    return st


def clear_holdings(cfg):
    st = load_state(cfg)
    st['holdings'] = []
    st['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_state(cfg, st)
    invalidate()
    return st


# ---------------------------------------------------------------- 推送

def _push_cfg(cfg):
    return rcfg(cfg).get('push') or {}


def push_state(cfg):
    f = _push_cfg(cfg).get('state_file') or os.path.join(DATA, 'r3_push_state.json')
    try:
        st = json.load(open(f, encoding='utf-8'))
    except Exception:
        st = {}
    p = _push_cfg(cfg)
    st['enabled'] = bool(p.get('enabled', True))
    st['auto'] = bool(p.get('auto', True))
    st['after'] = p.get('after') or '15:00'
    st['until'] = p.get('until') or '22:00'
    return st


def _write_push_state(cfg, obj):
    f = _push_cfg(cfg).get('state_file') or os.path.join(DATA, 'r3_push_state.json')
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, 'w', encoding='utf-8', newline='\n') as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=2)


def actionable(res):
    """只有真动作才值得推一条：(kind, 中文名)。没有就 (None, None)。

    非调仓日且没破止损 → 不推。空仓开关每天触发也只推一次（按 key 去重）。
    """
    pos = res.get('positions') or []
    if res.get('is_rebalance_day') and (res.get('buy') or res.get('sell_all')
                                        or res.get('sell_list')):
        return 'rebalance', '调仓'
    if any(x.get('broken') for x in pos):
        return 'stop', '破止损'
    return None, None


def action_key(res):
    kind, _ = actionable(res)
    if not kind:
        return ''
    pos = res.get('positions') or []
    sig = ','.join(sorted('%s:%s' % (x.get('code'), 'B' if x.get('broken') else 'H')
                          for x in pos))
    codes = ','.join(sorted(x['code'] for x in (res.get('buy') or [])))
    return '%s|%s|%s|%s' % (res.get('as_of'), kind, sig, codes)


def digest(cfg, res):
    """推送正文：直接问脚本要，别在这儿二次拼装，免得两处口径打架。"""
    try:
        return _run(cfg, ['--push-text'] + (['--stop-mode', rcfg(cfg)['stop_mode']]
                                           if (rcfg(cfg).get('stop_mode') or '').strip() else []))
    except Exception:
        return 'R3 卫星轮动 · %s\n%s' % (res.get('as_of'), res.get('action') or '')


def _node(cfg):
    p = _push_cfg(cfg) or {}
    for x in [p.get('node') or ''] + NODE_CANDIDATES:
        if x and os.path.isfile(x):
            return x
    return shutil.which('node') or ''


def send(cfg, text, timeout=60):
    """发到飞书。返回 (是否成功, 说明)。"""
    p = _push_cfg(cfg)
    chat = p.get('chat_id') or DEFAULT_CHAT
    if not chat:
        return False, '没配 chat_id（config.json 的 r3.push.chat_id）'
    tail = ['im', '+messages-send', '--as', 'bot', '--chat-id', chat, '--markdown', text]

    node, js = _node(cfg), p.get('run_js') or RUN_JS
    cmds = []
    if node and os.path.isfile(js):
        cmds.append([node, js] + tail)
    cli = p.get('cli') or LARK_CMD
    if os.path.isfile(cli):
        cmds.append(['cmd', '/c', cli] + tail)
    if not cmds:
        return False, '找不到 lark-cli（检查 config.json 的 r3.push）'

    last = ''
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                               cwd=ROOT, env=_env())
            out = ((r.stdout or b'') + (r.stderr or b'')).decode('utf-8', 'replace').strip()
            if r.returncode == 0:
                return True, (out[-200:] or 'HTTP ok')
            last = out[-300:] or ('退出码 %d' % r.returncode)
        except Exception as e:
            last = str(e)
    return False, last


def push(cfg, force=False, res=None):
    """有真动作才推，同一动作当天只推一次。返回 (是否发送, 说明)。"""
    if not _push_cfg(cfg).get('enabled', True):
        return False, '推送未开启（r3.push.enabled=false）'
    if res is None:
        res = payload(cfg, force=True)
    if not res.get('ok'):
        return False, 'R3 取数失败，不推空消息'

    kind, label = actionable(res)
    if not kind:
        return False, '无调仓/破止损动作，不推'
    key = action_key(res)
    st = push_state(cfg)
    if not force and st.get('key') == key:
        return False, '这条动作今天已经推过了（%s）' % (st.get('at') or '')

    text = digest(cfg, res)
    ok, msg = send(cfg, text)
    if ok:
        _write_push_state(cfg, {'key': key, 'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'kind': kind, 'label': label,
                                'as_of': res.get('as_of')})
    return ok, msg


def _hm(s):
    """'15:00' → 1500，用于比大小。"""
    try:
        a, b = str(s or '0:0').split(':')[:2]
        return int(a) * 100 + int(b)
    except Exception:
        return 0


def auto_push_tick(cfg, now=None):
    """自动推送的一次尝试：只在交易日 15:00~22:00 之间，有动作且当天没推过才发。

    程序开着就由后台线程每 check_seconds 叫一次；程序没开就靠
    `python app.py --push-only`（可以挂 Windows 计划任务）。
    """
    p = _push_cfg(cfg)
    if not p.get('enabled', True) or not p.get('auto', True):
        return False, '自动推送未开启'
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False, '周末不推'
    t = now.hour * 100 + now.minute
    if t < _hm(p.get('after') or '15:00'):
        return False, '还没到收盘结算时间'
    if t > _hm(p.get('until') or '22:00'):
        return False, '过了推送时间窗'
    return push(cfg, force=False)


# ---------------------------------------------------------------- 更新K线

def update_data(cfg):
    """后台更新 K 线（_update_hist.py），跑完自动失效缓存。"""
    with LOCK:
        if UPDATE['running']:
            return False
    r = rcfg(cfg)
    wd, _ = _paths(cfg)
    up = os.path.normpath(os.path.join(wd, r.get('updater') or '../_update_hist.py'))

    def work():
        with LOCK:
            UPDATE.update(running=True, msg='更新K线中', ok=None, tail='',
                          started=datetime.now().strftime('%H:%M:%S'), finished=None)
        ok, tail = False, ''
        try:
            if not os.path.isfile(up):
                raise RuntimeError('找不到更新脚本：%s' % up)
            p = subprocess.run([_py(cfg), up], cwd=wd, capture_output=True,
                               timeout=1800, env=_env())
            out = ((p.stdout or b'') + (p.stderr or b'')).decode('utf-8', 'replace')
            tail = '\n'.join([x for x in out.strip().splitlines() if x.strip()][-3:])
            ok = p.returncode == 0
        except Exception as e:
            tail = str(e)
        finally:
            with LOCK:
                UPDATE.update(running=False, ok=ok, tail=tail,
                              msg='更新完成' if ok else '更新失败',
                              finished=datetime.now().strftime('%H:%M:%S'))
            invalidate()

    threading.Thread(target=work, daemon=True).start()
    return True
