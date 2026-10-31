# -*- coding: utf-8 -*-
"""
UI 对照法（第二轮）——专门验证"手机号 mobile 是否本就在业务岗界面上可见"

判定逻辑：
  若 业务岗 在界面上（如 企业员工 页）本就能看到员工手机号 → 接口返回 mobile 属设计如此，不报缺陷
  若 界面任何位置都不展示手机号，而接口返回 → 属超出业务必要范围的数据暴露，报缺陷
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
PHONE = re.compile(r"1[3-9]\d{9}")


def main():
    sess = login("yucixin", oa_config.USER_PWD)
    sid = sess.cookies.get("PHPSESSID")

    xhr = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_cookies([{"name": "PHPSESSID", "value": sid, "domain": "localhost", "path": "/"}])
        page = ctx.new_page()
        page.on("response", lambda r: xhr.append((r.status, r.url)) if "/api/" in r.url else None)

        # ---- A. 企业员工页：业务岗能否访问、是否展示手机号 ----
        for path, name in [("/user/user/index", "企业员工")]:
            r = page.goto(BASE_URL + path, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(SHOT_DIR, "UI-05_业务岗%s页.png" % name), full_page=False)
            body = re.sub(r"\s+", " ", page.inner_text("body"))
            phones = PHONE.findall(body)
            print("\n[A] %s %s -> HTTP %s" % (name, path, r.status if r else "?"))
            print("    页面文本前 200 字: %s" % body[:200])
            print("    页面文本中出现的手机号: %d 个 %s" % (len(phones), phones[:3]))
            # 表格是否渲染出行
            rows = page.query_selector_all("table tbody tr")
            print("    表格行数: %d" % len(rows))
            if rows:
                print("    首行文本: %s" % re.sub(r"\s+", " ", rows[0].inner_text())[:160])

        # ---- B. 选择器：点开一个部门，抓 get_employee 的真实响应与渲染结果 ----
        xhr.clear()
        page.goto(BASE_URL + "/oa/workplan/add", wait_until="networkidle", timeout=30000)
        pickers = page.query_selector_all(".picker-admin")
        pickers[0].click()
        page.wait_for_timeout(2000)

        # 点部门树里的"人事部"（第 2 个节点，第 1 个是董事会）
        nodes = page.query_selector_all(".layui-tree-set, .layui-tree-entry, li[data-id]")
        clicked = None
        for n in nodes:
            t = (n.inner_text() or "").strip()
            if "人事部" in t or "技术部" in t:
                try:
                    n.click()
                    clicked = t.split("\n")[0]
                    break
                except Exception:
                    pass
        print("\n[B] 点击部门节点: %s" % clicked)
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(SHOT_DIR, "UI-06_业务岗选择器展开部门.png"))

        html = page.content()
        items = re.findall(r'layui-tags-span[^>]*>([^<]*)<', html)
        print("    选择器渲染出的员工条目: %s" % items[:8])
        print("    渲染条目中出现手机号: %s" % bool(PHONE.search(" ".join(items))))
        txt = re.sub(r"\s+", " ", page.inner_text("body"))
        print("    整个页面可见文本中出现手机号: %s" % bool(PHONE.search(txt)))

        print("\n    触发 get_employee 的请求:")
        for st, u in xhr:
            if "get_employee" in u:
                print("      HTTP %s %s" % (st, u))

        browser.close()


if __name__ == "__main__":
    main()
