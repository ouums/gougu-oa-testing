# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 —— 构造合同模块测试数据（主数据 + 销售合同 oa_contract + 采购合同 oa_purchase）

【为什么需要这个脚本】
  oa_contract / oa_purchase 在演示数据里都是 **0 行**，合同模块的 30 条用例
  （测试计划 §4.5 要求 15~20 条测试数据，覆盖 多状态 / 多金额区间 / 跨到期日）
  没有任何数据可测。

【为什么必须先补主数据 —— 实测发现的前置条件】
  合同新建界面的三个关键字段由 DataAuth 开关控制（oa_data_auth id=4 contract_admin：
  conf_3=is_customer=1、conf_4=is_product=1、conf_5=is_service=1）：
      conf_x=1 时，该字段渲染为 **readonly + 弹窗选择器**（见 contract/view/contract/
      add_product.html 的 {eq name="is_product" value="1"} 分支、add.html 的 is_customer 分支）。
  而本演示实例里 oa_customer=0 行、oa_product=0 行 —— 选择器点开是空的，字段又是
  必填（lay-verify="required"），**界面路径下销售合同根本建不出来**。
  经核实这是「演示数据缺失」而非代码缺陷：系统本身提供 客户管理 / 产品管理 / 供应商管理
  三个入口可以补齐（且上游确实这么设计）。故本脚本先补最小主数据，再建合同 ——
  这样合同的新建路径在界面上才是真正可用的，30 条用例才有执行前提。
  这一条会如实写进测试报告与存档，不当作缺陷上报。

【构造方式：全程走真实接口，不直接 INSERT】
  合同  : POST /contract/contract/add 、POST /contract/purchase/add
  主数据: POST /customer/customer/add 、POST /contract/product/add 、
          POST /contract/supplier/add 、POST /contract/services/add
  状态推进（自由审批模式，与界面 check_type=1 的流程走的是同一条路径）：
      POST /api/check/submit_check   {check_name, action_id, flow_id, check_uids}
      POST /api/check/flow_check     {check_name, action_id, check_flow_id, check:1|2|3|4, ...}
      POST /contract/api/contract_archive|contract_stop|contract_tovoid {id, *_status:1}
  为什么走接口而不是直接 INSERT：
      ① 走接口才会生成 content 的 serialize、check_* 字段、oa_flow_step/oa_flow_record、
         oa_admin_log 等联动数据，与"界面建单"完全一致；
      ② 直接 INSERT 出来的数据一看就假（缺 check_flow_id、缺审计日志）。

【关于「自由审批模式」—— 它就是这个实例界面的正常路径，不是绕过】
  19 条 oa_flow 的 flow_list 全部为空（LENGTH=0），check_type 全部为 1。
  界面 oaCheck.js 第 429 行：check_type == 1 时渲染「审批人选择器」(uidsTemplate)，
  提交时带上 check_uids —— 对应 Check::submit_check() 第 169 行的**自由审批模式**分支。
  即：本实例下"选审批人再提交"就是界面给出的正常操作，非自由模式反而会因为
  flow_list 为空而报「审批流程设置有问题」。

【幂等】合同按 code 判重（HT-TEST-xxx / CG-TEST-xxx），主数据按名称判重；已存在则跳过。

【用法】
  python 03_接口测试\\setup_contract_data.py                 # dry-run：只打印计划与现状
  python 03_接口测试\\setup_contract_data.py --apply         # 真正构造
  python 03_接口测试\\setup_contract_data.py --apply --limit 1   # 只建第 1 条（验证链路）

【回收】数据全部带 HT-TEST- / CG-TEST- / 【测试】 标记，回收脚本 cleanup_contract_data.py
"""
import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import date, timedelta

import requests

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from oa_login import BASE_URL, login  # noqa: E402
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

WSL_DISTRO = oa_config.WSL_DISTRO
DB = oa_config.DB_NAME
MYSQL = oa_config.MYSQL_NB

TODAY = date.today()
MARK = "HT-TEST"   # 销售合同编码前缀
MARK2 = "CG-TEST"  # 采购合同编码前缀


# ---------------------------------------------------------------------------
# SQL（经 wsl + base64 传输：PHP/SQL 里的 $ 与引号经 wsl.exe 命令行会被吞）
# ---------------------------------------------------------------------------
def run_sql(sql):
    # 临时文件名带 pid：早期写死 /tmp/oa_c.sql，两个进程同时跑会互相覆盖 ——
    # 实测（pytest 并行开发时）出现「一个进程的 SQL 文件被另一个进程改写」，
    # 报错文本里混入无关语句、查询结果串到别的模块。带 pid 即可根除。
    tmp = "/tmp/oa_c_%d.sql" % os.getpid()
    b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    shell = ("echo %s | base64 -d > %s && %s < %s; rc=$?; "
             "rm -f %s; exit $rc" % (b64, tmp, MYSQL, tmp, tmp))
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", shell],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if out.returncode != 0 or "ERROR" in (out.stderr or ""):
        raise RuntimeError("SQL 执行失败:\nstdout=%s\nstderr=%s" % (out.stdout, out.stderr))
    return (out.stdout or "").strip()


def rows(sql):
    txt = run_sql(sql)
    return [line.split("\t") for line in txt.splitlines() if line != ""]


def d(offset):
    """相对今天的日期字符串，offset 为天（负=过去）"""
    return (TODAY + timedelta(days=offset)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# HTTP 封装
# ---------------------------------------------------------------------------
_SESS = {}


def sess_of(username):
    """按用户名缓存会话 —— 每个账号只登录一次

    注意：登录本身是被审计的动作（add_log 会写一条 type=login 的 oa_admin_log）。
    早期版本在 dry-run 的循环里也对每个规格登录一次，导致一次 dry-run 就产生 13 条
    无意义的登录日志（uid=5 在 12 秒内登录 13 次）。这里改成"按需 + 缓存"，
    并且 dry-run 完全不调用本函数。
    """
    if username not in _SESS:
        pwd = oa_config.ADMIN_PWD if username == "admin" else oa_config.USER_PWD
        _SESS[username] = login(username, pwd)
        print("    [登录] %s" % username)
    return _SESS[username]


def api(sess, path, data=None, method="POST"):
    url = BASE_URL + path
    if method == "POST":
        r = sess.post(url, data=data, timeout=30)
    else:
        r = sess.get(url, params=data, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": (r.text or "")[:200]}


def ok(res):
    return res.get("code") == 0


def ret_id(res):
    """取新增返回的主键 —— data 有时是 {'return_id':N}，有时是 []（不同控制器写法不一）"""
    data = res.get("data")
    if isinstance(data, dict):
        return data.get("return_id")
    return None


# ---------------------------------------------------------------------------
# 主数据规格（全部带【测试】标记）
# ---------------------------------------------------------------------------
# 说明：Customer 模型 add()（app/customer/model/Customer.php）在插入后会用
# c_name/c_mobile/c_sex/c_qq/c_wechat/c_email 组装一条 oa_customer_contact 联系人记录，
# 少传 c_qq 会直接抛 "Undefined array key \"c_qq\""（实测）—— 故下列字段一个都不能省。
CUSTOMERS = [
    {"name": "【测试】客户-甲公司", "c_name": "张客户", "c_mobile": "13700000001",
     "c_sex": 1, "c_qq": "10000001", "c_wechat": "wx_test_01", "c_email": "cus1@test.local",
     "grade_id": 0, "industry_id": 0, "source_id": 0,
     "address": "测试省测试市甲路 1 号", "remark": "合同模块测试数据（可回收）"},
    {"name": "【测试】客户-乙公司", "c_name": "李客户", "c_mobile": "13700000002",
     "c_sex": 2, "c_qq": "10000002", "c_wechat": "wx_test_02", "c_email": "cus2@test.local",
     "grade_id": 0, "industry_id": 0, "source_id": 0,
     "address": "测试省测试市乙路 2 号", "remark": "合同模块测试数据（可回收）"},
    {"name": "【测试】客户-丙公司", "c_name": "王客户", "c_mobile": "13700000003",
     "c_sex": 1, "c_qq": "10000003", "c_wechat": "wx_test_03", "c_email": "cus3@test.local",
     "grade_id": 0, "industry_id": 0, "source_id": 0,
     "address": "测试省测试市丙路 3 号", "remark": "合同模块测试数据（可回收）"},
]

PRODUCTS = [
    {"title": "【测试】产品-A型服务器", "code": "CP-TEST-001", "unit": "台", "specs": "2U/64G",
     "sale_price": "12000.00", "base_price": "9000.00", "purchase_price": "8500.00",
     "cate_id": 1, "content": "合同模块测试数据（可回收）"},
    {"title": "【测试】产品-B型工作站", "code": "CP-TEST-002", "unit": "台", "specs": "塔式/32G",
     "sale_price": "6800.00", "base_price": "5000.00", "purchase_price": "4600.00",
     "cate_id": 1, "content": "合同模块测试数据（可回收）"},
    {"title": "【测试】产品-C型存储阵列", "code": "CP-TEST-003", "unit": "套", "specs": "24 盘位",
     "sale_price": "45000.00", "base_price": "33000.00", "purchase_price": "31000.00",
     "cate_id": 2, "content": "合同模块测试数据（可回收）"},
]

SERVICES = [
    {"title": "【测试】服务-系统实施", "price": "30000.00", "sort": 0},
    {"title": "【测试】服务-年度运维", "price": "18000.00", "sort": 0},
]

SUPPLIERS = [
    {"title": "【测试】供应商-甲公司", "c_name": "赵供应", "c_mobile": "13600000001", "c_sex": 1,
     "phone": "010-00000001", "email": "sup1@test.local", "address": "测试省测试市供甲路 1 号",
     "content": "合同模块测试数据（可回收）"},
    {"title": "【测试】供应商-乙公司", "c_name": "钱供应", "c_mobile": "13600000002", "c_sex": 2,
     "phone": "010-00000002", "email": "sup2@test.local", "address": "测试省测试市供乙路 2 号",
     "content": "合同模块测试数据（可回收）"},
]


# ---------------------------------------------------------------------------
# 合同规格
#   kind      : contract(销售) / purchase(采购)
#   types     : 1 普通 / 2 产品（采购为「物品」）/ 3 服务
#   cost      : 金额，覆盖 <1万 / 1~10万 / 10~100万 / >100万 四个区间
#   span      : (开始偏移天, 结束偏移天) —— 覆盖 已到期 / 即将到期(≤30天) / 未到期
#   flow      : 状态推进动作序列
#               submit=提交审批  approve=审批通过  reject=审批不通过
#               withdraw=撤回    archive=归档      stop=终止   void=作废
# ---------------------------------------------------------------------------
SPECS = [
    # ---------------- 销售合同 12 条 ----------------
    dict(kind="contract", code=MARK + "-001", types=1, cate_id=1, cost="5000.00",
         span=(-30, 335), owner="yucixin", customer=0, flow=[],
         name="【测试】销售合同-普通-小额未到期-待提交"),
    dict(kind="contract", code=MARK + "-002", types=1, cate_id=1, cost="50000.00",
         span=(0, 730), owner="yucixin", customer=1, flow=["submit"],
         name="【测试】销售合同-普通-中额未到期-审批中"),
    dict(kind="contract", code=MARK + "-003", types=1, cate_id=2, cost="500000.00",
         span=(-700, -30), owner="yucixin", customer=0, flow=["submit", "approve"],
         name="【测试】销售合同-普通-大额已到期-审批通过"),
    dict(kind="contract", code=MARK + "-004", types=1, cate_id=1, cost="1200000.00",
         span=(-330, 20), owner="yucixin", customer=1, flow=["submit", "approve", "archive"],
         name="【测试】销售合同-普通-百万即将到期-已归档"),
    dict(kind="contract", code=MARK + "-005", types=2, cate_id=1, cost="88000.00",
         span=(0, 365), owner="yucixin", customer=2, flow=["submit", "reject"],
         name="【测试】销售合同-产品-中额未到期-审批不通过"),
    dict(kind="contract", code=MARK + "-006", types=2, cate_id=2, cost="300000.00",
         span=(-400, -10), owner="yucixin", customer=0, flow=["submit", "withdraw"],
         name="【测试】销售合同-产品-大额已到期-已撤回"),
    dict(kind="contract", code=MARK + "-007", types=3, cate_id=1, cost="12000.00",
         span=(0, 180), owner="admin", customer=1, flow=["submit", "approve", "stop"],
         name="【测试】销售合同-服务-小额未到期-已终止"),
    dict(kind="contract", code=MARK + "-008", types=3, cate_id=1, cost="999999.99",
         span=(-300, 25), owner="admin", customer=2, flow=["submit", "approve", "void"],
         name="【测试】销售合同-服务-近百万即将到期-已作废"),
    dict(kind="contract", code=MARK + "-009", types=1, cate_id=2, cost="1.00",
         span=(0, 400), owner="admin", customer=0, flow=[],
         name="【测试】销售合同-普通-最小金额边界-待提交"),
    dict(kind="contract", code=MARK + "-010", types=1, cate_id=1, cost="99999999.99",
         span=(0, 1825), owner="admin", customer=1, flow=["submit"],
         name="【测试】销售合同-普通-最大金额边界-审批中"),
    dict(kind="contract", code=MARK + "-011", types=2, cate_id=1, cost="66666.66",
         span=(0, 90), owner="yucixin", customer=2, flow=["submit", "approve"],
         name="【测试】销售合同-产品-中额未到期-审批通过"),
    dict(kind="contract", code=MARK + "-012", types=1, cate_id=2, cost="20000.00",
         span=(-10, 1800), owner="yucixin", customer=0, flow=["submit", "approve"],
         name="【测试】销售合同-普通-长期五年-审批通过"),

    # ---------------- 采购合同 8 条 ----------------
    dict(kind="purchase", code=MARK2 + "-001", types=1, cate_id=1, cost="8000.00",
         span=(-20, 200), owner="yucixin", supplier=0, flow=[],
         name="【测试】采购合同-普通-小额未到期-待提交"),
    dict(kind="purchase", code=MARK2 + "-002", types=1, cate_id=1, cost="120000.00",
         span=(0, 365), owner="yucixin", supplier=1, flow=["submit"],
         name="【测试】采购合同-普通-中额未到期-审批中"),
    dict(kind="purchase", code=MARK2 + "-003", types=2, cate_id=2, cost="45000.00",
         span=(-500, -20), owner="yucixin", supplier=0, flow=["submit", "approve"],
         name="【测试】采购合同-物品-中额已到期-审批通过"),
    dict(kind="purchase", code=MARK2 + "-004", types=2, cate_id=1, cost="260000.00",
         span=(-350, 10), owner="yucixin", supplier=1, flow=["submit", "approve", "archive"],
         name="【测试】采购合同-物品-大额即将到期-已归档"),
    dict(kind="purchase", code=MARK2 + "-005", types=3, cate_id=1, cost="15000.00",
         span=(0, 120), owner="yucixin", supplier=0, flow=["submit", "reject"],
         name="【测试】采购合同-服务-小额未到期-审批不通过"),
    dict(kind="purchase", code=MARK2 + "-006", types=1, cate_id=2, cost="3333333.33",
         span=(0, 1095), owner="admin", supplier=1, flow=["submit", "withdraw"],
         name="【测试】采购合同-普通-超大额未到期-已撤回"),
    dict(kind="purchase", code=MARK2 + "-007", types=2, cate_id=1, cost="7500.00",
         span=(-600, -60), owner="admin", supplier=0, flow=["submit", "approve", "stop"],
         name="【测试】采购合同-物品-小额已到期-已终止"),
    dict(kind="purchase", code=MARK2 + "-008", types=1, cate_id=2, cost="1000000.00",
         span=(-5, 1825), owner="admin", supplier=1, flow=["submit", "approve", "void"],
         name="【测试】采购合同-普通-百万长期-已作废"),
]

KIND_CFG = {
    "contract": dict(cate_name="contract", cate_title="销售合同", flow_cate_id=13,
                     add_url="/contract/contract/add", api_prefix="/contract/api",
                     archive="contract_archive", stop="contract_stop", tovoid="contract_tovoid"),
    "purchase": dict(cate_name="purchase", cate_title="采购合同", flow_cate_id=14,
                     add_url="/contract/purchase/add", api_prefix="/contract/api",
                     archive="purchase_archive", stop="purchase_stop", tovoid="purchase_tovoid"),
}


# ---------------------------------------------------------------------------
def uid_of(username):
    r = rows("SELECT id FROM oa_admin WHERE username='%s' LIMIT 1;" % username)
    return int(r[0][0]) if r else None


def existing_codes():
    r = rows("SELECT code FROM oa_contract WHERE code LIKE '%s-%%' "
             "UNION SELECT code FROM oa_purchase WHERE code LIKE '%s-%%';" % (MARK, MARK2))
    return {x[0] for x in r}


def baseline():
    r = rows("SELECT (SELECT COUNT(*) FROM oa_contract),(SELECT COUNT(*) FROM oa_purchase),"
             "(SELECT COUNT(*) FROM oa_customer),(SELECT COUNT(*) FROM oa_product),"
             "(SELECT COUNT(*) FROM oa_supplier),(SELECT COUNT(*) FROM oa_services),"
             "(SELECT COUNT(*) FROM oa_admin_log),(SELECT COUNT(*) FROM oa_flow_step),"
             "(SELECT COUNT(*) FROM oa_flow_record),(SELECT COUNT(*) FROM oa_msg);")
    keys = ["oa_contract", "oa_purchase", "oa_customer", "oa_product", "oa_supplier",
            "oa_services", "oa_admin_log", "oa_flow_step", "oa_flow_record", "oa_msg"]
    return dict(zip(keys, r[0]))


def build_master_data(sess, apply_):
    """补最小主数据：客户 / 产品 / 服务 / 供应商"""
    created = {}
    plan = []

    # 客户
    have = {x[0] for x in rows("SELECT name FROM oa_customer WHERE name LIKE '【测试】%%';")}
    for c in CUSTOMERS:
        if c["name"] in have:
            plan.append(("客户", c["name"], "已存在，跳过"))
            continue
        plan.append(("客户", c["name"], "新建"))
        if apply_:
            sc, res = api(sess, "/customer/customer/add", dict(c, scene="add"))
            if not ok(res):
                raise RuntimeError("建客户失败 %s -> %s %s" % (c["name"], sc, res))
            created.setdefault("customer", []).append(ret_id(res))

    # 产品
    have = {x[0] for x in rows("SELECT title FROM oa_product WHERE title LIKE '【测试】%%';")}
    for p in PRODUCTS:
        if p["title"] in have:
            plan.append(("产品", p["title"], "已存在，跳过"))
            continue
        plan.append(("产品", p["title"], "新建"))
        if apply_:
            sc, res = api(sess, "/contract/product/add", p)
            if not ok(res):
                raise RuntimeError("建产品失败 %s -> %s %s" % (p["title"], sc, res))
            created.setdefault("product", []).append(ret_id(res))

    # 服务
    have = {x[0] for x in rows("SELECT title FROM oa_services WHERE title LIKE '【测试】%%';")}
    for s in SERVICES:
        if s["title"] in have:
            plan.append(("服务", s["title"], "已存在，跳过"))
            continue
        plan.append(("服务", s["title"], "新建"))
        if apply_:
            sc, res = api(sess, "/contract/services/add", s)
            if not ok(res):
                raise RuntimeError("建服务失败 %s -> %s %s" % (s["title"], sc, res))
            created.setdefault("service", []).append(ret_id(res))

    # 供应商
    have = {x[0] for x in rows("SELECT title FROM oa_supplier WHERE title LIKE '【测试】%%';")}
    for s in SUPPLIERS:
        if s["title"] in have:
            plan.append(("供应商", s["title"], "已存在，跳过"))
            continue
        plan.append(("供应商", s["title"], "新建"))
        if apply_:
            sc, res = api(sess, "/contract/supplier/add", s)
            if not ok(res):
                raise RuntimeError("建供应商失败 %s -> %s %s" % (s["title"], sc, res))
            created.setdefault("supplier", []).append(ret_id(res))

    return plan, created


def master_ids():
    """取本次要用的主数据 id（按名称精确取，避免依赖自增顺序）"""
    ids = {}
    ids["customer"] = [int(x[0]) for x in rows(
        "SELECT id FROM oa_customer WHERE name LIKE '【测试】%%' ORDER BY id;")]
    ids["product"] = [int(x[0]) for x in rows(
        "SELECT id FROM oa_product WHERE title LIKE '【测试】%%' ORDER BY id;")]
    ids["service"] = [int(x[0]) for x in rows(
        "SELECT id FROM oa_services WHERE title LIKE '【测试】%%' ORDER BY id;")]
    ids["supplier"] = [int(x[0]) for x in rows(
        "SELECT id FROM oa_supplier WHERE title LIKE '【测试】%%' ORDER BY id;")]
    return ids


def contract_payload(spec, ids, sess_uid, did):
    """组装 add 接口的表单参数（字段名与 add.html 完全一致）"""
    kind = spec["kind"]
    s, e = spec["span"]
    p = {
        "scene": "add",
        "name": spec["name"],
        "code": spec["code"],
        "types": spec["types"],
        "cate_id": spec["cate_id"],
        "cost": spec["cost"],
        "subject_id": "1",                      # 签约主体公司 = oa_enterprise id=1
        "did": did,
        "sign_department": "测试部门",
        "sign_uid": sess_uid,
        "sign_name": "",
        "prepared_uid": sess_uid,
        "prepared_name": "",
        "keeper_uid": sess_uid,
        "keeper_name": "",
        "sign_time": d(s - 3),
        "start_time": d(s),
        "end_time": d(e),
        "is_tax": 0,
        "tax": "0.00",
        "file_ids": "",
        "share_ids": "",
        "remark": "【测试】合同模块测试数据，由 setup_contract_data.py 经接口构造，可回收",
        "content": "",
    }
    if kind == "contract":
        cid = ids["customer"][spec.get("customer", 0) % max(len(ids["customer"]), 1)] \
            if ids["customer"] else 0
        p["customer_id"] = cid
        p["customer"] = "【测试】客户"
        p["contact_name"] = "测试联系人"
        p["contact_mobile"] = "13700000009"
        p["contact_address"] = "测试省测试市测试路 9 号"
        p["chance_id"] = 0
        if spec["types"] == 2:
            pid = ids["product"][0] if ids["product"] else 0
            p["product_title[]"] = "【测试】产品-A型服务器"
            p["product_id[]"] = str(pid)
            p["product_unit[]"] = "台"
            p["product_specs[]"] = "2U/64G"
            p["product_price[]"] = spec["cost"]
            p["product_num[]"] = "1"
            p["product_subtotal[]"] = spec["cost"]
            p["product_remark[]"] = "测试行"
        if spec["types"] == 3:
            sid = ids["service"][0] if ids["service"] else 0
            p["service_title[]"] = "【测试】服务-系统实施"
            p["service_id[]"] = str(sid)
            p["service_date[]"] = d(s) + "到" + d(e)
            p["service_price[]"] = spec["cost"]
            p["service_num[]"] = "1"
            p["service_subtotal[]"] = spec["cost"]
            p["service_remark[]"] = "测试行"
    else:  # purchase
        sup = ids["supplier"][spec.get("supplier", 0) % max(len(ids["supplier"]), 1)] \
            if ids["supplier"] else 0
        p["supplier_id"] = sup
        p["supplier"] = "【测试】供应商"
        p["contact_name"] = "测试供应商代表"
        p["contact_mobile"] = "13600000009"
        p["contact_address"] = "测试省测试市供货路 9 号"
        if spec["types"] == 2:
            pid = ids["product"][1] if len(ids["product"]) > 1 else (
                ids["product"][0] if ids["product"] else 0)
            p["purchased_title[]"] = "【测试】产品-B型工作站"
            p["purchased_id[]"] = str(pid)
            p["purchased_unit[]"] = "台"
            p["purchased_specs[]"] = "塔式/32G"
            p["purchased_price[]"] = spec["cost"]
            p["purchased_num[]"] = "1"
            p["purchased_subtotal[]"] = spec["cost"]
            p["purchased_remark[]"] = "测试行"
        if spec["types"] == 3:
            p["service_title[]"] = "【测试】服务-年度运维"
            p["service_date[]"] = d(s) + "到" + d(e)
            p["service_price[]"] = spec["cost"]
            p["service_num[]"] = "1"
            p["service_subtotal[]"] = spec["cost"]
            p["service_remark[]"] = "测试行"
    return p


# ---------------------------------------------------------------------------
# 状态推进
# ---------------------------------------------------------------------------
def flow_id_of(sess, kind):
    cfg = KIND_CFG[kind]
    sc, res = api(sess, "/api/check/get_flows", {"check_name": cfg["cate_name"]}, method="GET")
    if not ok(res) or not res.get("data"):
        raise RuntimeError("取审批流失败 %s -> %s %s" % (kind, sc, res))
    return int(res["data"][0]["id"])


def do_submit(sess, kind, cid, approver_uid):
    cfg = KIND_CFG[kind]
    fid = flow_id_of(sess, kind)
    sc, res = api(sess, "/api/check/submit_check", {
        "check_name": cfg["cate_name"], "action_id": cid, "flow_id": fid,
        "check_uids": str(approver_uid), "check_copy_uids": "",
    })
    return fid, sc, res


def do_flow_check(sess, kind, cid, fid, check, content="", node=1, next_uids=""):
    cfg = KIND_CFG[kind]
    return api(sess, "/api/check/flow_check", {
        "check_name": cfg["cate_name"], "action_id": cid, "check_flow_id": fid,
        "check": check, "check_node": node, "check_uids": next_uids,
        "check_files": "", "content": content,
    })


def do_flag(sess, kind, cid, action):
    """归档 / 终止 / 作废 —— 三个动作的接口名、参数名都不一致，这里统一映射

    接口：/contract/api/contract_archive | contract_stop | contract_tovoid
    参数：{id, archive_status} | {id, stop_status, stop_remark} | {id, void_status, void_remark}
    """
    cfg = KIND_CFG[kind]
    api_name = {"archive": cfg["archive"], "stop": cfg["stop"], "void": cfg["tovoid"]}[action]
    key = {"archive": "archive_status", "stop": "stop_status", "void": "void_status"}[action]
    path = "%s/%s" % (cfg["api_prefix"], api_name)
    return api(sess, path, {"id": cid, key: 1, key.replace("_status", "_remark"): "【测试】" + action})


def readback(table, cid):
    r = rows("SELECT check_status,check_flow_id,check_step_sort,check_uids,check_history_uids,"
             "admin_id,archive_time,stop_time,void_time,cost,types,did,sign_uid,"
             "FROM_UNIXTIME(start_time,'%%Y-%%m-%%d'),FROM_UNIXTIME(end_time,'%%Y-%%m-%%d'),"
             "LENGTH(content) FROM oa_%s WHERE id=%d;" % (table, cid))
    if not r:
        return None
    k = ["check_status", "check_flow_id", "check_step_sort", "check_uids", "check_history_uids",
         "admin_id", "archive_time", "stop_time", "void_time", "cost", "types", "did", "sign_uid",
         "start", "end", "content_len"]
    return dict(zip(k, r[0]))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条合同规格")
    args = ap.parse_args()

    print("=" * 78)
    print("勾股OA 合同模块测试数据构造%s" % ("" if args.apply else "（dry-run，不写入）"))
    print("=" * 78)

    base = baseline()
    print("基线（构造前）：")
    for k, v in base.items():
        print("    %-16s = %s" % (k, v))
    print("    今天 = %s" % TODAY)

    admin = sess_of("admin")

    # ---- 阶段 1：主数据 ----
    print("-" * 78)
    print("阶段 1：最小主数据（客户 / 产品 / 服务 / 供应商）")
    plan, created = build_master_data(admin, args.apply)
    for t, n, act in plan:
        print("    [%s] %-24s %s" % (t, n, act))
    ids = master_ids()
    print("    主数据 id：客户=%s 产品=%s 服务=%s 供应商=%s"
          % (ids["customer"], ids["product"], ids["service"], ids["supplier"]))
    if args.apply and not all(ids[k] for k in ("customer", "product", "service", "supplier")):
        raise RuntimeError("主数据不完整，无法继续（客户/产品/服务/供应商都至少要有一条）")

    # ---- 阶段 2：合同 ----
    print("-" * 78)
    print("阶段 2：合同（销售 %d 条 / 采购 %d 条）"
          % (sum(1 for s in SPECS if s["kind"] == "contract"),
             sum(1 for s in SPECS if s["kind"] == "purchase")))
    have = existing_codes()
    specs = SPECS[:args.limit] if args.limit else SPECS

    uid = {"admin": uid_of("admin"), "yucixin": uid_of("yucixin")}
    did = {"admin": 1, "yucixin": 3}
    print("    归属账号：admin(id=%s, did=1)  yucixin(id=%s, did=3)" % (uid["admin"], uid["yucixin"]))

    made = []
    for spec in specs:
        if spec["code"] in have:
            print("    [跳过] %-14s 已存在" % spec["code"])
            continue
        owner = spec["owner"]
        approver = "admin" if owner != "admin" else "yucixin"
        payload = contract_payload(spec, ids, uid[owner], did[owner])
        if not args.apply:
            print("    [计划] %-14s %-9s types=%d cost=%-12s %s~%s  归属=%-8s 状态流=%s"
                  % (spec["code"], spec["kind"], spec["types"], spec["cost"],
                     payload["start_time"], payload["end_time"], owner,
                     "→".join(spec["flow"]) if spec["flow"] else "（不提交）"))
            continue

        sess = sess_of(owner)
        sc, res = api(sess, KIND_CFG[spec["kind"]]["add_url"], payload)
        if not ok(res):
            print("    [失败] %-14s 建单被拒 -> HTTP %s %s" % (spec["code"], sc, res))
            continue
        cid = res["data"]["return_id"]
        print("    [建单] %-14s id=%-5s cost=%-12s %s  归属=%s(uid=%s)"
              % (spec["code"], cid, spec["cost"],
                 spec["flow"] and "→".join(spec["flow"]) or "（不提交）", owner, uid[owner]))

        fid = 0
        errs = []
        for act in spec["flow"]:
            if act == "submit":
                fid, sc, r = do_submit(sess, spec["kind"], cid, uid[approver])
            elif act == "approve":
                sc, r = do_flow_check(sess_of(approver), spec["kind"], cid, fid, 1, "同意")
            elif act == "reject":
                sc, r = do_flow_check(sess_of(approver), spec["kind"], cid, fid, 2, "【测试】不同意")
            elif act == "withdraw":
                sc, r = do_flow_check(sess, spec["kind"], cid, fid, 3, "【测试】撤回")
            else:
                sc, r = do_flag(admin, spec["kind"], cid, act)
            status = "OK " if ok(r) else "ERR"
            print("           %-9s %s HTTP %s %s" % (act, status, sc, r.get("msg", r)))
            if not ok(r):
                errs.append("%s:%s" % (act, r.get("msg")))
        rb = readback(spec["kind"], cid)
        print("           回读 %s" % json.dumps(rb, ensure_ascii=False))
        made.append(dict(spec=spec, id=cid, readback=rb, errs=errs))

    print("=" * 78)
    if not args.apply:
        print("dry-run 结束：未写入任何数据。加 --apply 执行。")
        print("=" * 78)
        return

    after = baseline()
    print("构造后统计：")
    for k in base:
        print("    %-16s %s -> %s" % (k, base[k], after[k]))
    print("    本次建单成功 %d 条，其中状态推进报错 %d 条"
          % (len(made), sum(1 for m in made if m["errs"])))
    for m in made:
        if m["errs"]:
            print("      · %s 失败动作：%s" % (m["spec"]["code"], m["errs"]))
    print("=" * 78)


if __name__ == "__main__":
    main()
