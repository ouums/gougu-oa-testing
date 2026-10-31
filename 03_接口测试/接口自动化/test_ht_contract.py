# -*- coding: utf-8 -*-
"""合同管理 30 条功能用例（HT-001 ~ HT-030）的 pytest 实现。

【结论对齐】与 03_接口测试/case_results_ht.json 逐条一致：**26 通过 / 4 失败**。
  4 条失败用例（HT-012 / HT-018 / HT-025 / HT-028）都是已单列缺陷的用例，标
  xfail(strict=True) + known_defect —— 断言写「**正确行为**」：缺陷存在时记 xfailed，
  缺陷修复后 XPASS 并被判 FAILED（pytest.ini 的 xfail_strict=true 让 xfail 成为活的回归探针）。

【与执行器 run_functional_cases_ht.py 的关系】请求参数、账号、判据、数据标记
  （code 前缀 HT-CASE- / name 前缀【用例数据】）全部沿用执行器；**结论字符串不复用** ——
  每条用例自己发请求、自己比较（README「断言必须真的比较」）。
  唯一的结构性差异：执行器按顺序共享上下文（HT-002 依赖 HT-001 建的合同、HT-025/028 依赖
  前序单据推进到位），本套件让**每条用例自带前置**（需要什么状态就自己推进到那个状态）——
  30 条可以单独跑、乱序跑，一条失败不会连带后面几条一起错。

【账号】按用例「前置条件」列指定，逐条写在函数 docstring 里：
  fengcailing(uid=4，管理岗) —— HT-001~003、005~008、010~023、025、028、029
  admin(uid=1，超管)        —— HT-004、009、024、026、027、029(归档)、030

【数据登记】每条新建合同都 K.track("oa_contract"/"oa_purchase", id) 登记（新建即登记）。
  ⚠️ 实测：pytest_cleanup.py 的 DOC_TABLES 只含单据表（leaves/overtimes/outs/…），
  **不含 oa_contract / oa_purchase**，故合同本体与其 flow 子表本轮不会被会话收尾回收 ——
  回收仍归 00_挂起存档.md §6 第 7 项的 cleanup_contract_data.py（尚未编写）。
  本模块**不自行 DELETE**：破坏性操作按 README 归 pytest_cleanup，不在测试文件里开口子。

【框架边界（框架补齐能力的，一律改回调用原语；剩下的写在各自 docstring 里）】
  _delete() / _html() —— 框架已支持 DELETE 与 res["text"]（≤20000 字全文），
                故这两个包装改为**转发 K.call()**，只在原地留一句交接说明；
                同一判定口径不留两份实现，否则两边迟早漂移。
  _raw_json() —— 仍是缺口：oa_kit.call() 只在 data 为 list/dict 时搬运它，
                get_codeno 返回的编号是**字符串**（会被整条丢掉），故保留本地实现。
  ⚠️ 与框架能力无关的一条环境事实：本轮之前 setup_contract_data.run_sql 的临时文件写死
    /tmp/oa_c.sql，同一目录下两个 pytest 会话并行时会互相覆盖 —— 实测出现过 SQL 报错文本
    里混进别的模块语句、刚建的 id 查不回行（HT-003/004/005/008/016/017/026/027 假失败）。
    框架已改为 /tmp/oa_c_<pid>.sql，复跑不再出现；**任何"查库结果为 None/串行"的失败，
    先排除这类串扰，再当产品缺陷**。
"""
import re
import time

import pytest

import oa_kit as K
import setup_contract_data as SCD

pytestmark = [pytest.mark.ht]

# ---------------------------------------------------------------------------
# 常量（与执行器同名同义）
# ---------------------------------------------------------------------------
MGR = "fengcailing"          # uid=4 did=3 管理岗
SUPER = "admin"              # uid=1 超管 —— isAuth 对 uid==1 短路，恒为合同管理员
APPROVER = "admin"           # 合同审批人
NAME_MARK = "【用例数据】"
CODE_MARK = "HT-CASE-"

# 本轮 run 标记：库中已有 17 条上次执行留下的 HT-CASE-*，编码里带时间戳才不会撞号
# （code 有 require|checkOne 唯一约束，撞号会被拒，用例会误判成"缺陷"）。
RUN_TAG = time.strftime("%m%d%H%M%S")
_SEQ = [0]


def _table(kind):
    """合同类型 → 物理表名（销售/采购分属两张表）"""
    return "oa_contract" if kind == "contract" else "oa_purchase"


def _new_code(kind):
    """本次用例建的合同编码：HT-CASE-PY<run标记>-<CO|PU><序号>（沿用 HT-CASE- 前缀）"""
    _SEQ[0] += 1
    return "%sPY%s-%s%03d" % (CODE_MARK, RUN_TAG, kind[:2].upper(), _SEQ[0])


def _new_name(kind):
    _SEQ[0] += 1
    return "%s HT %s%03d" % (NAME_MARK, kind[:2].upper(), _SEQ[0])


# ---------------------------------------------------------------------------
# 会话 / SQL 小工具
# ---------------------------------------------------------------------------
def _sess(user):
    """按账号取会话（与 conftest 的 sess_* fixture 同一个缓存对象，不会重复登录）"""
    return K.get_session(user)


_DID = {}


def _did_of(user):
    """取账号所属部门 id（建单表单要用）—— 按账号缓存，减少每轮建单的 SQL 往返"""
    if user not in _DID:
        _DID[user] = int(K.scalar("SELECT did FROM oa_admin WHERE username='%s' LIMIT 1;"
                                  % user, "1") or 1)
    return _DID[user]


def _state(kind, cid):
    """回读单据状态字段 —— 「已归档/已中止/已作废」不是 check_status 的取值，
    分别由 archive_time / stop_time / void_time > 0 表示（oa_contract 无 archive_status 列）"""
    r = K.rows("SELECT check_status,archive_time,stop_time,void_time,delete_time FROM %s "
               "WHERE id=%s;" % (_table(kind), cid))
    if not r:
        return None
    v = r[0]
    return dict(check_status=int(v[0]), archive_time=int(v[1]), stop_time=int(v[2]),
                void_time=int(v[3]), delete_time=int(v[4]))


def _field(kind, cid, col):
    return K.scalar("SELECT %s FROM %s WHERE id=%s;" % (col, _table(kind), cid), None)


def _row(kind, cid, cols):
    r = K.rows("SELECT %s FROM %s WHERE id=%s;" % (cols, _table(kind), cid))
    return r[0] if r else None


def _raw_count(kind):
    """oa_contract / oa_purchase 全表行数（含软删除行）—— 用于"行数没变"类判据"""
    return int(K.scalar("SELECT COUNT(*) FROM %s;" % _table(kind), "0") or 0)


def _live_count(kind, extra=""):
    """主列表口径的行数：与 Contract::datalist 的 where 完全对齐"""
    return int(K.scalar("SELECT COUNT(*) FROM %s WHERE delete_time=0 AND archive_time=0 "
                        "AND stop_time=0 AND void_time=0%s;" % (_table(kind), extra), "0") or 0)


# ---------------------------------------------------------------------------
# 请求小工具
# ---------------------------------------------------------------------------
def _layui(res):
    """把 layui table 响应体拆成 (count, 行列表)，兼容 data 为 dict 的写法"""
    d = res.get("data")
    if isinstance(d, dict):
        return int(res.get("count") or d.get("total") or 0), (d.get("data") or [])
    return int(res.get("count") or 0), (d if isinstance(d, list) else [])


def _ret_id(res):
    """取新增返回的主键（data.return_id；data 为 []/None 时返回 None）"""
    d = res.get("data")
    return d.get("return_id") if isinstance(d, dict) else None


def _biz_rejected(res):
    """业务层拒绝：HTTP 200 + 明确的非 0/非 405 业务码。

    ⚠️ 刻意不用 K.is_validation_error —— 它的语义是"闸门放行了"，对 code=0（成功）
    也返回 True，用它判"被拒"会把"接受"读成"拒绝"；
    也不能只写 `code != 0` —— 500/非 JSON 响应下 code 为 None，同样满足 `!= 0`。
    """
    ok = (res.get("http") == 200 and isinstance(res.get("code"), int)
          and res.get("code") not in (0, 405))
    return ok, "http=%s code=%s msg=%s" % (res.get("http"), res.get("code"), res.get("msg"))


def _list(sess, kind, view="datalist", **params):
    """列表接口（layui table 形态）→ (count, 行列表)。
    view: datalist(主列表) / archivelist / stoplist / voidlist"""
    res = K.call(sess, "/contract/%s/%s" % (kind, view), method="GET", data=params)
    return _layui(res)


def _html(sess, path, params=None):
    """取整段 HTML —— 详情页/编辑页的断言要按页面内容比对（如错误页里是否出现堆栈）。

    这里曾有本地包装（当时 oa_kit.call() 对非 JSON 只留 120 字 body_head）；
    框架补上 res["text"]（≤20000 字全文）后改回调用原语。"""
    res = K.call(sess, path, method="GET", data=params)
    return res.get("http"), (res.get("text") or res.get("body_head") or "")


def _delete(sess, path, cid):
    """发真正的 DELETE —— Contract::del() / Purchase::del() 判的是 request()->isDelete()，
    POST 会被回「错误的请求」；参数走查询串（与 GET 一致）。

    这里曾有本地包装（当时 oa_kit.call() 只发 GET/POST）；框架补上 DELETE 后改回调用原语 ——
    同一判定口径不留两份实现，否则两边迟早会漂移。"""
    return K.call(sess, path, method="DELETE", data={"id": cid})


def _raw_json(sess, path, params=None):
    """取响应体 JSON 原文 —— oa_kit.call() 只在 data 是 list/dict 时搬运它，
    而 /api/index/get_codeno 返回的 data 是**字符串**（编号本身），会被丢掉。"""
    r = sess.get(K.BASE + path, params=params, timeout=30, allow_redirects=False)
    try:
        return r.status_code, r.json()
    except Exception:                                       # noqa: BLE001
        return r.status_code, {"raw": (r.text or "")[:200]}


# ---------------------------------------------------------------------------
# 合同构造 / 审批流推进（沿用执行器的参数与数据标记）
# ---------------------------------------------------------------------------
def _spec(kind, **over):
    spec = dict(kind=kind, code=_new_code(kind), types=1, cate_id=1, cost="10000.00",
                span=(0, 365), name=_new_name(kind), customer=0, supplier=0)
    for k in ("types", "cate_id", "cost", "span", "name", "code"):
        if k in over:
            spec[k] = over[k]
    return spec


def _add(sess, user, kind, ids, **over):
    """建一条本用例的合同；返回 (id, res, payload)。

    表单字段与界面 add.html 完全一致（由 setup_contract_data.contract_payload 组装，
    customer/product/supplier 的 id 取自主数据夹具）。新建即 K.track 登记。
    """
    spec = _spec(kind, **over)
    p = SCD.contract_payload(spec, ids, K.UID[user], _did_of(user))
    p.update({k: v for k, v in over.items()
              if k not in ("types", "cate_id", "cost", "span", "name", "kind", "code")})
    res = K.call(sess, SCD.KIND_CFG[kind]["add_url"], method="POST", data=p)
    cid = _ret_id(res)
    K.track(_table(kind), cid, note="pytest %s 建单" % spec["code"])
    return cid, res, p


def _submit(sess, kind, cid, approver=APPROVER):
    """提交审批（自由审批模式，与本实例界面的正常路径一致：19 条 oa_flow 的 flow_list 全为空，
    check_type=1 ⇒ 界面就是"选审批人再提交"）。返回 (flow_id, res)。"""
    cfg = SCD.KIND_CFG[kind]
    _sc, fr = _raw_json(sess, "/api/check/get_flows", {"check_name": cfg["cate_name"]})
    flows = fr.get("data") or []
    fid = int(flows[0]["id"]) if flows else 0
    res = K.call(sess, "/api/check/submit_check", method="POST",
                 data={"check_name": cfg["cate_name"], "action_id": cid, "flow_id": fid,
                       "check_uids": str(K.UID[approver]), "check_copy_uids": ""})
    return fid, res


def _flow_check(sess, kind, cid, fid, check, content="", node=1):
    """审批动作：check=1 通过 / 2 拒绝 / 3 撤回（check_node 必传，否则 500）"""
    return K.call(sess, "/api/check/flow_check", method="POST",
                  data={"check_name": SCD.KIND_CFG[kind]["cate_name"], "action_id": cid,
                        "check_flow_id": fid, "check": check, "check_node": node,
                        "check_uids": "", "check_files": "", "content": content})


def _history(sess, kind, cid):
    """审批历史 —— 走界面渲染时间线用的那条 GET 接口（POST 会 500）"""
    fid = _field(kind, cid, "check_flow_id") or 0
    res = K.call(sess, "/api/check/get_flow_nodes", method="GET",
                 data={"check_name": SCD.KIND_CFG[kind]["cate_name"], "action_id": cid,
                       "flow_id": fid})
    d = res.get("data") if isinstance(res.get("data"), dict) else {}
    return (d or {}).get("check_record") or []


def _history_has(records, status, keyword):
    for r in records:
        if int(r.get("check_status", -1)) == status and keyword in str(r.get("content") or ""):
            return True
    return False


def _checklist(sess, status=1, limit=200):
    """审批中心「待我审批」列表（status=1 待审批）"""
    return _layui(K.call(sess, "/home/approve/checklist", method="GET",
                         data={"page": 1, "limit": limit, "status": status}))


def _approved(sess_mgr, sess_admin, kind, ids, **over):
    """前置构造：建单 → 提交审批（审批人 admin）→ admin 审批通过，返回 (id, payload)。
    用于需要「审批通过 / 已归档 / 已作废」前置状态的用例（HT-023~025、028、029）。"""
    cid, res, p = _add(sess_mgr, MGR, kind, ids, **over)
    assert cid, "前置建单失败：%s" % K.brief(res)
    fid, sub = _submit(sess_mgr, kind, cid, SUPER)
    assert K.is_allowed(sub)[0], "前置提交审批失败：%s" % K.brief(sub)
    chk = _flow_check(sess_admin, kind, cid, fid, 1, "前置：审批通过")
    assert K.is_allowed(chk)[0], "前置审批通过失败：%s" % K.brief(chk)
    return cid, p


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ht_ids(fixture_contract):
    """合同主数据 id（客户/产品/服务/供应商）。

    fixture_contract 是幂等夹具（setup_contract_data.py 按 code/名称判重，已在库则跳过），
    它同时保证了本模块建单所需的客户/产品/供应商存在 —— 界面路径下这三个字段是
    弹窗选择器（DataAuth 开关 conf_3/4/5=1），选择器空的时候合同建不出来。
    """
    ids = dict(SCD.master_ids())
    assert ids.get("customer") and ids.get("product") and ids.get("supplier") \
        and ids.get("service"), "合同主数据缺失（客户/产品/供应商/服务），夹具未就绪：%s" % ids
    return ids


# ===========================================================================
# HT-001 ~ HT-005  销售合同 CRUD     （账号：HT-001~003、005 管理岗 fengcailing；HT-004 超管 admin）
# ===========================================================================
def test_ht_001_sales_contract_add(sess_fengcailing, ht_ids):
    """HT-001 新增销售合同（普通类型）：保存成功且字段正确落库、列表中可见。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：Contract::add()（app/contract/controller/Contract.php:245）落库后由
    GET /contract/contract/datalist 可见；ContractValidate 的 add 场景只校验 name/code/cost。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="10000.00", types=1,
                        cate_id=1, span=(0, 365), is_tax=1, tax="6.00",
                        name="%s用例测试销售合同A" % NAME_MARK)
    K.record("fengcailing POST /contract/contract/add", res)
    assert cid, "建单失败：%s" % K.brief(res)

    row = _row("contract", cid, "types,cost,admin_id,is_tax,tax,"
               "FROM_UNIXTIME(start_time,'%Y-%m-%d'),FROM_UNIXTIME(end_time,'%Y-%m-%d')")
    _cnt, lst = _list(sess_fengcailing, "contract", page=1, limit=200)
    visible = any(str(x.get("id")) == str(cid) for x in lst)

    K.expect(K.is_allowed(res), "新增销售合同应保存成功（code=0）")
    K.expect((row is not None and row[0] == "1" and row[1] == "10000.00"
              and row[2] == str(K.UID[MGR]) and row[3] == "1" and row[4] == "6.00",
              "库中 types=%s cost=%s admin_id=%s is_tax=%s tax=%s（期望 1/10000.00/%s/1/6.00）"
              % (row[0], row[1], row[2], row[3], row[4], K.UID[MGR]) if row else "未落库"),
             "提交的合同字段应原样落库（types/cost/admin_id/is_tax/tax）")
    K.expect((visible, "列表命中=%s" % visible), "新建的合同应出现在销售合同列表中")


def test_ht_002_sales_contract_list_fields(sess_fengcailing, ht_ids):
    """HT-002 销售合同列表展示：名称/金额/编码与库一致，起止时间、关键字段齐全、分页可用。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：Contract::datalist()（controller:40 + model 的 each() 回调）渲染
    status_name / interval_time（"start 至 end"）/ sign_time；分页由 paginate 提供。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="20000.00",
                        span=(-5, 300), name="%s HT-002-列表字段核对" % NAME_MARK)
    K.record("fengcailing POST /contract/contract/add", res)
    assert cid, "建单失败：%s" % K.brief(res)

    cnt, lst = _list(sess_fengcailing, "contract", page=1, limit=200)
    me = [x for x in lst if str(x.get("id")) == str(cid)]
    db = _row("contract", cid, "name,cost,code,"
              "FROM_UNIXTIME(start_time,'%Y-%m-%d'),FROM_UNIXTIME(end_time,'%Y-%m-%d')")
    keys = ("name", "cost", "status_name", "interval_time", "code", "sign_time")
    missing = [k for k in keys if me and k not in me[0]]
    itv = str(me[0].get("interval_time") or "") if me else ""
    ok_field = bool(me) and db is not None \
        and me[0].get("name") == db[0] \
        and float(me[0].get("cost") or 0) == float(db[1]) \
        and me[0].get("code") == db[2] and db[3] in itv and db[4] in itv
    _cnt2, page2 = _list(sess_fengcailing, "contract", page=2, limit=5)

    K.expect((bool(me), "列表 count=%s 中命中该合同=%s" % (cnt, bool(me))),
             "新建的合同应出现在列表第 1 页")
    K.expect((ok_field, "列表 name/cost/code 与库比对=%s；interval_time=「%s」应覆盖库中 %s~%s"
              % (ok_field, itv, db[3] if db else "?", db[4] if db else "?"),
              ), "列表展示的名称/金额/编码应与库一致，起止时间应被 interval_time 覆盖")
    K.expect((not missing, "缺失字段=%s" % missing),
             "列表行应包含 %s 等关键字段" % "/".join(keys))
    K.expect((cnt <= 5 or len(page2) > 0, "count=%s 第 2 页取到 %d 条" % (cnt, len(page2))),
             "分页应可用（第 2 页能取到数据）")


def test_ht_003_sales_contract_edit(sess_fengcailing, ht_ids):
    """HT-003 编辑销售合同：名称与金额更新落库，详情页显示新值。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：编辑走 POST /contract/contract/add 的 isAjax 分支（scene=edit），
    ContractValidate 的 edit 场景为 name/code/cost/id；详情页 GET /contract/contract/view。
    """
    cid, res, p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="10000.00",
                       name="%s用例测试销售合同A" % NAME_MARK)
    assert cid, "建单失败：%s" % K.brief(res)
    before = _row("contract", cid, "name,cost")

    newname = "%s用例测试销售合同A-已改" % NAME_MARK
    edit = dict(p, scene="edit", id=cid, name=newname, cost="12000.00")
    res2 = K.call(sess_fengcailing, "/contract/contract/add", method="POST", data=edit)
    K.record("fengcailing POST /contract/contract/add(scene=edit)", res2)
    after = _row("contract", cid, "name,cost")
    st, html = _html(sess_fengcailing, "/contract/contract/view", {"id": cid})

    K.expect(K.is_allowed(res2), "编辑保存应成功（code=0）")
    K.expect((after is not None and after[0] == newname and after[1] == "12000.00",
              "库中为「%s / %s」，期望「%s / 12000.00」（改前 %s / %s）"
              % (after[0], after[1], newname, before[0], before[1]) if after else "记录不存在"),
             "编辑应把新名称与新金额写入库中")
    K.expect((st == 200 and newname in html and "12000.00" in html,
              "HTTP %s；详情页含新名称=%s 含新金额=%s" % (st, newname in html, "12000.00" in html)),
             "详情页应显示编辑后的名称与金额")


def test_ht_004_sales_contract_delete(sess_admin, ht_ids):
    """HT-004 删除销售合同：软删除（delete_time 置时间戳）、列表不再可见。

    账号：admin(uid=1)。关联缺陷：无（但 Contract::del() 只判请求方法、无归属与权限校验，
    已作为观察项归入 BUG-02「合同模块接口缺 RBAC 闸门」同族，不另开编号）。
    判据出处：Contract::del()（controller:513）+ /contract/contract/del 要求 DELETE 方法。
    """
    cid, res, _p = _add(sess_admin, SUPER, "contract", ht_ids, cost="3000.00")
    assert cid, "建单失败：%s" % K.brief(res)

    dres = _delete(sess_admin, "/contract/contract/del", cid)
    K.record("admin DELETE /contract/contract/del?id=%s" % cid, dres)
    dl = _field("contract", cid, "delete_time")
    _cnt, lst = _list(sess_admin, "contract", page=1, limit=200)
    gone = not any(str(x.get("id")) == str(cid) for x in lst)

    K.expect(K.is_allowed(dres), "删除应返回成功（code=0）")
    K.expect((int(dl or 0) > 0, "delete_time=%s（期望 >0）" % dl),
             "删除应为软删除：delete_time 置时间戳而非物理删除")
    K.expect((gone, "列表中仍可见=%s" % (not gone)), "已删除的合同不应再出现在列表中")


def test_ht_005_sales_contract_view(sess_fengcailing, ht_ids):
    """HT-005 销售合同详情页：字段与库中逐项一致，金额无精度丢失。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：GET /contract/contract/view?id=N 详情页渲染 oa_contract 全字段；
    测试点＝金额按 decimal(15,2) 存储，页面回显 8888.88 无精度丢失。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="8888.88",
                        span=(-10, 300))
    assert cid, "建单失败：%s" % K.brief(res)

    db = _row("contract", cid, "name,code,cost,"
              "FROM_UNIXTIME(start_time,'%Y-%m-%d'),FROM_UNIXTIME(end_time,'%Y-%m-%d')")
    assert db, "建单后未能回读到该合同（SQL 读取异常，非产品缺陷）"
    st, html = _html(sess_fengcailing, "/contract/contract/view", {"id": cid})
    hits = {k: (v in html) for k, v in (("名称", db[0]), ("编码", db[1]), ("金额", db[2]),
                                        ("开始日期", db[3]), ("结束日期", db[4]))}
    K.expect((st == 200 and all(hits.values()),
              "HTTP %s；字段命中 %s（库：%s / %s / %s / %s~%s）"
              % (st, hits, db[0], db[1], db[2], db[3], db[4])),
             "详情页应与库中数据逐项一致（名称/编码/金额/起止日期）")


# ===========================================================================
# HT-006 ~ HT-009  采购合同 CRUD     （账号：HT-006~008 管理岗 fengcailing；HT-009 超管 admin）
# ===========================================================================
def test_ht_006_purchase_contract_add(sess_fengcailing, ht_ids):
    """HT-006 新增采购合同（普通类型）：保存成功、字段落库、采购列表中可见。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：POST /contract/purchase/add → oa_purchase（与销售合同分属两张表）。
    测试点：用例「测试数据」列写了「税点：13」，但 oa_purchase **没有税点字段**
    （对采购合同未建模税点），故只核对确有落库的字段。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "purchase", ht_ids, cost="5000.00",
                        name="%s用例测试采购合同A" % NAME_MARK)
    K.record("fengcailing POST /contract/purchase/add", res)
    assert cid, "建单失败：%s" % K.brief(res)

    row = _row("purchase", cid, "types,cost,admin_id")
    _cnt, lst = _list(sess_fengcailing, "purchase", page=1, limit=200)
    visible = any(str(x.get("id")) == str(cid) for x in lst)

    K.expect(K.is_allowed(res), "新增采购合同应保存成功（code=0）")
    K.expect((row is not None and row[0] == "1" and row[1] == "5000.00"
              and row[2] == str(K.UID[MGR]),
              "库中 types=%s cost=%s admin_id=%s（期望 1/5000.00/%s）"
              % (row[0], row[1], row[2], K.UID[MGR]) if row else "未落库"),
             "采购合同字段应原样落库（types/cost/admin_id）")
    K.expect((visible, "采购列表命中=%s" % visible), "新建的采购合同应出现在采购列表中")


def test_ht_007_purchase_list_isolation(sess_fengcailing, ht_ids):
    """HT-007 采购合同列表：字段与库一致，且不会串入销售合同数据。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：两张表各自自增，判重必须用 code 而非 id（id 不具跨表唯一性）。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "purchase", ht_ids, cost="9000.00")
    assert cid, "建单失败：%s" % K.brief(res)

    cnt, lst = _list(sess_fengcailing, "purchase", page=1, limit=500)
    me = [x for x in lst if str(x.get("id")) == str(cid)]
    db = _row("purchase", cid, "name,cost,code")
    ok_field = bool(me) and db is not None and me[0].get("name") == db[0] \
        and float(me[0].get("cost") or 0) == float(db[1]) and me[0].get("code") == db[2]

    _scnt, sales = _list(sess_fengcailing, "contract", page=1, limit=500)
    pcodes = {str(x[0]) for x in K.rows("SELECT code FROM oa_purchase;")}
    cross = [x.get("code") for x in sales if str(x.get("code")) in pcodes]

    K.expect((bool(me) and ok_field,
              "采购列表 count=%s 命中=%s 字段一致=%s（库：%s / %s / %s）"
              % (cnt, bool(me), ok_field, db[0], db[1], db[2]) if db else "未落库"),
             "采购列表中的名称/金额/编码应与库一致")
    K.expect((not cross, "销售列表 %d 条中串入采购 code %s" % (len(sales), cross[:5])),
             "销售合同列表不应串入采购合同数据（两表数据隔离）")


def test_ht_008_purchase_contract_edit(sess_fengcailing, ht_ids):
    """HT-008 编辑采购合同：金额与备注更新落库，详情页显示新金额。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：POST /contract/purchase/add 的 scene=edit 分支 → GET /contract/purchase/view。
    """
    cid, res, p = _add(sess_fengcailing, MGR, "purchase", ht_ids, cost="5000.00")
    assert cid, "建单失败：%s" % K.brief(res)
    before = _row("purchase", cid, "cost,remark")

    edit = dict(p, scene="edit", id=cid, cost="5500.00",
                remark="【用例数据】采购编辑后备注")
    res2 = K.call(sess_fengcailing, "/contract/purchase/add", method="POST", data=edit)
    K.record("fengcailing POST /contract/purchase/add(scene=edit)", res2)
    after = _row("purchase", cid, "cost,remark")
    st, html = _html(sess_fengcailing, "/contract/purchase/view", {"id": cid})

    K.expect(K.is_allowed(res2), "编辑保存应成功（code=0）")
    K.expect((after is not None and after[0] == "5500.00" and "编辑后备注" in (after[1] or ""),
              "库中 cost=%s（期望 5500.00，改前 %s）、remark=%s"
              % (after[0], before[0], (after[1] or "")[:30]) if after else "记录不存在"),
             "编辑应更新库中的金额与备注")
    K.expect((st == 200 and "5500.00" in html, "HTTP %s；详情页含新金额=%s" % (st, "5500.00" in html)),
             "详情页应显示编辑后的金额")


def test_ht_009_purchase_contract_delete(sess_admin, ht_ids):
    """HT-009 删除采购合同：软删除（delete_time 置时间戳）。

    账号：admin(uid=1)。关联缺陷：无。
    判据出处：Purchase::del() + DELETE /contract/purchase/del?id=N。
    """
    cid, res, _p = _add(sess_admin, SUPER, "purchase", ht_ids, cost="1500.00")
    assert cid, "建单失败：%s" % K.brief(res)

    dres = _delete(sess_admin, "/contract/purchase/del", cid)
    K.record("admin DELETE /contract/purchase/del?id=%s" % cid, dres)
    dl = _field("purchase", cid, "delete_time")

    K.expect(K.is_allowed(dres), "删除应返回成功（code=0）")
    K.expect((int(dl or 0) > 0, "delete_time=%s（期望 >0）" % dl),
             "采购合同删除应为软删除：delete_time 置时间戳")


# ===========================================================================
# HT-010 ~ HT-020  表单校验（账号全部为管理岗 fengcailing）
# ===========================================================================
def test_ht_010_required_fields_blank(sess_fengcailing, ht_ids):
    """HT-010 必填校验：名称/编码/金额/日期全空时应被拒，且不生成记录。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：ContractValidate 的 add 场景 name.require / code.require / cost.require
    （message 表给提示语），日期先后在 Contract::add 内联判断。
    """
    cid, res, p = _add(sess_fengcailing, MGR, "contract", ht_ids)
    assert cid, "取表单母本失败：%s" % K.brief(res)

    blank = dict(p, scene="add")
    blank.pop("id", None)
    blank.update({"name": "", "code": "", "cost": "", "start_time": "", "end_time": ""})
    before = _raw_count("contract")
    res2 = K.call(sess_fengcailing, "/contract/contract/add", method="POST", data=blank)
    K.record("fengcailing POST /contract/contract/add（必填全空）", res2)
    after = _raw_count("contract")

    K.expect(_biz_rejected(res2), "必填项全空时保存应被业务层拒绝")
    K.expect((after == before, "oa_contract 行数 %d→%d" % (before, after)),
             "被拒时不应生成合同记录")


def test_ht_011_amount_min_nonzero(sess_fengcailing, ht_ids):
    """HT-011 金额边界值 0.01：保存成功，decimal(15,2) 原样存储无精度丢失。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：ContractValidate 的 cost 规则为 require|checkNumer（is_numeric），无范围校验。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="0.01")
    K.record("fengcailing POST /contract/contract/add（cost=0.01）", res)
    v = _field("contract", cid, "cost") if cid else None

    K.expect(K.is_allowed(res), "金额 0.01 应保存成功（边界：decimal(15,2) 的最小非零值）")
    K.expect((cid is not None and v == "0.01", "库中 cost=「%s」（期望 0.01）return_id=%s" % (v, cid)),
             "0.01 应原样存储，不被舍入为 0")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-17 合同金额与税点缺少服务端范围校验（一般 C）", strict=True)
def test_ht_012_amount_negative(sess_fengcailing, ht_ids):
    """HT-012 金额 -100：应被服务端范围校验拒绝（实测被接受并入库，缺陷）。

    账号：fengcailing(uid=4)。关联缺陷：BUG-17（关联证据：证据-12）。
    判据出处：ContractValidate 的 cost 规则只有 require|checkNumer(is_numeric)，
    没有 >0 的范围校验 —— 正确行为是服务端拒绝负数金额。
    测试点：用例预期写明「或明确记录系统是否允许负数（若允许则属校验缺失）」，
    实测允许入库，按校验缺失记失败 ⇒ 本用例写「应被拒绝」的断言，缺陷在则 xfail。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="-100")
    K.record("fengcailing POST /contract/contract/add（cost=-100）", res)
    v = _field("contract", cid, "cost") if cid else None
    persisted = cid is not None and v is not None and float(v) < 0

    K.expect((_biz_rejected(res)[0] and not persisted,
              "code=%s msg=%s；oa_contract id=%s cost=%s" % (res.get("code"), res.get("msg"), cid, v)),
             "负数金额应被服务端范围校验拒绝，且不得入库")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-17 合同金额与税点缺少服务端范围校验（一般 C）", strict=True)
def test_ht_018_tax_out_of_range(sess_fengcailing, ht_ids):
    """HT-018 税点越界 -5 / 101：应被服务端范围校验拒绝（实测均被接受入库，缺陷）。

    账号：fengcailing(uid=4)。关联缺陷：BUG-17（关联证据：证据-12）。
    判据出处：ContractValidate 的规则表里根本没有 tax 这一项（只有 code/name/cost/id），
    -5% 与 101% 都能直接入库并参与后续金额计算。
    测试点：用例预期写明「或明确记录系统未做范围校验（属校验缺失）」，实测未做任何范围校验。
    """
    accepted = []
    for tax in ("-5.00", "101.00"):
        cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="10000.00",
                            is_tax=1, tax=tax)
        K.record("fengcailing POST /contract/contract/add（tax=%s）" % tax, res)
        if res.get("code") == 0 and cid:
            accepted.append("税点 %s → 保存成功，id=%s 库中 tax=%s"
                            % (tax, cid, _field("contract", cid, "tax")))

    K.expect((not accepted, "越界税点被接受：%s" % "；".join(accepted)),
             "税点 -5 与 101 应被服务端范围校验拒绝（合法区间 0~100）")


def test_ht_013_amount_over_precision(sess_fengcailing, ht_ids):
    """HT-013 金额 99999999999999.99（超 decimal(15,2) 整数位上限）：应拒绝或按精度截断。

    账号：fengcailing(uid=4)。关联缺陷：无（观察项：拒绝时把数据库原始报错整段抛给用户，
    属错误信息外泄，与 BUG-12 同族，不另开编号）。
    判据出处：cost 为 decimal(15,2)（整数位上限 13 位），该值 14 位 ⇒ 数据库拒绝。
    ⚠️ 断言刻意带上 http != 500：HTTP 500 / 非 JSON 时 body 没有 code（为 None），
    只写 `code != 0` 会把"没拦住、异常放大"读成"被拒绝"（假通过）。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="99999999999999.99")
    K.record("fengcailing POST /contract/contract/add（cost=99999999999999.99）", res)
    v = _field("contract", cid, "cost") if cid else None
    rejected = _biz_rejected(res)[0]
    truncated = cid is not None and v is not None and len(str(v).split(".")[0]) <= 13

    K.expect((res.get("http") != 500, "HTTP %s body=%s" % (res.get("http"), res.get("body_head"))),
             "超精度金额不应把异常放大成 HTTP 500")
    K.expect((rejected or truncated,
              "code=%s msg=%s；库中 cost=%s（既未拒绝也未按精度截断）"
              % (res.get("code"), res.get("msg"), v)),
             "超字段精度上限的值应被拒绝或按精度截断")


def test_ht_014_amount_not_numeric(sess_fengcailing, ht_ids):
    """HT-014 金额 abc：非数字应被拒（命中 checkNumer 的 is_numeric），且不生成脏数据。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：ContractValidate::checkNumer + message「合同金额只能是数字」。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="abc")
    K.record("fengcailing POST /contract/contract/add（cost=abc）", res)

    K.expect(_biz_rejected(res), "非数字金额应被业务层拒绝（checkNumer / is_numeric）")
    K.expect((cid is None, "return_id=%s（期望 None：未落库）" % cid),
             "被拒时不应生成合同记录")


def test_ht_015_date_order_reversed(sess_fengcailing, ht_ids):
    """HT-015 日期先后校验：开始日期=次年、结束日期=当年 ⇒ 应被拒且不生成合同。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：该判断在 Contract::add 内联完成（不在 Validate 规则表里），
    message 为「结束时间需要大于开始时间」。
    """
    cid, res, p = _add(sess_fengcailing, MGR, "contract", ht_ids)
    assert cid, "取表单母本失败：%s" % K.brief(res)

    bad = dict(p, scene="add")
    bad.pop("id", None)
    bad["code"] = _new_code("contract")
    bad["start_time"] = SCD.d(365)
    bad["end_time"] = SCD.d(1)
    before = _raw_count("contract")
    res2 = K.call(sess_fengcailing, "/contract/contract/add", method="POST", data=bad)
    K.record("fengcailing POST /contract/contract/add（开始日期晚于结束日期）", res2)
    after = _raw_count("contract")

    K.expect(_biz_rejected(res2), "开始日期晚于结束日期时应被业务层拒绝")
    K.expect((after == before, "oa_contract 行数 %d→%d" % (before, after)),
             "被拒时不应生成合同记录")


def test_ht_016_tax_zero(sess_fengcailing, ht_ids):
    """HT-016 税点 0（is_tax=1）：0 值应被正常存储，不被当作「未填」清空。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：oa_contract.tax 为 decimal(15,2) NOT NULL DEFAULT 0.00。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="10000.00",
                        is_tax=1, tax="0.00")
    K.record("fengcailing POST /contract/contract/add（tax=0.00）", res)
    v = _field("contract", cid, "tax") if cid else None
    it = _field("contract", cid, "is_tax") if cid else None

    K.expect(K.is_allowed(res), "税点 0 应保存成功（code=0）")
    K.expect((cid is not None and v == "0.00" and it == "1",
              "库中 tax=「%s」（期望 0.00）is_tax=%s return_id=%s" % (v, it, cid)),
             "0 值应原样存储，不被当作未填而清空")


def test_ht_017_tax_hundred(sess_fengcailing, ht_ids):
    """HT-017 税点 100（is_tax=1）：按 100 存储，未被截断或拒绝。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：同样是"服务端不做范围校验"的边界取值 —— 100 恰是合法上界，应原样保存。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="10000.00",
                        is_tax=1, tax="100.00")
    K.record("fengcailing POST /contract/contract/add（tax=100.00）", res)
    v = _field("contract", cid, "tax") if cid else None

    K.expect(K.is_allowed(res), "税点 100 应保存成功（code=0）")
    K.expect((cid is not None and v == "100.00", "库中 tax=「%s」（期望 100.00）return_id=%s" % (v, cid)),
             "税点 100 应原样存储，未被截断")


def test_ht_019_code_no_generation(sess_fengcailing, ht_ids):
    """HT-019 合同编号生成：格式统一（prefix + YmdHis(14) + rand(1)，共 15 位）、无空编号，
    且以该编号建单成功。

    账号：fengcailing(uid=4)。关联缺陷：无（观察项：末位 rand(1,9) 使同一秒内两次调用不保证
    递增，并发建单有约 1/9 概率撞号、由 checkOne 拒绝后需人工重试 —— 判定口径见执行器）。
    判据出处：app/api/controller/Index.php:305 `$prefix.date('YmdHis').rand(1,9)`；
    服务端对 code 有 require|checkOne 双重约束。
    """
    codes = []
    for _ in range(2):
        sc, j = _raw_json(sess_fengcailing, "/api/index/get_codeno", {"prefix": "S"})
        K.record("fengcailing GET /api/index/get_codeno?prefix=S",
                 {"http": sc, "code": j.get("code"), "msg": str(j.get("msg", "")), "data": j.get("data")})
        codes.append(j.get("data"))
    fmt = [bool(re.match(r"^S\d{15}$", str(x or ""))) for x in codes]

    cid, res, p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="2000.00")
    K.record("fengcailing POST /contract/contract/add（用生成的编号建单）", res)

    K.expect((all(fmt) and not any(x in (None, "") for x in codes),
              "两次编号=%s 格式校验=%s（期望 prefix + YmdHis14 + rand1）" % (codes, fmt)),
             "两次调用生成的编号都应非空且格式统一")
    K.expect(K.is_allowed(res), "以生成的编号建单应成功（code=0，编号=%s）" % p.get("code"))


def test_ht_020_code_duplicate(sess_fengcailing, ht_ids):
    """HT-020 编号重复：手工填写已存在的编号 ⇒ 应被 checkOne 拒绝，不生成第二条。

    账号：fengcailing(uid=4)。关联缺陷：无。
    判据出处：ContractValidate::checkOne 的判重 SQL 带 delete_time=0 且排除自身 id；
    提示语「同样的合同编码已经存在」。
    """
    cid, res, p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="4000.00")
    assert cid, "建单失败：%s" % K.brief(res)

    dup = dict(p, name="%s HT-020-重复编号" % NAME_MARK)
    before = _raw_count("contract")
    res2 = K.call(sess_fengcailing, "/contract/contract/add", method="POST", data=dup)
    K.record("fengcailing POST /contract/contract/add（重复编号 %s）" % p["code"], res2)
    after = _raw_count("contract")
    n = int(K.scalar("SELECT COUNT(*) FROM oa_contract WHERE code='%s' AND delete_time=0;"
                     % p["code"], "0") or 0)

    K.expect(_biz_rejected(res2), "重复编号应被业务层拒绝（checkOne）")
    K.expect((after == before and n == 1,
              "oa_contract 行数 %d→%d；同编号活跃条数=%d（期望 1）" % (before, after, n)),
             "重复编号不应生成第二条记录")


# ===========================================================================
# HT-021 ~ HT-029  审批与状态流转
#   （账号：建单/提交/改单 = 管理岗 fengcailing；审批/归档/中止/作废 = 超管 admin）
# ===========================================================================
def test_ht_021_submit_check(sess_fengcailing, sess_admin, ht_ids):
    """HT-021 提交审批：单据状态 草稿(0) → 审批中(1)，审批人待办列表出现该合同。

    账号：fengcailing(uid=4) 建单并提交，审批人 admin(uid=1)。
    关联缺陷：无。判据出处：POST /api/check/submit_check（自由审批模式，check_uids 指定审批人）
    → oa_contract.check_status=1 + /home/approve/checklist 的「待我审批」列表。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="6000.00")
    assert cid, "建单失败：%s" % K.brief(res)
    st0 = _state("contract", cid)["check_status"]

    fid, sub = _submit(sess_fengcailing, "contract", cid, SUPER)
    K.record("fengcailing POST /api/check/submit_check（flow_id=%s）" % fid, sub)
    st1 = _state("contract", cid)["check_status"]
    cnt, todo = _checklist(sess_admin, status=1)
    hit = any(str(x.get("id")) == str(cid) and str(x.get("table_name")) == "contract"
              for x in todo)

    K.expect((st0 == 0, "提交前 check_status=%s（期望 0 草稿）" % st0), "建单后的初始状态应为草稿")
    K.expect(K.is_allowed(sub), "提交审批应成功（code=0）")
    K.expect((st1 == 1, "提交后 check_status=%s（期望 1 审批中）" % st1),
             "提交后单据状态应变为审批中")
    K.expect((hit, "待我审批列表（共 %d 条）命中=%s" % (cnt, hit)),
             "审批人的待办列表应出现该合同（table_name=contract）")


def test_ht_022_flow_check_pass(sess_fengcailing, sess_admin, ht_ids):
    """HT-022 审批通过：状态 审批中(1) → 审批通过(2)，审批历史可见意见。

    账号：审批人 admin(uid=1)，申请人 fengcailing(uid=4)。
    关联缺陷：无。判据出处：POST /api/check/flow_check check=1 → check_status=2；
    历史走 GET /api/check/get_flow_nodes（必须 GET，POST 会 500）。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="6100.00")
    assert cid, "建单失败：%s" % K.brief(res)
    fid, sub = _submit(sess_fengcailing, "contract", cid, SUPER)
    assert K.is_allowed(sub)[0], "前置提交审批失败：%s" % K.brief(sub)

    chk = _flow_check(sess_admin, "contract", cid, fid, 1, "用例测试通过")
    K.record("admin POST /api/check/flow_check(check=1)", chk)
    st = _state("contract", cid)["check_status"]
    ok_h = _history_has(_history(sess_fengcailing, "contract", cid), 1, "用例测试通过")

    K.expect(K.is_allowed(chk), "审批通过动作应成功（code=0）")
    K.expect((st == 2, "check_status=%s（期望 2 审批通过/待归档）" % st),
             "审批通过后单据状态应为 2")
    K.expect((ok_h, "审批历史含「用例测试通过」=%s" % ok_h),
             "审批历史中应能看到该动作与审批意见")


def test_ht_023_reject_edit_resubmit(sess_fengcailing, sess_admin, ht_ids):
    """HT-023 驳回 → 申请人改单 → 重新提交：状态 1 → 3 → 改单 → 1。

    账号：审批人 admin(uid=1)，申请人 fengcailing(uid=4)。
    关联缺陷：无。判据出处：flow_check check=2 驳回 ⇒ check_status=3（已驳回）；
    驳回后申请人可编辑（scene=edit）并再次 submit_check ⇒ 回到审批中(1)。
    """
    cid, res, p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="7000.00")
    assert cid, "建单失败：%s" % K.brief(res)
    fid, sub = _submit(sess_fengcailing, "contract", cid, SUPER)
    assert K.is_allowed(sub)[0], "前置提交审批失败：%s" % K.brief(sub)

    rej = _flow_check(sess_admin, "contract", cid, fid, 2, "用例测试驳回")
    K.record("admin POST /api/check/flow_check(check=2)", rej)
    st = _state("contract", cid)["check_status"]
    ok_opin = _history_has(_history(sess_fengcailing, "contract", cid), 2, "用例测试驳回")

    edit = dict(p, scene="edit", id=cid, cost="7500.00")
    re_edit = K.call(sess_fengcailing, "/contract/contract/add", method="POST", data=edit)
    K.record("fengcailing POST /contract/contract/add(scene=edit 驳回后改单)", re_edit)
    _fid2, re_sub = _submit(sess_fengcailing, "contract", cid, SUPER)
    K.record("fengcailing POST /api/check/submit_check（重新提交）", re_sub)
    st2 = _state("contract", cid)["check_status"]

    K.expect(K.is_allowed(rej), "审批驳回动作应成功（code=0）")
    K.expect((st == 3, "驳回后 check_status=%s（期望 3 已驳回）" % st), "驳回后单据状态应为 3")
    K.expect((ok_opin, "申请人视角审批历史含「用例测试驳回」=%s" % ok_opin),
             "申请人应能在审批历史中看到驳回意见")
    K.expect(K.is_allowed(re_edit), "驳回后申请人应能编辑单据（code=0）")
    K.expect(K.is_allowed(re_sub), "驳回后应能重新提交审批（code=0）")
    K.expect((st2 == 1, "重新提交后 check_status=%s（期望 1 审批中）" % st2),
             "重新提交后单据状态应回到审批中")


def test_ht_024_archive(sess_fengcailing, sess_admin, ht_ids):
    """HT-024 归档：已审批通过的合同被归档后转入归档列表、主列表转出。

    账号：操作人 admin(uid=1，本实例只有超管能走界面归档，见观察项)。
    关联缺陷：无（观察项：oa_data_auth.contract_admin.conf_1 为空 ⇒ 归档入口只对超管渲染；
    接口侧对任何账号都放行，见 BUG-02 同族）。
    判据出处：POST /contract/api/contract_archive{id,archive_status:1} ⇒ archive_time>0、
    archive_uid=操作人；主列表以 archive_time=0 过滤，归档件出现在 archivelist。
    """
    cid, p = _approved(sess_fengcailing, sess_admin, "contract", ht_ids, cost="9000.00")
    before = _state("contract", cid)

    res2 = K.call(sess_admin, "/contract/api/contract_archive", method="POST",
                  data={"id": cid, "archive_status": 1})
    K.record("admin POST /contract/api/contract_archive", res2)
    after = _state("contract", cid)
    au = _field("contract", cid, "archive_uid")
    _c1, main = _list(sess_admin, "contract", page=1, limit=500)
    _c2, arch = _list(sess_admin, "contract", view="archivelist", page=1, limit=500)
    gone = not any(str(x.get("id")) == str(cid) for x in main)
    inarch = any(str(x.get("id")) == str(cid) for x in arch)

    K.expect((before["archive_time"] == 0, "归档前 archive_time=%s（期望 0）" % before["archive_time"]),
             "归档前不应带 archive_time")
    K.expect(K.is_allowed(res2), "归档动作应成功（code=0）")
    K.expect((after["archive_time"] > 0, "archive_time %d→%d（期望 >0）"
              % (before["archive_time"], after["archive_time"])),
             "归档应以 archive_time>0 标记（本系统「已归档」不是 check_status 的取值）")
    K.expect((str(au) == str(K.UID[SUPER]), "archive_uid=%s（期望 %s 操作人本人）" % (au, K.UID[SUPER])),
             "归档应记录操作人 archive_uid")
    K.expect((after["check_status"] == 2, "归档后 check_status=%s（期望保持 2）" % after["check_status"]),
             "归档应保持 check_status=2")
    K.expect((gone and inarch, "主列表已转出=%s；归档列表可见=%s" % (gone, inarch)),
             "归档件应从主列表转出并出现在归档合同列表")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-18 合同状态机只在界面层校验（一般 C）", strict=True)
def test_ht_025_archived_contract_edit_blocked(sess_fengcailing, sess_admin, ht_ids):
    """HT-025 已归档合同不可编辑：写接口应拒绝改写已归档单据（实测被改写，缺陷）。

    账号：操作人 fengcailing(uid=4)。关联缺陷：BUG-18（关联证据：证据-13）。
    判据出处：Contract.php:393 的 GET 分支对 check_status ∈ {1,2,3} 返回 403，
    但 isAjax 的**写入分支**（同文件 347~378 行）对 check_status 不做任何判断 ⇒
    状态校验只做在「打开表单」这一步，接口层没有 —— 正确行为是写接口同样拒绝。
    """
    cid, _p = _approved(sess_fengcailing, sess_admin, "contract", ht_ids, cost="9000.00")
    arch = K.call(sess_admin, "/contract/api/contract_archive", method="POST",
                  data={"id": cid, "archive_status": 1})
    assert K.is_allowed(arch)[0], "前置归档失败：%s" % K.brief(arch)
    assert _state("contract", cid)["archive_time"] > 0, "前置归档未生效"

    # 界面路径：打开编辑页会返回 403 提示页（这一层是拦住的）
    ui_st, ui_html = _html(sess_fengcailing, "/contract/contract/add", {"id": cid})
    # 写接口路径：POST scene=edit（isAjax 分支没有状态判断）
    p = dict(_p, scene="edit", id=cid, name="%s HT-025-试图改写已归档" % NAME_MARK, cost="1.00")
    before = _row("contract", cid, "name,cost")
    res = K.call(sess_fengcailing, "/contract/contract/add", method="POST", data=p)
    K.record("fengcailing POST /contract/contract/add（改写已归档合同）", res)
    after = _row("contract", cid, "name,cost")

    K.expect((before == after,
              "编辑页 HTTP %s；写接口 code=%s msg=%s；库中「%s / %s」→「%s / %s」（应保持不变）"
              % (ui_st, res.get("code"), res.get("msg"), before[0], before[1], after[0], after[1])),
             "已归档合同的关键字段不应被写接口改写")
    K.expect(_biz_rejected(res), "改写已归档合同的写请求应被服务端拒绝")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-18 合同状态机只在界面层校验（一般 C）", strict=True)
def test_ht_028_voided_contract_resubmit_blocked(sess_fengcailing, sess_admin, ht_ids):
    """HT-028 已作废合同不再参与审批：提交接口应拒绝（实测被推进到审批中，缺陷）。

    账号：操作人 fengcailing(uid=4) 试图提交，作废人 admin(uid=1)。
    关联缺陷：BUG-18（关联证据：证据-13）。
    判据出处：view.html 把整个审批区包在 {if stop_time==0 AND void_time==0} 内 ——
    「作废后不再参与审批」只由模板条件保证，submit_check **不校验** void_time / stop_time。
    正确行为：界面无入口（这条当前成立）**且**接口拒绝提交（这条当前不成立）。
    """
    cid, _p = _approved(sess_fengcailing, sess_admin, "contract", ht_ids, cost="13000.00")
    void = K.call(sess_admin, "/contract/api/contract_tovoid", method="POST",
                  data={"id": cid, "void_status": 1, "void_remark": "【用例数据】作废理由"})
    assert K.is_allowed(void)[0], "前置作废失败：%s" % K.brief(void)
    st0 = _state("contract", cid)["check_status"]

    _st, html = _html(sess_fengcailing, "/contract/contract/view", {"id": cid})
    no_entry = 'id="checkBox"' not in html     # 不能只匹配 checkBox：页面头部有一条同名字段
    fid, res = _submit(sess_fengcailing, "contract", cid, SUPER)
    K.record("fengcailing POST /api/check/submit_check（已作废合同）", res)
    st1 = _state("contract", cid)["check_status"]

    K.expect((no_entry, "详情页审批容器 #checkBox 未渲染=%s（期望 True）" % no_entry),
             "已作废合同的详情页不应再渲染提交审批入口")
    K.expect(_biz_rejected(res), "已作废合同不应能被提交审批（应被服务端拒绝）")
    K.expect((st1 == st0, "check_status %d→%d（期望不变）" % (st0, st1)),
             "被拒后单据状态不应发生变化")


def test_ht_026_stop(sess_admin, ht_ids):
    """HT-026 中止合同：stop_time/stop_uid/中止原因落库，转入中止列表。

    账号：admin(uid=1)。关联缺陷：无。
    判据出处：POST /contract/api/contract_stop{id,stop_status:1,stop_remark} ⇒
    stop_time>0、stop_uid=操作人；中止件出现在 stoplist。
    """
    cid, res, _p = _add(sess_admin, SUPER, "contract", ht_ids, cost="11000.00")
    assert cid, "建单失败：%s" % K.brief(res)

    res2 = K.call(sess_admin, "/contract/api/contract_stop", method="POST",
                  data={"id": cid, "stop_status": 1, "stop_remark": "【用例数据】中止理由"})
    K.record("admin POST /contract/api/contract_stop", res2)
    st = _state("contract", cid)
    rem = _field("contract", cid, "stop_remark")
    su = _field("contract", cid, "stop_uid")
    _c, stop = _list(sess_admin, "contract", view="stoplist", page=1, limit=500)
    instop = any(str(x.get("id")) == str(cid) for x in stop)

    K.expect(K.is_allowed(res2), "中止动作应成功（code=0）")
    K.expect((st["stop_time"] > 0, "stop_time=%s（期望 >0）" % st["stop_time"]),
             "中止应以 stop_time>0 标记")
    K.expect((str(su) == str(K.UID[SUPER]), "stop_uid=%s（期望 %s）" % (su, K.UID[SUPER])),
             "中止应记录操作人 stop_uid")
    K.expect((bool(rem), "stop_remark=%s" % (rem or "")[:30]), "中止原因应被保存")
    K.expect((instop, "中止列表可见=%s" % instop), "中止件应出现在中止合同列表")


def test_ht_027_void(sess_admin, ht_ids):
    """HT-027 作废合同：void_time/void_uid/作废理由落库，主列表转出，详情页不再渲染操作区。

    账号：admin(uid=1)。关联缺陷：无。
    判据出处：POST /contract/api/contract_tovoid{id,void_status:1,void_remark} ⇒ void_time>0；
    view.html 的审批区包在 {if stop_time==0 AND void_time==0} 内 ⇒ 作废后 #checkBox 不渲染。
    """
    cid, res, _p = _add(sess_admin, SUPER, "contract", ht_ids, cost="12000.00")
    assert cid, "建单失败：%s" % K.brief(res)

    res2 = K.call(sess_admin, "/contract/api/contract_tovoid", method="POST",
                  data={"id": cid, "void_status": 1, "void_remark": "【用例数据】作废理由"})
    K.record("admin POST /contract/api/contract_tovoid", res2)
    st = _state("contract", cid)
    vu = _field("contract", cid, "void_uid")
    rem = _field("contract", cid, "void_remark")
    _c1, main = _list(sess_admin, "contract", page=1, limit=500)
    _c2, void = _list(sess_admin, "contract", view="voidlist", page=1, limit=500)
    gone = not any(str(x.get("id")) == str(cid) for x in main)
    invoid = any(str(x.get("id")) == str(cid) for x in void)
    _st, html = _html(sess_admin, "/contract/contract/view", {"id": cid})
    no_area = 'id="checkBox"' not in html

    K.expect(K.is_allowed(res2), "作废动作应成功（code=0）")
    K.expect((st["void_time"] > 0, "void_time=%s（期望 >0）" % st["void_time"]),
             "作废应以 void_time>0 标记")
    K.expect((str(vu) == str(K.UID[SUPER]), "void_uid=%s（期望 %s）" % (vu, K.UID[SUPER])),
             "作废应记录操作人 void_uid")
    K.expect((bool(rem), "void_remark=%s" % (rem or "")[:30]), "作废理由应被保存")
    K.expect((gone and invoid, "主列表已转出=%s；作废列表可见=%s" % (gone, invoid)),
             "作废件应从主列表转出并出现在作废合同列表")
    K.expect((no_area, "详情页仍渲染审批/归档操作区=%s" % (not no_area)),
             "作废后详情页不应再渲染审批操作区")


def test_ht_029_status_chain(sess_fengcailing, sess_admin, ht_ids):
    """HT-029 状态流转链：建单(0) → 提交审批(1) → 审批通过(2) → 归档(check_status 保持 2)。

    账号：建单/提交 fengcailing(uid=4)；审批/归档 admin(uid=1)。
    关联缺陷：无。判据出处：每步都回读 oa_contract 状态字段并与库中核对；
    本系统以 archive_time 标记归档（归档阶段 check_status 保持 2）。
    """
    cid, res, _p = _add(sess_fengcailing, MGR, "contract", ht_ids, cost="13000.00")
    assert cid, "建单失败：%s" % K.brief(res)

    steps = []
    st = _state("contract", cid)
    steps.append(("建单(草稿)", st["check_status"], st["archive_time"]))
    fid, r2 = _submit(sess_fengcailing, "contract", cid, SUPER)
    K.record("fengcailing POST /api/check/submit_check", r2)
    st = _state("contract", cid)
    steps.append(("提交审批(审批中)", st["check_status"], st["archive_time"]))
    r3 = _flow_check(sess_admin, "contract", cid, fid, 1, "通过")
    K.record("admin POST /api/check/flow_check(check=1)", r3)
    st = _state("contract", cid)
    steps.append(("审批通过", st["check_status"], st["archive_time"]))
    r4 = K.call(sess_admin, "/contract/api/contract_archive", method="POST",
                data={"id": cid, "archive_status": 1})
    K.record("admin POST /contract/api/contract_archive", r4)
    st = _state("contract", cid)
    steps.append(("归档", st["check_status"], st["archive_time"]))

    seq = [x[1] for x in steps]
    chain = " → ".join("%s: check_status=%s / archive_time=%s" % x for x in steps)
    K.expect((seq == [0, 1, 2, 2], "状态链 %s" % chain),
             "四阶段状态应为 0 → 1 → 2 → 2（归档不改 check_status，以 archive_time 标记）")
    K.expect((steps[3][2] > 0, "归档阶段 archive_time=%s（期望 >0）" % steps[3][2]),
             "归档阶段应写入 archive_time")
    K.expect((K.is_allowed(r2)[0] and K.is_allowed(r3)[0] and K.is_allowed(r4)[0],
              "各步 code=%s/%s/%s" % (r2.get("code"), r3.get("code"), r4.get("code"))),
             "三个流转动作都应成功（无状态回退、无卡死）")


# ===========================================================================
# HT-030  列表筛选与分页（账号：超管 admin）
# ===========================================================================
_FILTERS = [
    ("类型=普通(types=1)", {"types": 1}, " AND types=1"),
    ("类型=产品(types=2)", {"types": 2}, " AND types=2"),
    ("状态=待提交(check_status=0)", {"check_status": 0}, " AND check_status=0"),
    ("状态=审批中(check_status=1)", {"check_status": 1}, " AND check_status=1"),
    ("状态=审批通过(check_status=2)", {"check_status": 2}, " AND check_status=2"),
    ("分类(cate_id=1)", {"cate_id": 1}, " AND cate_id=1"),
]


def test_ht_030_list_filter_and_paging(sess_admin, sess_fengcailing, ht_ids):
    """HT-030 列表筛选与分页：6 组筛选逐项与库一致，全量翻页不重不漏，双视角不越权。

    账号：admin(uid=1) 做筛选/分页基线（isAuth 对 uid==1 短路 ⇒ 恒为合同管理员，可见全量）；
    fengcailing(uid=4) 复核数据权限分支。
    关联缺陷：无（观察项：oa_data_auth.contract_admin.conf_1 为空 ⇒ 数据权限 whereOr 分支
    在演示实例里只对普通账号生效；本次以管理岗复核该分支，其可见条数未超出本人相关范围）。
    判据出处：Contract::datalist 的基线 where 为 delete_time=0 AND archive_time=0 AND
    stop_time=0 AND void_time=0，再叠加各筛选条件；分页由 paginate 提供。
    """
    detail, bad = [], []
    for kind, cn in (("contract", "销售"), ("purchase", "采购")):
        main_total = _live_count(kind)
        cnt0, _lst0 = _list(sess_admin, kind, page=1, limit=1)
        detail.append("%s合同列表总数 库%s/接口%s" % (cn, main_total, cnt0))
        if str(cnt0) != str(main_total):
            bad.append("%s 列表总数不一致（库 %s / 接口 %s）" % (cn, main_total, cnt0))

        for label, params, extra in _FILTERS:
            db_n = _live_count(kind, extra)
            q = {"page": 1, "limit": 500}
            q.update(params)
            cnt, lst = _list(sess_admin, kind, **q)
            key = list(params)[0]
            wrong = [x.get("id") for x in lst if str(x.get(key)) != str(params[key])]
            detail.append("%s %s → 库%d / 接口count=%s / 返回%d / 越界%d"
                          % (cn, label, db_n, cnt, len(lst), len(wrong)))
            if str(cnt) != str(db_n) or wrong or len(lst) != db_n:
                bad.append("%s %s 不一致（库 %d / count %s / 返回 %d / 越界 %s）"
                           % (cn, label, db_n, cnt, len(lst), wrong[:5]))

        seen, page, dup, pages, cnt = [], 1, [], 0, 0
        while page <= 60:
            cnt, lst = _list(sess_admin, kind, page=page, limit=5)
            pages += 1
            if not lst:
                break
            ids = [str(x.get("id")) for x in lst]
            dup += [i for i in ids if i in seen]
            seen += ids
            page += 1
        detail.append("%s 分页：翻 %d 页取到 %d 条 / 接口总数 %s / 重复 %s"
                      % (cn, pages, len(seen), cnt, dup or "无"))
        if dup or len(seen) != int(cnt or 0):
            bad.append("%s 分页不重不漏校验失败（取到 %d / 总数 %s / 重复 %s）"
                       % (cn, len(seen), cnt, dup[:5]))

    # 跨类型串数据要用 code 比对，不能比 id —— oa_contract 与 oa_purchase 是两张独立自增的表
    pcodes = {str(x[0]) for x in K.rows("SELECT code FROM oa_purchase;")}
    _scnt, sales = _list(sess_admin, "contract", page=1, limit=1000)
    cross = [x.get("code") for x in sales if str(x.get("code")) in pcodes]
    detail.append("跨类型串数据：销售列表 %d 条中 code 命中采购表的 %d 条" % (len(sales), len(cross)))
    if cross:
        bad.append("销售列表串入采购合同 code %s" % cross[:5])

    # 数据权限视角（管理岗 fengcailing 非合同管理员）—— 自洽校验
    fcnt, _flist = _list(sess_fengcailing, "contract", page=1, limit=1000)
    fscope = int(K.scalar(
        "SELECT COUNT(*) FROM oa_contract WHERE delete_time=0 AND archive_time=0 "
        "AND stop_time=0 AND void_time=0 AND (admin_id=%d OR prepared_uid=%d "
        "OR sign_uid=%d OR keeper_uid=%d OR FIND_IN_SET('%d',share_ids) "
        "OR FIND_IN_SET('%d',check_uids) OR FIND_IN_SET('%d',check_history_uids));"
        % ((K.UID[MGR],) * 7), "0") or 0)
    detail.append("数据权限：管理岗可见 %s 条（按其本人范围算得 %d 条）" % (fcnt, fscope))
    if int(fcnt or 0) > fscope:
        bad.append("管理岗可见条数 %s 超出其数据权限范围 %d" % (fcnt, fscope))

    K.expect((not bad, "以下项不一致：%s；明细：%s" % ("；".join(bad), "；".join(detail))),
             "列表筛选/分页/跨表隔离/数据权限应逐项与库中口径一致")
