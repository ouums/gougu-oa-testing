# -*- coding: utf-8 -*-
"""
压测残留回收：删掉 sessions.csv 里那批会话对应的 session 文件

【为什么需要它】压测**只读**，不写库、不留业务数据 —— 唯一残留是 WSL
`runtime/session/sess_<sid>` 下多出的会话文件（等价于手工登录了 N 次）。实测该目录已累积 308 个。

【为什么不能 `rm sess_*`】本项目的一条硬规矩：**清理必须显式 id 列表 + 分步回读，
绝不用通配符/LIKE 批量删**。`runtime/session/` 里同时躺着**别人正在用的**会话
（浏览器里的 admin、其他测试脚本），`rm sess_*` 会把它们一起踢下线。
所以这里只删 `sessions.csv` 里**逐个列出来**的那些 sid，且默认 dry-run。

【注意】删掉之后 `sessions.csv` 里那些会话即失效，重跑压测前需重新
`gen_sessions.py`。`RulesSrc{uid}` 权限缓存**不动** —— 它按 uid 共享，不属于某一次会话。
"""
import argparse
import csv
import os
import subprocess
import sys

import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "sessions.csv")
WSL_DISTRO = oa_config.WSL_DISTRO
SESSION_DIR = oa_config.SESSION_DIR


def read_sids(path):
    if not os.path.exists(path):
        raise SystemExit("[失败] 找不到 %s" % path)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    sids = [r["sid"].strip() for r in rows if (r.get("sid") or "").strip()]
    if not sids:
        raise SystemExit("[失败] %s 里没有 sid" % path)
    for s in sids:                      # 自检：sid 必须是十六进制，杜绝把奇怪东西拼进命令行
        if not all(c in "0123456789abcdef" for c in s.lower()):
            raise SystemExit("[失败] 非法 sid：%r" % s)
    return sids


def wsl_ls(sids):
    """一次问清这批文件哪些真的存在（不存在的自然不进删除命令）

    ⚠ 不要在这里用 shell 变量（`for f in …; do test -e $f …`）—— 本项目已踩过：
    shell 代码里的 `$` 经 wsl.exe 命令行传递会被吞掉，必须改走 base64。
    这里索性绕开变量：直接 `ls -d` 一串**显式绝对路径**，存在的进 stdout、
    不存在的进 stderr（不读），一行一个，天然就是「存在清单」。
    """
    paths = ["%s/sess_%s" % (SESSION_DIR, s) for s in sids]
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c",
                          "ls -d " + " ".join(paths)],
                         capture_output=True, text=True, timeout=120)
    return [l.strip() for l in (out.stdout or "").splitlines() if l.strip()]


def wsl_count():
    """会话目录当前文件数 —— **实测，不写死**

    首版把「目录下共有 308 个」「删前 308」写成常量，到收尾时实测已是 420，
    于是脚本自己印出一句假话。这个坑与备份份数那条相同：**随过程累积的量不要写死**，
    要么现算、要么带日期标注（见 00_挂起存档.md §2.14 ④）。
    """
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c",
                          "ls %s | wc -l" % SESSION_DIR],
                         capture_output=True, text=True, timeout=60)
    return (out.stdout or "").strip()


def main():
    ap = argparse.ArgumentParser(description="回收压测会话文件（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真的删；不加只报告")
    ap.add_argument("--file", default=SESSIONS)
    args = ap.parse_args()

    sids = read_sids(args.file)
    before = wsl_count()
    print("=" * 78)
    print("压测会话回收  —— 会话清单来自 %s" % os.path.basename(args.file))
    print("模式：%s" % ("**真删**" if args.apply else "dry-run（只报告，不动文件）"))
    print("=" * 78)

    print("\n[1] 清单解析")
    print("    显式列出 %d 个 sid，全部通过十六进制自检" % len(sids))
    print("    会话目录 %s（目录下共有 %s 个，**只删清单里的这些**）" % (SESSION_DIR, before))

    print("\n[2] 逐个回读：这些文件当前是否存在")
    present = wsl_ls(sids)
    print("    存在 %d / 清单 %d" % (len(present), len(sids)))
    for p in present[:5]:
        print("      %s" % p)
    if len(present) > 5:
        print("      … 其余 %d 个" % (len(present) - 5))

    if not present:
        print("\n[OK] 清单里的会话文件都已不在，空操作。")
        return 0

    if not args.apply:
        print("\n[dry-run] 未删除。确认无误后加 --apply 执行。")
        print("          注意：删后 sessions.csv 里的会话即失效，重跑压测前需重新 gen_sessions.py")
        return 0

    print("\n[3] 删除（一次命令行内，逐个绝对路径，无通配符）")
    cmd = "rm -f " + " ".join(present)
    r = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", cmd],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("    [失败] %s" % (r.stderr or "").strip()[:300])
        return 2

    print("\n[4] 删除后回读")
    left = wsl_ls(sids)
    print("    仍在 %d 个（应为 0）" % len(left))
    print("    目录现存总数：%s（删前 %s）" % (wsl_count(), before))
    if left:
        print("\n[失败] 仍有文件未删除")
        return 2
    print("\n[OK] 清单内会话已全部回收。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
