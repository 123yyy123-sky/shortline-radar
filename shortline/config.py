# -*- coding: utf-8 -*-
"""配置加载。

优先级：内置默认值  <  config.json  <  环境变量

所有本机相关的东西（行情 CLI 路径、端口、webhook）都不写在代码里，
只写在 config.json 里。config.json 不进版本库，模板见 config.example.json。
"""
import copy
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, 'config.json')
EXAMPLE_PATH = os.path.join(ROOT, 'config.example.json')

DEFAULTS = {
    # 数据源：hithink = 用本机 hithink-finance CLI 拉实时行情；demo = 用内置样例数据
    'data_source': 'hithink',

    'hithink': {
        # 留空则依次尝试：环境变量 HITHINK_CLI、vendor/hithink-finance.cmd、PATH 里的 hithink-finance
        'cli_path': '',
        # 一次快照多少只。实测 500 是上限，超过返回 400
        'batch_size': 500,
        'timeout': 120,
    },

    'web': {
        'host': '127.0.0.1',
        'port': 8848,
        'open_browser': True,
        # 关掉页面之后多久自动退出（秒）。0 = 常驻不清
        'idle_exit_seconds': 120,
    },

    # 筛选条件。默认值就是回测里跑出正期望的那一组，不懂不要动
    'filters': {
        'min_gain_pct': 7.0,          # 当日涨幅下限（%），先框出「够强」的
        'open_gain_min_pct': 3.0,     # 开盘涨幅下限（%）
        'open_gain_max_pct': 9.0,     # 开盘涨幅上限（%），超过说明跳空太贵
        'vr_max': 1.0,                # 预估全天量比上限
        'max_boards': 1,              # 最大允许连板数，1 = 只要首板
        'exclude_st': True,
        'exclude_bj': True,           # 排除北交所（涨跌幅 30%，样本规律不同）
        'require_sealed': False,      # True = 只列已封板的；False = 未封的也列出来盯着
    },

    # 日内累计成交量占全天的比例曲线，用于把「现在的推进率」折成「预估全天量比」。
    # 换成你自己券商的实测值会准很多。格式 [[时刻, 占比], ...]，按时刻升序。
    'intraday_share': [
        ['09:31', 0.030], ['09:35', 0.080], ['09:45', 0.180], ['10:00', 0.290],
        ['10:15', 0.360], ['10:30', 0.420], ['11:00', 0.520], ['11:30', 0.580],
        ['13:30', 0.660], ['14:00', 0.740], ['14:15', 0.780], ['14:30', 0.820],
        ['15:00', 1.000],
    ],

    # 出信号时的通知。type=none 就只在界面里显示
    'notify': {
        'type': 'none',        # none | webhook
        'webhook_url': '',     # 任意支持 {"msg_type":"text","content":{"text":"..."}} 的机器人
    },

    'holdings_file': 'data/holdings.json',
}

# 环境变量名 -> 配置路径
ENV_MAP = {
    'SL_DATA_SOURCE': 'data_source',
    'HITHINK_CLI': 'hithink.cli_path',
    'SL_PORT': 'web.port',
    'SL_HOST': 'web.host',
}


def _set_path(d, path, value):
    keys = path.split('.')
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    # 类型按原值的类型走，避免 "8848" 变成字符串
    old = cur.get(keys[-1])
    if isinstance(old, int) and not isinstance(old, bool):
        try:
            value = int(value)
        except ValueError:
            return
    cur[keys[-1]] = value


def load_config(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    p = path or CONFIG_PATH
    if os.path.exists(p):
        try:
            user = json.load(open(p, encoding='utf-8'))
        except Exception as e:
            raise SystemExit(f'config.json 格式错误：{e}')
        _merge(cfg, user)
    for env, key in ENV_MAP.items():
        v = os.environ.get(env)
        if v:
            _set_path(cfg, key, v)
    cfg['_root'] = ROOT
    return cfg


def _merge(base, over):
    """递归合并，只覆盖 base 里已有的键，防止手滑写错键名后静默生效。"""
    for k, v in (over or {}).items():
        if k.startswith('_'):
            continue
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def save_config(cfg, path=None):
    """把界面上改的筛选条件写回 config.json（只写可改的几项）。"""
    p = path or CONFIG_PATH
    cur = {}
    if os.path.exists(p):
        try:
            cur = json.load(open(p, encoding='utf-8'))
        except Exception:
            cur = {}
    if cfg.get('filters'):
        cur['filters'] = cfg['filters']
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    return p


def ensure_config_file():
    """首次运行时，若没有 config.json 就从模板复制一份。"""
    if not os.path.exists(CONFIG_PATH) and os.path.exists(EXAMPLE_PATH):
        with open(EXAMPLE_PATH, encoding='utf-8') as f:
            txt = f.read()
        with open(CONFIG_PATH, 'w', encoding='utf-8', newline='\n') as f:
            f.write(txt)
        return CONFIG_PATH
    return None
