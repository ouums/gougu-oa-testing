# -*- coding: utf-8 -*-
r"""
勾股OA 6.1.2 —— pytest 接口自动化 · 审批流模块（SP-001 ~ SP-035）

【本文件做什么】
  把 02_测试用例/测试用例.xlsx 里「审批流」35 条功能用例，按私有一次性执行器
  已确认的**请求参数、账号、判据**逐条重新发请求、自己比较，**一条用例 = 一个测试函数**。
  SP-023~026 的最终结论以该执行器为准；公开仓库只保留可重复的 pytest 版本。

【判定口径（与 oa_kit.py 的 9 个断言原语同源，不另立一套）】
  · 一律解析响应体 `code`，不看 HTTP 状态码（本系统 checkAuth 失败 = HTTP 200 + code=405）
  · 三态：通过 / 失败 / 待执行。已知缺陷用例标 `xfail(strict=True)` + `known_defect`，
    **不标 skip** —— skip 丢掉失败证据，也掩盖「系统当前是坏的」；xfail 两者都保留。
    xfail 用例内部写的是「**正确行为**」的断言：缺陷在 ⇒ xfailed；缺陷修好 ⇒ XPASS 判 FAILED。
  · 会签/或签（SP-023~026）与分页（SP-034）的判据**以库为准**（oa_leaves.check_status /
    check_step_sort / check_uids、oa_flow_step、oa_flow_record），接口返回值只作旁证。

【口径说明：本系统「建单」与「提交审批」是两个动作】
  建单  POST /home/{module}/add      -> oa_*.check_status = 0（待提交审批）
  提交  POST /api/check/submit_check -> oa_*.check_status = 1（审批中）
  用例（如 SP-001）把两步合并表述为「提交」，故一律按完整路径执行 —— 见执行器 sp_001 的「测试点」。

【数据回收】
  本模块新建的每一条单据都在建单处即刻 `K.track(<表>, id)` 登记，会话结束由
  pytest_cleanup.py 按**显式 id** 连同 oa_flow_step / oa_flow_record / oa_msg 一起回收；
  **绝不用 LIKE 批量删**（项目红线：mysql CLI 遇错即停不回滚，LIKE 写宽即不可逆）。
  会签/或签（SP-023~026）另需 `fixture_flow` —— 它自动重建 oa_flow 夹具流程、跑完自动回收。

【夹具流程 id 不硬编码】
  oa_flow.id 是自增的：夹具重建后落到哪一号取决于当时的 AUTO_INCREMENT ——
  本机 2026-09-21 实测出现过 22（会签）/ 23（或签）（历次运行的流程被回收后自增不回退），
  与文档口径的 20/21 不一致。故一律从 setup_flow_data.py 写出的侧车 flow_fixture.json
  取 id，并当场回读 oa_flow 校验 check_type=2 / status=1 / delete_time=0。
"""
import json
import pathlib
from datetime import datetime, timedelta

import pytest

import oa_kit as K

pytestmark = [pytest.mark.sp]

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT_DIR = HERE.parent                       # 03_接口测试
SIDECAR = SCRIPT_DIR / "flow_fixture.json"     # setup_flow_data.py 写出的夹具侧车

MARK = "【用例数据】"                            # 与执行器同一套数据标记（回收脚本据此识别）

# 模块 -> (表名, 自由审批流程 id, check_name)；与私有一次性执行器口径一致
MODULES = {
    "leaves":    ("oa_leaves",    1, "leaves"),
    "overtimes": ("oa_overtimes", 4, "overtimes"),
    "trips":     ("oa_trips",     2, "trips"),
    "outs":      ("oa_outs",      3, "outs"),
}

APPLICANT = "yucixin"       # uid=5  业务岗 —— SP 用例的「申请人 / 本人」
APPROVER = "fengcailing"    # uid=4  管理岗 —— SP 用例的「指定审批人 / 他人」
ADMIN = "admin"             # uid=1  超管   —— 两级审批的第二级、抄送人、非指定审批人

# 服务器时钟回拨的判定阈值（秒）。仅用于 SP-031 的时间线判据：
#   本机 WSL guest 时钟会在两次写入之间向后跳 144~147 秒（Windows 主机比 guest 快 146~147 秒，
#   实测 `System clock synchronized: no`），全表 12 组 id/time 反序的行对**跳幅全在这一区间**；
#   而逻辑性的顺序错乱只会是秒级。取 60 秒作分界：≥60s 判为环境时钟回拨，<60s 一律判失败。
_CLOCK_STEP_SECONDS = 60


# ===========================================================================
# 通用助手（请求参数与判据口径全部对齐执行器，不引入新口径）
# ===========================================================================
_UIDS = {}


def uid(username):
    """账号 uid 从库里查（不写死数字：写死会在账号被重建后静默错位）"""
    if username not in _UIDS:
        _UIDS[username] = int(K.scalar(
            "SELECT id FROM oa_admin WHERE username='%s' LIMIT 1;" % username, "0") or 0)
        assert _UIDS[username], "账号 %s 不在 oa_admin 中" % username
    return _UIDS[username]


def _table(module):
    return MODULES[module][0]


def _flow_id(module):
    return MODULES[module][1]


def _check_name(module):
    return MODULES[module][2]


def _dt(offset_days, hh, mm=0):
    """相对今天的时间串（与执行器 _dt 同格式：%Y-%m-%d %H:%M）"""
    t = datetime.now() + timedelta(days=offset_days)
    return t.replace(hour=hh, minute=mm, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")


def _reason(cid, note=""):
    return "%s%s%s" % (MARK, cid, ("-" + note) if note else "")


def _max_id(table):
    return int(K.scalar("SELECT IFNULL(MAX(id),0) FROM %s;" % table, "0") or 0)


def _state(module, rid):
    """单据当前审批状态（0 待提交 / 1 审批中 / 2 通过 / 3 不通过 / 4 已撤回）"""
    v = _field(module, rid, "check_status")
    return int(v) if v is not None else None


def _field(module, rid, col):
    r = K.rows("SELECT %s FROM %s WHERE id=%d;" % (col, _table(module), rid))
    if not r or r[0][0] == "NULL":
        return None
    return r[0][0]


def _add(sess, module, cid, note="", duration=1, start=None, end=None, **over):
    """建单：POST /home/{module}/add -> check_status=0。返回 (新单 id 或 None, 响应)。

    建单成功即登记该 id（track）—— 每条新建数据都必须登记，否则会话收尾留下污染。
    """
    data = {"duration": duration, "start_date": start or _dt(1, 9),
            "end_date": end or _dt(1, 18), "reason": _reason(cid, note), "file_ids": ""}
    if module == "leaves":
        data["types"] = 1                                  # 事假
    data.update(over)
    res = K.call(sess, "/home/%s/add" % module, method="POST", data=data)
    d = res.get("data")
    rid = d.get("return_id") if isinstance(d, dict) and res.get("code") == 0 else None
    K.track(_table(module), rid, note="%s 新建" % cid)
    return rid, res


def _sql_str(v):
    """SQL 字符串字面量（只用于 reason 这类固定文本）"""
    return "'%s'" % str(v).replace("\\", "\\\\").replace("'", "''")


def _reason_where(marker):
    if marker in (None, ""):
        return "(reason IS NULL OR reason='')"
    return "reason=%s" % _sql_str(marker)


def _count_where(table, where):
    return int(K.scalar("SELECT COUNT(*) FROM %s WHERE %s;" % (table, where), "0") or 0)


def _add_expect_reject(sess, module, data):
    """「应被服务端拒绝」的建单：返回 (响应, 本次请求真正落库的行数)。

    ⚠️ 计数**不用 MAX(id) 差值**：套件与别的模块并发跑时，别人插的行会混进差值里，
    把「已拒绝」读成「落库了」（本机 2026-09-21 与三个并发模块同跑时，实测出现过
    25 行的假增量，而那次请求只建了 1 行）。改用**本次请求自己的指纹**定位：
      · 接口回传了 return_id（code=0 时的建单响应）→ 精确到那一行；
      · 否则退化为「reason 文本 + id 水位」——SP-003/SP-014 的 reason 带用例号，唯一。
    即便用例期望被拒，也要为**万一真落库了的脏行**登记 id：缺陷回归时用例会红，
    但数据仍会被精确回收，不把「红」变成「脏」。
    """
    table = _table(module)
    where = _reason_where(data.get("reason"))
    before_id = _max_id(table)
    before_n = _count_where(table, where)
    res = K.call(sess, "/home/%s/add" % module, method="POST", data=data)
    after_n = _count_where(table, where)
    d = res.get("data")
    rid = d.get("return_id") if isinstance(d, dict) and res.get("code") == 0 else None
    if rid:
        K.track(table, rid, note="应被拒绝却落库的脏行（%s）" % module)
    else:
        for row in K.rows("SELECT id FROM %s WHERE %s AND id > %d;"
                          % (table, where, before_id)):
            K.track(table, row[0], note="应被拒绝却落库的脏行（%s，按指纹兜底）" % module)
    return res, (1 if rid else after_n - before_n)


def _submit(sess, module, rid, check_uids, copy_uids=""):
    """提交审批（自由审批流）：带 check_uids 即走 Check.php:169 的自由审批分支"""
    return K.call(sess, "/api/check/submit_check", method="POST", data={
        "check_name": _check_name(module), "action_id": rid, "flow_id": _flow_id(module),
        "check_uids": check_uids, "check_copy_uids": copy_uids})


def _submit_fixed(sess, module, rid, flow_id):
    """按**固定审批流**提交：故意不带 check_uids —— Check.php:89 判 !isset，
    传了（哪怕空串）就会退回自由审批模式、flow_list 根本不生效。"""
    return K.call(sess, "/api/check/submit_check", method="POST", data={
        "check_name": _check_name(module), "action_id": rid, "flow_id": flow_id,
        "check_copy_uids": ""})


def _check(sess, module, rid, check, content="", node=1, next_uids="", flow_id=None):
    """审批动作。check=1 通过 / 2 驳回（界面文案「退回」，同一分支）/ 3 撤回。

    check_node 与 content **必须带**（Check.php:385/467 无 isset 兜底，缺则 500）—— 见测试报告摘要。
    """
    return K.call(sess, "/api/check/flow_check", method="POST", data={
        "check_name": _check_name(module), "action_id": rid,
        "check_flow_id": _flow_id(module) if flow_id is None else flow_id,
        "check": check, "content": content, "check_node": node,
        "check_uids": next_uids, "check_files": ""})


def _flow_history(sess, module, rid, flow_id=0):
    """审批历史 —— 走界面真正使用的那条接口（oaCheck.js 渲染 #checkBox 的数据源）。

    口径要点：Check::get_flow_nodes() 查 oa_flow_record 时**不带 delete_time 条件**，
    故「驳回后重新提交」被软删的历史同样会返回并在时间线上展示 —— 断言审批历史
    必须用这个口径，自己加 delete_time=0 去数会得出相反结论。必须是 GET（POST 会 500）。
    """
    j = K.call(sess, "/api/check/get_flow_nodes",
               data={"check_name": _check_name(module), "action_id": rid, "flow_id": flow_id})
    d = j.get("data") or {}
    if isinstance(d, list):
        return []
    return d.get("check_record") or []


def _history_has(records, status, keyword):
    """审批历史里是否存在「指定动作 + 意见含关键字」的一条"""
    for r in records:
        if int(r.get("check_status", -1)) == status and keyword in str(r.get("content") or ""):
            return True
    return False


def _layui(res):
    """layui table 响应体 {code, count, data:[...]} -> (count, rows)"""
    d = res.get("data")
    if isinstance(d, dict):
        return res.get("count", d.get("count")), (d.get("data") or [])
    return res.get("count"), (d or [])


def _has_doc(lst, table, rid):
    """在 /home/approve/* 列表里找**本表本号**的单据。

    ⚠️ 必须带 table 限定：这些列表是 19 类单据 UNION 后的总列表，**各表 id 独立自增、
    跨表同号是常态**（本机 2026-09-21 实测 admin 待办里 id=1222、1007 各出现两次，
    分属两张表；探针也曾读到 leaves 单 1316 与 contract 1316 同号）。
    只比 id 会把「别的类型单据的同号行」当成自己那一单 —— SP-029 正是这样被误判过一次。
    行内 table_name 取值为来源单据表名去 `oa_` 前缀（leaves/trips/outs/overtimes/contract/
    purchase），实测该字段稳定返回。
    """
    return any(str(x.get("id")) == str(rid) and str(x.get("table_name")) == table
               for x in (lst or []))


# ---- /home/approve/* 是「19 类单据 UNION 后的总列表」，比对基数必须同口径 ----
_CHECK_TABLES = None


def _paged_traverse(sess, limit, pages_cap):
    """逐页取回某人单据列表，返回 (count, 页数, 键->首次出现页, 重复项, 是否降序)

    键取 (table_name, id)：19 类单据各表 id 独立自增，只比 id 会把不同表的同号行误判为重复。
    """
    seen, dup, pages, prev, order_ok, cnt = {}, [], 0, None, True, 0
    for pageno in range(1, pages_cap + 1):
        res = K.call(sess, "/home/approve/mylist", data={"page": pageno, "limit": limit})
        K.record("yucixin GET /home/approve/mylist?page=%d&limit=%d" % (pageno, limit), res)
        if pageno == 1:
            cnt = int(res.get("count") or 0)
        _, lst = _layui(res)
        if not lst:
            break
        pages += 1
        for x in lst:
            k = (x.get("table_name"), str(x.get("id")))
            if k in seen:
                dup.append((k, seen[k], pageno))
            else:
                seen[k] = pageno
            t = str(x.get("create_time") or "")
            if prev is not None and t > prev:
                order_ok = False
            prev = t
    return cnt, pages, seen, dup, order_ok


def _boundary_split_limits(rows_all, lo=5, hi=12, want=2):
    """从**实时数据**算出「页边界必然落在同秒并列组内部」的页长，返回 (页长列表, 并列组数)。

    排序键只有 create_time（秒级）时，并列组的跨页切分点正是顺序不稳定的位置；
    页长不同，切分点落点也不同。只试一个页长（执行器用的 10）等于只赌一个对齐点 ——
    本机 2026-09-21 实测：页长 10 恰好干净，而 2~9、11~12 全部稳定复现重复与漏行。
    """
    times = sorted((str(x.get("create_time") or "") for x in rows_all), reverse=True)
    total = len(times)
    groups, i = [], 0
    while i < total:
        j = i
        while j + 1 < total and times[j + 1] == times[i]:
            j += 1
        if j > i:
            groups.append((i + 1, j + 1))          # 1 基 rank 区间 [start, end]
        i = j + 1
    out = []
    for lim in range(hi, lo - 1, -1):
        if any(g[0] <= b < g[1] for g in groups for b in range(lim, total, lim)):
            out.append(lim)
        if len(out) >= want:
            break
    return out, len(groups)


def _check_tables():
    """oa_flow_cate 里 status=1 的 check_table（去前缀），且该表真实存在

    必须用与 Approve::get_list() 同一套 UNION 口径去库里数，否则「本人单据数」
    只数 oa_leaves 会和接口的跨模块总数对不上（执行器 check_tables() 同此实现）。
    """
    global _CHECK_TABLES
    if _CHECK_TABLES is None:
        cand = [r[0] for r in K.rows("SELECT DISTINCT check_table FROM oa_flow_cate WHERE status=1;")]
        exist = {r[0] for r in K.rows("SHOW TABLES;")}
        _CHECK_TABLES = [t for t in cand if ("oa_" + t) in exist]
    return _CHECK_TABLES


def _union_count(uid_=None, bucket=None, copy_to=None, pend_to=None):
    """按 Approve::get_list() 的同一套 SQL 条件数出「库里应有的条数」

    uid_    ：本人单据（admin_id = uid）
    bucket  ：1 = check_status<2（待审批）2 = =2（通过）3 = >2（不通过/撤回）
    copy_to ：抄送人 uid（FIND_IN_SET check_copy_uids）
    pend_to ：当前审批人 uid（FIND_IN_SET check_uids）
    """
    parts = []
    for t in _check_tables():
        c = "delete_time=0"
        if uid_ is not None:
            c += " AND admin_id=%d" % uid_
        if bucket == 1:
            c += " AND check_status < 2"
        elif bucket == 2:
            c += " AND check_status = 2"
        elif bucket == 3:
            c += " AND check_status > 2"
        if copy_to is not None:
            c += " AND FIND_IN_SET('%d', check_copy_uids) > 0" % copy_to
        if pend_to is not None:
            c += " AND FIND_IN_SET('%d', check_uids) > 0" % pend_to
        parts.append("SELECT COUNT(*) AS c FROM oa_%s WHERE %s" % (t, c))
    return int(K.scalar("SELECT SUM(c) FROM (%s) x;" % " UNION ALL ".join(parts), "0") or 0)


def _pend_hits(module, rid, who):
    """该单是否仍落在某人的待办里（与执行器 sp_008/032 同一判据）"""
    return int(K.scalar(
        "SELECT COUNT(*) FROM %s WHERE id=%d AND FIND_IN_SET('%d',check_uids);"
        % (_table(module), rid, who), "0") or 0)


def _flow_record_count(module, rid):
    """该单已产生的审批记录条数（Check.php:407 判会签是否凑满人数用的同一口径，不带 delete_time）"""
    return int(K.scalar("SELECT COUNT(*) FROM oa_flow_record WHERE action_id=%d AND check_table='%s';"
                        % (rid, _check_name(module)), "0") or 0)


# ---- 固定审批流（会签/或签）专用 -------------------------------------------------
def _fixture_flow_id(key):
    """从 flow_fixture.json 取夹具流程 id 并当场回读校验。

    **绝不硬编码 20/21**：oa_flow.id 自增，夹具重建后本机实测为 22/23（2026-09-21），
    写死数字会在夹具重建后指向别的（或不存在）的行。
    """
    assert SIDECAR.exists(), ("找不到夹具侧车 %s —— 请先跑 "
                              "setup_flow_data.py --apply" % SIDECAR)
    fx = json.loads(SIDECAR.read_text(encoding="utf-8"))
    for f in fx.get("流程") or []:
        if f.get("key") != key:
            continue
        fid = int(f["id"])
        r = K.rows("SELECT check_type, status, delete_time FROM oa_flow WHERE id=%d;" % fid)
        assert r and r[0][0] == "2" and r[0][1] == "1" and r[0][2] == "0", (
            "夹具流程 %s（id=%d）状态异常（期望 check_type=2/status=1/delete_time=0）：%r"
            % (f.get("title"), fid, r))
        return fid
    raise AssertionError("flow_fixture.json 中没有 key=%s 的流程定义" % key)


def _flow_steps(rid, flow_id):
    """该单在某固定流程下的审批步骤（sort 升序）；与执行器 steps_of 同口径"""
    out = []
    for r in K.rows("SELECT id, sort, check_role, check_types, IFNULL(check_uids,'') "
                    "FROM oa_flow_step WHERE action_id=%d AND flow_id=%d AND delete_time=0 "
                    "ORDER BY sort;" % (rid, flow_id)):
        out.append({"id": int(r[0]), "sort": int(r[1]), "check_role": int(r[2]),
                    "check_types": int(r[3]), "check_uids": r[4]})
    return out


def _step_records(rid, flow_id, step_id):
    """本级已产生的审批记录条数（Check.php:407 就是用这个数判会签是否凑满人数）
    口径与源码一致：**不带 delete_time 条件**。"""
    return int(K.scalar("SELECT COUNT(*) FROM oa_flow_record WHERE action_id=%d AND flow_id=%d "
                        "AND step_id=%d;" % (rid, flow_id, step_id), "0") or 0)


def _doc_state(rid):
    """固定审批流用例的库侧探针：check_status / check_step_sort / check_uids / check_flow_id"""
    r = K.rows("SELECT check_status, check_step_sort, IFNULL(check_uids,''), check_flow_id, delete_time "
               "FROM oa_leaves WHERE id=%d;" % rid)
    if not r:
        return None
    return {"check_status": int(r[0][0]), "check_step_sort": int(r[0][1]),
            "check_uids": r[0][2], "check_flow_id": int(r[0][3]), "delete_time": int(r[0][4])}


def _csv_uids(v):
    """逗号串 uid 列表 -> set。**按元素**比对，避免 "1" in "14" 这类子串误判。"""
    return {x for x in str(v or "").split(",") if x}


def _uids(state):
    return sorted(_csv_uids(state["check_uids"]))


def _in_todo(rid, who):
    """该单是否出现在该用户的待办里（与审批中心 list 同一判据：check_status=1 且 FIND_IN_SET>0）

    ⚠️ FIND_IN_SET 返回的是**1 基下标**（uid=4 在 "1,4" 里返回 2），故必须判 `> 0`。
    """
    r = K.rows("SELECT CONCAT(check_status, '|', IFNULL(FIND_IN_SET('%d', check_uids), 0)) "
               "FROM oa_leaves WHERE id=%d;" % (who, rid))
    if not r or "|" not in r[0][0]:
        return False
    st, _, pos = r[0][0].partition("|")
    return st == "1" and int(pos or 0) > 0


def _two_level_doc(sess_yc, sess_fc, cid, note):
    """建一张两级审批单：第一级 fengcailing 通过并指定第二级 admin，停在第二级。

    与 `_two_level_doc`（见下方实现）同一路径：
    自由审批模式下第一级通过时带 check_node=2 + check_uids 即追加下一级（Check.php:385-398）。
    """
    rid, res = _add(sess_yc, "leaves", cid, note)
    assert rid, "%s 建单失败：%s" % (cid, K.brief(res))
    sub = _submit(sess_yc, "leaves", rid, str(uid(APPROVER)))
    assert sub.get("code") == 0, "%s 提交审批失败：%s" % (cid, K.brief(sub))
    r1 = _check(sess_fc, "leaves", rid, 1, "第一级通过", node=2, next_uids=str(uid(ADMIN)))
    assert r1.get("code") == 0, "%s 第一级通过失败：%s" % (cid, K.brief(r1))
    return rid


def _fixed_flow_doc(sess_yc, cid, note, flow_id):
    """按固定审批流建单并提交，返回 (单据 id, 提交响应, 步骤列表, 提交后库侧状态)"""
    rid, res = _add(sess_yc, "leaves", cid, note)
    assert rid, "%s 建单失败：%s" % (cid, K.brief(res))
    sub = _submit_fixed(sess_yc, "leaves", rid, flow_id)
    return rid, sub, _flow_steps(rid, flow_id), _doc_state(rid)


# ===========================================================================
# SP-001 ~ SP-012  请假单
# ===========================================================================
def test_sp_001_leave_submit_normal(sess_yucixin):
    """SP-001 请假单-正常提交：提交成功、单据生成且状态为待审批、oa_leaves.admin_id 为当前账号。

    判据：见断言（sp_001）。
    判据：见断言。
    口径：本系统「建单」与「提交审批」是两个动作，用例的「提交」按两者合并的完整路径执行。
    """
    yc = uid(APPLICANT)
    rid, res = _add(sess_yucixin, "leaves", "SP-001", "正常提交", duration=1)
    K.record("yucixin POST /home/leaves/add", res)
    assert rid, "建单失败：%s" % K.brief(res)
    sub = _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    K.record("yucixin POST /api/check/submit_check", sub)
    st = _state("leaves", rid)
    aid = _field("leaves", rid, "admin_id")
    assert sub.get("code") == 0 and st == 1 and int(aid or 0) == yc, (
        "期望「提交 code=0 且 check_status=1（审批中）且 admin_id=%d」；实测：建单 id=%s code=%s；"
        "提交 code=%s msg=%s；check_status=%s；admin_id=%s"
        % (yc, rid, res.get("code"), sub.get("code"), sub.get("msg"), st, aid))


@pytest.mark.known_defect
@pytest.mark.xfail(reason="观察-06 必填项校验缺失（轻微 E）—— 接口层不校验必填，空表单也落库",
                   strict=True)
def test_sp_002_leave_required_blank(sess_yucixin):
    """SP-002 请假单-必填项为空提交：应被服务端必填校验拒绝、不生成单据（**已知缺陷**）。

    关联缺陷：观察-06（必填项校验缺失，查实但不单列缺陷，定级轻微 E）——
      界面侧 lay-verify=required 拦住、接口侧没拦，属「界面与接口的边界不一致」。
    判据：见断言（sp_002）+ EXTRA_SP_002（:336-348）。
    xfail 原因：实测 code=0「操作成功」并真的落库一条内容为空的草稿单；
      此处断言的是**正确行为**（被拒绝且不落库）—— 缺陷修复后本用例会 XPASS 判 FAILED，回来销账。
    """
    res, made = _add_expect_reject(sess_yucixin, "leaves", {"reason": "", "file_ids": ""})
    K.record("yucixin POST /home/leaves/add（必填项全空）", res)
    assert res.get("code") != 0 and made == 0, (
        "必填项全空应被服务端拒绝且不生成单据；实测：code=%s msg=%s，新增 %d 条脏记录"
        % (res.get("code"), res.get("msg"), made))


def test_sp_003_leave_time_reversed(sess_yucixin):
    """SP-003 请假单-结束时间早于开始时间：应被拒绝并提示、不生成单据。

    判据：见断言（sp_003），实测文案
      「结束时间不能小于开始时间」。判据：见断言。
    """
    res, made = _add_expect_reject(sess_yucixin, "leaves", {
        "types": 1, "duration": 1, "start_date": _dt(1, 18), "end_date": _dt(1, 9),
        "reason": _reason("SP-003", "时间倒挂"), "file_ids": ""})
    K.record("yucixin POST /home/leaves/add（结束早于开始）", res)
    assert res.get("code") != 0 and made == 0, (
        "结束早于开始应被拒绝且不生成单据；实测：code=%s msg=%s，新增 %d 条"
        % (res.get("code"), res.get("msg"), made))


def test_sp_004_leave_duration_half_day(sess_yucixin):
    """SP-004 请假单-请假时长 0.5 天（边界）：按 0.5 存储，无精度丢失。

    判据：见断言（sp_004）——
      字段 decimal(10,1)，按**数值**比较（回读是字符串 "0.5"）。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-004", "0.5天", duration=0.5,
                    start=_dt(1, 9), end=_dt(1, 12))
    K.record("yucixin POST /home/leaves/add（duration=0.5）", res)
    assert rid, "建单失败：%s" % K.brief(res)
    dv = _field("leaves", rid, "duration")
    assert float(dv) == 0.5, (
        "期望 oa_leaves.duration=0.5；实测 id=%s duration=%s（建单 code=%s）"
        % (rid, dv, res.get("code")))


def test_sp_005_leave_duration_999_days(sess_yucixin):
    """SP-005 请假单-请假时长 999 天（边界）：系统未设上限，按「允许则正常入参」判通过。

    判据：见断言（sp_005）——
      要求 duration 正确入库且 check_status=0，无 500、无数据错乱。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-005", "999天", duration=999,
                    start=_dt(1, 9), end=_dt(1000, 18))
    K.record("yucixin POST /home/leaves/add（duration=999）", res)
    assert rid, "建单失败：%s" % K.brief(res)
    dv = _field("leaves", rid, "duration")
    st = _state("leaves", rid)
    assert float(dv) == 999.0 and st == 0, (
        "期望 duration=999.0 且 check_status=0（待提交审批）；实测 id=%s duration=%s check_status=%s"
        % (rid, dv, st))


def test_sp_006_leave_status_pending(sess_yucixin):
    """SP-006 请假单-提交后状态为待审批：check_status=1（系统枚举值「审批中」，与「待审批」语义一致）。

    判据：见断言（sp_006）——
      用例写「待审批」，系统枚举值为 1，二者语义一致故判通过。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-006", "提交后待审批")
    assert rid, "建单失败：%s" % K.brief(res)
    sub = _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    K.record("yucixin POST /api/check/submit_check", sub)
    st = _state("leaves", rid)
    name = {0: "待提交审批", 1: "审批中", 2: "审批通过", 3: "审批不通过", 4: "已撤回"}.get(st, "未知")
    assert st == 1, ("期望 id=%s check_status=1（审批中）；实测 %s（%s），提交 code=%s"
                     % (rid, st, name, sub.get("code")))


def test_sp_007_leave_view_own_detail(sess_yucixin):
    """SP-007 请假单-本人可查看详情：详情页正常返回且字段与提交内容一致。

    判据：见断言（sp_007）——
      GET /home/leaves/view?id=<本人单据>，判 HTTP 200 且页面含提交时填写的事由。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-007", "详情可查看")
    assert rid, "建单失败：%s" % K.brief(res)
    sub = _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    assert sub.get("code") == 0, "提交失败：%s" % K.brief(sub)
    r = sess_yucixin.get(K.BASE + "/home/leaves/view", params={"id": rid},
                         timeout=30, allow_redirects=False)
    reason = _field("leaves", rid, "reason")
    K.record("yucixin GET /home/leaves/view?id=%s" % rid,
             {"http": r.status_code, "body_head": (r.text or "")[:120].replace("\n", " ")})
    assert r.status_code == 200 and reason and reason in r.text, (
        "期望 HTTP 200 且页面含事由「%s」；实测 HTTP %s、页面长度 %d、含事由=%s"
        % (reason, r.status_code, len(r.text or ""), bool(reason and reason in (r.text or ""))))


def test_sp_008_leave_withdraw_pending(sess_yucixin):
    """SP-008 请假单-本人撤回待审批单据：撤回成功、状态变 4（已撤回）、不再出现在审批人待办。

    判据：见断言（sp_008）。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-008", "撤回目标")
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    st_before = _state("leaves", rid)
    r = _check(sess_yucixin, "leaves", rid, 3, "用例测试撤回")
    K.record("yucixin POST /api/check/flow_check check=3（撤回）", r)
    st_after = _state("leaves", rid)
    pend = _pend_hits("leaves", rid, uid(APPROVER))
    assert st_before == 1 and r.get("code") == 0 and st_after == 4 and pend == 0, (
        "期望「撤回前 1；撤回 code=0；撤回后 4；审批人待办命中 0」；实测：撤回前 %s；撤回 code=%s "
        "msg=%s；撤回后 %s；待办命中 %s"
        % (st_before, r.get("code"), r.get("msg"), st_after, pend))


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-18 状态机只在界面层校验（一般 C）—— 对已通过单据撤回被接口放行",
                   strict=True)
def test_sp_009_leave_withdraw_approved(sess_yucixin, sess_fengcailing):
    """SP-009 请假单-已通过单据不可撤回：撤回应被拒绝、状态保持 2（已通过）（**已知缺陷**）。

    关联缺陷：BUG-18 状态机只在界面层校验（一般 C）。
    判据：见断言（sp_009）+ EXTRA_SP_009（:350-358）：
      该对照排除了「归属校验」解释（QX-046 实测撤回收他人单据被拒），缺的是**状态**校验。
    xfail 原因：实测撤回 code=0「操作成功」、状态 2→4；此处断言的是**正确行为**，
      缺陷修复后本用例会 XPASS 判 FAILED，回来销账。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-009", "已通过不可撤回")
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    ap = _check(sess_fengcailing, "leaves", rid, 1, "审批通过")
    assert ap.get("code") == 0, "审批通过失败（前置）：%s" % K.brief(ap)
    st_before = _state("leaves", rid)
    assert st_before == 2, "前置不成立：期望审批后 check_status=2，实测 %s" % st_before
    r = _check(sess_yucixin, "leaves", rid, 3, "用例测试撤回")
    K.record("yucixin POST /api/check/flow_check check=3（对已通过单据撤回）", r)
    st_after = _state("leaves", rid)
    assert r.get("code") != 0 and st_after == 2, (
        "已通过单据的撤回应被拒绝且状态保持 2；实测：撤回 code=%s msg=%s，状态 %s→%s"
        % (r.get("code"), r.get("msg"), st_before, st_after))


def test_sp_010_leave_approve_writeback(sess_yucixin, sess_fengcailing):
    """SP-010 请假单-审批通过后状态回写：状态变 2，且审批人/审批时间在**申请人可见**的审批历史里。

    判据：见断言（sp_010）——
      判定落在「申请人可见」：状态已回写 + get_flow_nodes 返回的最后一个通过记录有审批人与时间。
    观察项（不单列缺陷）：自由审批模式下单据表 check_time / check_last_uid 均不回写
      （Check.php 更新单据的 field 列表未含 check_time），但视图与前端 JS 均未消费这两列。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-010", "审批通过")
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    r = _check(sess_fengcailing, "leaves", rid, 1, "用例测试通过")
    K.record("fengcailing POST /api/check/flow_check check=1", r)
    st = _state("leaves", rid)
    hist = _flow_history(sess_yucixin, "leaves", rid)
    appr = [x for x in hist if int(x.get("check_status", -1)) == 1]
    shown = bool(appr) and bool(appr[-1].get("check_time_str")) and bool(appr[-1].get("name"))
    assert r.get("code") == 0 and st == 2 and shown, (
        "期望「审批 code=0、check_status=2、审批历史中可见审批人与时间」；实测 code=%s msg=%s；"
        "check_status=%s；历史 %d 条，末条通过记录=%s"
        % (r.get("code"), r.get("msg"), st, len(hist), appr[-1] if appr else None))


def test_sp_011_leave_reject_with_opinion(sess_yucixin, sess_fengcailing):
    """SP-011 请假单-审批驳回后状态与驳回意见：状态变 3，驳回意见对申请人可见。

    判据：见断言（sp_011）——
      详情页审批时间线由 oaCheck.js 取 /api/check/get_flow_nodes 渲染（#checkBox 是空容器），
      故可见性打在该接口上，而不是 HTML 里搜字。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-011", "审批驳回")
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    r = _check(sess_fengcailing, "leaves", rid, 2, "用例测试驳回")
    K.record("fengcailing POST /api/check/flow_check check=2（驳回）", r)
    st = _state("leaves", rid)
    hist = _flow_history(sess_yucixin, "leaves", rid)
    ok_opin = _history_has(hist, 2, "用例测试驳回")
    assert r.get("code") == 0 and st == 3 and ok_opin, (
        "期望「驳回 code=0、check_status=3、审批历史含『审核拒绝 + 用例测试驳回』」；"
        "实测 code=%s msg=%s；check_status=%s；历史 %d 条，命中驳回意见=%s"
        % (r.get("code"), r.get("msg"), st, len(hist), ok_opin))


def test_sp_012_leave_resubmit_after_reject(sess_yucixin, sess_fengcailing):
    """SP-012 请假单-驳回后重新提交：编辑（add 带 id）后重新提交，状态回到 1，审批链重新生成。

    判据：见断言（sp_012）——
      Leaves::add() 带 id 即走 edit 分支；判定含「新审批链 flow_step ≥ 1 条」。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-012", "驳回后重提")
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    r = _check(sess_fengcailing, "leaves", rid, 2, "用例测试驳回")
    assert r.get("code") == 0, "驳回失败（前置）：%s" % K.brief(r)
    st_before = _state("leaves", rid)
    edit = K.call(sess_yucixin, "/home/leaves/add", method="POST", data={
        "id": rid, "types": 1, "duration": 1, "start_date": _dt(1, 9), "end_date": _dt(1, 18),
        "reason": _reason("SP-012", "改写后重新提交"), "file_ids": ""})
    K.record("yucixin POST /home/leaves/add（带 id 编辑）", edit)
    sub = _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    K.record("yucixin POST /api/check/submit_check（重新提交）", sub)
    st_after = _state("leaves", rid)
    steps = int(K.scalar("SELECT COUNT(*) FROM oa_flow_step WHERE action_id=%d AND flow_id=1 "
                         "AND delete_time=0;" % rid, "0") or 0)
    assert st_before == 3 and edit.get("code") == 0 and sub.get("code") == 0 \
        and st_after == 1 and steps >= 1, (
        "期望「重提前 3；编辑 code=0；重新提交 code=0；重提后 1；新审批链 ≥1 条」；"
        "实测 重提前 %s；编辑 code=%s；提交 code=%s msg=%s；重提后 %s；flow_step=%s"
        % (st_before, edit.get("code"), sub.get("code"), sub.get("msg"), st_after, steps))


# ===========================================================================
# SP-013 ~ SP-019  加班 / 出差 / 外出
# ===========================================================================
def _submit_case(cid, module, title_cn, note, duration, start, end,
                 sess_yc, sess_fc, sess_ad, **over):
    """加班/出差/外出的「正常提交」用例 —— 四模块结构一致，抽成一个函数

    判据：见断言（_submit_case）——
      用例的「提交」= 建单动作，此时 check_status=0（待提交审批）。
    """
    rid, res = _add(sess_yc, module, cid, note, duration=duration, start=start, end=end, **over)
    K.record("yucixin POST /home/%s/add" % module, res)
    assert rid, "%s 建单失败：%s" % (cid, K.brief(res))
    st = _state(module, rid)
    assert st == 0, ("%s 期望 %s.check_status=0（待提交审批）；实测 id=%s check_status=%s"
                     % (cid, title_cn, rid, st))


def test_sp_013_overtime_submit_normal(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-013 加班单-正常提交：oa_overtimes 新增记录且状态为待审批（check_status=0）。

    判据：见断言（sp_013）→ _submit_case。
    """
    _submit_case("SP-013", "overtimes", "加班单", "正常提交", 2, _dt(1, 19), _dt(1, 21),
                 sess_yucixin, sess_fengcailing, sess_admin)


def test_sp_014_overtime_time_reversed(sess_yucixin):
    """SP-014 加班单-结束时间早于开始时间：应被拒绝并提示、不生成单据。

    判据：见断言（sp_014），实测文案
      「结束时间不能小于开始时间」。
    """
    res, made = _add_expect_reject(sess_yucixin, "overtimes", {
        "duration": 2, "start_date": _dt(1, 21), "end_date": _dt(1, 19),
        "reason": _reason("SP-014", "时间倒挂"), "file_ids": ""})
    K.record("yucixin POST /home/overtimes/add（结束早于开始）", res)
    assert res.get("code") != 0 and made == 0, (
        "结束早于开始应被拒绝且不生成单据；实测：code=%s msg=%s，新增 %d 条"
        % (res.get("code"), res.get("msg"), made))


def test_sp_015_overtime_approve(sess_yucixin, sess_fengcailing):
    """SP-015 加班单-审批通过：状态变 2，加班时长与提交值一致。

    判据：见断言（sp_015）—— duration 期望 2.0。
    """
    rid, res = _add(sess_yucixin, "overtimes", "SP-015", "审批通过", duration=2,
                    start=_dt(1, 19), end=_dt(1, 21))
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "overtimes", rid, str(uid(APPROVER)))
    r = _check(sess_fengcailing, "overtimes", rid, 1, "用例测试通过")
    K.record("fengcailing POST /api/check/flow_check check=1（加班单）", r)
    st = _state("overtimes", rid)
    dv = _field("overtimes", rid, "duration")
    assert r.get("code") == 0 and st == 2 and float(dv) == 2.0, (
        "期望「审批 code=0、check_status=2、duration=2.0」；实测 code=%s msg=%s；check_status=%s；"
        "duration=%s" % (r.get("code"), r.get("msg"), st, dv))


def test_sp_016_trip_submit_normal(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-016 出差单-正常提交：oa_trips 新增记录且状态为待审批（check_status=0）。

    判据：见断言（sp_016）→ _submit_case。
    """
    _submit_case("SP-016", "trips", "出差单", "正常提交", 2, _dt(1, 9), _dt(3, 18),
                 sess_yucixin, sess_fengcailing, sess_admin)


def test_sp_017_trip_reject(sess_yucixin, sess_fengcailing):
    """SP-017 出差单-审批驳回：状态变 3，驳回意见对申请人可见。

    判据：见断言（sp_017）——
      与 SP-011 同口径：详情页时间线由 JS 取 get_flow_nodes 渲染，按该接口判定可见性。
    """
    rid, res = _add(sess_yucixin, "trips", "SP-017", "审批驳回", duration=2,
                    start=_dt(1, 9), end=_dt(3, 18))
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "trips", rid, str(uid(APPROVER)))
    r = _check(sess_fengcailing, "trips", rid, 2, "用例测试驳回")
    K.record("fengcailing POST /api/check/flow_check check=2（出差单驳回）", r)
    st = _state("trips", rid)
    hist = _flow_history(sess_yucixin, "trips", rid)
    ok_opin = _history_has(hist, 2, "用例测试驳回")
    assert r.get("code") == 0 and st == 3 and ok_opin, (
        "期望「驳回 code=0、check_status=3、历史含驳回意见」；实测 code=%s msg=%s；check_status=%s；"
        "历史 %d 条，命中意见=%s" % (r.get("code"), r.get("msg"), st, len(hist), ok_opin))


def test_sp_018_out_submit_normal(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-018 外出单-正常提交：oa_outs 新增记录且状态为待审批（check_status=0）。

    判据：见断言（sp_018）→ _submit_case。
    """
    _submit_case("SP-018", "outs", "外出单", "正常提交", 3, _dt(1, 14), _dt(1, 17),
                 sess_yucixin, sess_fengcailing, sess_admin)


def test_sp_019_out_approve(sess_yucixin, sess_fengcailing):
    """SP-019 外出单-审批通过：状态变 2 并正确回写。

    判据：见断言（sp_019）。
    """
    rid, res = _add(sess_yucixin, "outs", "SP-019", "审批通过", duration=3,
                    start=_dt(1, 14), end=_dt(1, 17))
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "outs", rid, str(uid(APPROVER)))
    r = _check(sess_fengcailing, "outs", rid, 1, "用例测试通过")
    K.record("fengcailing POST /api/check/flow_check check=1（外出单）", r)
    st = _state("outs", rid)
    assert r.get("code") == 0 and st == 2, (
        "期望「审批 code=0、check_status=2」；实测 code=%s msg=%s；check_status=%s"
        % (r.get("code"), r.get("msg"), st))


# ===========================================================================
# SP-020 ~ SP-022  多级审批
# ===========================================================================
def test_sp_020_two_level_flow(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-020 全流程-多级审批逐级流转：两级均通过后状态才变 2。

    判据：见断言（sp_020）——
      审批链 flow_step=2 级；第一级通过后 check_status=1 且当前审批人转为 admin；
      第二级通过后 check_status=2。源码：Check.php:385-398（带 check_node=2 追加下一级）。
    """
    rid = _two_level_doc(sess_yucixin, sess_fengcailing, "SP-020", "两级审批")
    steps = int(K.scalar("SELECT COUNT(*) FROM oa_flow_step WHERE action_id=%d AND flow_id=1 "
                         "AND delete_time=0;" % rid, "0") or 0)
    st1 = _state("leaves", rid)
    uids_ = _field("leaves", rid, "check_uids")
    r2 = _check(sess_admin, "leaves", rid, 1, "第二级通过")
    K.record("admin POST /api/check/flow_check check=1（第二级）", r2)
    st2 = _state("leaves", rid)
    assert steps == 2 and st1 == 1 and str(uid(ADMIN)) in (uids_ or "") \
        and r2.get("code") == 0 and st2 == 2, (
        "期望「flow_step=2；第一级后 status=1 且当前审批人含 admin(%d)；第二级 code=0；最终 2」；"
        "实测 flow_step=%s；第一级后 status=%s；check_uids=%s；第二级 code=%s msg=%s；最终 %s"
        % (uid(ADMIN), steps, st1, uids_, r2.get("code"), r2.get("msg"), st2))


def test_sp_021_two_level_step_pointer(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-021 多级审批-第一级通过后流转到第二级：当前步骤字段指向第二级、第二级待办出现该单。

    判据：见断言（sp_021）——
      判据为 check_status=1 且 check_uids 含 admin(1) 且 check_history_uids 含 fengcailing(4)。
    """
    rid = _two_level_doc(sess_yucixin, sess_fengcailing, "SP-021", "流转到第二级")
    st = _state("leaves", rid)
    sort_ = _field("leaves", rid, "check_step_sort")
    uids_ = _field("leaves", rid, "check_uids") or ""
    hist_uids = _field("leaves", rid, "check_history_uids") or ""
    assert st == 1 and str(uid(ADMIN)) in uids_ and str(uid(APPROVER)) in hist_uids, (
        "期望「check_status=1；check_uids 含 admin(%d)；check_history_uids 含 fengcailing(%d)」；"
        "实测 check_status=%s；check_step_sort=%s；check_uids=%s；check_history_uids=%s"
        % (uid(ADMIN), uid(APPROVER), st, sort_, uids_, hist_uids))


def test_sp_022_two_level_middle_reject(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-022 多级审批-中间级驳回后流程终止：状态变 3、不再产生后续待办、第一级记录保留。

    判据：见断言（sp_022）——
      记录数 rec_after ≥ rec_before（第一级记录保留）。源码：Check.php:527 起的驳回分支。
    """
    rid = _two_level_doc(sess_yucixin, sess_fengcailing, "SP-022", "第二级驳回")
    rec_before = _flow_record_count("leaves", rid)
    r = _check(sess_admin, "leaves", rid, 2, "用例测试驳回")
    K.record("admin POST /api/check/flow_check check=2（第二级驳回）", r)
    st = _state("leaves", rid)
    rec_after = _flow_record_count("leaves", rid)
    pend = int(K.scalar("SELECT COUNT(*) FROM oa_leaves WHERE id=%d AND check_status=1;" % rid, "0") or 0)
    assert r.get("code") == 0 and st == 3 and pend == 0 and rec_after >= rec_before, (
        "期望「驳回 code=0；check_status=3；后续待办 0；记录 %d→≥%d」；实测 code=%s msg=%s；"
        "check_status=%s；待办=%s；记录 %d→%d"
        % (rec_before, rec_before, r.get("code"), r.get("msg"), st, pend, rec_before, rec_after))


# ===========================================================================
# SP-023 ~ SP-026  会签 / 或签（需要 fixture_flow：自动重建 oa_flow 夹具流程，跑完自动回收）
#   最终口径以已确认结论为准（4 条全部通过）
# ===========================================================================
@pytest.mark.needs_flow_fixture
def test_sp_023_co_sign_all_pass(fixture_flow, sess_yucixin, sess_fengcailing, sess_admin):
    """SP-023 会签-全部通过才通过：前几位通过后仍「审批中」，最后一位通过后才变「已通过」。

    判据：见断言（sp_023），判据
      app/api/controller/Check.php:414（check_types==1 需凑满本级人数才流转）。
    判据：见断言。
    口径：固定审批流提交**不带 check_uids**（Check.php:89 的 !isset 分支），否则退回自由审批。
    """
    fid = _fixture_flow_id("co_sign")
    rid, sub, st, d0 = _fixed_flow_doc(sess_yucixin, "SP-023", "会签全过", fid)
    K.record("yucixin POST /api/check/submit_check(flow_id=%d，不带 check_uids)" % fid, sub)
    assert sub.get("code") == 0 and len(st) == 1 and d0, (
        "提交固定审批流应成功且生成 1 级步骤；实测 code=%s msg=%s；步骤 %d 级；单据 %s"
        % (sub.get("code"), sub.get("msg"), len(st), d0))
    lv = st[0]
    a1 = _check(sess_admin, "leaves", rid, 1, "SP-023 会签第1人通过", flow_id=fid)
    K.record("admin POST /api/check/flow_check（会签第1人）", a1)
    d1 = _doc_state(rid)
    n1 = _step_records(rid, fid, lv["id"])
    a2 = _check(sess_fengcailing, "leaves", rid, 1, "SP-023 会签第2人通过", flow_id=fid)
    K.record("fengcailing POST /api/check/flow_check（会签第2人）", a2)
    d2 = _doc_state(rid)
    assert (lv["check_types"] == 1 and _uids(d0) == ["1", "4"] and d0["check_status"] == 1
            and a1.get("code") == 0
            and d1["check_status"] == 1 and _uids(d1) == ["4"] and d1["check_step_sort"] == 0
            and n1 == 1
            and a2.get("code") == 0 and d2["check_status"] == 2 and _uids(d2) == []), (
        "期望「提交后 check_status=1 且 check_uids=1,4；第1位通过后仍为 1 且仅剩第2位（本级记录 1 条）；"
        "第2位通过后才变 2」。实测：提交后 check_status=%s check_uids=%s（步骤 %d 级，check_types=%s）；"
        "第1位通过 code=%s 后 check_status=%s check_uids=%s check_step_sort=%s 本级记录 %s；"
        "第2位通过 code=%s 后 check_status=%s check_uids=%s（第1人 msg=%s）"
        % (d0["check_status"], d0["check_uids"], len(st), lv["check_types"], a1.get("code"),
           d1["check_status"], d1["check_uids"], d1["check_step_sort"], n1,
           a2.get("code"), d2["check_status"], d2["check_uids"] or "(空)", a1.get("msg")))


@pytest.mark.needs_flow_fixture
def test_sp_024_co_sign_one_reject(fixture_flow, sess_yucixin, sess_fengcailing, sess_admin):
    """SP-024 会签-一人驳回则整体驳回：整单置不通过（3）、check_uids 清空、其余会签人待办终止。

    判据：见断言（sp_024），判据
      Check.php:527 起的驳回分支（本级 check_role=4≠5，不走 :542 的回退逻辑，直接置终态 3）。
    判据：见断言。
    """
    fid = _fixture_flow_id("co_sign")
    rid, sub, st, d0 = _fixed_flow_doc(sess_yucixin, "SP-024", "会签一人驳回", fid)
    assert sub.get("code") == 0 and len(st) == 1, (
        "提交固定审批流应成功且生成 1 级步骤；实测 code=%s msg=%s；步骤 %d 级"
        % (sub.get("code"), sub.get("msg"), len(st)))
    a1 = _check(sess_admin, "leaves", rid, 1, "SP-024 会签第1人通过", flow_id=fid)
    K.record("admin POST /api/check/flow_check（会签第1人通过）", a1)
    d1 = _doc_state(rid)
    todo_before = _in_todo(rid, uid(APPROVER))
    a2 = _check(sess_fengcailing, "leaves", rid, 2, "SP-024 会签第2人驳回", flow_id=fid)
    K.record("fengcailing POST /api/check/flow_check（会签第2人驳回）", a2)
    d2 = _doc_state(rid)
    todo_after = _in_todo(rid, uid(APPROVER))
    assert (a1.get("code") == 0 and d1["check_status"] == 1 and _uids(d1) == ["4"]
            and todo_before is True and a2.get("code") == 0
            and d2["check_status"] == 3 and _uids(d2) == [] and todo_after is False), (
        "期望「第1位通过后 check_status=1、check_uids=4、第2位有待办；第2位驳回后 check_status=3、"
        "check_uids 清空、第2位待办终止」。实测：第1位通过 code=%s 后 status=%s uids=%s 待办=%s；"
        "第2位驳回 code=%s msg=%s 后 status=%s uids=%s 待办=%s"
        % (a1.get("code"), d1["check_status"], d1["check_uids"], todo_before,
           a2.get("code"), a2.get("msg"), d2["check_status"], d2["check_uids"] or "(空)", todo_after))


@pytest.mark.needs_flow_fixture
def test_sp_025_or_sign_any_pass(fixture_flow, sess_yucixin, sess_admin):
    """SP-025 或签-任一人通过即通过：任一人通过即流转，不等本级其余审批人。

    判据：见断言（sp_025），判据 Check.php:414
      （check_types==2 任一人通过即流转）。
    预期结果修订（已确认结论明载）：或签处于**非末级**时，任一人通过是
      「进入下一级」而非「整单已通过」—— 故第1级通过后 check_status 仍=1、check_step_sort 0→1、
      check_uids 改写为第2级审批人。
    """
    fid = _fixture_flow_id("or_sign")
    rid, sub, st, d0 = _fixed_flow_doc(sess_yucixin, "SP-025", "或签任一通过", fid)
    assert sub.get("code") == 0 and len(st) == 2 and d0, (
        "提交或签固定流程应成功且生成 2 级步骤；实测 code=%s msg=%s；步骤 %d 级"
        % (sub.get("code"), sub.get("msg"), len(st)))
    lv0, lv1 = st[0], st[1]
    todo_before = _in_todo(rid, uid(APPROVER))
    a1 = _check(sess_admin, "leaves", rid, 1, "SP-025 或签任一人通过", flow_id=fid)
    K.record("admin POST /api/check/flow_check（或签第1位通过）", a1)
    d1 = _doc_state(rid)
    n1 = _step_records(rid, fid, lv0["id"])
    todo_loser = _in_todo(rid, uid(APPROVER))
    assert (lv0["check_types"] == 2 and _uids(d0) == ["1", "4"] and d0["check_status"] == 1
            and lv1["sort"] == 1 and _uids(lv1) == ["1"]
            and todo_before is True and a1.get("code") == 0
            and d1["check_status"] == 1 and d1["check_step_sort"] == 1
            and _uids(d1) == _uids(lv1) and n1 == 1 and todo_loser is False), (
        "期望「或签第1级 check_types=2、提交后 check_status=1 且 check_uids=1,4；仅第1位通过后"
        "check_step_sort 0→1、check_status 仍 1、check_uids 改写为第2级审批人、第1级记录仅 1 条、"
        "落选者待办终止」。实测：check_types=%s；提交后 status=%s uids=%s；第1位通过 code=%s msg=%s 后 "
        "step_sort=%s status=%s uids=%s 第1级记录=%s 落选者待办 %s→%s"
        % (lv0["check_types"], d0["check_status"], d0["check_uids"], a1.get("code"), a1.get("msg"),
           d1["check_step_sort"], d1["check_status"], d1["check_uids"], n1,
           todo_before, todo_loser))


@pytest.mark.needs_flow_fixture
def test_sp_026_or_sign_loser_todo_cancelled(fixture_flow, sess_yucixin, sess_admin):
    """SP-026 或签-其余审批人待办自动取消：落选者待办消失，且单据**仍在审批中**（非整单结束）。

    判据：见断言（sp_026）。分辨力来自夹具刻意配的第 2 级：
      只有流程流转到下一级，「落选取消」才与「整单审批完成清空待办」区分开。
      判据含「落选者跨模块待办总数减少 1」（与审批中心同一判据 union_count(pend_to=4)）。
      源码：Check.php:411-413（移出已通过者）与 :430（或签流转时整体改写为下一级审批人）。
    判据：见断言。
    """
    fid = _fixture_flow_id("or_sign")
    rid, sub, st, d0 = _fixed_flow_doc(sess_yucixin, "SP-026", "或签待办取消", fid)
    assert sub.get("code") == 0 and len(st) == 2 and d0, (
        "提交或签固定流程应成功且生成 2 级步骤；实测 code=%s msg=%s；步骤 %d 级"
        % (sub.get("code"), sub.get("msg"), len(st)))
    lv0 = st[0]
    loser = uid(APPROVER)
    todo_before = _in_todo(rid, loser)
    pend_before = _union_count(pend_to=loser)
    a1 = _check(sess_admin, "leaves", rid, 1, "SP-026 或签任一人通过", flow_id=fid)
    K.record("admin POST /api/check/flow_check（或签第1位通过）", a1)
    todo_after = _in_todo(rid, loser)
    pend_after = _union_count(pend_to=loser)
    d1 = _doc_state(rid)
    n1 = _step_records(rid, fid, lv0["id"])
    assert (lv0["check_types"] == 2 and todo_before is True and todo_after is False
            and pend_before - pend_after == 1
            and d1["check_status"] == 1 and d1["check_step_sort"] == 1
            and a1.get("code") == 0 and n1 == 1), (
        "期望「落选者本单待办 True→False、跨模块待办总数差 1，且 check_status 仍=1、check_step_sort=1"
        "（证明是落选取消而非整单结束）」。实测：check_types=%s；通过 code=%s msg=%s；"
        "本单待办 %s→%s；跨模块待办 %d→%d（差 %d）；check_status=%s；check_step_sort=%s；"
        "check_uids=%s；第1级记录 %s"
        % (lv0["check_types"], a1.get("code"), a1.get("msg"), todo_before, todo_after,
           pend_before, pend_after, pend_before - pend_after,
           d1["check_status"], d1["check_step_sort"], d1["check_uids"], n1))


# ===========================================================================
# SP-027 ~ SP-032  越权 / 待办 / 抄送 / 退回 / 历史 / 撤回
# ===========================================================================
def test_sp_027_approve_not_designated(sess_yucixin, sess_admin):
    """SP-027 审批动作-非指定审批人调用审批接口：被拦截、状态不变、零审批痕迹。

    判据：见断言（sp_027）——
      指定审批人为 fengcailing(4)，由 admin(1) 发起审批；实测拦截文案「您没权限审核该审批」。
    关联记录：测试报告摘要 ④（同一系统内审批动作有归属校验 vs 四模块 CRUD 没有）。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-027", "非指定审批人")
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    st_before = _state("leaves", rid)
    rec_before = _flow_record_count("leaves", rid)
    r = _check(sess_admin, "leaves", rid, 1, "越权审批尝试")
    K.record("admin POST /api/check/flow_check check=1（非指定审批人）", r)
    st_after = _state("leaves", rid)
    rec_after = _flow_record_count("leaves", rid)
    assert st_before == 1 and r.get("code") != 0 and st_after == 1 and rec_after == rec_before, (
        "期望「被拦截（code≠0）、状态 1→1 不变、审批记录 0 痕迹」；实测 code=%s msg=%s；"
        "状态 %s→%s；记录 %s→%s"
        % (r.get("code"), r.get("msg"), st_before, st_after, rec_before, rec_after))


def test_sp_028_my_checklist_only_own(sess_fengcailing):
    """SP-028 待我审批列表-仅列出本人待审批单据：每条当前审批人均含本人，且条数与库一致。

    判据：见断言（sp_028）——
      status=1 才是「仅当前步骤审批人是我」（不带 status 会连历史审批一起返回）；
      比对基数用同一套 19 类单据 UNION 口径（union_count(pend_to=4, bucket=1)）。
    """
    fc = uid(APPROVER)
    bad, cnt, n, db_n = [], 0, 0, 0
    for attempt in (1, 2):
        # 「接口 vs 库」是同一瞬间的两条口径：别的模块正在建单/审批时可能瞬时错位，
        # 故不一致就整对比读一次 —— 真缺陷会两轮都不一致，环境噪声只影响其中一轮。
        res = K.call(sess_fengcailing, "/home/approve/checklist",
                     data={"page": 1, "limit": 500, "status": 1})
        K.record("fengcailing GET /home/approve/checklist?status=1（第 %d 轮）" % attempt, res)
        assert res.get("code") == 0, "待我审批列表接口未正常返回：%s" % K.brief(res)
        cnt, lst = _layui(res)
        lst = lst or []
        db_n = _union_count(pend_to=fc, bucket=1)
        bad = [(it.get("id"), it.get("check_uids")) for it in lst
               if str(fc) not in str(it.get("check_uids") or "").split(",")]
        n = len(lst)
        if not bad and n == db_n:
            break
    assert not bad and n == db_n, (
        "期望「列表每条当前审批人均含 fengcailing(uid=%d)，且条数与库中同口径一致」；"
        "实测（两轮读数均不一致）：列表 %d 条（count=%s），库中 %d 条；"
        "%d 条当前审批人不含本人：%s" % (fc, n, cnt, db_n, len(bad), bad[:5]))


def test_sp_029_copy_sees_not_approves(sess_yucixin, sess_admin):
    """SP-029 抄送-抄送人可见但不需审批：抄送人可见该单，且不出现在其待我审批列表。

    判据：见断言（sp_029）——
      提交时带 check_copy_uids=admin(1)，落库字段 oa_leaves.check_copy_uids 应含该 uid。
    比对口径：列表是按 (table_name, id) 定位本单（见 _has_doc 的跨表同号说明）；
      另加库级断言「抄送人 uid 不在 check_uids 里」——「可见但不需审批」的落库依据。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-029", "抄送")
    assert rid, "建单失败：%s" % K.brief(res)
    copy_uid = uid(ADMIN)
    sub = _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)), copy_uids=str(copy_uid))
    K.record("yucixin POST /api/check/submit_check（带 check_copy_uids）", sub)
    ccopy = _field("leaves", rid, "check_copy_uids") or ""
    cuids = _field("leaves", rid, "check_uids") or ""
    cl = K.call(sess_admin, "/home/approve/copylist", data={"page": 1, "limit": 500})
    K.record("admin GET /home/approve/copylist", cl)
    _, cl_data = _layui(cl)
    seen = _has_doc(cl_data, "leaves", rid)
    ck = K.call(sess_admin, "/home/approve/checklist", data={"page": 1, "limit": 500, "status": 1})
    K.record("admin GET /home/approve/checklist?status=1", ck)
    _, ck_data = _layui(ck)
    in_todo = _has_doc(ck_data, "leaves", rid)
    assert (sub.get("code") == 0 and str(copy_uid) in _csv_uids(ccopy)
            and str(copy_uid) not in _csv_uids(cuids) and seen and not in_todo), (
        "期望「提交 code=0；check_copy_uids 含 %d、check_uids 不含 %d（抄送人不是审批人）；"
        "抄送列表可见 leaves-%s；待办列表不出现 leaves-%s」；"
        "实测：提交 code=%s；check_copy_uids=%s；check_uids=%s；抄送可见=%s；待办中出现=%s"
        % (copy_uid, copy_uid, rid, rid, sub.get("code"), ccopy, cuids, seen, in_todo))


def test_sp_030_back_to_applicant(sess_yucixin, sess_fengcailing):
    """SP-030 退回-审批人退回后回到申请人：状态 3（审批不通过），申请人可修改后重新提交。

    判据：见断言（sp_030）——
      本系统「退回」与「驳回」同走 check=2 分支（Check.php），故落库状态同为 3。
    """
    rid, res = _add(sess_yucixin, "leaves", "SP-030", "退回")
    assert rid, "建单失败：%s" % K.brief(res)
    _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    r = _check(sess_fengcailing, "leaves", rid, 2, "用例测试退回")
    K.record("fengcailing POST /api/check/flow_check check=2（退回）", r)
    st = _state("leaves", rid)
    edit = K.call(sess_yucixin, "/home/leaves/add", method="POST", data={
        "id": rid, "types": 1, "duration": 1, "start_date": _dt(1, 9), "end_date": _dt(1, 18),
        "reason": _reason("SP-030", "退回后改写"), "file_ids": ""})
    sub2 = _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    K.record("yucixin POST /home/leaves/add（退回后改写）+ submit_check", sub2)
    st2 = _state("leaves", rid)
    assert r.get("code") == 0 and st == 3 and st2 == 1, (
        "期望「退回 code=0、check_status=3；申请人改写后重新提交，状态回到 1」；"
        "实测 退回 code=%s msg=%s；状态=%s；改写 code=%s；重新提交 code=%s；状态=%s"
        % (r.get("code"), r.get("msg"), st, edit.get("code"), sub2.get("code"), st2))


def test_sp_031_approval_history_chain(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-031 审批历史记录-完整记录审批链：多级 + 驳回后重提的历史完整、时间升序、元数据齐全。

    判据：见断言（sp_031）——
      口径：get_flow_nodes 查 oa_flow_record **不过滤 delete_time**，重提软删的旧记录仍返回，
      故历史 ≥4 条（提交/一级通过/驳回/重提提交）。

    ⚠️ 与执行器唯一的一处口径差异（实测证据在下方 K.record，失败时随报告落盘）：
      执行器写的是 `ordered = times == sorted(times)` —— 隐含「服务器时钟单调」。本机实测
      **不成立**：WSL guest 时钟会在两次写入之间向后跳约 146 秒（Windows 主机时钟比 guest
      快 146~147 秒，实测 `System clock synchronized: no`）。全表实测 12 组「id 升序而
      check_time 反序」的行对，**跳幅全部是 144~147 秒**，横跨 leaves/trips/contract/purchase
      四类单据（如 leaves 1106：提交/退回=1789748701，其后重提=1789748556；而该单自己的
      create_time 正是 1789748701）；而逻辑性的顺序错乱只会是**秒级**小跳。
      故本用例把判据拆成两半：**逻辑顺序**必须是 id 升序（真错立即红），时间不单调时
      只接受「≥60 秒的时钟回拨」这一环境解释，其它任何反序一律判失败 ——
      既不放走真缺陷，也不把 WSL 时钟回拨记成产品缺陷。
      同时留档一条产品观察：`Check::get_flow_nodes()`（Check.php:263-267）取审批记录**没有
      ORDER BY**，oa_flow_record 只有 PRIMARY(id) 索引，界面时间线的顺序依赖存储顺序；
      时钟回拨时界面会出现「时间倒序」，属接口缺排序保证（轻微）。
    """
    rid = _two_level_doc(sess_yucixin, sess_fengcailing, "SP-031", "审批历史")
    r = _check(sess_admin, "leaves", rid, 2, "用例测试驳回")
    K.record("admin POST /api/check/flow_check check=2（二级驳回）", r)
    assert r.get("code") == 0, "第二级驳回失败（前置）：%s" % K.brief(r)
    sub = _submit(sess_yucixin, "leaves", rid, str(uid(APPROVER)))
    K.record("yucixin POST /api/check/submit_check（驳回后重新提交）", sub)
    hist = _flow_history(sess_yucixin, "leaves", rid)
    K.record("审批历史原始次序与时间（接口返回顺序 vs 库内无 ORDER BY 的返回顺序）", {
        "api_order": [{"id": x.get("id"), "check_time": x.get("check_time"),
                       "status_str": x.get("status_str")} for x in hist],
        "db_rows_id_time_delete_status":
            K.rows("SELECT id, check_time, delete_time, check_status FROM oa_flow_record "
                   "WHERE action_id=%d AND check_table='leaves';" % rid),
    })
    acts = [(x.get("status_str"), x.get("name"), str(x.get("content") or "")) for x in hist]
    has_chain = any("用例测试驳回" in a[2] for a in acts)
    times = [int(x.get("check_time") or 0) for x in hist]
    ids = [int(x.get("id") or 0) for x in hist]
    id_asc = ids == sorted(ids)                 # 逻辑顺序：接口按单据流程顺序返回（实测 = id 升序）
    drops = [a - b for a, b in zip(times, times[1:]) if b < a]
    ordered = times == sorted(times)
    clock_step = bool(drops) and all(d >= _CLOCK_STEP_SECONDS for d in drops)
    has_meta = all(int(x.get("check_time") or 0) > 0 and x.get("name") for x in hist)
    assert len(hist) >= 4 and has_chain and id_asc and (ordered or clock_step) and has_meta, (
        "期望「审批历史 ≥4 条、含驳回意见、顺序与流程一致（id 升序）、时间不出现非时钟回拨的"
        "反序、每条均有审批人与时间」；实测 %d 条（%s）；含驳回意见=%s；id 升序=%s；"
        "时间升序=%s；回拨秒数=%s；元数据齐全=%s"
        % (len(hist), " → ".join("%s/%s" % (a[0], a[1]) for a in acts), has_chain, id_asc,
           ordered, drops or "无", has_meta))


def test_sp_032_withdraw_after_level1(sess_yucixin, sess_fengcailing, sess_admin):
    """SP-032 撤回-撤回后流程终止：状态变 4、后续审批人待办清空、已产生的审批记录保留。

    判据：见断言（sp_032）。
    """
    rid = _two_level_doc(sess_yucixin, sess_fengcailing, "SP-032", "撤回")
    rec_before = _flow_record_count("leaves", rid)
    r = _check(sess_yucixin, "leaves", rid, 3, "用例测试撤回")
    K.record("yucixin POST /api/check/flow_check check=3（流转到第二级后撤回）", r)
    st = _state("leaves", rid)
    rec_after = _flow_record_count("leaves", rid)
    pend = int(K.scalar("SELECT COUNT(*) FROM oa_leaves WHERE id=%d AND check_status=1 "
                        "AND FIND_IN_SET('%d',check_uids);" % (rid, uid(ADMIN)), "0") or 0)
    assert r.get("code") == 0 and st == 4 and pend == 0 and rec_after >= rec_before, (
        "期望「撤回 code=0；check_status=4；第二级待办 0；记录 %d→≥%d」；实测 code=%s msg=%s；"
        "check_status=%s；待办=%s；记录 %d→%d"
        % (rec_before, rec_before, r.get("code"), r.get("msg"), st, pend, rec_before, rec_after))


# ===========================================================================
# SP-033 ~ SP-035  列表筛选 / 分页 / 归属隔离
# ===========================================================================
def test_sp_033_mylist_status_filter(sess_yucixin):
    """SP-033 列表筛选-按状态筛选：各状态筛选结果与库中本人单据条数逐项一致、无跨状态串数据。

    判据：见断言（sp_033）——
      Approve::mylist() 用 status 三档：1 = check_status<2 待审批；2 = =2 通过；3 = >2 不通过/撤回；
      比对基数按 19 类单据 UNION 口径（union_count(uid=5, bucket=n)），与接口同源。
    """
    yc = uid(APPLICANT)
    buckets = ((1, "待审批(check_status<2)"), (2, "已通过(check_status=2)"),
               (3, "已驳回/已撤回(check_status>2)"))
    detail, bad = [], []
    for attempt in (1, 2):
        # 与 SP-028 同理：接口与库是同一瞬间的两条口径，别的模块并发建单时会瞬时错位，
        # 不一致就整对比读一次；真缺陷两轮都不一致。
        detail, bad = [], []
        for st, st_name in buckets:
            db_n = _union_count(uid_=yc, bucket=st)
            res = K.call(sess_yucixin, "/home/approve/mylist",
                         data={"page": 1, "limit": 500, "status": st})
            K.record("yucixin GET /home/approve/mylist?status=%d（第 %d 轮）" % (st, attempt), res)
            cnt, lst = _layui(res)
            lst = lst or []
            wrong = [x.get("id") for x in lst
                     if not ((st == 1 and int(x.get("check_status", 0)) < 2)
                             or (st == 2 and int(x.get("check_status", 0)) == 2)
                             or (st == 3 and int(x.get("check_status", 0)) > 2))
                     or str(x.get("admin_id")) != str(yc)]
            detail.append("%s: 库%d/接口%d(count=%s)/越界%d"
                          % (st_name, db_n, len(lst), cnt, len(wrong)))
            if len(lst) != db_n or wrong:
                bad.append(st_name)
        if not bad:
            break
    assert not bad, ("各状态筛选必须与库中本人单据条数逐项一致且无跨状态/他人数据；"
                     "实测（两轮读数均不一致）：%s；明细 %s" % (bad, "；".join(detail)))


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-16 列表分页排序键不唯一致跨页重复/漏行（一般 C）", strict=True)
def test_sp_034_mylist_pagination(sess_yucixin):
    """SP-034 列表分页-第二页数据正确：逐页遍历不重复、不缺失、时间降序（**已知缺陷**）。

    关联缺陷：BUG-16（列表分页排序键不唯一致跨页重复/漏行，一般 C）。
    判据：见断言（sp_034）——
      唯一键取 (table_name, id)（19 类单据各表 id 独立自增，只比 id 会误判为重复）；
      逐页遍历而非只比相邻两页，以覆盖「并列组恰好压在页边界」的场景。
      根因：Approve::get_list() 收尾为 ORDER BY create_time DESC LIMIT offset,size，
      只有 create_time 一个排序键且精度到秒；UNION 后同秒并列的记录在不同 OFFSET 查询里
      顺序不固定，并列组压边界时即重复出现并挤掉本应展示的记录。
    **与执行器的取舍（重要）**：执行器只试了页长 10，那只是**一个对齐点**；
      本机 2026-09-21 实测：页长 10 恰好干净（0 重复 0 漏行），而页长 2~9、11~12 全部
      稳定复现（如页长 11/12 各 2 条重复、2 条漏行）—— 只按页长 10 判会把「侥幸对齐」
      读成「分页正确」，故本用例在页长 10 之外，**按实时并列组算出页边界必然切在
      并列组内部的页长**一并遍历（页长从数据推导，不写死数字）。
    校准口径：以接口自身的一次性读回（limit=500）作为全集基准，比对逐页遍历的键集合 ——
      两读同源，不受其他并发会话改库影响。
    xfail 原因：实测确有跨页重复与漏行；此处断言的是**正确行为**（不重复、不遗漏），
      缺陷修复后本用例会 XPASS 判 FAILED，回来销账。
    """
    one = K.call(sess_yucixin, "/home/approve/mylist", data={"page": 1, "limit": 500})
    K.record("yucixin GET /home/approve/mylist?page=1&limit=500（一次性读回，作全集基准）", one)
    assert one.get("code") == 0, "列表接口未正常返回：%s" % K.brief(one)
    cnt, rows_all = _layui(one)
    rows_all = rows_all or []
    assert int(cnt or 0) == len(rows_all), (
        "页长 500 大于总数时应一次给全：count=%s 但返回 %d 条" % (cnt, len(rows_all)))
    keys_all = {(x.get("table_name"), str(x.get("id"))) for x in rows_all}
    extra, ngroups = _boundary_split_limits(rows_all)
    plan = [10] + extra
    detail, bad = [], []
    for lim in plan:
        c2, pages, seen, dup, order_ok = _paged_traverse(
            sess_yucixin, lim, (len(rows_all) // lim) + 3)
        missing = keys_all - set(seen)
        detail.append("页长%d: 接口count=%s，取回%d页/%d个唯一键，跨页重复%d，漏行%d，降序=%s"
                      % (lim, c2, pages, len(seen), len(dup), len(missing), order_ok))
        if dup or missing or not order_ok:
            bad.append(lim)
    assert not bad, (
        "分页应无跨页重复、无漏行、按 create_time 降序（全集 %d 条，同秒并列组 %d 个，"
        "本次遍历页长 %s）；实测异常页长 %s —— %s"
        % (len(rows_all), ngroups, plan, bad, "；".join(detail)))


def test_sp_035_own_attendance_list(sess_yucixin):
    """SP-035 单据列表-他人单据不出现在本人列表：「我的假勤」四个列表均只含本人单据。

    判据：见断言（sp_035）——
      用例的「我的假勤」= 菜单 496 → /oa/attendance/datalist（tab 1请假/2出差/3外出/4加班），
      该控制器显式加 $where[] = ['admin_id','=',$this->uid]，是系统的「本人列表」。
    观察项：另有 /home/{leaves,trips,outs,overtimes}/datalist 为全员可见的团队视图
      （源码无 admin_id 过滤），非「我的」列表，未计入本条判定。
    """
    yc = uid(APPLICANT)
    detail, bad = [], []
    for tab, module, table, cn in ((1, "leaves", "oa_leaves", "请假"), (2, "trips", "oa_trips", "出差"),
                                   (3, "outs", "oa_outs", "外出"), (4, "overtimes", "oa_overtimes", "加班")):
        own = int(K.scalar("SELECT COUNT(*) FROM %s WHERE admin_id=%d AND delete_time=0;"
                           % (table, yc), "0") or 0)
        res = K.call(sess_yucixin, "/oa/attendance/datalist",
                     data={"tab": tab, "page": 1, "limit": 500})
        K.record("yucixin GET /oa/attendance/datalist?tab=%d" % tab, res)
        _, lst = _layui(res)
        leak = sorted({str(x.get("id")) for x in (lst or [])
                       if str(x.get("admin_id")) != str(yc)})
        detail.append("%s(tab=%d): 库中本人%d条，接口返回%d条，他人%d条"
                      % (cn, tab, own, len(lst or []), len(leak)))
        if leak:
            bad.append("%s(越界 id %s)" % (cn, leak[:6]))
    assert not bad, ("「我的假勤」四个列表均不得出现他人单据（归属过滤须生效）；"
                     "实测越界：%s；明细 %s" % (bad, "；".join(detail)))
