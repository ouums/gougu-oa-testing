# -*- coding: utf-8 -*-
"""
性能测试前置：批量生成登录会话，写出 sessions.csv（供 JMeter 每线程绑定一个）

【为什么每个线程要绑不同会话 —— 以及为什么这**不是**为了绕开锁】
`probe_session_lock.py` 已实测：同一 PHPSESSID 并发共用**不会**被串行化
（B 臂并发墙钟 109 ms ≈ C 臂 8 会话并发 107 ms，A 臂串行 291 ms）。
所以「每线程独立会话」不是被迫的，是**主动选的**：50 个虚拟用户共用一个登录态
不构成真实负载模型（真实场景是 50 个人各自登录），而每个会话的权限缓存
`RulesSrc{uid}`、session 文件读写在多用户下才有代表性。这是建模选择，不是绕坑。

【前置】需被测系统在跑；本地 session 目录可读（验证码方案见 oa_login.py 顶部说明）。
【产出】sessions.csv —— 表头 `account,sid`，第 1 列是账号、第 2 列是 PHPSESSID。
        JMeter 侧按**线程号取模**取行，故行数需 ≥ 最大并发数。
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import oa_login  # noqa: E402
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "sessions.csv")


def main():
    ap = argparse.ArgumentParser(description="批量生成压测用登录会话")
    ap.add_argument("--count", type=int, default=60,
                    help="生成会话数，必须 ≥ 最大并发数（默认 60，覆盖梯度上界 50）")
    ap.add_argument("--user", default=oa_config.ADMIN_USER)
    ap.add_argument("--password", default=oa_config.ADMIN_PWD)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    print("=" * 78)
    print("生成压测会话：%d 个（账号 %s）" % (args.count, args.user))
    print("=" * 78)

    rows, t0 = [], time.perf_counter()
    for i in range(1, args.count + 1):
        for attempt in (1, 2, 3):                       # 偶发文件未落盘，重试
            try:
                s = oa_login.login(args.user, args.password)
                break
            except Exception as e:                      # noqa: BLE001
                if attempt == 3:
                    print("\n[失败] 第 %d 个会话连续 3 次登录失败：%s" % (i, e))
                    return 2
                time.sleep(0.5)
        sid = s.cookies.get("PHPSESSID")
        if not sid:
            print("\n[失败] 第 %d 个会话未拿到 PHPSESSID" % i)
            return 2
        rows.append((args.user, sid))
        if i % 10 == 0 or i == args.count:
            print("  已生成 %3d/%d  （累计 %.1f s）" % (i, args.count, time.perf_counter() - t0))

    # 去重自检：会话 id 若重复，JMeter 侧「每线程一个会话」就是假的
    uniq = {r[1] for r in rows}
    if len(uniq) != len(rows):
        print("\n[失败] 会话 id 有重复：%d 个行只有 %d 个不同 sid" % (len(rows), len(uniq)))
        return 2

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["account", "sid"])
        w.writerows(rows)

    print("\n[OK] 已写出 %s" % args.out)
    print("     %d 行数据 / %d 个不同 sid / 耗时 %.1f s"
          % (len(rows), len(uniq), time.perf_counter() - t0))
    print("     JMeter 侧按线程号取模绑定，故并发数上限 = %d" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
