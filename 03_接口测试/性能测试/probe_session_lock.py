# -*- coding: utf-8 -*-
"""
并发前哨实验：确认「一个 PHPSESSID 能否被多个并发线程共用」

【为什么必须先做这一步 —— 它决定 JMeter 工程的结构】
PHP 原生 session（`session_start()`）默认对 session 文件**加独占锁**，同一 PHPSESSID 的
并发请求会被**串行化**。此时若 JMeter 所有线程共用一个会话，测出来的「并发」其实是排队，
TPS 曲线会平得反常，整套压测结论作废 —— 而且是**看不出来**的那种作废。

ThinkPHP 用的是自己的 File 驱动（读 `file_get_contents`、写 `file_put_contents` 于请求结束），
**不加锁**；本项目 session 落在 `runtime/session/`。但「不锁」只是读源码得出的**推断**，
压测的地基不能建在推断上 —— 本脚本用三个对照臂实测：

  A 臂：1 个 session，8 次请求，**串行**      —— 单会话的基准耗时
  B 臂：1 个 session，8 次请求，**并发**      —— 会话被锁则墙钟≈A
  C 臂：8 个 session，8 次请求，**并发**      —— 无锁时的正常并发

判读（取严）：
  · B 墙钟 ≈ C 墙钟  ⇒ 会话可共用，JMeter 一个会话即可（工程最简）
  · B 墙钟 ≈ A 墙钟  ⇒ **会话被锁**，JMeter 必须每线程独立会话
  · B 居中          ⇒ 存在部分串行化，同样按「每线程独立会话」处理

三个臂都打同一个**只读**接口，不写库、不留数据。
"""
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import oa_login  # noqa: E402
import oa_config  # noqa: E402

BASE = oa_login.BASE_URL
PATH = "/contract/contract/datalist"
PARAMS = {"page": 1, "limit": 20}
N = 8                       # 每臂请求数
ACCOUNT = (oa_config.ADMIN_USER, oa_config.ADMIN_PWD)


def timed(sess, barrier=None):
    """发一次请求，返回 (耗时ms, 响应字节数, code)。barrier 用来让并发真正同时起跑。"""
    if barrier is not None:
        barrier.wait()
    t0 = time.perf_counter()
    try:
        r = sess.get(BASE + PATH, params=PARAMS, timeout=60)
        dt = (time.perf_counter() - t0) * 1000
        try:
            code = r.json().get("code")
        except ValueError:
            code = "非JSON(HTTP %s)" % r.status_code
        return dt, len(r.content), code
    except Exception as e:                                   # noqa: BLE001
        return (time.perf_counter() - t0) * 1000, 0, "异常:%s" % type(e).__name__


def arm_serial(sess):
    return [timed(sess) for _ in range(N)]


def arm_parallel(sessions):
    barrier = threading.Barrier(len(sessions))
    with ThreadPoolExecutor(max_workers=len(sessions)) as ex:
        return list(ex.map(lambda s: timed(s, barrier), sessions))


def report(name, results, wall):
    lat = [r[0] for r in results]
    codes = {}
    for r in results:
        codes[r[2]] = codes.get(r[2], 0) + 1
    print("  %-22s 墙钟 %7.0f ms   单次 中位 %6.1f / 最大 %6.1f ms   code 分布 %s"
          % (name, wall, statistics.median(lat), max(lat), codes))
    return wall


def main():
    print("=" * 84)
    print("并发前哨实验：同一 PHPSESSID 能否被并发共用")
    print("接口 GET %s?page=1&limit=20   （只读，不写库）" % PATH)
    print("=" * 84)

    print("\n[0] 准备会话")
    s1 = oa_login.login(*ACCOUNT)
    print("    主会话 PHPSESSID = %s" % s1.cookies.get("PHPSESSID"))
    others = []
    for i in range(N - 1):
        others.append(oa_login.login(*ACCOUNT))
    print("    另建 %d 个独立会话（共 %d 个）" % (len(others), N))

    if not others:
        others = [s1]

    print("\n[1] A 臂：1 个会话，%d 次**串行**" % N)
    t0 = time.perf_counter()
    a_res = arm_serial(s1)
    wall_a = (time.perf_counter() - t0) * 1000
    report("A 串行(1会话)", a_res, wall_a)

    print("\n[2] B 臂：1 个会话，%d 次**并发**（复用同一个 s1）" % N)
    t0 = time.perf_counter()
    b_res = arm_parallel([s1] * N)
    wall_b = (time.perf_counter() - t0) * 1000
    report("B 并发(1会话)", b_res, wall_b)

    print("\n[3] C 臂：%d 个会话，%d 次**并发**" % (N, N))
    t0 = time.perf_counter()
    c_res = arm_parallel([s1] + others)
    wall_c = (time.perf_counter() - t0) * 1000
    report("C 并发(%d会话)" % N, c_res, wall_c)

    print("\n" + "=" * 84)
    print("判读")
    print("=" * 84)
    print("  A 串行墙钟 = %7.0f ms   （单会话最坏情况：完全排队）" % wall_a)
    print("  B 并发墙钟 = %7.0f ms   （会话被锁则应 ≈ A）" % wall_b)
    print("  C 并发墙钟 = %7.0f ms   （无锁正常并发）" % wall_c)

    # 并发加速比：B 相对 A 缩短了多少
    speedup_b = wall_a / wall_b if wall_b else 0
    print("\n  B/A = %.2f   （≈1.0 ⇒ 完全串行；≈%.1f ⇒ 完全并行）"
          % (wall_b / wall_a if wall_a else 0, N))
    print("  C/A = %.2f" % (wall_c / wall_a if wall_a else 0))

    if wall_b < wall_a * 0.6:
        print("\n  ✅ 结论：同一 PHPSESSID **可以**并发共用（ThinkPHP File 驱动不加锁）。")
        print("     ⇒ JMeter 工程可让所有线程共用/或少量复用会话，结构最简。")
    else:
        print("\n  ⚠️ 结论：同一 PHPSESSID **被串行化**，不能共用。")
        print("     ⇒ JMeter 工程**必须**每线程一个独立会话，并发数 ≤ 预生成会话数。")

    print("\n  （判据取严：只要 B 没有明显快于 A，就按「每线程独立会话」设计。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
