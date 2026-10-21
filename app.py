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
"""
import argparse
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

WEB = os.path.join(ROOT, 'web')
ARGS = None
# 命令行 --source 的临时覆盖。请求里每次都会重读 config.json，
# 所以覆盖值必须单独存一份，不能只改一次 cfg。
SOURCE_OVERRIDE = None

# 扫描状态。同一时刻只允许跑一个 —— 扫描要拉全市场，并发没有意义还会被限流。
STATE = {
    'running': False, 'stage': '', 'done': 0, 'total': 0, 'msg': '',
    'result': None, 'error': None, 'started_at': None, 'finished_at': None,
}
LOCK = threading.Lock()


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


def run_scan_async(cfg, at=None):
    with LOCK:
        if STATE['running']:
            return False
        STATE.update(running=True, stage='start', done=0, total=1, msg='准备中',
                     result=None, error=None, finished_at=None,
                     started_at=datetime.now().strftime('%H:%M:%S'))

    def work():
        try:
            def prog(stage, done, total, msg):
                with LOCK:
                    STATE.update(stage=stage, done=done, total=total, msg=msg)
            r = scan(cfg, at=at, progress=prog)
            with LOCK:
                STATE['result'] = r
                STATE['stage'] = 'done'
            # 有可打的票才推通知
            if any(x['stage'] == 'pass' for x in r['rows']):
                notify.send(cfg, '短线雷达：发现可打标的', notify.scan_digest(r))
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
    server_version = f'ShortlineRadar/{__version__}'

    def log_message(self, fmt, *a):
        # 只打错误，静态资源的每一条日志会把控制台刷掉
        if not str(a[1] if len(a) > 1 else '').startswith(('2', '3')):
            sys.stderr.write('  %s %s\n' % (a[0] if a else '', a[1] if len(a) > 1 else ''))

    # ---------- 工具
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
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path.startswith('/api/'):
            cfg = self._cfg()
            try:
                if path == '/api/status':
                    return self._json(status_payload(cfg))
                if path == '/api/scan/state':
                    return self._json(scan_state())
                if path == '/api/holdings':
                    return self._json(holdings_payload(cfg))
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
        u = urlparse(self.path)
        path = u.path
        cfg = self._cfg()
        b = self._body()
        try:
            if path == '/api/scan':
                at = (b.get('at') or (ARGS.at if ARGS else None) or None)
                started = run_scan_async(cfg, at=at)
                return self._json({'started': started, **scan_state()})

            if path == '/api/holdings':
                hold.upsert_holding(cfg, b)
                return self._json({'ok': True, **holdings_payload(cfg)})

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
        u = urlparse(self.path)
        if u.path == '/api/holdings':
            code = (parse_qs(u.query).get('code') or [''])[0]
            if not code:
                return self._json({'error': '缺少 code'}, 400)
            cfg = self._cfg()
            hold.remove_holding(cfg, code)
            return self._json({'ok': True, **holdings_payload(cfg)})
        return self._json({'error': 'not found'}, 404)


def port_in_use(host, port):
    """Windows 上允许两个进程绑同一端口，只有一个能收连接，
    表现就是「新起的服务打不开」。所以启动前先探一下。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def main():
    global ARGS, SOURCE_OVERRIDE
    ap = argparse.ArgumentParser(description='短线雷达 —— A 股涨停板扫描器')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default=None)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--at', default=None, help='把现在当成几点，如 10:15（回放/测试用）')
    ap.add_argument('--source', default=None, help='临时覆盖数据源：hithink / demo')
    ARGS = ap.parse_args()

    created = ensure_config_file()
    cfg = load_config()
    if ARGS.source:
        SOURCE_OVERRIDE = ARGS.source
        cfg['data_source'] = ARGS.source

    host = ARGS.host or cfg['web'].get('host', '127.0.0.1')
    port = ARGS.port or int(cfg['web'].get('port', 8848))
    url = f'http://{host}:{port}/'

    print(f'短线雷达 v{__version__}')
    print(f'  数据源：{cfg["data_source"]}   配置：{"已建 " + os.path.basename(created) if created else CONFIG_PATH}')
    if ARGS.at:
        print(f'  时点已锁定在 {ARGS.at}（回放模式）')

    if port_in_use(host, port):
        print(f'\n端口 {port} 已经有服务在跑。')
        print(f'直接打开：{url}')
        print('如果是旧实例想换掉它，先关掉那个窗口，或者用 --port 换个端口。')
        sys.exit(2)

    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        print(f'\n端口 {port} 起不来：{e}')
        print('换一个端口：python app.py --port 9000')
        sys.exit(1)

    print(f'  地址：{url}')
    print('  按 Ctrl+C 停止\n')

    if cfg['web'].get('open_browser', True) and not ARGS.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')
        srv.shutdown()


if __name__ == '__main__':
    main()
