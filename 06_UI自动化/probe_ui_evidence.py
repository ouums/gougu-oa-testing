# -*- coding: utf-8 -*-
"""
勾股OA —— UI 对照法取证脚本（Playwright）

目的：对"接口可越权访问"的判定做界面侧核对（测试计划 2.1 节 / Flarum 误报教训）：
  1. 业务岗 yucixin 的界面里，员工选择器是否本就向该用户开放 → 决定"接口返回全量员工"是否算缺陷
  2. 选择器界面实际渲染了哪些字段 → 判断接口多返回的 mobile 是否被界面使用
  3. 顶栏"清空缓存"按钮是否对业务岗可见 → cache_clear 缺陷的界面侧证据

会话获取方式：用 oa_login 解决验证码拿到 PHPSESSID，再注入浏览器，
避免在浏览器里重复对付验证码。
"""
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03_接口测试"))
from oa_login import BASE_URL, login  # noqa: E402
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "04_缺陷报告", "截图")
os.makedirs(SHOT_DIR, exist_ok=True)


def main():
    sess = login("yucixin", oa_config.USER_PWD)
    sid = sess.cookies.get("PHPSESSID")
    print("业务岗 yucixin 会话: %s" % sid)

    xhr_log = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_cookies([{"name": "PHPSESSID", "value": sid, "domain": "localhost", "path": "/"}])
        page = ctx.new_page()

        # 记录所有业务 XHR
        def on_response(resp):
            u = resp.url
            if "/api/" in u or "/home/" in u:
                try:
                    body = resp.text()[:400]
                except Exception:
                    body = "<无法读取>"
                xhr_log.append((resp.status, u, body))

        page.on("response", on_response)

        # ---- 1. 工作台首页：清空缓存按钮是否可见 ----
        page.goto(BASE_URL + "/home/index/index", wait_until="networkidle", timeout=30000)
        page.screenshot(path=os.path.join(SHOT_DIR, "UI-01_业务岗工作台首页.png"), full_page=False)
        btn = page.query_selector('[gg-event="cache"]')
        print("\n[UI-1] 顶栏[清空缓存]按钮存在: %s" % (btn is not None))
        if btn:
            print("        可见: %s   title=%s" % (btn.is_visible(), btn.get_attribute("title")))
            print("        指向: %s" % btn.get_attribute("data-href"))
            page.screenshot(path=os.path.join(SHOT_DIR, "UI-02_业务岗可见清空缓存按钮.png"))

        # ---- 2. 工作计划新建页：员工选择器是否为该用户开放 ----
        xhr_log.clear()
        page.goto(BASE_URL + "/oa/workplan/add", wait_until="networkidle", timeout=30000)
        page.screenshot(path=os.path.join(SHOT_DIR, "UI-03_业务岗工作计划新建页.png"), full_page=True)
        pickers = page.query_selector_all(".picker-admin")
        print("\n[UI-2] /oa/workplan/add 页面上的员工选择器数量: %d" % len(pickers))

        if pickers:
            pickers[0].click()
            page.wait_for_timeout(2500)
            page.screenshot(path=os.path.join(SHOT_DIR, "UI-04_业务岗可打开员工选择器.png"), full_page=False)
            html = page.content()
            # 选择器渲染出来的文本片段（看有没有手机号）
            m = re.findall(r'mbui-picker-name">([^<]*)<', html)
            print("        选择器渲染出的条目样例: %s" % m[:5])
            print("        选择器 DOM 中是否出现 11 位手机号: %s"
                  % bool(re.search(r'1[3-9]\d{9}', " ".join(m))))

        # ---- 3. 员工管理页（矩阵预期：业务岗 ❌） ----
        for path, tag in [("/home/admin/index", "员工管理"), ("/home/role/index", "角色组管理")]:
            r = page.goto(BASE_URL + path, wait_until="networkidle", timeout=30000)
            txt = re.sub(r"\s+", " ", page.inner_text("body"))[:200]
            print("\n[UI-3] %s %s -> HTTP %s" % (tag, path, r.status if r else "?"))
            print("       页面文本: %s" % txt)

        # ---- 4. 打印抓到的业务 XHR ----
        print("\n[UI-4] 页面实际发出的业务请求：")
        seen = set()
        for st, u, body in xhr_log:
            key = u.split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            print("   HTTP %s  %s" % (st, u))
            print("        %s" % body.replace("\n", " ")[:220])

        browser.close()

    print("\n截图目录: %s" % os.path.abspath(SHOT_DIR))

    # ---- 5. 接口侧对照：手机号字段是否真的返回 ----
    print("\n[UI-5] 接口侧对照（同会话直接请求）:")
    r = sess.get(BASE_URL + "/api/index/get_employee", timeout=20)
    rows = r.json().get("data") or []
    if rows:
        keys = sorted(rows[0].keys())
        print("    get_employee 返回字段: %s" % ", ".join(keys))
        print("    含 mobile 且非空: %d/%d 条" % (sum(1 for x in rows if x.get("mobile")), len(rows)))


if __name__ == "__main__":
    main()
