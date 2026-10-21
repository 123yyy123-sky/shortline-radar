# -*- coding: utf-8 -*-
"""停止后台服务。

为什么不用 .bat：双击 .bat 一定会闪一个黑色命令行窗口，
这个脚本是给桌面图标用的，用 pythonw 跑就完全无窗口。

  python stop.py            # 停 8848
  python stop.py 9000       # 停指定端口
"""
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, 'data', 'app.log')

# pythonw 没有控制台，直接调 netstat / taskkill 会各自弹一个黑框
if os.name == 'nt':
    NO_WIN = {'creationflags': 0x08000000, 'stdin': subprocess.DEVNULL}
else:
    NO_WIN = {'stdin': subprocess.DEVNULL}


def listeners(port):
    """返回监听该端口的进程号。"""
    try:
        out = subprocess.run(
            ['netstat', '-ano', '-p', 'TCP'],
            capture_output=True, text=True, errors='ignore', timeout=10,
            **NO_WIN).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        # 第 2 列是本地地址，第 4 列是状态，第 5 列是 PID
        if not parts[1].endswith(':' + port):
            continue
        if parts[3].upper() != 'LISTENING':      # 中文系统也返回 LISTENING
            continue
        if parts[4].isdigit() and parts[4] != '0':
            pids.append(parts[4])
    return pids


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else '8848'
    pids = listeners(port)
    lines = [f'\n--- 停止 {datetime.now():%Y-%m-%d %H:%M:%S} 端口 {port} ---']
    if not pids:
        lines.append('没有服务在跑，什么都不用做。')
    else:
        for pid in pids:
            r = subprocess.run(['taskkill', '/F', '/PID', pid],
                               capture_output=True, text=True, errors='ignore',
                               **NO_WIN)
            ok = r.returncode == 0
            lines.append(f'PID {pid} -> {"已结束" if ok else "结束失败"}')
    text = '\n'.join(lines)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(text + '\n')
    except OSError:
        pass
    # pythonw 下没有控制台，print 会抛异常，写日志就够了
    try:
        print(text)
    except Exception:
        pass


if __name__ == '__main__':
    main()
