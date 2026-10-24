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

    # 打板条件，来源真源 [[短线交易系统]] §6.5「打板前置条件」（2026-10-24）。
    # ⚠️ 打板在日线上只能打平（全部触板 −0.682%，炸板率 35.0%）。
    #    下面这组条件的作用是把炸板率压到 30.6%，**不是赚钱门槛**：
    #    四项叠加 +0.020%（标准误 0.15pp，与零无差别），扣费 −0.13%。
    #    翻正门槛是炸板率 ≤26.05%，日线变量碰不到。
    'filters': {
        'min_gain_pct': 7.0,          # 候选框：当日涨幅下限（%），真源无此条，只为缩表
        'open_gain_min_pct': 1.0,     # §6.5 条件②：开盘涨幅 1~3%
        'open_gain_max_pct': 3.0,     # 开盘 >7% 是 −1.988%，全场最差
        'vr_max': None,               # ⚠️ 已停用：全天量比是未来函数式判据，新口径未复现
        'max_boards': 1,              # §6.5 条件①：首板（昨日非涨停）
        'min_price': 20.0,            # §6.5 条件④：价格 ≥20 元（低价股炸板代价更大）
        'prefer_20cm': True,          # §6.5 条件⑤：20cm 炸板率 30.4% < 10cm 35.4%（只打标）
        'exclude_st': True,
        'exclude_bj': True,           # 排除北交所（涨跌幅 30%，样本规律不同）
        'require_sealed': False,      # True = 只列已封板的；False = 未封的也列出来盯着
    },

    # 半路板模式（mode=banlu）的条件，来源真源 §6.4（2026-10-24 复核后重写）。
    # 20cm、T=5%、开盘涨幅 >2%、首板、次一交易日收盘卖 =
    # +1.381% / 胜率 50.1% / n=42,257，2020~2026 7/7 年正。
    # ⚠️ 原「量比≥1.5」「情绪热≥50」是未来函数（用收盘才知道的数），已删除；
    #    2024 单年贡献过半（+4.172%），剔除后 +0.587% → 保守 +0.35~0.45% 净。
    'banlu_filters': {
        'trigger_pct': 5.0,          # 触发涨幅 T（%），触发价 = round(昨收×(1+T%), 2)
        'min_open_gap_pct': 2.0,     # §6.4 条件②：开盘涨幅 >2%（唯一有效的可执行过滤）
        'exclude_st': True,
        'exclude_bj': True,
        'cutoff': '14:30',           # 此后停止接新单（尾盘胜率全场最低）
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
    # 三价台账（买入价/卖出价/止损价/成本价）。只存你手改过的字段
    'levels_file': 'data/levels.json',
    # B线路已买入台账（成交之后的持仓：写定止损 / 浮盈阶梯 / 时间止损）
    'bline_positions_file': 'data/bline_positions.json',

    # B线路选股（题材 ETF 右侧突破）。判定在工作区的 scan_watchlist.py，
    # 这里只放路径、缓存和分账金额。workdir 留空就跟着 r3.workdir 走。
    'bline': {
        'workdir': '',
        'script': 'scan_watchlist.py',
        'python': '',
        'cache_seconds': 180,
        # live: auto = 交易时段用盘中半根K（--live），否则用收盘口径
        'live': 'auto',
        'timeout': 240,
        # v1.23 分账：B线题材 6,000，计划位 3,000，试探仓 750
        'budget': 6000,
        'trial': 750,
    },

    # R3 卫星动量轮动（ETF 每 10 个交易日换仓）。规则实现在交易工作区的
    # r3_rotation.py，这里只放路径、止损口径和推送偏好。workdir 留空就是没接。
    'r3': {
        'enabled': True,
        'workdir': '',
        'script': 'r3_rotation.py',
        'updater': '../_update_hist.py',
        'python': '',
        # ma20（默认，收盘破该只 MA20 走人）｜atr｜pct｜none
        'stop_mode': 'ma20',
        'cache_seconds': 120,
        'push': {
            'enabled': True,
            'auto': True,
            'after': '15:00',
            'until': '22:00',
            'check_seconds': 600,
            'chat_id': '',          # 留空用代码里的默认「ETF」群
            'cli': '',
            'run_js': '',
            'state_file': 'data/r3_push_state.json',
        },
    },
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
