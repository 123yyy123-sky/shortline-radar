"""在桌面创建「短线雷达」快捷方式。

做的事：
  1. 用 Windows 的 SpecialFolders("Desktop") 取「真实桌面」路径
     （OneDrive 把桌面重定向过去时，USERPROFILE\\Desktop 可能是空壳）
  2. 建 .lnk —— 默认用 pythonw.exe 启动，**不弹黑窗口**
  3. 回读 .lnk 逐项校验，任何一项不对就报错退出

为什么默认无窗口：
  有窗口时，那个黑框就是服务本体，关掉窗口服务就停，不关就一直占着
  任务栏和内存。改成 pythonw 之后进程在后台静默跑，日志写
  data/app.log；关掉所有浏览器页面 2 分钟后它自己退出
  （config.json 的 web.idle_exit_seconds，0 = 常驻）。
  想手动停：双击「停止短线雷达」。

为什么不用 pywin32：
  建快捷方式只是调 WScript.Shell，Windows 自带 cscript 就能干。
  少一个第三方依赖，换机器也能直接跑。

用法：
    python tools/install_shortcut.py                     # 无窗口 + 顺带建停止图标
    python tools/install_shortcut.py --console           # 保留黑窗口（看日志直观）
    python tools/install_shortcut.py --pythonw D:\\py\\pythonw.exe
    python tools/install_shortcut.py --name "涨停雷达" --force
    python tools/install_shortcut.py --no-stop           # 不建停止图标
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAT = os.path.join(ROOT, 'run.bat')
STOP_BAT = os.path.join(ROOT, 'stop.bat')
ICON = os.path.join(ROOT, 'assets', 'icon.ico')

VBS = r'''Option Explicit
Dim sh, fso, chk, out

{{VARS}}

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Sub MakeLink(lnk, tgt, args, wd, desc, icon)
    Dim s
    Set s = sh.CreateShortcut(lnk)
    s.TargetPath = tgt
    If Len(args) > 0 Then s.Arguments = args
    s.WorkingDirectory = wd
    s.Description = desc
    s.WindowStyle = 1
    If Len(icon) > 0 Then s.IconLocation = icon & ",0"
    s.Save
End Sub

Set out = fso.CreateTextFile(RESULT, True, True)
out.WriteLine "DESKTOP=" & sh.SpecialFolders("Desktop")

MakeLink LINK, TGT, ARG, WD, DESC, ICON
Set chk = sh.CreateShortcut(LINK)
out.WriteLine "TARGET=" & chk.TargetPath
out.WriteLine "ARGS=" & chk.Arguments
out.WriteLine "WD=" & chk.WorkingDirectory
out.WriteLine "ICON=" & chk.IconLocation

If Len(STOPLINK) > 0 Then
    MakeLink STOPLINK, STOPTGT, STOPARG, WD, STOPDESC, ICON
    Set chk = sh.CreateShortcut(STOPLINK)
    out.WriteLine "STOP_TARGET=" & chk.TargetPath
End If

out.Close
'''


def vbs_str(s):
    """把 Python 字符串变成 VBS 字符串字面量（双引号内部用两个双引号转义）。"""
    return '"' + (s or '').replace('"', '""') + '"'


def find_pythonw(explicit=None):
    """找 pythonw.exe。顺序：显式指定 -> 环境变量 -> 当前解释器同级 -> PATH。"""
    cands = []
    if explicit:
        cands.append(explicit)
    env = os.environ.get('SHORTLINE_PYTHONW')
    if env:
        cands.append(env)
    cands.append(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))
    w = shutil.which('pythonw')
    if w:
        cands.append(w if w.lower().endswith('.exe') else w + '.exe')
    for c in cands:
        if c and os.path.isfile(c) and c.lower().endswith('pythonw.exe'):
            return os.path.abspath(c)
    return None


def cscript_exe():
    p = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                     'System32', 'cscript.exe')
    return p if os.path.isfile(p) else 'cscript'


def run_vbs(vars_):
    """用 cscript 跑一段生成的 VBS，返回回读到的键值。"""
    tmpdir = tempfile.mkdtemp(prefix='sl_lnk_')
    vbs_path = os.path.join(tmpdir, 'make.vbs')
    res_path = os.path.join(tmpdir, 'result.txt')

    body = 'Const RESULT = %s\n' % vbs_str(res_path)
    for k, v in vars_.items():
        body += 'Const %s = %s\n' % (k, vbs_str(v))
    # Option Explicit 必须留在第一行，所以常量要插到 Dim 之后
    body = VBS.replace('{{VARS}}', body)

    # VBS 里有中文（描述、快捷方式名），必须存成 UTF-16LE + BOM
    with open(vbs_path, 'w', encoding='utf-16') as f:
        f.write(body)

    r = subprocess.run([cscript_exe(), '//nologo', vbs_path],
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    try:
        with open(res_path, encoding='utf-16') as f:
            lines = [x.rstrip('\r\n') for x in f if '=' in x]
    except OSError:
        raise SystemExit('建快捷方式失败，cscript 没有产出结果文件。\n'
                         f'stdout: {(r.stdout or "").strip()[:400]}\n'
                         f'stderr: {(r.stderr or "").strip()[:400]}')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    out = {}
    for ln in lines:
        k, _, v = ln.partition('=')
        out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser(description='创建桌面快捷方式')
    ap.add_argument('--name', default='短线雷达', help='快捷方式显示名')
    ap.add_argument('--desktop', default=None,
                    help='桌面路径，默认让系统给（OneDrive 重定向也能认对）')
    ap.add_argument('--force', action='store_true', help='已存在则覆盖')
    ap.add_argument('--console', action='store_true',
                    help='保留控制台窗口（走 run.bat，看日志直观）')
    ap.add_argument('--pythonw', default=None, help='指定 pythonw.exe 路径')
    ap.add_argument('--no-stop', action='store_true', help='不建「停止」快捷方式')
    args = ap.parse_args()

    if not os.path.isfile(BAT):
        raise SystemExit(f'找不到启动脚本：{BAT}')

    # 选启动方式
    if args.console:
        target, arguments, mode = BAT, '', '有窗口（run.bat）'
    else:
        pw = find_pythonw(args.pythonw)
        if pw:
            target, arguments, mode = pw, '"app.py"', '无窗口（pythonw）'
        else:
            target, arguments, mode = BAT, '', '有窗口（没找到 pythonw，退回 run.bat）'

    # 停止图标也跟着走无窗口：双击 stop.bat 会闪一个黑框，用 pythonw 跑 stop.py 就没有
    stop_py = os.path.join(ROOT, 'tools', 'stop.py')
    if mode.startswith('无窗口') and os.path.isfile(stop_py):
        stop_target, stop_args = target, '"tools\\stop.py"'
    else:
        stop_target, stop_args = STOP_BAT, ''

    have_icon = os.path.isfile(ICON)
    if not have_icon:
        print('· 图标不存在，先跑一次 tools/make_icon.py（将不设图标）')

    # 先算桌面路径（用 VBS 的 SpecialFolders，避免自己猜）
    probe = run_vbs({'PROBE': '', 'LINK': os.path.join(
        os.environ.get('TEMP', '.'), '_sl_probe.lnk'), 'TGT': BAT, 'ARG': '',
        'WD': ROOT, 'DESC': '', 'ICON': '', 'STOPLINK': '',
        'STOPTGT': '', 'STOPARG': ''})
    desk = args.desktop or probe.get('DESKTOP') or os.path.expanduser('~')
    try:
        os.remove(os.path.join(os.environ.get('TEMP', '.'), '_sl_probe.lnk'))
    except OSError:
        pass

    lnk = os.path.join(desk, args.name + '.lnk')
    stop_lnk = '' if args.no_stop else os.path.join(desk, '停止' + args.name + '.lnk')

    if os.path.exists(lnk) and not args.force:
        raise SystemExit(f'已存在：{lnk}\n要覆盖就加 --force')
    if stop_lnk and os.path.exists(stop_lnk) and not args.force:
        stop_lnk_use = ''
    else:
        stop_lnk_use = stop_lnk if os.path.isfile(STOP_BAT) else ''

    res = run_vbs({
        'LINK': lnk,
        'TGT': target,
        'ARG': arguments,
        'WD': ROOT,
        'DESC': '短线雷达 — A股涨停板扫描器（缩量 + 开盘涨 3~9% + 首板）',
        'ICON': ICON if have_icon else '',
        'STOPLINK': stop_lnk_use,
        'STOPTGT': stop_target,
        'STOPARG': stop_args,
        'STOPDESC': '关掉正在后台运行的短线雷达',
    })

    # 校验
    bad = []
    if os.path.normcase(res.get('TARGET', '')) != os.path.normcase(target):
        bad.append(f'目标: 期望 {target}，实际 {res.get("TARGET")}')
    if arguments and (res.get('ARGS') or '') != arguments:
        bad.append(f'参数: 期望 {arguments}，实际 {res.get("ARGS")}')
    if os.path.normcase(res.get('WD', '')) != os.path.normcase(ROOT):
        bad.append(f'工作目录: 期望 {ROOT}，实际 {res.get("WD")}')
    if have_icon and os.path.normcase((res.get('ICON') or '').split(',')[0]) != os.path.normcase(ICON):
        bad.append(f'图标: 实际 {res.get("ICON")}')
    if bad:
        raise SystemExit('校验失败：\n  ' + '\n  '.join(bad))

    print(f'✓ 已创建：{lnk}')
    print(f'  方式    {mode}')
    print(f'  目标    {res.get("TARGET")}')
    if res.get('ARGS'):
        print(f'  参数    {res.get("ARGS")}')
    print(f'  工作目录 {res.get("WD")}')
    print(f'  图标    {res.get("ICON")}')

    if mode.startswith('无窗口'):
        print(f'  日志    {os.path.join(ROOT, "data", "app.log")}')
        print('  关掉浏览器 2 分钟后自动退出；想立刻停就双击「停止' + args.name + '」')

    if res.get('STOP_TARGET'):
        print(f'✓ 已创建：{stop_lnk_use}')
    elif args.no_stop:
        pass
    else:
        print('· 跳过停止图标（已存在或不支持）')


if __name__ == '__main__':
    sys.exit(main())
