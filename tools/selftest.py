# -*- coding: utf-8 -*-
"""端到端自检：状态 -> 触发扫描 -> 轮询 -> 打印漏斗。

用法：先启动服务，再跑 python tools/selftest.py
"""
import json
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:%s' % (sys.argv[1] if len(sys.argv) > 1 else '8848')


def get(path, timeout=60):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def post(path, payload=None, timeout=60):
    data = json.dumps(payload or {}).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


print('1) 状态')
st = get('/api/status')
print('   v%s  数据源=%s(%s)  基准=%s' % (
    st.get('version'), st.get('source_ok'), st.get('source_note'), st.get('bench_asof')))

print('2) 触发扫描')
r = post('/api/scan')
print('   started =', r.get('started'))

print('3) 轮询')
last = None
for i in range(60):
    time.sleep(3)
    s = get('/api/scan/state')
    stage = '%s %s/%s' % (s.get('stage'), s.get('done'), s.get('total'))
    if stage != last:
        print('   ', stage)
        last = stage
    if s.get('error'):
        print('   ERROR:', s['error'][:400])
        break
    if not s.get('running') and s.get('result'):
        f = s['result']['funnel']
        print('4) 漏斗  ' + ' -> '.join('%s:%s' % (k, f.get(k)) for k in
                                       ('total', 'gain', 'open_range', 'vr_ok', 'clean', 'sealed')))
        rows = s['result'].get('rows') or []
        print('   候选 %d 只' % len(rows))
        for row in rows[:10]:
            print('     %s %-6s 量比 %-5s %s' % (row.get('code'), row.get('name'),
                                                row.get('vr'), row.get('stage')))
        break
else:
    print('   超时，扫描没有在 3 分钟内结束')
