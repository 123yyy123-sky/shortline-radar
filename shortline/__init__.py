# -*- coding: utf-8 -*-
"""短线雷达 —— A 股涨停板扫描器。

把「缩量 + 开盘涨 3~9% + 首板」这一条筛选条件做成能每天用的工具。

模块分工：
  config      配置加载（本机路径全部外置）
  rules       交易规则与判定（改规则只改这里）
  datasource  数据源抽象（换行情源只改这里）
  scanner     扫描主流程，返回结构化结果
  holdings    持仓与卖出提醒
  notify      可选的通知推送
"""

__version__ = '1.0.6'
__all__ = ['config', 'rules', 'datasource', 'scanner', 'holdings', 'notify']
