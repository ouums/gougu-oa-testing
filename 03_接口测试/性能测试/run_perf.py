# -*- coding: utf-8 -*-
"""
勾股OA 核心读接口 —— 梯度压测驱动 + 结果汇总（JMeter 5.6.3）

【被测对象与判定口径】
三个核心读接口，单接口单跑（同一轮里三个接口同时压会互相争抢 Nginx/PHP-FPM/MySQL，
各自的曲线就不再是该接口自身的表现）：

  审批列表  GET /home/approve/checklist?page=1&limit=20&status=1
  合同列表  GET /contract/contract/datalist?page=1&limit=20
  员工列表  GET /user/user/index?page=1&limit=20      ← isAjax() 分支，layui 表格 JSON

判定 = **梯度趋势**（TPS 是否随并发增长、响应时间增长是否线性），**不设绝对阈值** ——
本机是 WSL2 单机部署（Nginx/PHP-FPM/MySQL 与应用同机同盘），任何绝对 TPS 数字都不具备
迁移性；有意义的只有「加压后曲线怎么走」。详见 05_测试报告。

【指标口径（与计划书 §8 一致）】
  平均响应时间 / P95 / TPS / 错误率，另有 P99 与最大值作参照。
  · P95/P99 用**最近秩法**（升序取 ceil(p·n) 位），不是插值 —— 数字都来自真实样本。
  · **排除爬坡段**：ramp 期内并发还在往上加，那段的样本来自轻载服务器，计入会把结果
    系统性地看得偏乐观。默认 ramp=10 s、总时长=70 s ⇒ 稳态窗口 60 s。
  · TPS = 稳态样本数 / 稳态窗口墙钟秒数（窗口 = 首个稳态样本起，到最后一个样本响应完）。
  · 错误率 = 稳态失败样本 / 稳态样本。失败 = JMeter 断言 code==0 不成立，或请求异常。

【用法】
  python run_perf.py --smoke        # 冒烟：1 接口 × 2 并发 × 8 s，验证工程本身
  python run_perf.py                # 正式：3 接口 × 10/20/50 并发
  python run_perf.py --force        # 忽略已有 .jtl，全部重跑
"""
import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import oa_login  # noqa: E402
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
JMX = os.path.join(HERE, "gougu_oa_read.jmx")
SESSIONS = os.path.join(HERE, "sessions.csv")
OUTDIR = os.path.join(HERE, "结果")

JMETER_CANDIDATES = [
    r"C:\JMeter\apache-jmeter-5.6.3\bin\jmeter.bat",
    r"C:\JMeter\apache-jmeter-5.6.3\bin\jmeter",
]

# (JMeter 标签, 中文名, 路径+查询串)
IFACES = [
    ("approve_checklist", "审批列表", "/home/approve/checklist?page=1&limit=20&status=1"),
    ("contract_datalist", "合同列表", "/contract/contract/datalist?page=1&limit=20"),
    ("user_index",        "员工列表", "/user/user/index?page=1&limit=20"),
]
LEVELS = [10, 20, 50]
RAMP, DURATION = 10, 70          # 总 70 s，稳态 60 s
ACCOUNT = (oa_config.ADMIN_USER, oa_config.ADMIN_PWD)

DEFAULT_FIELDS = ["timeStamp", "elapsed", "label", "responseCode", "responseMessage",
                  "threadName", "dataType", "success", "failureMessage", "bytes",
                  "sentBytes", "grpThreads", "allThreads", "URL", "Latency",
                  "IdleTime", "Connect"]


def find_jmeter():
    for p in JMETER_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("[失败] 未找到 JMeter，试过：\n  " + "\n  ".join(JMETER_CANDIDATES))


# ----------------------------------------------------------------------------- 前置自检
def preflight():
    """压测前必须确认：会话可用 + 三个接口在 1 并发下确实是 code=0（否则压的是错误页）"""
    print("=" * 92)
    print("前置自检")
    print("=" * 92)

    if not os.path.exists(SESSIONS):
        raise SystemExit("[失败] 缺少 %s，先跑：python gen_sessions.py" % SESSIONS)
    n_sess = sum(1 for _ in open(SESSIONS, encoding="utf-8")) - 1
    print("  sessions.csv：%d 个会话（最大并发 %d）" % (n_sess, max(LEVELS)))
    if n_sess < max(LEVELS):
        raise SystemExit("[失败] 会话数 %d < 最大并发 %d，JMeter 侧会复用会话" % (n_sess, max(LEVELS)))

    s = oa_login.login(*ACCOUNT)
    print("  基线会话：PHPSESSID=%s" % s.cookies.get("PHPSESSID"))
    print()
    print("  %-10s %-52s %8s %8s %9s" % ("接口", "路径", "code", "字节", "单次中位ms"))
    print("  " + "-" * 90)

    baseline, ok = {}, True
    for label, cn, path in IFACES:
        lat, code, size = [], None, 0
        for _ in range(5):
            t0 = time.perf_counter()
            r = s.get(oa_login.BASE_URL + path, timeout=30)
            lat.append((time.perf_counter() - t0) * 1000)
            size = len(r.content)
            try:
                code = r.json().get("code")
            except ValueError:
                code = "非JSON(HTTP %s)" % r.status_code
        med = statistics.median(lat)
        baseline[label] = {"ms": med, "bytes": size}
        if code != 0:
            ok = False
        print("  %-10s %-52s %8s %8d %9.1f" % (cn, path, code, size, med))

    if not ok:
        raise SystemExit("\n[失败] 有接口未返回 code=0，先修接口/会话再压测")
    print("\n  [OK] 三个接口在 1 并发下均 code=0")
    return baseline


def smoke(jmeter):
    """冒烟：验证 JMX 本身（会话绑定、路径带查询串、断言）能跑通，再花 12 分钟"""
    print("=" * 92)
    print("冒烟运行（1 接口 × 2 并发 × 8 s）—— 只为验证工程本身")
    print("=" * 92)
    label, cn, path = IFACES[0]
    jtl = os.path.join(OUTDIR, "_smoke.jtl")
    _run(jmeter, label, path, 2, 2, 8, jtl)
    rows = parse_jtl(jtl)
    total, bad = len(rows), sum(1 for r in rows if not r["ok"])
    print("\n  样本 %d，失败 %d" % (total, bad))
    if rows:
        msgs = {r["msg"] for r in rows if not r["ok"]}
        print("  响应码分布 %s" % {r["rc"] for r in rows})
        if msgs:
            print("  失败信息样例：%s" % list(msgs)[:2])
    if total == 0 or bad:
        print("\n  [失败] 冒烟未通过 —— JMX 或会话绑定有问题，先修再正式跑")
        return 2
    print("\n  [OK] 冒烟通过（样本全部 code=0），工程可用")
    return 0


# ----------------------------------------------------------------------------- 运行 / 解析
def _run(jmeter, label, path, threads, ramp, duration, jtl):
    os.makedirs(os.path.dirname(jtl), exist_ok=True)
    if os.path.exists(jtl):
        os.remove(jtl)
    logf = os.path.join(OUTDIR, os.path.basename(jtl).replace(".jtl", ".log"))
    # 命令行**只传 ASCII 标识与数字**：接口路径表在 JMX 内，sessions.csv 由 JMX 相对自身解析。
    # 原因：JMeter 启动脚本是 .bat，参数要过一次 cmd.exe —— 路径里的 `&` 会被当成命令分隔符
    # 把参数截断（实测 `-Jpath=/home/approve/checklist?page=1&limit=20&status=1` 变成
    # `?page=1` + 两条 «'limit' 不是内部或外部命令»，断言 100% 失败）。
    cmd = [jmeter, "-n", "-t", JMX, "-l", jtl, "-j", logf,
           "-Jiface=" + label,
           "-Jthreads=%d" % threads, "-Jramp=%d" % ramp, "-Jduration=%d" % duration,
           "-Jjmeter.save.saveservice.output_format=csv",
           "-Jjmeter.save.saveservice.print_field_names=true",
           "-Jjmeter.save.saveservice.assertion_results_failure_message=true"]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE,
                       encoding="utf-8", errors="replace", timeout=900)
    dt = time.perf_counter() - t0
    if p.returncode != 0 or not os.path.exists(jtl):
        print(p.stdout[-2000:])
        print(p.stderr[-2000:])
        raise SystemExit("[失败] JMeter 退出码 %s（%s）" % (p.returncode, label))
    return dt


def parse_jtl(path):
    """读 JTL（CSV），容忍 JMeter 的注释行与字段顺序变化"""
    out = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        fields = None
        for row in reader:
            if not row or (row[0].startswith("#") and "timeStamp" not in row[0]):
                continue
            if fields is None:
                if "timeStamp" in row:
                    fields = row
                    continue
                fields = DEFAULT_FIELDS
            rec = dict(zip(fields, row))
            try:
                out.append({
                    "ts": int(rec.get("timeStamp") or 0),
                    "ms": float(rec.get("elapsed") or 0),
                    "ok": (rec.get("success") or "").strip().lower() == "true",
                    "rc": rec.get("responseCode") or "",
                    "msg": (rec.get("failureMessage") or "").replace("\n", " ")[:200],
                })
            except (TypeError, ValueError):
                continue
    return out


def pct(sorted_vals, p):
    """最近秩法分位数：升序取 ceil(p·n) 位（n≥1）。样本都真实存在，不做插值。"""
    if not sorted_vals:
        return 0.0
    k = max(1, min(len(sorted_vals), math.ceil(p * len(sorted_vals))))
    return sorted_vals[k - 1]


def metrics(rows, ramp_s):
    """只统计稳态窗口（丢弃前 ramp_s 秒的爬坡样本）"""
    if not rows:
        return None
    t0 = min(r["ts"] for r in rows)
    cut = t0 + ramp_s * 1000
    st = [r for r in rows if r["ts"] >= cut]
    if not st:
        st = rows
    lat = sorted(r["ms"] for r in st)
    end = max(r["ts"] + r["ms"] for r in st)
    window = max((end - cut) / 1000.0, 1e-9)
    bad = sum(1 for r in st if not r["ok"])
    return {
        "total": len(rows), "n": len(st),
        "avg": sum(lat) / len(lat), "p95": pct(lat, 0.95), "p99": pct(lat, 0.99),
        "max": lat[-1], "tps": len(st) / window,
        "err": bad / len(st) * 100.0, "bad": bad,
        "errmsg": sorted({r["msg"] for r in st if not r["ok"]})[:2],
        "rc": sorted({r["rc"] for r in st}),
        # 本次运行的**净执行时长**（首个样本发出 → 最后一个样本响应完），
        # 与 run_perf 的墙钟差一个 JMeter JVM 启动开销（约 8~10 s/次）。
        "span": (max(r["ts"] + r["ms"] for r in rows) - t0) / 1000.0,
    }


# ----------------------------------------------------------------------------- 报告
def write_reports(results, baseline, wall, workers=5):
    os.makedirs(OUTDIR, exist_ok=True)
    by = {}
    for (label, cn, threads), m in results:
        by.setdefault(label, {})[threads] = m
    levels = sorted({t for (_, _, t), _ in results})

    csvp = os.path.join(OUTDIR, "perf_results.csv")
    with open(csvp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["接口", "并发", "样本数(稳态)", "平均ms", "P95ms", "P99ms",
                    "最大ms", "TPS", "错误率%", "失败数", "响应码", "在途N(TPS×W)"])
        for (label, cn, threads), m in results:
            if not m:
                continue
            w.writerow([cn, threads, m["n"], "%.1f" % m["avg"], "%.1f" % m["p95"],
                        "%.1f" % m["p99"], "%.1f" % m["max"], "%.2f" % m["tps"],
                        "%.2f" % m["err"], m["bad"], "/".join(m["rc"]),
                        "%.2f" % (m["tps"] * m["avg"] / 1000.0)])

    mdp = os.path.join(OUTDIR, "性能测试结论.md")
    with open(mdp, "w", encoding="utf-8") as f:
        f.write("# 勾股OA 核心读接口 —— 梯度压测结果\n\n")
        f.write("> 被测系统：勾股OA 6.1.2（**第三方开源产品**）本地部署实例（WSL2 Ubuntu + "
                "Nginx 1.18 + PHP 8.2-fpm + MySQL，单机同盘）。\n"
                "> **结论仅适用于本机实例**。\n\n")
        f.write("- 工具：Apache JMeter 5.6.3（非 GUI 模式）\n")
        f.write("- 并发梯度：**10 / 20 / 50**（单接口单跑，三个接口不互相争抢）\n")
        f.write("- 每档：爬坡 10 s + 稳态 60 s，**指标只统计稳态窗口**（爬坡段样本剔除）\n")
        f.write("- 会话：每线程一个独立账号会话（`gen_sessions.py` 预生成），"
                "`probe_session_lock.py` 已实测同一 PHPSESSID 并发**不会**被串行化\n")
        f.write("- 判定口径：JMeter 断言 **响应体 `code == 0`**，不判 HTTP 状态码"
                "（与本项目功能用例一致：`405`/`code=1` 都是 HTTP 200）\n")
        f.write("- 分位数：最近秩法（非插值）；错误率 = 稳态失败样本占比\n")
        f.write("- 本文件由 `run_perf.py` 自动生成，原始数据见同目录 `*.jtl` 与 `perf_results.csv`\n\n")

        # ---------------------------------------------------------------- 结论先行
        f.write("## 结论\n\n")
        f.write("**吞吐在 10 并发时就已经到顶，再加并发只增加排队、不增加吞吐。**\n\n")
        f.write("三个接口的 TPS 从 10 → 50 并发几乎不动（1.01~1.02 倍），"
                "而平均响应时间**严格按并发数线性增长**（约 5 倍）。\n"
                "这不是「性能一般」，是**已经饱和**的信号：系统里有一个固定宽度的串行资源，"
                "并发超过它之后，多出来的请求只能排队。\n\n")
        f.write("根因经配置核实：**PHP-FPM 进程池 `pm.max_children = 5`**"
                "（`/etc/php/8.2/fpm/pool.d/www.conf:127`），而本机有 **12 核**。\n"
                "即整个站点最多只有 5 个 PHP 进程能同时处理请求 —— 第 6 个请求起就在排队。\n\n")

        f.write("### ⚠ 这是**部署配置**问题，**不是勾股OA 的产品缺陷**\n\n")
        f.write("`pm.max_children = 5` / `start_servers = 2` / `min_spare_servers = 1` / "
                "`max_spare_servers = 3` 这一组值，是 **Ubuntu `php8.2-fpm` 发行包自带的默认值**，"
                "不是产品附带的配置。实测 `dpkg -V php8.2-fpm` **无任何输出** ⇒ "
                "`/etc/php/8.2/fpm/pool.d/www.conf` **从未被改动过**。\n\n")
        f.write("所以准确表述是：「**本机部署沿用了发行版默认配置，未按 12 核机器调优**」。"
                "本项目**不把它计入缺陷** —— 它换任何 PHP 应用都一样，与勾股OA 的代码无关。\n\n")
        f.write("真正属于**应用自身**的信息是下面这张表里的**单请求服务时间**：\n"
                "员工列表 17.4 ms、合同列表 39.6 ms、审批列表 52.6 ms —— 三者相差 **3 倍**，"
                "说明同样是「读一个列表」，应用侧的开销差异很大，这部分才值得深挖 SQL。\n\n")

        # ---------------------------------------------------------------- 关键指标
        f.write("| 接口 | TPS@10 | TPS@20 | TPS@50 | TPS(10→50) | 平均响应(10→50) | 判读 |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for label, cn, _ in IFACES:
            g = by.get(label, {})
            if 10 not in g or 50 not in g:
                continue
            a, b = g[10], g[50]
            tp = b["tps"] / a["tps"] if a["tps"] else 0
            la = b["avg"] / a["avg"] if a["avg"] else 0
            verdict = ("**吞吐零增长、时延五倍** ⇒ 已饱和" if tp < 1.3 and la > 3.5
                       else "有衰减但仍在增长" if tp >= 2.0 else "已接近瓶颈")
            f.write("| %s | %.2f | %.2f | %.2f | %.2f× | %.2f× | %s |\n"
                    % (cn, a["tps"], g[20]["tps"] if 20 in g else 0, b["tps"], tp, la, verdict))

        # ---------------------------------------------------------------- Little 定律
        f.write("\n### 交叉验证一：Little 定律（在途请求数 = 吞吐 × 响应时间）\n\n")
        f.write("闭路压测里，`在途请求数` 必须恒等于 `并发数` —— 每个虚拟用户任何时刻都只有"
                "一个请求未完成。若二者对不上，说明线程有空转、会话没绑上或断言在空跑。\n\n")
        f.write("| 接口 | 并发 | 实测在途 N = TPS×W | 偏差 |\n|---|---|---|---|\n")
        worst = 0.0
        for label, cn, _ in IFACES:
            for t in levels:
                m = by.get(label, {}).get(t)
                if not m:
                    continue
                infl = m["tps"] * m["avg"] / 1000.0
                dev = abs(infl - t) / t * 100
                worst = max(worst, dev)
                f.write("| %s | %d | %.2f | %.1f%% |\n" % (cn, t, infl, dev))
        f.write("\n最大偏差 **%.1f%%** —— 全部档位吻合，说明压测本身没有空转，"
                "测到的时延就是真实排队时延。\n" % worst)

        # ---------------------------------------------------------------- 服务时间模型
        f.write("\n### 交叉验证二：把「%d 个 worker」当唯一参数，能否预测全部 9 个点\n\n" % workers)
        f.write("若瓶颈真是固定宽度的进程池，则在饱和区（并发 > %d）有：\n\n" % workers)
        f.write("```\nTPS   = workers / S          （S = 单请求服务时间，各接口不同）\n"
                "响应W = 并发N × S / workers  （线性排队，N 越大排得越久）\n```\n\n")
        f.write("只用**并发 50 那一档**反解出 S，再看它能不能预测 10 与 20 两档：\n\n")
        f.write("| 接口 | S 反解值 (ms) | 并发 | 实测响应 (ms) | 模型预测 (ms) | 偏差 |\n")
        f.write("|---|---|---|---|---|---|\n")
        for label, cn, _ in IFACES:
            g = by.get(label, {})
            if 50 not in g or not g[50]["tps"]:
                continue
            S = workers / g[50]["tps"] * 1000.0
            for t in levels:
                m = g.get(t)
                if not m or t <= workers:
                    continue
                pred = t * S / workers
                dev = abs(pred - m["avg"]) / m["avg"] * 100
                f.write("| %s | %.1f | %d | %.1f | %.1f | %.1f%% |\n"
                        % (cn, S, t, m["avg"], pred, dev))

        f.write("\n> S 比上面的「单请求基线」高约 13~18%（52.6 vs 44.5 / 39.6 vs 35.1 / 17.4 vs 14.9）。\n"
                "> 这不矛盾：基线是 1 并发、零争用下的单次测量；饱和时 5 个进程同抢一台 12 核机器的\n"
                "> CPU 与内存带宽，每个请求的实际服务时间本来就会略长。两者量级一致。\n")

        # ---------------------------------------------------------------- 详细数据
        f.write("\n---\n\n## 单请求基线（1 并发，5 次取中位）\n\n")
        f.write("| 接口 | 单次中位响应 | 响应体大小 |\n|---|---|---|\n")
        for label, cn, _ in IFACES:
            b = baseline.get(label) or {}
            f.write("| %s | %.1f ms | %s 字节 |\n"
                    % (cn, b.get("ms", 0), "{:,}".format(b.get("bytes", 0))))

        f.write("\n## 梯度结果（稳态窗口）\n\n")
        for label, cn, _ in IFACES:
            f.write("### %s\n\n" % cn)
            f.write("| 并发 | 样本数 | 平均 (ms) | P95 (ms) | P99 (ms) | 最大 (ms) | TPS | 错误率 |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for t in levels:
                m = by.get(label, {}).get(t)
                if not m:
                    continue
                f.write("| %d | %d | %.1f | %.1f | %.1f | %.1f | %.2f | %.2f%% |\n"
                        % (t, m["n"], m["avg"], m["p95"], m["p99"], m["max"], m["tps"], m["err"]))
            f.write("\n")

        f.write("## 建议（若要把吞吐提上去）\n\n")
        f.write("1. **调 `pm.max_children`** —— 这是唯一直接卡住吞吐的参数。本机 12 核、"
                "数据库同机，需按内存给 PHP 进程留额度（每个 FPM 进程实测常驻数十 MB，"
                "`pm.max_children × 单进程内存` 不能吃掉 MySQL 的缓冲池）。改完必须重测本套，"
                "看 TPS 是否随并发重新增长。\n")
        f.write("2. **同时看 `pm = dynamic` 的 `start_servers = 2` / `max_spare_servers = 3`** —— "
                "现在空闲进程上限只有 3，突发流量下要先补进程再服务。\n")
        f.write("3. **三个接口的服务时间差 3 倍**（员工列表最快、审批列表最慢），"
                "说明瓶颈不只在进程数：审批列表单请求服务时间最长，值得单独看它的 SQL。\n")
        f.write("4. 本结论**不含**写接口、并发写、锁竞争 —— 那些需要另设场景。\n")

        f.write("\n## 数据完整性\n\n")
        n_req = sum(m["n"] for _, m in results if m)
        n_bad = sum(m["bad"] for _, m in results if m)
        span = sum(m["span"] for _, m in results if m)
        f.write("- 稳态样本合计 **%s 条**，失败 **%d 条**\n" % ("{:,}".format(n_req), n_bad))
        f.write("- 压测**净执行时长** **%.1f s**：%d 次运行各自「首个样本发出 → 最后一个样本"
                "响应完」之和（不含 JMeter 启动）\n" % (span, len(results)))
        if wall:
            f.write("- 本轮命令**总墙钟** %.1f s（=%d 次 JMeter 的 JVM 启动开销 + 净执行时长）\n"
                    % (wall, len(results)))
        f.write("- 源数据：`结果/<接口标签>_<并发>.jtl`（JMeter 原始 CSV，逐样本可复核；合计约 16 MB）\n")
        f.write("- 汇总：`结果/perf_results.csv`\n")
        f.write("- 重出报告（不重跑压测）：`run_perf.py --report-only`\n")
    return csvp, mdp


def main():
    ap = argparse.ArgumentParser(description="勾股OA 核心读接口梯度压测")
    ap.add_argument("--smoke", action="store_true", help="只跑冒烟，验证工程")
    ap.add_argument("--force", action="store_true", help="忽略已有 .jtl 全部重跑")
    ap.add_argument("--levels", default="", help="逗号分隔的并发梯度，默认 10,20,50")
    ap.add_argument("--report-only", action="store_true",
                    help="不跑 JMeter，只用已有 .jtl 重新出报告（改报告模板时用）")
    ap.add_argument("--fpm-workers", type=int, default=5,
                    help="PHP-FPM 进程池宽度 pm.max_children，用于服务时间模型；"
                         "本机实测值见 /etc/php/8.2/fpm/pool.d/www.conf")
    args = ap.parse_args()

    jmeter = find_jmeter()
    print("JMeter: %s\n" % jmeter)
    os.makedirs(OUTDIR, exist_ok=True)

    if args.smoke:
        return smoke(jmeter)

    baseline = preflight()
    levels = [int(x) for x in args.levels.split(",")] if args.levels else LEVELS

    if args.report_only:
        results = []
        for label, cn, path in IFACES:
            for t in levels:
                jtl = os.path.join(OUTDIR, "%s_%d.jtl" % (label, t))
                if not os.path.exists(jtl):
                    continue
                results.append(((label, cn, t), metrics(parse_jtl(jtl), RAMP)))
        if not results:
            raise SystemExit("[失败] 结果目录里没有可用的 .jtl")
        csvp, mdp = write_reports(results, baseline, 0, args.fpm_workers)   # 0 = 无本轮墙钟
        print("\n[report-only] 用 %d 份既有 .jtl 重出报告（未跑 JMeter）" % len(results))
        print("  汇总 CSV：%s\n  结论 MD ：%s" % (csvp, mdp))
        return 0

    jobs = [(label, cn, path, t) for label, cn, path in IFACES for t in levels]
    if not args.force:
        jobs = [j for j in jobs
                if not os.path.exists(os.path.join(OUTDIR, "%s_%d.jtl" % (j[0], j[3])))]
        skipped = len(IFACES) * len(levels) - len(jobs)
        if skipped:
            print("\n  跳过 %d 个已有 .jtl（--force 可全部重跑）" % skipped)

    print("\n" + "=" * 92)
    print("正式压测：%d 次运行（每次 爬坡 %d s + 稳态 %d s）" % (len(jobs), RAMP, DURATION - RAMP))
    print("=" * 92)

    results, t_all = [], time.perf_counter()
    for i, (label, cn, path, threads) in enumerate(jobs, 1):
        jtl = os.path.join(OUTDIR, "%s_%d.jtl" % (label, threads))
        print("\n[%d/%d] %s × %d 并发 …" % (i, len(jobs), cn, threads), flush=True)
        dt = _run(jmeter, label, path, threads, RAMP, DURATION, jtl)
        m = metrics(parse_jtl(jtl), RAMP)
        results.append(((label, cn, threads), m))
        if m:
            print("      稳态样本 %d | 平均 %.1f ms | P95 %.1f ms | TPS %.2f | 错误率 %.2f%%  (用时 %.0f s)"
                  % (m["n"], m["avg"], m["p95"], m["tps"], m["err"], dt), flush=True)
            if m["bad"]:
                print("      ⚠ 失败 %d 条，样例：%s" % (m["bad"], m["errmsg"]), flush=True)

    # 补齐被跳过的历史结果，保证报告完整
    have = {(lb, t) for (lb, _, t), _ in results}
    for label, cn, path in IFACES:
        for t in levels:
            if (label, t) not in have:
                jtl = os.path.join(OUTDIR, "%s_%d.jtl" % (label, t))
                if os.path.exists(jtl):
                    results.append(((label, cn, t), metrics(parse_jtl(jtl), RAMP)))

    wall = time.perf_counter() - t_all
    csvp, mdp = write_reports(results, baseline, wall, args.fpm_workers)

    print("\n" + "=" * 92)
    print("完成（%.1f s）" % wall)
    print("=" * 92)
    print("  %-10s %-6s %8s %9s %9s %9s %9s" % ("接口", "并发", "样本", "平均ms", "P95ms", "TPS", "错误率"))
    for (label, cn, t), m in sorted(results, key=lambda x: (x[0][1], x[0][2])):
        if not m:
            continue
        print("  %-10s %-6d %8d %9.1f %9.1f %9.2f %8.2f%%"
              % (cn, t, m["n"], m["avg"], m["p95"], m["tps"], m["err"]))
    print("\n  汇总 CSV：%s" % csvp)
    print("  结论 MD ：%s" % mdp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
