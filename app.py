# -*- coding: utf-8 -*-
"""本地 Web 服务。只用标准库，不装任何 Web 框架。

启动：
  python app.py                  # 起服务并自动打开浏览器
  python app.py --port 9000      # 换端口
  python app.py --no-browser     # 不自动开浏览器
  python app.py --at 10:15       # 把「现在」当成 10:15（回放用）

接口：
  GET  /api/status          环境状态（数据源、基准日期、时段）
  POST /api/scan            触发一次扫描（后台跑，立刻返回）
  GET  /api/scan/state      轮询进度和结果
  GET  /api/holdings        持仓 + 卖出计划
  POST /api/holdings        新增/覆盖一只持仓
  DELETE /api/holdings?code= 删除一只持仓
  POST /api/filters         保存筛选条件到 config.json
  GET  /api/config          当前配置（脱敏后）

R3 卫星轮动（ETF 轮动，规则在交易工作区的 r3_rotation.py）：
  GET  /api/r3                调仓判断 + 持仓视图（?force=1 跳过缓存）
  POST /api/r3/holdings       记一笔成交（成本=真实成交价）
  DELETE /api/r3/holdings?code= 删掉一只
  POST /api/r3/clear          清空持仓（全部卖了之后用）
  POST /api/r3/push           立刻推一次飞书（force，忽略当天去重）
  POST /api/r3/update         后台更新 K 线缓存

B线路选股（判定在工作区的 scan_watchlist.py）：
  GET  /api/bline             档位 + focus + 观察池候选（?force=1 跳过缓存，?live=1 盘中口径）

B线路观察池名单（增删都走工作区脚本，出池=物理删行+removed_log+备份）：
  GET  /api/bline/pool        名单 + 最近出池记录
  POST /api/bline/pool        加一只（调 _add_watchlist.py）
  DELETE /api/bline/pool?code=&reason=  出池（调 _prune_watchlist.py）

B线路已买入台账（成交之后这一笔归这里管：写定止损 / 回MA20 / 浮盈阶梯 / 时间止损）：
  GET  /api/bline/positions   全部持仓 + 汇总
  POST /api/bline/positions   记一笔买入；带 field 则是改一个字段（value=null 回自动口径）
  DELETE /api/bline/positions?code=  删掉一只
  POST /api/bline/positions/clear    全部清空（都卖了之后用）

R3 排除名单（R3 没有名单，只有「不要哪几只」；默认空 = 回测口径不变）：
  GET  /api/r3/exclude        当前排除名单
  POST /api/r3/exclude        加一只
  DELETE /api/r3/exclude?code= 去掉一只

三价台账（买入价 / 卖出价 / 止损价 / 成本价，按代码覆盖规则默认值）：
  GET  /api/levels            全部自定义价
  POST /api/levels            改一个价（value=null 清除回默认）
  DELETE /api/levels?code=    整只清空
"""
import argparse
import io
import json
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from shortline import __version__
from shortline.config import load_config, save_config, CONFIG_PATH, ensure_config_file
from shortline.datasource import get_source, SourceError
from shortline.scanner import scan, trade_session, load_benchmark
from shortline import holdings as hold
from shortline import notify
from shortline import r3 as r3mod
from shortline import bline as blinemod
from shortline import bline_pos as bposmod
from shortline import pool as poolmod
from shortline import levels as levelsmod

WEB = os.path.join(ROOT, 'web')
ARGS = None
# 命令行 --source 的临时覆盖。请求里每次都会重读 config.json，
# 所以覆盖值必须单独存一份，不能只改一次 cfg。
SOURCE_OVERRIDE = None
STARTED_AT = time.time()

# 扫描状态。同一时刻只允许跑一个 —— 扫描要拉全市场，并发没有意义还会被限流。
STATE = {
    'running': False, 'stage': '', 'done': 0, 'total': 0, 'msg': '',
    'result': None, 'error': None, 'started_at': None, 'finished_at': None,
    'last_active': time.time(),
}
LOCK = threading.Lock()


def ensure_std_streams():
    """pythonw.exe 启动时没有控制台，sys.stdout / sys.stderr 是 None，
    任何 print 都会抛 AttributeError。这里重定向到 data/app.log，
    双击启动（无窗口）时也能事后查原因。

    返回日志路径；有控制台时返回 None。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    logdir = os.path.join(ROOT, 'data')
    os.makedirs(logdir, exist_ok=True)
    path = os.path.join(logdir, 'app.log')
    try:
        if os.path.getsize(path) > 1024 * 1024:
            os.replace(path, path + '.1')
    except OSError:
        pass
    f = open(path, 'a', encoding='utf-8', buffering=1)
    f.write(f'\n--- 启动 {datetime.now():%Y-%m-%d %H:%M:%S} ---\n')
    sys.stdout = sys.stderr = f
    return path


def fatal(msg):
    """启动失败时告诉用户到底怎么了。

    pythonw 没有控制台，异常只会静默吞掉 —— 用户看到的只是「双击没反应」。
    所以除了写日志，还要弹一个系统对话框。
    """
    try:
        print(msg)
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, msg, '交易台 —— 启动失败', 0x10)
    except Exception:
        pass


def open_browser(url):
    """打开默认浏览器。

    os.startfile 走 ShellExecute，比 webbrowser 在 pythonw 下更稳；
    webbrowser 只作退路。
    """
    try:
        os.startfile(url)          # noqa: 仅 Windows
        return True
    except Exception:
        pass
    try:
        return webbrowser.open(url)
    except Exception:
        return False


# ---------------------------------------------------------------- 业务

def status_payload(cfg):
    now = datetime.now()
    sess, sess_text = trade_session(now.strftime('%H:%M'), now.weekday())
    out = {
        'version': __version__,
        'now': now.strftime('%Y-%m-%d %H:%M:%S'),
        'session': sess, 'session_text': sess_text,
        'data_source': cfg['data_source'],
        'config_exists': os.path.exists(CONFIG_PATH),
        'holdings_count': len(hold.load_holdings(cfg)),
        'filters': cfg['filters'],
        'r3_configured': bool(((cfg.get('r3') or {}).get('workdir') or '').strip()),
        'r3_stop_mode': (cfg.get('r3') or {}).get('stop_mode') or 'ma20',
        'bline_configured': bool((((cfg.get('bline') or {}).get('workdir')
                                  or (cfg.get('r3') or {}).get('workdir') or '')).strip()),
        'levels_count': len(levelsmod.all_items(cfg)),
    }
    try:
        bench = load_benchmark()
        out['bench_asof'] = bench.get('asof')
        out['bench_count'] = len(bench.get('items') or {})
    except SystemExit as e:
        out['bench_asof'] = None
        out['bench_error'] = str(e)
    try:
        src = get_source(cfg)
        ok, note = src.health()
        out['source_label'] = src.label
        out['source_ok'] = ok
        out['source_note'] = note
    except SourceError as e:
        out['source_label'] = cfg['data_source']
        out['source_ok'] = False
        out['source_note'] = str(e)
    return out


def run_scan_async(cfg, at=None, mode='dban'):
    with LOCK:
        if STATE['running']:
            return False
        STATE.update(running=True, stage='start', done=0, total=1, msg='准备中',
                     result=None, error=None, finished_at=None,
                     mode=mode,
                     started_at=datetime.now().strftime('%H:%M:%S'))

    def work():
        try:
            def prog(stage, done, total, msg):
                with LOCK:
                    STATE.update(stage=stage, done=done, total=total, msg=msg)
            r = scan(cfg, at=at, progress=prog, mode=mode)
            with LOCK:
                STATE['result'] = r
                STATE['stage'] = 'done'
            # 有可打的票才推通知
            if any(x['stage'] == 'pass' for x in r['rows']):
                label = '半路板已触发' if mode == 'banlu' else '发现可打标的'
                notify.send(cfg, f'交易台：{label}', notify.scan_digest(r))
        except Exception as e:
            with LOCK:
                STATE['error'] = f'{type(e).__name__}: {e}'
                STATE['stage'] = 'error'
        finally:
            with LOCK:
                STATE['running'] = False
                STATE['finished_at'] = datetime.now().strftime('%H:%M:%S')

    threading.Thread(target=work, daemon=True).start()
    return True


def holdings_payload(cfg):
    """持仓 + 卖出计划。

    跌停价要用「今天的昨收」才算得准，所以先拉一次只含持仓票的快照
    （几只票，1 秒内）。拉不到就退化成 null，界面会提示手填昨收，
    绝不拿旧数据瞎算。
    """
    hs = hold.load_holdings(cfg)
    smap = {}
    if hs:
        try:
            src = get_source(cfg)
            snaps = src.snapshot([h['code'] for h in hs if h.get('code')], share=1.0)
            smap = {s['thscode']: s for s in snaps if s.get('thscode')}
        except Exception:
            smap = {}
    return {'items': hold.sell_plan(cfg, smap),
            'snapshot_ok': bool(smap),
            'count': len(hs)}


def scan_state():
    with LOCK:
        d = dict(STATE)
        d['progress'] = round(d['done'] / d['total'], 3) if d['total'] else 0
        return d


# ---------------------------------------------------------------- HTTP

class Handler(SimpleHTTPRequestHandler):
    server_version = f'TradingDesk/{__version__}'

    def log_message(self, fmt, *a):
        # 只打错误，静态资源的每一条日志会把控制台刷掉
        if not str(a[1] if len(a) > 1 else '').startswith(('2', '3')):
            sys.stderr.write('  %s %s\n' % (a[0] if a else '', a[1] if len(a) > 1 else ''))

    # ---------- 工具
    def _touch(self):
        """任何请求都算「页面还活着」，供空闲自停判断。"""
        with LOCK:
            STATE['last_active'] = time.time()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return {}

    def _cfg(self):
        cfg = load_config()
        if SOURCE_OVERRIDE:
            cfg['data_source'] = SOURCE_OVERRIDE
        return cfg


    # ---------- GET
    def do_GET(self):
        self._touch()
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path.startswith('/api/'):
            if path == '/api/ping':
                # 前端心跳。放最前面，不读配置文件，图的就是快。
                return self._json({'ok': True})
            cfg = self._cfg()
            try:
                if path == '/api/status':
                    return self._json(status_payload(cfg))
                if path == '/api/scan/state':
                    return self._json(scan_state())
                if path == '/api/holdings':
                    return self._json(holdings_payload(cfg))
                if path == '/api/r3':
                    force = (qs.get('force') or ['0'])[0].lower() in ('1', 'true', 'yes')
                    asof = (qs.get('asof') or [''])[0].strip() or None
                    return self._json(r3mod.payload(cfg, force=force, asof=asof))
                if path == '/api/bline':
                    force = (qs.get('force') or ['0'])[0].lower() in ('1', 'true', 'yes')
                    lv = (qs.get('live') or [''])[0].strip().lower()
                    live = None if lv in ('', 'auto') else (lv in ('1', 'true', 'yes'))
                    return self._json(blinemod.payload(cfg, force=force, live=live))
                if path == '/api/bline/pool':
                    return self._json(poolmod.payload(cfg))
                if path == '/api/r3/exclude':
                    return self._json({'ok': True, 'codes': r3mod.load_exclude(cfg)})
                if path == '/api/bline/positions':
                    st = bposmod.load(cfg)
                    return self._json({'ok': True, 'items': st.get('items') or [],
                                       'updated': st.get('updated')})
                if path == '/api/levels':
                    st = levelsmod.load(cfg)
                    return self._json({'items': st.get('items') or {},
                                       'updated': st.get('updated'),
                                       'count': len(st.get('items') or {})})
                if path == '/api/config':
                    c = json.loads(json.dumps(cfg, default=str))
                    c.pop('_root', None)
                    c['notify'] = {'type': cfg['notify'].get('type'),
                                   'webhook_url': '***' if cfg['notify'].get('webhook_url') else ''}
                    return self._json(c)
            except SourceError as e:
                return self._json({'error': str(e)}, 400)
            except Exception as e:
                return self._json({'error': f'{type(e).__name__}: {e}'}, 500)
            return self._json({'error': 'not found'}, 404)

        if path in ('/', '/index.html'):
            return self._serve_file('index.html')
        return self._serve_file(path.lstrip('/'))

    def _serve_file(self, rel):
        safe = os.path.normpath(rel).replace('\\', '/')
        if safe.startswith('..') or os.path.isabs(safe):
            return self._json({'error': 'bad path'}, 400)
        p = os.path.join(WEB, safe)
        if not os.path.isfile(p):
            self.send_error(404, 'Not Found')
            return
        ext = os.path.splitext(p)[1].lower()
        ctype = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                 '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml',
                 '.png': 'image/png', '.ico': 'image/x-icon'}.get(ext, 'application/octet-stream')
        with open(p, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    # ---------- POST
    def do_POST(self):
        self._touch()
        u = urlparse(self.path)
        path = u.path
        cfg = self._cfg()
        b = self._body()
        try:
            if path == '/api/scan':
                at = (b.get('at') or (ARGS.at if ARGS else None) or None)
                mode = b.get('mode') if b.get('mode') in ('dban', 'banlu') else 'dban'
                started = run_scan_async(cfg, at=at, mode=mode)
                return self._json({'started': started, **scan_state()})

            if path == '/api/holdings':
                hold.upsert_holding(cfg, b)
                return self._json({'ok': True, **holdings_payload(cfg)})

            # ---------- 三价台账（买卖止损，按代码覆盖默认值）
            if path == '/api/levels':
                item, cleared = levelsmod.set_field(
                    cfg, b.get('code'), b.get('field'), b.get('value'))
                return self._json({'ok': True, 'code': levelsmod.norm_code(b.get('code')),
                                   'item': item, 'cleared': cleared,
                                   'levels_updated': levelsmod.load(cfg).get('updated')})

            # ---------- B线路观察池名单
            if path == '/api/bline/pool':
                try:
                    res = poolmod.add(cfg, b.get('code'), b.get('name'), b.get('note'))
                except ValueError as e:
                    return self._json({'error': str(e)}, 400)
                return self._json(res)

            # ---------- R3 排除名单
            if path == '/api/r3/exclude':
                codes = r3mod.set_exclude(cfg, b.get('code'))
                return self._json({'ok': True, 'codes': codes,
                                   'r3': r3mod.payload(cfg, force=True)})

            # ---------- B线路已买入台账
            if path == '/api/bline/positions':
                if b.get('field'):
                    rec = bposmod.patch(cfg, b.get('code'), b.get('field'), b.get('value'))
                else:
                    rec = bposmod.upsert(cfg, b)
                st = bposmod.load(cfg)
                return self._json({'ok': True, 'item': rec,
                                   'count': len(st.get('items') or []),
                                   'updated': st.get('updated')})
            if path == '/api/bline/positions/clear':
                bposmod.clear(cfg)
                return self._json({'ok': True, 'items': [], 'count': 0})

            # ---------- R3 卫星轮动
            if path == '/api/r3/holdings':
                r3mod.upsert_holding(cfg, b)
                return self._json({'ok': True, 'r3': r3mod.payload(cfg, force=True)})
            if path == '/api/r3/clear':
                r3mod.clear_holdings(cfg)
                return self._json({'ok': True, 'r3': r3mod.payload(cfg, force=True)})
            if path == '/api/r3/push':
                ok, msg = r3mod.push(cfg, force=True)
                return self._json({'ok': True, 'sent': ok, 'msg': msg,
                                   'r3': r3mod.payload(cfg)})
            if path == '/api/r3/update':
                started = r3mod.update_data(cfg)
                return self._json({'ok': True, 'started': started})

            if path == '/api/filters':
                f = b.get('filters') or {}
                allowed = set(cfg['filters'])
                cfg['filters'].update({k: v for k, v in f.items() if k in allowed})
                p = save_config(cfg)
                return self._json({'ok': True, 'saved': p, 'filters': cfg['filters']})

            if path == '/api/reload':
                # 重建数据文件后热加载，免得重启服务
                from shortline import scanner as sc
                sc.load_benchmark(force=True)
                sc.load_symbols(force=True)
                return self._json({'ok': True})
        except Exception as e:
            return self._json({'error': f'{type(e).__name__}: {e}'}, 400)
        return self._json({'error': 'not found'}, 404)

    def do_DELETE(self):
        self._touch()
        u = urlparse(self.path)
        if u.path == '/api/holdings':
            code = (parse_qs(u.query).get('code') or [''])[0]
            if not code:
                return self._json({'error': '缺少 code'}, 400)
            cfg = self._cfg()
            hold.remove_holding(cfg, code)
            return self._json({'ok': True, **holdings_payload(cfg)})
        if u.path == '/api/levels':
            qs = parse_qs(u.query)
            code = (qs.get('code') or [''])[0]
            if not code:
                return self._json({'error': '缺少 code'}, 400)
            cfg = self._cfg()
            levelsmod.clear_code(cfg, code)
            st = levelsmod.load(cfg)
            return self._json({'ok': True, 'items': st.get('items') or {},
                               'updated': st.get('updated')})
        if u.path == '/api/bline/pool':
            qs = parse_qs(u.query)
            code = (qs.get('code') or [''])[0]
            if not code:
                return self._json({'error': '缺少 code'}, 400)
            reason = (qs.get('reason') or [''])[0]
            cfg = self._cfg()
            try:
                return self._json(poolmod.remove(cfg, code, reason))
            except Exception as e:
                return self._json({'error': f'{type(e).__name__}: {e}'}, 400)
        if u.path == '/api/r3/exclude':
            code = (parse_qs(u.query).get('code') or [''])[0]
            if not code:
                return self._json({'error': '缺少 code'}, 400)
            cfg = self._cfg()
            codes = r3mod.remove_exclude(cfg, code)
            return self._json({'ok': True, 'codes': codes,
                               'r3': r3mod.payload(cfg, force=True)})
        if u.path == '/api/bline/positions':
            code = (parse_qs(u.query).get('code') or [''])[0]
            if not code:
                return self._json({'error': '缺少 code'}, 400)
            cfg = self._cfg()
            try:
                bposmod.remove(cfg, code)
            except ValueError as e:
                return self._json({'error': str(e)}, 400)
            st = bposmod.load(cfg)
            return self._json({'ok': True, 'items': st.get('items') or [],
                               'count': len(st.get('items') or [])})
        if u.path == '/api/r3/holdings':
            code = (parse_qs(u.query).get('code') or [''])[0]
            if not code:
                return self._json({'error': '缺少 code'}, 400)
            cfg = self._cfg()
            try:
                r3mod.remove_holding(cfg, code)
            except ValueError as e:
                return self._json({'error': str(e)}, 400)
            return self._json({'ok': True, 'r3': r3mod.payload(cfg, force=True)})
        return self._json({'error': 'not found'}, 404)


def port_in_use(host, port):
    """Windows 上允许两个进程绑同一端口，只有一个能收连接，
    表现就是「新起的服务打不开」。所以启动前先探一下。"""
    import socket
    # 绑 0.0.0.0 时不能拿它当连接目标，探回环地址
    probe = '127.0.0.1' if host in ('0.0.0.0', '::', '') else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex((probe, port)) == 0
    finally:
        s.close()



def auto_push_loop(get_cfg, interval, first_delay=5):
    """R3 自动推送：程序开着的时候，每 interval 秒试一次。

    有真动作（调仓日买卖 / 持仓破止损）才发，同一动作当天只推一次 ——
    去重靠 data/r3_push_state.json，所以重复调用是安全的。
    程序没开的情况用 `python app.py --push-only`（可挂 Windows 计划任务）。
    """
    def tick():
        try:
            ok, msg = r3mod.auto_push_tick(get_cfg())
            if ok:
                print(f'  R3 已推送：{msg}')
        except Exception as e:
            print(f'  R3 自动推送跳过：{type(e).__name__}: {e}')

    time.sleep(max(1, first_delay))
    tick()
    while True:
        time.sleep(max(60, interval))
        tick()


def idle_watchdog(srv, idle_exit):
    """页面全关之后自动退出，不在后台留进程。

    无窗口启动（pythonw.exe）时，用户没有「关掉那个黑窗口」这个动作，
    必须靠这个机制收尾 —— 否则关掉浏览器后进程会一直占着内存和端口，
    下次双击就变成「已经有一个实例在跑」。
    """
    if idle_exit <= 0:
        return
    while True:
        time.sleep(5)
        with LOCK:
            if STATE['running']:
                # 扫描途中不能算空闲
                STATE['last_active'] = time.time()
                continue
            idle = time.time() - STATE['last_active']
        if idle > idle_exit:
            print(f'\n{int(idle)} 秒没有页面请求，自动退出。'
                  f'（想让它常驻：--idle-exit 0）')
            srv.shutdown()
            return


def _main():
    global ARGS, SOURCE_OVERRIDE
    log_path = ensure_std_streams()
    ap = argparse.ArgumentParser(description='交易台 —— 短线扫描 / B线路选股 / R3 轮动')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default=None)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--at', default=None, help='把现在当成几点，如 10:15（回放/测试用）')
    ap.add_argument('--source', default=None, help='临时覆盖数据源：hithink / demo')
    ap.add_argument('--idle-exit', type=int, default=None, metavar='秒',
                    help='多久没有页面请求就自动退出，0 = 常驻。默认读 config.json')
    ap.add_argument('--lan', action='store_true',
                    help='绑 0.0.0.0，让同一 WiFi 下的其他设备也能打开（默认只给本机）')
    ap.add_argument('--push-only', action='store_true',
                    help='只跑一次 R3 自动推送然后退出（给 Windows 计划任务用，不开界面）')
    ARGS = ap.parse_args()

    created = ensure_config_file()
    cfg = load_config()
    if ARGS.source:
        SOURCE_OVERRIDE = ARGS.source
        cfg['data_source'] = ARGS.source

    if ARGS.push_only:
        # 给计划任务用：不开服务、不开浏览器，判断完就退出
        print(f'R3 卫星轮动 · 自动推送检查（{datetime.now():%Y-%m-%d %H:%M}）')
        try:
            ok, msg = r3mod.auto_push_tick(cfg)
        except Exception as e:
            ok, msg = False, f'{type(e).__name__}: {e}'
        print('  结果：%s' % ('已推送' if ok else '没推'))
        print('  说明：%s' % msg)
        return 0

    host = ARGS.host or cfg['web'].get('host', '127.0.0.1')
    if ARGS.lan:
        host = '0.0.0.0'
    port = ARGS.port or int(cfg['web'].get('port', 8848))
    # 绑 0.0.0.0 不能在浏览器里直接打开，本机要看回环地址
    local_url = f'http://{"127.0.0.1" if host in ("0.0.0.0", "::") else host}:{port}/'

    if ARGS.idle_exit is not None:
        idle_exit = ARGS.idle_exit
    else:
        idle_exit = int(cfg['web'].get('idle_exit_seconds', 120) or 0)

    print(f'交易台 v{__version__}')
    print(f'  数据源：{cfg["data_source"]}   配置：{"已建 " + os.path.basename(created) if created else CONFIG_PATH}')
    if ARGS.at:
        print(f'  时点已锁定在 {ARGS.at}（回放模式）')
    if log_path:
        print(f'  无控制台模式，日志：{log_path}')
    if idle_exit > 0:
        print(f'  {idle_exit} 秒无页面请求会自动退出（常驻用 --idle-exit 0）')

    if port_in_use(host, port):
        # 双击快捷方式时，旧实例往往还开着。这里不要报错退出，
        # 直接把浏览器指过去 —— 用户要的是「打开软件」，不是「看报错」。
        print(f'\n端口 {port} 上已经有一个实例在跑，直接用它。')
        print(f'  地址：{local_url}')
        print('  （想关掉它：双击「停止交易台」，或者关掉浏览器等它自己退）')
        if cfg['web'].get('open_browser', True) and not ARGS.no_browser:
            open_browser(local_url)
        return

    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        print(f'\n端口 {port} 起不来：{e}')
        print('换一个端口：python app.py --port 9000')
        sys.exit(1)

    print(f'  地址：{local_url}')
    print('  按 Ctrl+C 停止（或者直接双击「停止交易台」）\n')

    if cfg['web'].get('open_browser', True) and not ARGS.no_browser:
        threading.Timer(0.8, lambda: open_browser(local_url)).start()

    # R3 自动推送：程序开着就一直挂着，有真动作才发，当天只发一次
    r3cfg = cfg.get('r3') or {}
    if (r3cfg.get('workdir') or '').strip():
        interval = int((r3cfg.get('push') or {}).get('check_seconds') or 600)
        threading.Thread(target=auto_push_loop, args=(load_config, interval),
                         daemon=True).start()
        print(f'  R3 自动推送：每 {max(60, interval) // 60} 分钟查一次'
              f'（15:00~22:00 有调仓/破止损才推）')

    if idle_exit > 0:
        threading.Thread(target=idle_watchdog, args=(srv, idle_exit),
                         daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')
        srv.shutdown()
    finally:
        print('服务已关闭。')


def main():
    """双击启动时的统一入口：任何异常都要让用户看见。"""
    import traceback
    try:
        _main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        pass
    except Exception:
        detail = traceback.format_exc()
        tail = '\n'.join(detail.strip().splitlines()[-8:])
        fatal(f'启动失败，原因是：\n\n{tail}\n\n'
              f'完整日志在 data\\app.log。\n'
              f'也可以在命令行里跑 run.bat，能看到更详细的输出。')
        sys.exit(1)


if __name__ == '__main__':
    main()
