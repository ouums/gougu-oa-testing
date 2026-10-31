# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 —— 薪资管理模块接口自动化（XZ-001 ~ XZ-015，共 15 条）

【与执行器的关系】
  请求参数、账号、判据全部取自一次性执行器 `03_接口测试/run_functional_cases_salary.py`
  （需求的权威来源），结论必须与 `case_results_salary.json` 一致：10 通过 / 5 失败。
  **不复用执行器的"结论"字符串** —— 这里自己发请求、自己回读库、自己比较。
  5 条已知缺陷用例标 xfail(strict=True) + known_defect，断言内部写「正确行为」：
  缺陷在时记 xfailed（保留失败证据），缺陷修复后会 XPASS 并直接判 FAILED（活的回归探针）。

【批次独占与顺序共享（本模块不支持 -k 单条运行）】
  夹具批次 A(1000) 已发放，B~J(1001~1009) 待发放，每个用例独占一个批次 —— 理由是
  RecordsValidate.checkOne 的唯一性判据是 (salary_id, uid)，且写操作会触发 update_salary()
  重算批次汇总，共用批次会互相污染期望值（见 setup_salary_data.py:33-36）。
  两处例外，与执行器同构，故**必须整文件顺序执行**：
    · 批次 I(1008) 由 XZ-011 / 012 / 013 顺序累加（1 条 → 3 条 → 7 条）
    · 批次 J(1009) 由 XZ-014 / 015 顺序接力（XZ-015 的删除对象由 XZ-014 留下）
  执行器 `main()` 同样以 `--only` 直接拒绝局部执行（run_functional_cases_salary.py:725-728）。

【夹具】15 条全部依赖薪资夹具（teststaff/110、组 5、职位 5、批次 1000~1009 + 明细 A 的 3 条）。
  模块级 session 级 autouse fixture 引用 conftest 的 `fixture_salary`：开跑前自动
  `setup_salary_data.py --apply`，跑完自动 `cleanup_salary_data.py --apply`。
  用 autouse 而非把签名写进 15 个函数，是为了保证**夹具先于任何 sess_* 登录建立**
  （`sess_teststaff` 要登录 teststaff，该账号由夹具创建；autouse 的 session 级 fixture
  在同一 scope 内先于显式请求的 fixture 建立，实测顺序见文件末注释）。

【禁止调用】全程不调用 `/adm/api/salary_pay`（唯一能把批次置为已发放的入口，会写站内信
  oa_msg 并经 FinanceLog 注入财务流水，属对外发行为）、不调用邮件外发与任何第三方接口。
  夹具的「已发放」形态由 SQL 复现，**发放动作本身不在测试范围内**。

【夹具阻断：曾存在，2026-09-21 已由框架侧修复，本注释随之更新】
  原状：`conftest._apply_fixture` 当时用 `need_marker="(uid=110，仅节点 266,267)"`（**半角括号**
  U+0028/U+0029）匹配 setup 脚本 stdout 来判断夹具是否落库，而 setup_salary_data.py 打印的是
  **全角括号**（U+FF08/U+FF09）—— 子串永远不命中 ⇒ 脚本 rc=0、数据确实已落库，`_apply_fixture`
  仍抛 RuntimeError；且该异常发生在 `yield` **之前**，finally 里的回收分支不执行，夹具会残留在库中。

  修法（框架侧，见 00_挂起存档.md §2.6 框架缺陷 #5）：**不再匹配 stdout 字符串**，改为传入
  `_verify_salary()` 直接**查库核对不变量**（批次 10 / 批次A明细 3 / teststaff(110) 1）。文案与
  编码从此与判定无关，全角半角之争不复存在；建立+回收同处 `try/finally`，建立失败也会回收。

  现行状态（本轮实测复验，不再是阻断）：`setup_salary_data.py --apply` 落库 10/3/1，
  `conftest._verify_salary()` 返回 True（"批次 10/10、批次A明细 3/3、teststaff(110) 1/1"）。
  故本模块**不做任何绕过**，`fixture_salary` 由框架按约定建立；此前在项目外用等价夹具取得的
  10 passed / 5 xfailed 与 case_results_salary.json 逐条一致，现由框架内夹具正式复现。

【框架未覆盖的两处能力（就地只读实现，**未改动框架**，详见各自 docstring）】
  · `K.call()` 只支持 GET/POST（其余方法一律走 sess.post）⇒ DELETE 动作见 `_delete()`
  · `K.call()` 对非 JSON 响应只保留前 120 字符 ⇒ 详情页 HTML 子串检索见 `_page()`
"""
from decimal import Decimal

import pytest

import oa_kit as K

# ---------------------------------------------------------------------------
# 标记与夹具
# ---------------------------------------------------------------------------
pytestmark = [pytest.mark.xz, pytest.mark.needs_salary_fixture]

STAFF = "teststaff"        # uid=110 组5，rules 仅 266,267（我的薪资-列表/详情）
STAFF_UID = 110
MGR = "fengcailing"        # uid=4   组2 管理岗，持 220 无 222
MGR_DEL = "yucixin"        # uid=5   组3 业务岗，额外持 222（records_del）

# 夹具批次（与 setup_salary_data.py:BATCH 一致）
BATCH = {"A": 1000, "B": 1001, "C": 1002, "D": 1003, "E": 1004,
         "F": 1005, "G": 1006, "H": 1007, "I": 1008, "J": 1009}

# 12 个工资奖金项 / 3 个应扣项 / 3 个代扣代缴项（与 records_add.html 的三个 class 分组一致）
BASIC = ["salary_basic", "salary_position", "salary_performance", "salary_quanqin",
         "salary_overwork", "salary_meal", "salary_phone", "salary_traffic",
         "salary_house", "salary_fuli", "salary_protecting", "salary_bonus"]
DEDUCT = ["deduct_belate", "deduct_leave", "deduct_absenteeism"]
STATUTORY = ["deduct_social", "deduct_gongjijin", "deduct_tax"]


@pytest.fixture(scope="session", autouse=True)
def _salary_fixture_autouse(fixture_salary):
    """模块级挂载薪资夹具：15 条用例全部依赖它，且必须早于 `sess_*` 登录。

    只在函数签名里请求 `fixture_salary` 会有序风险（`sess_teststaff` 是 session 级显式
    请求的 fixture，与 `fixture_salary` 同 scope）；session 级 autouse 保证它先建立。
    """
    return fixture_salary


# ---------------------------------------------------------------------------
# 本地辅助：请求与回读（判定一律走 oa_kit 的断言原语，口径与本套件其余模块一致）
# ---------------------------------------------------------------------------
def _rid(res):
    """取新增/编辑返回的主键：data 为 {'return_id': N}（不同控制器写法不一，故容错）"""
    data = res.get("data")
    return data.get("return_id") if isinstance(data, dict) else None


def _money(v):
    return str(Decimal(str(v)).quantize(Decimal("0.01")))


def _pay(**amts):
    """按 records_add.html:150-180 的 JS 算式补齐 22 个金额列。

    前端三组求和：total_payable=Σ12 工资奖金、total_deduction=Σ3 应扣、
    total_statutory=Σ3 代扣代缴、total_payment=应发-应扣-代扣代缴。
    复刻它只为让提交载荷与真实界面完全同形（服务端不重算，见 XZ-008 的缺陷）。
    """
    p = {k: "0.00" for k in BASIC + DEDUCT + STATUTORY}
    p.update({k: _money(v) for k, v in amts.items()})
    tp = sum(Decimal(p[k]) for k in BASIC)
    td = sum(Decimal(p[k]) for k in DEDUCT)
    ts = sum(Decimal(p[k]) for k in STATUTORY)
    p["total_payable"], p["total_deduction"], p["total_statutory"] = _money(tp), _money(td), _money(ts)
    p["total_payment"] = _money(tp - td - ts)
    return p


def _add_rec(sess, b, uid, who, rid=None, **amts):
    """新增/编辑一条工资明细：POST /adm/salary/records_add。

    传 rid 即 scene=edit（控制器按 `!empty($param['id'])` 分流，
    app/adm/controller/Salary.php:176-198），否则为 scene=add。who 仅用于失败报告的标签。
    """
    data = _pay(**amts)
    data["salary_id"] = b
    data["uid"] = uid
    if rid:
        data["id"] = rid
    res = K.call(sess, "/adm/salary/records_add", data=data)
    K.record("%s POST /adm/salary/records_add（批次 %s uid=%s%s）"
             % (who, b, uid, "，scene=edit" if rid else ""), res)
    return res, _rid(res)


def _delete(sess, path, params):
    """发一次 DELETE 请求，返回与 K.call 同形的结果。

    ⚠️ 框架能力缺口：`K.call()` 对非 GET 一律走 `sess.post()`，无法发 DELETE，
    而 /adm/salary/records_del 的动作前提正是 `request()->isDelete()`
    （app/adm/controller/Salary.php:117-123）。故就地发一次（只读该接口的返回结构写法，
    判定仍用 oa_kit 的断言原语），**未改动 oa_kit**。
    """
    r = sess.request("DELETE", K.BASE + path, params=params, timeout=30, allow_redirects=False)
    res = {"http": r.status_code}
    if r.status_code in (301, 302):
        res["location"] = r.headers.get("Location", "")
        return res
    try:
        j = r.json()
        res["code"] = j.get("code")
        res["msg"] = str(j.get("msg", ""))
    except Exception:                                       # noqa: BLE001
        res["body_head"] = (r.text or "")[:120].replace("\n", " ")
    return res


def _page(sess, path, params):
    """取页面原文（HTML），返回 (http, 原文)。

    ⚠️ 框架能力缺口：`K.call()` 对非 JSON 响应只保留 `body_head` 前 120 字符，
    页面类断言（XZ-002/003/004 要做姓名/金额/字段的子串检索）用不了它。
    """
    r = sess.get(K.BASE + path, params=params, timeout=30, allow_redirects=False)
    return r.status_code, (r.text or "")


def _db_summary(b):
    r = K.rows("SELECT amount,salary,social,gongjijin,tax,status FROM oa_salary WHERE id=%s;" % b)
    return dict(zip(("amount", "salary", "social", "gongjijin", "tax", "status"), r[0])) if r else {}


def _db_amount(b):
    return Decimal(K.scalar("SELECT amount FROM oa_salary WHERE id=%s;" % b, "0") or "0")


def _recompute(b):
    """用 Decimal 独立复算批次汇总（与 SalaryRecords::update_salary 的 where 一致：delete_time=0）

    算式见 app/adm/model/SalaryRecords.php:141-150：
      amount = Σtotal_payment + Σdeduct_social + Σdeduct_gongjijin + Σdeduct_tax
    **不采信接口返回值**，一律回读 oa_salary 复算。
    """
    r = K.rows("SELECT COALESCE(SUM(total_payment),0),COALESCE(SUM(deduct_social),0),"
               "COALESCE(SUM(deduct_gongjijin),0),COALESCE(SUM(deduct_tax),0) "
               "FROM oa_salary_records WHERE salary_id=%s AND delete_time=0;" % b)
    v = [Decimal(x) for x in r[0]]
    return sum(v), v


def _summary_case(b):
    """汇总类用例的公共收尾：返回 (库中 amount 是否等于 Decimal 复算值, 说明文本)"""
    amt, parts = _recompute(b)
    s = _db_summary(b)
    same = Decimal(s.get("amount") or "0") == amt
    return same, ("库中 amount=%s / salary=%s / social=%s / gongjijin=%s / tax=%s；"
                  "Decimal 复算 Σ实发=%s + Σ社保=%s + Σ公积金=%s + Σ个税=%s = %s"
                  % (s.get("amount"), s.get("salary"), s.get("social"), s.get("gongjijin"),
                     s.get("tax"), parts[0], parts[1], parts[2], parts[3], amt))


def _rec_count(b, uid=None):
    """有效（delete_time=0）明细条数"""
    w = "salary_id=%s" % b
    if uid is not None:
        w += " AND uid=%s" % uid
    return int(K.scalar("SELECT COUNT(*) FROM oa_salary_records WHERE %s AND delete_time=0;" % w))


def _my_list(sess, limit=200):
    """员工侧「我的薪资」列表：GET /oa/salary/datalist"""
    res = K.call(sess, "/oa/salary/datalist", method="GET", data={"page": 1, "limit": limit})
    K.record("%s GET /oa/salary/datalist" % STAFF, res)
    return res


def _uid_of(username):
    return K.scalar("SELECT id FROM oa_admin WHERE username='%s' LIMIT 1;" % username, "")


# ===========================================================================
# a. 工资条归属越权（XZ-001 ~ XZ-004）
# ===========================================================================
def test_xz_001_staff_datalist_only_self(sess_teststaff):
    """XZ-001 普通员工「我的薪资」列表只下发本人记录（界面侧受限）。

    判据出处：GET /oa/salary/datalist 的 where 为 delete_time=0 + uid=$this->uid + status=1
    （app/oa/controller/Salary.php:45-47，源码实读）。
    前置：夹具批次 A(1000) 已发放，含 uid 110/108/109 三条明细 —— 其中 108/109 与
    teststaff 同部门同级（did=3），排除「上级看下级」的正当性。
    本条同时充当测试计划 §2.1「UI 对照法」中「界面受限」的一半（另一半是 XZ-002）。
    关联缺陷：无（本条通过，是 XZ-002 的对照面）。
    """
    res = _my_list(sess_teststaff)
    K.expect(K.is_allowed(res), "普通员工本人的薪资列表应正常放行（而非被 405 拦掉）")
    lst = res.get("data") or []
    uids = sorted({int(x.get("uid") or 0) for x in lst})
    others = [x for x in lst if int(x.get("uid") or 0) != STAFF_UID]

    # 非空性 + 夹具前置：库里同批次确有 3 条（含同事），接口只下发本人的 1 条
    db_n = int(K.scalar("SELECT COUNT(*) FROM oa_salary_records "
                        "WHERE salary_id=%s AND delete_time=0;" % BATCH["A"]))
    K.expect((db_n == 3, "批次 A(1000) 下有效明细 %d 条（期望 3）" % db_n),
             "夹具前置：批次 A 下应有 uid 110/108/109 三条已发放明细")
    K.expect((bool(lst), "n=%s count=%s" % (res.get("n"), res.get("count"))),
             "本人应有至少 1 条已发放明细可读（否则本条无从判定）")

    detail = "；".join("id=%s uid=%s 月份=%s 状态=%s 实发=%s"
                      % (x.get("id"), x.get("uid"), x.get("month_time"),
                         x.get("status_name"), x.get("total_payment")) for x in lst)
    K.expect((not others and uids == [STAFF_UID],
              "列表返回 %d 条，uid 集合=%s（期望 [%d]）；他人记录 %d 条：%s；逐条：%s"
              % (len(lst), uids, STAFF_UID, len(others),
                 "；".join("id=%s uid=%s" % (x.get("id"), x.get("uid")) for x in others), detail)),
             "员工侧列表只能下发本人（uid=%d）的记录" % STAFF_UID)

    # 状态过滤：datalist 的 where 含 status=1 —— 返回项应全部为「已发放」
    bad_status = [x for x in lst if int(x.get("status") or 0) != 1]
    K.expect((not bad_status, "非已发放项 %d 条：%s"
              % (len(bad_status), [(x.get("id"), x.get("status")) for x in bad_status])),
             "员工侧列表只应包含已发放（status=1）的明细")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-19 员工侧工资条详情无归属校验（水平越权，一般 B）", strict=True)
def test_xz_002_staff_view_others_record(sess_teststaff):
    """XZ-002 普通员工能否越权读取同事的工资条详情（应被拒绝，实测可读 —— 缺陷）。

    判据出处：app/oa/controller/Salary.php:59 的 view($id) 直接 $this->model->getById($id)，
    既无 uid 判断也无 status 判断（对照同文件 :45-47 的 datalist 明确有 uid+status 双过滤）。
    满足测试计划 §2.1「UI 对照法」：界面受限（本人列表无任何指向他人明细的入口）而接口不受限。
    关联缺陷：BUG-19（一般 B）；关联证据：证据-14。
    本条断言写「正确行为」：越权读取必须被拒绝 —— 缺陷在时失败 → xfail；
    修复后通过 → XPASS → strict 模式下 FAILED，提醒回来销账。
    """
    targets = [("testemp", 109, 1002), ("testmin", 108, 1001)]   # 同部门同级同事的明细
    lines, leaked = [], []
    for _, tuid, rid in targets:
        st, html = _page(sess_teststaff, "/oa/salary/view", {"id": rid})
        K.record("teststaff GET /oa/salary/view?id=%s" % rid,
                 {"http": st, "body_head": html[:200].replace("\n", " ")})
        db_name = K.scalar("SELECT name FROM oa_admin WHERE id=%d;" % tuid, "")
        pay_val = K.scalar("SELECT total_payment FROM oa_salary_records WHERE id=%d;" % rid, "")
        hit_name = bool(db_name) and db_name in html
        hit_pay = bool(pay_val) and str(pay_val) in html
        lines.append("目标明细 id=%s（uid=%s %s）：HTTP %s，响应含受害者姓名=%s、含其实发工资 %s=%s"
                     % (rid, tuid, db_name, st, hit_name, pay_val, hit_pay))
        if hit_name or hit_pay:
            leaked.append("id=%s(uid=%s %s) 姓名命中=%s 实发命中=%s"
                          % (rid, tuid, db_name, hit_name, hit_pay))

    # 界面侧对照：本人列表里没有任何指向他人明细的入口（正确行为，缺陷在时亦成立）
    ui = _my_list(sess_teststaff)
    ui_ids = {str(x.get("id")) for x in (ui.get("data") or [])}
    ui_leak = [r for _, _, r in targets if str(r) in ui_ids]

    if leaked:
        detail = ("越权读取成立：%s。界面侧本人「我的薪资」列表返回 %d 条（id 集合 %s）、"
                  "不含同事明细 —— 界面受限而接口不受限，满足测试计划 §2.1 UI 对照法。逐条：%s"
                  % ("；".join(leaked), len(ui_ids), sorted(ui_ids), "；".join(lines)))
    else:
        detail = ("界面侧本人列表混入了同事明细 %s（接口侧亦未泄露）；逐条：%s"
                  % (ui_leak, "；".join(lines)))
    K.expect((not leaked and not ui_leak, detail),
             "员工 A 不得读到同事 B 的工资条详情（view 应带 uid/status 过滤）")


def test_xz_003_staff_view_does_not_expose_bank_fields(sess_teststaff):
    """XZ-003 越权响应中不含模板未渲染的敏感字段（后果边界界定，防夸大）。

    判据出处：app/oa/view/salary/view.html 渲染的 detail 字段清单里没有 bank / bank_no，
    故 BUG-19 的后果界定为「他人工资明细泄露」，**不含银行卡号泄露**。
    remark 一列模板确有渲染（view.html:84），作本条的**阳性对照** —— 证明检索方式本身有效、
    不是「搜什么都搜不到」。关联缺陷：无（本条通过，是 XZ-002 的防夸大控制项）。
    """
    rid = 1001                                     # 与 XZ-002 同一目标明细（uid=108）
    st, html = _page(sess_teststaff, "/oa/salary/view", {"id": rid})
    K.record("teststaff GET /oa/salary/view?id=%s" % rid,
             {"http": st, "body_head": html[:200].replace("\n", " ")})
    row = K.rows("SELECT bank,bank_no,remark FROM oa_salary_records WHERE id=%d;" % rid)
    bank, bank_no, remark = row[0] if row else ("", "", "")
    leaked = {k: v for k, v in (("bank", bank), ("bank_no", bank_no)) if v and v in html}
    rendered_ok = bool(remark) and remark in html

    K.expect((bool(html), "HTTP %s 响应长度 %d" % (st, len(html))),
             "应取到详情页响应原文（空响应会让下面的字段检索无意义）")
    K.expect((not leaked,
              "库中 bank=「%s」bank_no=「%s」，越权响应中出现了未渲染字段：%s（响应长度 %d）"
              % (bank, bank_no, leaked, len(html))),
             "越权响应中不得出现模板未渲染的 bank / bank_no（泄露范围不得夸大）")
    if "找不到页面" not in html:
        # 只在响应确实是详情页时才检阳性对照：一旦 BUG-19 被修复、view 返回 404 提示页，
        # 本条的前提（有一份越权响应原文）即消失，按执行器口径仍记通过。
        K.expect((rendered_ok,
                  "目标明细（id=%s）库中 remark=「%s」，模板渲染后未能在响应中检索到（响应长度 %d）"
                  % (rid, remark, len(html))),
                 "阳性对照：模板确实渲染的 remark 应能被检索到（证明检索方式有效）")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-19 员工侧工资条详情无归属校验（水平越权，一般 B）", strict=True)
def test_xz_004_own_unpaid_record_not_readable(sess_fengcailing, sess_teststaff):
    """XZ-004 本人未发放明细：列表与详情的 status 过滤口径应一致（实测不一致 —— 缺陷）。

    判据出处：datalist 过滤了 status=1（app/oa/controller/Salary.php:47），
    view($id) 未过滤（同文件 :59）—— 同一函数同时缺 uid 与 status 两道校验。
    前置：经接口在待发放批次 B(1001) 为 teststaff 建一条明细（真实接口，非 SQL）。
    关联缺陷：BUG-19（作为同一处缺失的佐证并入）；关联证据：证据-14。
    本条断言写「正确行为」：未发放明细既不在本人列表、也不可读详情。
    """
    b = BATCH["B"]
    status = K.scalar("SELECT status FROM oa_salary WHERE id=%s;" % b, "?")
    K.expect((status == "0", "批次 %s status=%s（期望 0 待发放）" % (b, status)),
             "夹具前置：批次 B 应为待发放")

    res, rid = _add_rec(sess_fengcailing, b, STAFF_UID, MGR, salary_basic="6000.00",
                        salary_position="1000.00", deduct_social="700.00",
                        deduct_gongjijin="500.00", deduct_tax="80.00")
    K.track("oa_salary_records", rid, note="XZ-004 未发放明细")
    K.expect((res.get("code") == 0 and rid is not None,
              "code=%s msg=%s rid=%s" % (res.get("code"), res.get("msg"), rid)),
             "前置：应能经 /adm/salary/records_add 新增一条未发放明细")
    rstatus = K.scalar("SELECT status FROM oa_salary_records WHERE id=%d;" % rid, "?")

    lst = (_my_list(sess_teststaff).get("data") or [])
    in_list = any(str(x.get("id")) == str(rid) for x in lst)
    st, html = _page(sess_teststaff, "/oa/salary/view", {"id": rid})
    K.record("teststaff GET /oa/salary/view?id=%s" % rid,
             {"http": st, "body_head": html[:200].replace("\n", " ")})
    readable = ("工资详细" in html) and ("找不到页面" not in html)

    K.expect((not in_list and not readable,
              "未发放明细（id=%s，status=%s）不在本人列表中（%d 条，命中=%s），"
              "但详情接口可读（HTTP %s，命中=%s）" % (rid, rstatus, len(lst), in_list, st, readable)),
             "本人未发放明细既不应当出现在列表中，也不应当可读（列表与详情 status 口径须一致）")


# ===========================================================================
# b. 工资项金额范围校验（XZ-005 ~ XZ-010）
# ===========================================================================
def test_xz_005_records_add_normal_values(sess_fengcailing):
    """XZ-005 正常值新增工资明细：接口可用、提交值逐项落库、批次汇总联动。

    判据出处：app/adm/controller/Salary.php:134-205 的 records_add 与
    app/adm/model/SalaryRecords.php:141-150 的 update_salary()。
    测试点：先证明接口可用，后续 b 组的失败用例才有归因意义。
    关联缺陷：无（本条通过）。批次 C(1002) 由本条独占。
    """
    b = BATCH["C"]
    uid = STAFF_UID
    before = _rec_count(b, uid)
    res, rid = _add_rec(sess_fengcailing, b, uid, MGR, salary_basic="8000.00",
                        salary_performance="2000.00", deduct_social="800.00",
                        deduct_gongjijin="600.00", deduct_tax="150.00")
    K.track("oa_salary_records", rid, note="XZ-005 正常值新增")
    K.expect((res.get("code") == 0 and rid is not None,
              "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "正常值新增应保存成功并返回新明细 id")

    want = {"salary_basic": "8000.00", "salary_performance": "2000.00", "deduct_social": "800.00",
            "deduct_gongjijin": "600.00", "deduct_tax": "150.00"}
    # 派生列独立期望：应发=Σ工资奖金、代扣代缴=Σ三项、实发=应发-应扣-代扣代缴
    want.update({"total_payable": "10000.00", "total_statutory": "1550.00", "total_payment": "8450.00"})
    cols = list(want)
    db = K.rows("SELECT %s FROM oa_salary_records WHERE id=%d;" % (",".join(cols), rid))[0]
    bad = {c: v for c, v in zip(cols, db) if v != want[c]}
    K.expect((not bad, "落库不符字段 %s（提交值 %s）" % (bad, want)),
             "五项提交值与三项派生值应逐项与库中实测一致")

    n_after = _rec_count(b, uid)
    K.expect((n_after == before + 1, "该批次该员工 %d→%d 条" % (before, n_after)),
             "新增后该批次该员工的明细条数应恰好 +1")
    rstatus = K.scalar("SELECT status FROM oa_salary_records WHERE id=%d;" % rid, "?")
    K.expect((rstatus == "0", "该行 status=%s" % rstatus),
             "新建明细的 status 应为 0（待发放）")

    same, detail = _summary_case(b)
    K.expect((same, detail), "批次汇总 amount 应等于 Σ实发+Σ社保+Σ公积金+Σ个税（Decimal 复算）")


def _negative_case(sess, bkey, amts, expect_cols, note):
    """b 组负值用例的公共实现：提交 → 回读 → 返回 (批次, 响应, 明细 id, 是否落库, 负值字段, 实测串)"""
    b = BATCH[bkey]
    res, rid = _add_rec(sess, b, STAFF_UID, MGR, **amts)
    K.track("oa_salary_records", rid, note=note)
    landed = rid is not None
    bad, detail = {}, ""
    if landed:
        vals = K.rows("SELECT %s FROM oa_salary_records WHERE id=%d;"
                      % (",".join(expect_cols), rid))[0]
        bad = {c: v for c, v in zip(expect_cols, vals) if Decimal(v) < 0}
        detail = "；".join("%s=%s" % (c, v) for c, v in zip(expect_cols, vals))
    return b, res, rid, landed, bad, detail


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-20 工资明细金额字段缺服务端范围校验（一般 C）", strict=True)
def test_xz_006_negative_basic_salary(sess_fengcailing):
    """XZ-006 基本工资 -8000.00：应被服务端范围校验拒绝（实测被接受并落库 —— 缺陷）。

    判据出处：app/adm/validate/RecordsValidate.php:28-31 的规则表只有 uid(require|checkOne)
    与 id(require)，20 多个金额字段一个都没校验；控制器 app/adm/controller/Salary.php:134-184
    只对空值置 0；前端 layui 的 number 规则是 isNaN(value)（form.js:31-35），
    isNaN('-8000.00')===false ⇒ 负数在界面上同样放行，属前后端均无校验。
    关联缺陷：BUG-20（一般 C）；关联证据：证据-15。批次 D(1003) 由本条独占。
    本条断言写「正确行为」：应被拒绝，或至少不得落库为负值。
    """
    cols = ["salary_basic", "total_payable", "total_payment"]
    b, res, rid, landed, bad, detail = _negative_case(
        sess_fengcailing, "D",
        dict(salary_basic="-8000.00", salary_position="2000.00", deduct_social="800.00",
             deduct_gongjijin="600.00", deduct_tax="150.00"), cols,
        note="XZ-006 负值基本工资（预期被拒）")
    K.expect((not landed or not bad,
              "负数基本工资被接受并落库：code=%s，id=%s；%s（负值字段 %s）"
              % (res.get("code"), rid, detail, bad)),
             "负数基本工资应被服务端范围校验拒绝（或被拒后不落库），批次 %s" % b)


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-20 工资明细金额字段缺服务端范围校验（一般 C）", strict=True)
def test_xz_007_negative_deduction_items(sess_fengcailing):
    """XZ-007 社保 -800.00 / 公积金 -600.00：应被服务端范围校验拒绝（实测被接受并落库 —— 缺陷）。

    判据出处：同 XZ-006（RecordsValidate 规则表无金额字段校验）。
    后果：负扣款经 update_salary() 的 Σdeduct_social / Σdeduct_gongjijin 反向推高批次汇总
    amount（算式见 XZ-011）。关联缺陷：BUG-20（一般 C）；关联证据：证据-15。
    批次 E(1004) 由本条独占。断言写「正确行为」：负扣款应被拒绝。
    """
    cols = ["deduct_social", "deduct_gongjijin", "total_statutory", "total_payment"]
    b, res, rid, landed, bad, detail = _negative_case(
        sess_fengcailing, "E",
        dict(salary_basic="8000.00", deduct_social="-800.00",
             deduct_gongjijin="-600.00", deduct_tax="150.00"), cols,
        note="XZ-007 负值社保/公积金（预期被拒）")
    K.expect((not landed or not bad,
              "负的扣款项被接受并落库：code=%s，id=%s；%s（负值字段 %s）"
              % (res.get("code"), rid, detail, bad)),
             "负数社保 / 公积金应被服务端范围校验拒绝，批次 %s" % b)


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-20 工资明细金额字段缺服务端范围校验（一般 C）", strict=True)
def test_xz_008_negative_total_payment(sess_fengcailing):
    """XZ-008 应发 5000.00 / 应扣 6000.00 ⇒ 实发 -1000.00：应被拒绝或由服务端重算（实测都没有 —— 缺陷）。

    判据出处：应发/应扣/代扣代缴/实发四列在界面上是 JS 算好后经 <input type="hidden">
    提交的（records_add.html:77-85 与 :150-180），**服务端只做入库、不重算也不校验**
    （app/adm/controller/Salary.php:130-205）—— 即客户端可直接指定实发额。
    关联缺陷：BUG-20（一般 C）；关联证据：证据-15。批次 F(1005) 由本条独占。
    断言写「正确行为」：不得存在实发为负的工资条（被拒绝，或重算后不为负）。
    """
    b = BATCH["F"]
    data = _pay(salary_basic="5000.00")
    data.update({"salary_id": b, "uid": STAFF_UID,
                 "total_payable": "5000.00", "total_deduction": "6000.00",
                 "total_payment": "-1000.00"})
    res = K.call(sess_fengcailing, "/adm/salary/records_add", data=data)
    K.record("fengcailing POST /adm/salary/records_add（人为指定实发 -1000.00，批次 %s）" % b, res)
    rid = _rid(res)
    K.track("oa_salary_records", rid, note="XZ-008 实发为负")
    landed = rid is not None
    got = K.scalar("SELECT total_payment FROM oa_salary_records WHERE id=%d;" % rid, "-") if landed else "-"
    K.expect((not landed or Decimal(got) >= 0,
              "实发为负的工资条被接受并落库：code=%s，id=%s，库中 total_payment=%s"
              % (res.get("code"), rid, got)),
             "实发为负的工资条应被拒绝，或由服务端按业务规则重算（批次 %s）" % b)


def test_xz_009_amount_over_db_column_range(sess_fengcailing):
    """XZ-009 基本工资 999999999.99（超 decimal(10,2) 上限）：应被拒绝且不写脏数据。

    判据出处：oa_salary_records.salary_basic 为 decimal(10,2)（8 位整数上限），
    999999999.99 为 9 位整数 ⇒ MySQL 1264 Out of range；SalaryRecords::add() 的 catch
    (app/adm/model/SalaryRecords.php:65-67) 把数据库报错原样回给客户端。
    本条为**对照组**：拦截来自数据库列宽而非业务规则，不计作业务校验生效 ——
    它恰恰反证服务端缺的是「业务范围校验」这一层（8 位以内的一切取值含负数全部放行，见 XZ-006/007）。
    另记观察项：数据库原始报错外泄（与 BUG-12 同族），不另开编号。
    关联缺陷：无（本条通过）。批次 G(1006) 由本条独占。
    """
    b = BATCH["G"]
    res, rid = _add_rec(sess_fengcailing, b, STAFF_UID, MGR, salary_basic="999999999.99")
    K.track("oa_salary_records", rid, note="XZ-009 超列宽（预期不落库）")
    n = int(K.scalar("SELECT COUNT(*) FROM oa_salary_records WHERE salary_id=%s;" % b))
    msg = str(res.get("msg") or "")
    db_err = ("SQLSTATE" in msg or "Out of range" in msg or "1264" in msg)

    K.expect(K.is_validation_error(res),
             "超列宽金额应被拒绝：闸门须放行、由业务/数据库层拒绝，HTTP 不得 500")
    K.expect((rid is None and n == 0,
              "return_id=%s，批次 %s 下落库 %d 行；msg=%s" % (rid, b, n, msg)),
             "被拒绝时不得写入任何错乱数据")
    # 观察项（不参与判定，仅进失败报告）：数据库原始报错是否外泄（与 BUG-12 同族）
    K.record("观察项：拦截来自数据库列宽而非业务规则；数据库原始报错外泄=%s" % db_err,
             {"http": res.get("http"), "code": res.get("code"), "msg": msg[:120]})


def test_xz_010_duplicate_record_rejected(sess_fengcailing):
    """XZ-010 同一批次下对同一员工重复新增：应被 checkOne 判重拒绝（正向对照，通过）。

    判据出处：RecordsValidate.checkOne 的判重 SQL 为 salary_id + uid + delete_time=0
    + 排除自身 id（app/adm/validate/RecordsValidate.php:21-26）。
    测试点：证明「并非所有校验都缺失」，避免把 b 组结论夸大成「服务端完全无校验」。
    关联缺陷：无（正向对照）。批次 H(1007) 为空白批次，先自建前置再判重。
    """
    b = BATCH["H"]
    r1, rid1 = _add_rec(sess_fengcailing, b, STAFF_UID, MGR, salary_basic="7000.00",
                        salary_position="1000.00", deduct_social="500.00",
                        deduct_gongjijin="500.00", deduct_tax="60.00")
    K.track("oa_salary_records", rid1, note="XZ-010 步骤1 前置明细")
    K.expect((rid1 is not None, "步骤1 code=%s msg=%s" % (r1.get("code"), r1.get("msg"))),
             "步骤1 前置：空白批次下应能新增第一条明细")

    before = _rec_count(b, STAFF_UID)
    res, rid = _add_rec(sess_fengcailing, b, STAFF_UID, MGR, salary_basic="8888.00")
    K.track("oa_salary_records", rid, note="XZ-010 步骤2 重复录入（预期被拒）")
    after = _rec_count(b, STAFF_UID)

    K.expect((res.get("code") != 0 and rid is None and before == after == 1,
              "重复录入未被拒绝：code=%s msg=「%s」return_id=%s；条数 %d→%d"
              % (res.get("code"), res.get("msg"), rid, before, after)),
             "同一批次下对同一员工（uid=%d）重复新增应被 checkOne 拒绝且不产生第二行" % STAFF_UID)


# ===========================================================================
# c. 批次汇总算式（XZ-011 ~ XZ-015）
# ===========================================================================
def test_xz_011_batch_summary_single_record(sess_fengcailing):
    """XZ-011 批次汇总 = Σ实发 + Σ社保 + Σ公积金 + Σ个税（单人，分位精确）。

    判据出处：app/adm/model/SalaryRecords.php:141-150 的 update_salary()。
    **不采信接口返回值**，一律回读 oa_salary 并用 Python Decimal 独立复算。
    预期结果修订：用例标题写「=应发+社保+公积金+个税」，预期结果列写的是 Σtotal_payment
    （实发）+社保+公积金+个税，与源码一致；标题措辞不严谨，判定以预期结果列与源码为准。
    关联缺陷：无（本条通过）。批次 I(1008) 由 XZ-011/012/013 顺序累加，本条为第 1 条。
    """
    b = BATCH["I"]
    res, rid = _add_rec(sess_fengcailing, b, STAFF_UID, MGR, salary_basic="5000.01",
                        salary_position="1200.03", salary_performance="800.07",
                        deduct_social="800.11", deduct_gongjijin="600.13", deduct_tax="150.17")
    K.track("oa_salary_records", rid, note="XZ-011 批次 I 第 1 条")
    K.expect((res.get("code") == 0 and rid is not None,
              "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "明细应新增成功")

    row = K.rows("SELECT total_payment,deduct_social,deduct_gongjijin,deduct_tax "
                 "FROM oa_salary_records WHERE id=%d;" % rid)[0]
    exact = sum(Decimal(x) for x in row)
    n = _rec_count(b)
    K.expect((n == 1, "批次 I 下有效明细 %d 条（期望 1，本条为批次 I 第 1 条）" % n),
             "该批次下应恰好 1 条有效明细")
    same, detail = _summary_case(b)
    K.expect((same, detail), "批次汇总 amount 应与 Decimal 独立复算一致")
    amt = _db_amount(b)
    K.expect((Decimal(amt) == exact,
              "库中 amount=%s，该条明细四项之和=%s（实发=%s 社保=%s 公积金=%s 个税=%s）"
              % (amt, exact, row[0], row[1], row[2], row[3])),
             "批次汇总应精确等于该条明细的 Σ实发+Σ社保+Σ公积金+Σ个税，精确到分")
    K.expect((Decimal(amt) == Decimal("7000.11"), "库中 amount=%s（期望 7000.11）" % amt),
             "单人分位金额的汇总应为 7000.11（与执行器实测一致）")


def test_xz_012_batch_summary_three_records(sess_fengcailing):
    """XZ-012 批次汇总 = 3 人四项之和（多人累加，分位精确）。

    判据出处：同 XZ-011（SalaryRecords.php:141-150）。
    前置：批次 I 已有 XZ-011 的 1 条（uid=110），本条再为 uid 108 / 109 各录 1 条。
    关联缺陷：无（本条通过）。
    """
    b = BATCH["I"]
    addrs = []
    for tag in ("testmin", "testemp"):
        u = int(_uid_of(tag))
        r, rid = _add_rec(sess_fengcailing, b, u, MGR, salary_basic="6000.02",
                          deduct_social="900.05", deduct_gongjijin="700.07", deduct_tax="200.09")
        K.track("oa_salary_records", rid, note="XZ-012 批次 I 新增 %s" % tag)
        addrs.append("uid=%s id=%s code=%s" % (u, rid, r.get("code")))
        K.expect((r.get("code") == 0 and rid is not None,
                  "%s code=%s msg=%s" % (tag, r.get("code"), r.get("msg"))),
                 "为 %s(uid=%s) 新增明细应成功" % (tag, u))

    n = _rec_count(b)
    K.expect((n == 3, "批次 I 下有效明细 %d 条（期望 3）；新增：%s" % (n, "；".join(addrs))),
             "批次 I 下应累计 3 条有效明细（XZ-011 的 1 条 + 本条 2 条）")
    same, detail = _summary_case(b)
    K.expect((same, detail), "批次汇总 amount 应等于 3 人四项之和（Decimal 独立复算）")
    amt = _db_amount(b)
    K.expect((Decimal(amt) == Decimal("19000.15"), "库中 amount=%s（期望 19000.15）" % amt),
             "3 人分位金额的汇总应为 19000.15（与执行器实测一致）")


def test_xz_013_batch_summary_fractional_amounts(sess_fengcailing):
    """XZ-013 0.01 级分位多笔：汇总分位精确、无浮点残差落库。

    判据出处：SalaryRecords.php:148 的浮点补偿写法 ($x*10000+…)/10000 本意即抵消浮点误差；
    出现分位偏差即记失败。库中列为 decimal(10,2)，复算一律用 Decimal 精确比对。
    刻意不用大额：oa_salary.amount 为 decimal(10,2)（上限 99999999.99），量级边界已由 XZ-009 覆盖。
    uid 取该批次尚未占用的（checkOne 判重键是 (salary_id, uid)）。
    关联缺陷：无（本条通过）。
    """
    b = BATCH["I"]
    used = {int(x[0]) for x in K.rows("SELECT DISTINCT uid FROM oa_salary_records "
                                     "WHERE salary_id=%s;" % b)}
    picked = []
    for nm in ("fengcailing", "yucixin", "qinjiaxian", "yexiaochai", "tanwuyu", "hongchenxue"):
        u = _uid_of(nm)
        if u and int(u) not in used:
            used.add(int(u))
            picked.append((nm, int(u)))
        if len(picked) == 4:
            break
    K.expect((len(picked) == 4, "批次 I 已占用 uid=%s，可用候选仅 %d 个"
              % (sorted(used), len(picked))),
             "批次 I 中应有 4 个未占用的 uid 可供本条使用")

    specs = [(dict(salary_basic="0.07", salary_position="0.29", salary_performance="0.83",
                   deduct_social="0.11", deduct_gongjijin="0.13", deduct_tax="0.17")),
             (dict(salary_basic="0.19", salary_position="0.23",
                   deduct_social="0.31", deduct_gongjijin="0.37", deduct_tax="0.41")),
             (dict(salary_basic="0.43", salary_position="0.47", salary_performance="0.53",
                   deduct_social="0.59", deduct_gongjijin="0.61", deduct_tax="0.67")),
             (dict(salary_basic="0.71", salary_position="0.73", salary_performance="0.79",
                   deduct_social="0.89", deduct_gongjijin="0.97", deduct_tax="0.01"))]
    made = []
    for (nm, u), kw in zip(picked, specs):
        r, rid = _add_rec(sess_fengcailing, b, u, MGR, **kw)
        K.track("oa_salary_records", rid, note="XZ-013 批次 I 分位金额 %s" % nm)
        made.append("%s(uid=%s) id=%s code=%s" % (nm, u, rid, r.get("code")))
        K.expect((r.get("code") == 0 and rid is not None,
                  "%s(uid=%s) code=%s msg=%s" % (nm, u, r.get("code"), r.get("msg"))),
                 "分位金额明细新增应成功")

    n = _rec_count(b)
    K.expect((n == 7, "批次 I 下有效明细 %d 条（期望 7）；本次新增：%s" % (n, "；".join(made))),
             "批次 I 下应累计 7 条有效明细（XZ-011/012 的 3 条 + 本条 4 条）")
    same, detail = _summary_case(b)
    K.expect((same, detail), "批次汇总 amount 应等于 7 条明细四项之和（Decimal 独立复算）")
    amt = _db_amount(b)
    K.expect((amt == amt.quantize(Decimal("0.01")),
              "库中 amount=%s 含 0.01 以下残差" % amt),
             "汇总不得出现 0.30000000000000004 之类浮点残差（库列为 decimal(10,2)）")
    K.expect((amt == Decimal("19005.42"), "库中 amount=%s（期望 19005.42）" % amt),
             "7 条分位金额的汇总应为 19005.42（与执行器实测一致）")


def test_xz_014_edit_recomputes_batch_summary(sess_fengcailing):
    """XZ-014 编辑明细后批次汇总应联动重算（绩效 2000.00 → 3500.00）。

    判据出处：app/adm/model/SalaryRecords.php:75-86 的 edit() 末尾调用 $this->update_salary()；
    scene=edit 由带 id 参数触发（app/adm/controller/Salary.php:176-186）。
    关联缺陷：无（本条通过）。批次 J(1009) 由 XZ-014/015 顺序接力，本条为第 1 条。
    """
    b = BATCH["J"]
    res, rid = _add_rec(sess_fengcailing, b, STAFF_UID, MGR, salary_basic="8000.00",
                        salary_performance="2000.00", deduct_social="800.00",
                        deduct_gongjijin="600.00", deduct_tax="150.00")
    K.track("oa_salary_records", rid, note="XZ-014 批次 J 前置明细")
    K.expect((res.get("code") == 0 and rid is not None,
              "前置新增 code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "前置：应先新增一条明细成功")
    before = _db_amount(b)

    res2, rid2 = _add_rec(sess_fengcailing, b, STAFF_UID, MGR, rid=rid, salary_basic="8000.00",
                          salary_performance="3500.00", deduct_social="800.00",
                          deduct_gongjijin="600.00", deduct_tax="150.00")
    after = _db_amount(b)
    perf = K.scalar("SELECT salary_performance FROM oa_salary_records WHERE id=%d;" % rid, "?")
    same, detail = _summary_case(b)

    K.expect((res2.get("code") == 0, "编辑 code=%s msg=%s" % (res2.get("code"), res2.get("msg"))),
             "编辑明细应成功")
    K.expect((str(rid2) == str(rid), "返回 id=%s（原 id=%s）" % (rid2, rid)),
             "编辑应返回原明细 id（更新而非新增）")
    K.expect((perf == "3500.00", "库中 salary_performance=%s（期望 3500.00）" % perf),
             "绩效工资应被更新为 3500.00")
    K.expect((after != before, "批次汇总 amount %s→%s（应有变化）" % (before, after)),
             "编辑后批次汇总应随之变化")
    K.expect((same, detail), "编辑后批次汇总应与 Decimal 复算一致")
    K.expect((after == Decimal("11500.00"), "库中 amount=%s（期望 11500.00）" % after),
             "改后汇总应为 11500.00（与执行器实测一致）")


def test_xz_015_delete_soft_and_recomputes(sess_fengcailing, sess_yucixin):
    """XZ-015 删除明细为软删除且批次汇总联动扣除（须持节点 222 的账号执行）。

    判据出处：app/adm/model/SalaryRecords.php:115-139 的 delById() 末尾调用 update_salary()；
    update_salary 的 where 含 delete_time=0，故删除为软删除且汇总随之扣除。
    观察项①：删除动作须持节点 222（adm/salary/records_del），管理岗角色（组2，fengcailing）
    **不含 222**，首轮实测即被 405 拒绝，故改用业务岗（组3，yucixin）—— 本条把这个账号边界
    作为负向对照一并验证。
    观察项③：该动作响应体为空，判定以库中 delete_time 为准。
    关联缺陷：无（本条通过）。批次 J(1009) 由 XZ-014/015 顺序接力，删除对象为本条新建的明细。
    """
    b = BATCH["J"]
    res, rid = _add_rec(sess_yucixin, b, int(_uid_of("testmin")), MGR_DEL, salary_basic="4000.00",
                        deduct_social="400.00", deduct_gongjijin="300.00", deduct_tax="70.00")
    K.track("oa_salary_records", rid, note="XZ-015 待删除明细")
    K.expect((res.get("code") == 0 and rid is not None,
              "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "前置：第二位员工（testmin）的明细应新增成功")
    n_before, before = _rec_count(b), _db_amount(b)

    # 负向对照：无节点 222 的管理岗删除应被 RBAC 拦下（观察项①）
    r_deny = _delete(sess_fengcailing, "/adm/salary/records_del", {"id": rid})
    K.record("fengcailing DELETE /adm/salary/records_del?id=%s（无节点 222）" % rid, r_deny)
    dl_deny = K.scalar("SELECT delete_time FROM oa_salary_records WHERE id=%d;" % rid, "0")
    K.expect(K.is_denied(r_deny), "无节点 222 的账号删除明细应被 405 拦截")
    K.expect((int(dl_deny or 0) == 0, "delete_time=%s（期望 0）" % dl_deny),
             "被 405 拒绝后该明细不应被软删除")

    # 正向：业务岗（组3，持 220+222）执行删除
    r_del = _delete(sess_yucixin, "/adm/salary/records_del", {"id": rid})
    K.record("yucixin DELETE /adm/salary/records_del?id=%s" % rid, r_del)
    dl = K.scalar("SELECT delete_time FROM oa_salary_records WHERE id=%d;" % rid, "0")
    n_after, after = _rec_count(b), _db_amount(b)
    same, detail = _summary_case(b)

    K.expect((int(dl or 0) > 0, "delete_time=%s（期望 >0）；删除响应=%s" % (dl, r_del.get("msg"))),
             "删除应为软删除（delete_time>0）")
    K.expect((n_after == n_before - 1, "有效条数 %d→%d" % (n_before, n_after)),
             "删除后该批次有效明细条数应减 1")
    K.expect((after != before, "批次汇总 amount %s→%s（应扣除该条）" % (before, after)),
             "删除后批次汇总应扣除该条明细")
    K.expect((same, detail), "删除后批次汇总应与 Decimal 复算一致（update_salary 的 where 含 delete_time=0）")
    K.expect((after == Decimal("11500.00"), "库中 amount=%s（期望 11500.00）" % after),
             "删除后汇总应回到 11500.00（与执行器实测一致）")
