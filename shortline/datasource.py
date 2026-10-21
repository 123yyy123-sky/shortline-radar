# -*- coding: utf-8 -*-
"""数据源。

扫描只需要两样东西：全市场实时快照、涨停池。
把它们抽成接口，是为了让软件不绑死在某一个行情源上。

内置两种实现：
  HithinkSource  调本机 hithink-finance CLI（需要自己安装并配置凭据）
  DemoSource     读 data/demo_snapshot.json（构造数据，用来演示界面）

要接自己的数据源，继承 BaseSource 实现 snapshot() 和 limit_pool() 即可，
然后在 datasource.get_source() 里加一行分支。
"""
import json
import os
import shutil
import subprocess
import tempfile

from .config import ROOT


class SourceError(RuntimeError):
    """数据源不可用。消息直接把怎么解决写在里面，界面原文显示。"""


class BaseSource:
    name = 'base'
    label = '未知数据源'

    def snapshot(self, codes, progress=None, share=1.0):
        """返回 list[dict]，每只票至少要能给：thscode/last_price/open_price/
        high_price/volume/prev_price。

        share 是「当前时刻累计成交量占全天的比例」，只有 DemoSource 用得上：
        它要按这个比例把「全天量」折成「此刻的累计量」。
        真实数据源直接忽略即可 —— 快照里的 volume 本来就是累计量。
        """
        raise NotImplementedError

    def limit_pool(self):
        """返回 list[dict]，涨停池。没有就返回空列表，不要抛错。"""
        return []

    def health(self):
        """返回 (是否可用, 说明)。"""
        return True, ''


# ---------------------------------------------------------------- 同花顺 CLI

class HithinkSource(BaseSource):
    name = 'hithink'
    label = 'hithink-finance CLI'

    def __init__(self, cfg):
        self.cfg = cfg.get('hithink', {})
        self.timeout = self.cfg.get('timeout', 120)
        self.batch = min(int(self.cfg.get('batch_size', 500)), 500)
        self.cli = self._resolve_cli()

    def _resolve_cli(self):
        # 1) 配置里指定的路径（可能来自 config.json，也可能被环境变量覆盖）
        p = (self.cfg.get('cli_path') or '').strip().strip('"')
        if p:
            if os.path.isfile(p):
                return p
            found = shutil.which(p)
            if found:
                return found
            from_env = bool(os.environ.get('HITHINK_CLI'))
            where = '环境变量 HITHINK_CLI' if from_env else 'config.json 的 hithink.cli_path'
            raise SourceError(
                f'{where} 指向的文件不存在：\n{p}\n'
                '改成你机器上的实际路径，或者把这一项留空让它自动找。')
        # 2) vendor 目录（把 CLI 放这里最省事）
        for name in ('hithink-finance.cmd', 'hithink-finance.exe', 'hithink-finance'):
            cand = os.path.join(ROOT, 'vendor', name)
            if os.path.isfile(cand):
                return cand
        # 3) PATH
        for name in ('hithink-finance.cmd', 'hithink-finance'):
            found = shutil.which(name)
            if found:
                return found
        raise SourceError(
            '找不到 hithink-finance。三种解决方式，任选一种：\n'
            '  1. 把 data_source 改成 "demo"，先用样例数据看界面\n'
            '  2. 把 CLI 放到 vendor/ 目录下\n'
            '  3. 在 config.json 的 hithink.cli_path 填绝对路径')

    def health(self):
        try:
            r = subprocess.run([self.cli, '--version'], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=30)
            out = (r.stdout or r.stderr or '').strip().splitlines()
            return True, out[0] if out else '版本未知'
        except Exception as e:
            return False, f'CLI 调用失败：{e}'

    def _call(self, args, timeout=None):
        r = subprocess.run([self.cli] + args, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=timeout or self.timeout)
        try:
            d = json.loads(r.stdout)
        except Exception:
            raise SourceError(f'CLI 返回的不是 JSON：{(r.stdout or r.stderr)[:300]}')
        if not d.get('ok'):
            err = d.get('error') or {}
            msg = err.get('message') if isinstance(err, dict) else str(err)
            raise SourceError(f'CLI 报错：{msg or json.dumps(err, ensure_ascii=False)[:200]}')
        return d

    def snapshot(self, codes, progress=None, share=1.0):
        rows, total = [], len(codes)
        for i in range(0, total, self.batch):
            chunk = codes[i:i + self.batch]
            fd, tmp = tempfile.mkstemp(suffix='.txt', prefix='sl_codes_')
            os.close(fd)
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(chunk))
                d = self._call(['market', 'snapshot', '--codes-file', tmp,
                                '--limit', str(len(chunk)), '--format', 'json'])
                rows += d['data']['item']
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if progress:
                progress(min(i + self.batch, total), total)
        return rows

    def limit_pool(self):
        try:
            d = self._call(['special', 'limit-up-pool', '--format', 'json'])
            return d['data']['item']
        except Exception:
            # 涨停池是加分项，取不到不该让整次扫描失败
            return []


# ---------------------------------------------------------------- 演示数据

class DemoSource(BaseSource):
    name = 'demo'
    label = '内置样例数据'

    def __init__(self, cfg):
        self.path = os.path.join(ROOT, 'data', 'demo_snapshot.json')
        if not os.path.exists(self.path):
            raise SourceError(
                f'缺少演示数据 {self.path}\n运行 python tools/make_demo.py 生成。')
        self._d = json.load(open(self.path, encoding='utf-8'))

    def health(self):
        n = len(self._d.get('snapshot', []))
        return True, f'{n} 只构造数据（不是真实行情）'

    def snapshot(self, codes, progress=None, share=1.0):
        if progress:
            progress(1, 1)
        out = []
        for s in self._d.get('snapshot', []):
            s = dict(s)
            # 演示数据里存的是「目标全天量比」，快照要的是「此刻累计量」，
            # 所以按当前时点的成交量占比折算一次。这样任何时点看都自洽。
            if s.get('_vr') is not None and s.get('_v5'):
                s['volume'] = int(s['_v5'] * s['_vr'] * share)
            out.append(s)
        return out

    def limit_pool(self):
        return list(self._d.get('limit_pool', []))

    def names(self):
        return dict(self._d.get('names') or {})


# ---------------------------------------------------------------- 工厂

_CACHE = {}


def get_source(cfg):
    kind = (cfg.get('data_source') or 'hithink').lower()
    key = (kind, json.dumps(cfg.get(kind, {}), sort_keys=True, ensure_ascii=False))
    if key in _CACHE:
        return _CACHE[key]
    if kind == 'demo':
        src = DemoSource(cfg)
    elif kind == 'hithink':
        src = HithinkSource(cfg)
    else:
        raise SourceError(f'未知数据源 "{kind}"，只支持 hithink / demo')
    _CACHE[key] = src
    return src
