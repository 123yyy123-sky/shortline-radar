# -*- coding: utf-8 -*-
"""交易台 —— 一个窗口管三条线：短线打板 / B线路选股 / R3 卫星轮动。

短线（A股涨停板）：把真源 §6.5 的打板门槛（首板 / 开盘 1~3% / 价格≥20 / 20cm 优先）
                  做成每天能用的工具。⚠️ 打板只到打平（−0.682% → +0.020%），不构成开仓理由。
B线路（题材 ETF）：把工作区的 scan_watchlist.py 的判定搬进界面，可改三价，
                  成交之后记进「已买入台账」，由它算写定止损/浮盈阶梯/时间止损。
R3（ETF 轮动）：把 r3_rotation.py 的结果搬进界面，可改成本/止损/卖出价。

模块分工：
  config      配置加载（本机路径全部外置）
  rules       短线交易规则与判定（改规则只改这里）
  datasource  数据源抽象（换行情源只改这里）
  scanner     短线扫描主流程，返回结构化结果
  holdings    短线持仓与卖出提醒
  notify      可选的通知推送
  r3          R3 卫星轮动（调仓判断/持仓/飞书推送，规则在工作区 r3_rotation.py）
  bline       B线路选股（规则在工作区 scan_watchlist.py）
  pool        B线路观察池名单（增删都走工作区脚本，出池=物理删行）
  bline_pos   B线路已买入台账（成交之后的持仓，四道闸门逐条算给你看）
  levels      三价台账（买入价/卖出价/止损价/成本价，按代码覆盖默认值）
  quotes      实时价与日线（腾讯，只给界面看，不参与判定）
"""

__version__ = '1.1.3'
__all__ = ['config', 'rules', 'datasource', 'scanner', 'holdings', 'notify',
           'r3', 'bline', 'bline_pos', 'pool', 'levels', 'quotes']
