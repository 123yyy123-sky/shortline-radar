"""在桌面创建「短线雷达」快捷方式。

做的事：
  1. 用 Windows 的 Known Folder API 取「真实桌面」路径
     （OneDrive 把桌面重定向过去时，USERPROFILE\\Desktop 可能是空壳）
  2. 建 .lnk，目标 run.bat，工作目录为项目根，图标用 assets/icon.ico
  3. 回读 .lnk 逐项校验，任何一项不对就报错退出

依赖：pywin32（只有本文件需要，app 本身零第三方依赖）
    python -m pip install pywin32

用法：
    python tools/install_shortcut.py
    python tools/install_shortcut.py --name "涨停雷达"
    python tools/install_shortcut.py --desktop "D:\\Desktop"
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAT = os.path.join(ROOT, 'run.bat')
ICON = os.path.join(ROOT, 'assets', 'icon.ico')


def desktop_dir():
    """取真实桌面路径。优先 Known Folder API，失败再退回环境变量。"""
    try:
        from win32com.shell import shell, shellcon
        p = shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    for cand in (os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop'),
                 os.path.join(os.path.expanduser('~'), 'Desktop')):
        if os.path.isdir(cand):
            return cand
    raise SystemExit('找不到桌面目录，请用 --desktop 指定')


def main():
    ap = argparse.ArgumentParser(description='创建桌面快捷方式')
    ap.add_argument('--name', default='短线雷达', help='快捷方式显示名')
    ap.add_argument('--desktop', default=None, help='桌面路径，默认自动探测')
    ap.add_argument('--force', action='store_true', help='已存在则覆盖')
    args = ap.parse_args()

    if not os.path.isfile(BAT):
        raise SystemExit(f'找不到启动脚本：{BAT}')
    if not os.path.isfile(ICON):
        print(f'· 图标不存在，先跑一次 tools/make_icon.py（将不设图标）')

    try:
        import win32com.client
    except ImportError:
        raise SystemExit(
            '缺少 pywin32。装一下再跑：\n'
            '    python -m pip install pywin32')

    desk = args.desktop or desktop_dir()
    lnk = os.path.join(desk, args.name + '.lnk')
    if os.path.exists(lnk) and not args.force:
        raise SystemExit(f'已存在：{lnk}\n要覆盖就加 --force')

    shell = win32com.client.Dispatch('WScript.Shell')
    sc = shell.CreateShortCut(lnk)
    sc.Targetpath = BAT
    sc.WorkingDirectory = ROOT
    sc.Description = '短线雷达 — A股涨停板扫描器（缩量 + 开盘涨 3~9% + 首板）'
    sc.WindowStyle = 1          # 1 常规窗口 3 最大化 7 最小化
    if os.path.isfile(ICON):
        sc.IconLocation = ICON + ',0'
    sc.save()

    # 回读校验
    chk = shell.CreateShortCut(lnk)
    want = {
        '目标': (chk.Targetpath, BAT),
        '工作目录': (chk.WorkingDirectory, ROOT),
    }
    bad = [f'{k}: 期望 {v[1]}，实际 {v[0]}' for k, v in want.items() if
           os.path.normcase(v[0] or '') != os.path.normcase(v[1])]
    if os.path.isfile(ICON):
        if os.path.normcase(chk.IconLocation.split(',')[0]) != os.path.normcase(ICON):
            bad.append(f'图标: 实际 {chk.IconLocation}')
    if bad:
        raise SystemExit('校验失败：\n  ' + '\n  '.join(bad))

    print(f'✓ 已创建：{lnk}')
    print(f'  目标    {chk.Targetpath}')
    print(f'  工作目录 {chk.WorkingDirectory}')
    print(f'  图标    {chk.IconLocation}')
    print(f'  大小    {os.path.getsize(lnk)} B')


if __name__ == '__main__':
    sys.exit(main())
