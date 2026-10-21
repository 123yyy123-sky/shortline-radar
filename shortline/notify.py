# -*- coding: utf-8 -*-
"""出信号时的通知。默认关闭，只在界面里显示。

不绑定任何特定平台：给一个 webhook 地址就能用。
按 URL 自动判断是飞书、钉钉还是企业微信机器人，发对应的格式。
"""
import json
import urllib.request

TIMEOUT = 8


def _payload_for(url):
    u = url.lower()
    if 'feishu' in u or 'larksuite' in u:
        return lambda title, text: {
            'msg_type': 'text',
            'content': {'text': f'{title}\n{text}'},
        }
    if 'dingtalk' in u:
        return lambda title, text: {
            'msgtype': 'text',
            'text': {'content': f'{title}\n\n{text}'},
        }
    if 'qyapi.weixin' in u:
        return lambda title, text: {
            'msgtype': 'text',
            'text': {'content': f'{title}\n{text}'},
        }
    # 自定义服务：给结构化的，自己解析
    return lambda title, text: {'title': title, 'text': text}


def send(cfg, title, text):
    """返回 (是否成功, 说明)。失败不抛异常 —— 通知不该影响主流程。"""
    n = cfg.get('notify') or {}
    if (n.get('type') or 'none') == 'none':
        return False, '通知未开启'
    url = (n.get('webhook_url') or '').strip()
    if not url:
        return False, 'config.json 里没填 notify.webhook_url'

    body = json.dumps(_payload_for(url)(title, text), ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=body,
                                 headers={'Content-Type': 'application/json; charset=utf-8'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return True, f'HTTP {r.status}'
    except Exception as e:
        return False, f'发送失败：{e}'


def scan_digest(result):
    """把扫描结果压成一条短消息。"""
    lines = [f"{result['generated_at']} · 时点 {result['at']} · {result['session_text']}"]
    rows = [r for r in result['rows'] if r['stage'] in ('pass', 'watch')]
    if not rows:
        f = result['funnel']
        lines.append(f"无符合条件。全市场 {f['total']} → 涨幅达标 {f['gain']}"
                     f" → 开盘达标 {f['open_range']} → 量比达标 {f['vr_ok']} → 全条件 {f['clean']}")
        return '\n'.join(lines)
    for r in rows:
        tag = '可打' if r['stage'] == 'pass' else '盯住'
        lines.append(f"[{tag}] {r['code']} {r['name']} 涨{r['gain']:+.2f}% "
                     f"开{r['open_gain']:+.2f}% 量比{r['vr']:.2f}")
        if r['stage'] == 'pass':
            lines.append('  → 挂涨停价排队，买不进就空仓；T+1 竞价挂跌停价清光')
        else:
            lines.append('  → 封不上就不买，别抢跑')
    lines.append('仅供参考，不构成投资建议。')
    return '\n'.join(lines)
