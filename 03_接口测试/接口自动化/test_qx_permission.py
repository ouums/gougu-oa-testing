# -*- coding: utf-8 -*-
r"""
勾股OA 6.1.2 —— pytest 接口自动化 · 权限体系（QX-001 ~ QX-055，共 55 条）

【本模块是什么】
  `02_测试用例/测试用例.xlsx` 里 55 条权限用例（QX-001~055）的**可重复自动化版本**。
  判定口径沿用私有一次性执行器（QX-001~055）已确认的口径 ——
  参数、账号、判据均取自执行器，但**结论不读它的 JSON**：
  每个函数自己发起请求、自己比较返回值（项目质量红线「断言必须真的比较」）。

【结果对齐】与测试报告摘要的统计口径一致：
  · 记为**通过**的 44 条 → 正常断言（本系统当前行为即正确行为）；
  · 记为**失败**的 11 条 → `xfail(strict=True)` + `known_defect`，函数体里写**正确行为**的断言：
        QX-030（缺陷三 删职位后权限未回收 / 见缺陷摘要）
        QX-039~044（水平越权四模块读改删 / 见缺陷摘要，严重 B）
        QX-047、QX-048（站内消息 check/del 无归属校验 / 见缺陷摘要，严重 B）
        QX-052（/api/map/getLocation 未登录可达 + 参数未兜底 500 / 见缺陷摘要）
        QX-054（app/api 缺 3 道闸门：会话超时后仍返回业务数据 / 见缺陷摘要）
    `pytest.ini` 开 `xfail_strict=true` ⇒ 缺陷修复后这些用例 XPASS 并直接判 FAILED，
    提醒回来销账（xfail 是活的回归探针，不是永不失效的注释）。

【预期结果修订台账】QX-002 / QX-007 等 12 条按执行器 `REVISIONS` 的**修订后口径**断言
  模块内逐条在 docstring 中标注。

【只读为主 + 数据回收】本模块多数用例是只读权限探测。凡新建数据：
  · 单据表 → `K.track()` 登记，由 conftest 会话级回收（pytest_cleanup）按显式 id 删除；
  · 框架回收计划**不覆盖**的表（oa_trips、oa_message、oa_msg、oa_admin_group、oa_position、
    oa_position_group、oa_admin、oa_department_admin）→ 额外登记到本模块的显式 WHERE 清单，
    由模块级 autouse 夹具 `_qx_env` 收尾删除并**分步回读**（绝不用 LIKE 批量删）。

【已知的框架边界（未改框架，实测记录见交付报告）】
  · 删除类动作要求 `request()->isDelete()` 为真（Role/Position/Message/Leaves 的 del/check），
    故一律走 `K.call(..., "DELETE")`（框架已支持；本模块的 `_delete()` 只是它的语义别名）；
  · `oa_kit.ACCOUNTS` 未登记执行器 QX-033/034 指定的攻击者 qinjiaxian → 该两条用例自行登录；
  · `pytest_cleanup.DOC_TABLES` 里写了库中**不存在**的 `oa_evection` / `oa_business_trip`，
    却**漏了实际存在的 `oa_trips`**（出差单落库表名，实测 oa/trips 的 add 写入 `oa_trips`）
    ⇒ 对 oa_trips 的 `K.track()` 会被回收计划静默忽略，故本模块按显式 id 自行兜底回收，
    不依赖该清单；同理，独立站内信 oa_message/oa_msg 与 oa_admin/oa_position/oa_admin_group/
    oa_position_group/oa_department_admin 的 track 也不被采纳（oa_position_group 更无 id 列，
    只能按 pid/group_id 定位，`track()` 的 id 口径承载不了）。

【环境】WSL2 三服务需在跑；账号 admin(1)/fengcailing(4)/yucixin(5)/testmin(108)/testemp(109)。
"""
import time

import pytest

import oa_kit as K
from oa_login import check_session, login

pytestmark = [pytest.mark.qx]

# ---------------------------------------------------------------------------
# 常量：账号、角色组、节点、夹具标题（全部取自两个执行器的源码）
# ---------------------------------------------------------------------------
UID_ADMIN = K.UID["admin"]            # 1
UID_YUCXIN = K.UID["yucixin"]         # 5   业务岗（组 3）—— 水平越权的**受害者**
UID_TESTEMP = K.UID["testemp"]        # 109 业务岗（组 3）—— 与受害者同级同角色，攻击者
UID_TESTMIN = K.UID["testmin"]        # 108 最小权限（组 4）—— BUG-01 的语义承重对象
ATTACKER_ZERO = "qinjiaxian"          # QX-033/034 的攻击者（执行器指定；oa_kit.ACCOUNTS 未登记）

GID_FIX = 4                           # 角色组 4「测试-最小权限角色」
TITLE_GROUP_FIX = "测试-最小权限角色"
NODE_ATT = 496                        # oa/attendance/datalist 我的假勤
NODE_EMP = 97                         # user/user/index 企业员工

TITLE_GROUP_A = "测试-新增角色A"       # QX-004 新建的角色组（QX-029 改映射的目标）
TITLE_POS_A = "测试职位A"              # QX-027 新建的职位（QX-029/030 的操作对象）
NAME_ACCT_A = "测试账号甲"             # QX-028 新建的账号（QX-029/030 的观察对象）
TITLE_MSG = "【QX用例数据】水平越权-站内消息"   # QX-047/048 的受害者消息（标题定值，便于幂等清理）

TAG = "【QX用例数据】"                 # 本模块新建数据统一标记（与项目既有标记口径一致）
NONCE = str(int(time.time()))[-6:]    # 每轮唯一，避免与历史残留撞标记

FLOW_TARGET_ID = 1072                 # QX-038/046 的目标单据 oa_leaves id=1072（归属 uid=4）
CONTRACT_ID = 1020                    # QX-034 的目标合同（code=HT-TEST-001）

# 本模块自建、框架回收计划覆盖不到的数据：[(表名, WHERE 子句, 说明)]
_LOCAL = []
# 受害者单据缓存：{(表名): (id, 标记)} —— QX-039/040/041 共用一张单（对齐私有取证记录的取证顺序）
_DOCS = {}
# 会话缓存
_SESS = {}


# ---------------------------------------------------------------------------
# 请求封装：DELETE 通道 + 原始页面读取
#   oa_kit.call() 对非 GET 一律发 POST，而本系统多个删除动作只在
#   `request()->isDelete()` 为真时才进入动作体（Role::delete / Position::delete /
#   Message::del / Message::check / Leaves::del），故必须自备 DELETE 通道。
#   解析口径与 `oa_kit.call()` 保持一致，返回值可直接喂给 K.brief / 9 个断言原语。
# ---------------------------------------------------------------------------
def _delete(sess, path, params=None):
    """发 DELETE。

    本系统多个删除动作只在 `request()->isDelete()` 为真时才进入动作体
    （Role::delete / Position::delete / Message::del / Message::check / Leaves::del），
    故必须真发 DELETE —— 早先 oa_kit.call() 只有 GET/POST（其余一律走 POST），DELETE 会
    拿到 `code=1 错误的请求`、断言随之失真；该缺口已由框架补上（oa_kit.py:102-105），
    这里直接复用框架的 DELETE 通道，不再自备解析。
    """
    return K.call(sess, path, "DELETE", params)


def _is_redirect_to(res, frag):
    ok = res.get("http") in (301, 302) and frag in res.get("location", "")
    return ok, "http=%s location=%s" % (res.get("http"), res.get("location"))


def _no_leak_ok(page, marker):
    """越权读的判据：他人单据/消息原文不得出现，且**必须能证明这次响应真的承载了内容**。

    · 拿到详情页（HTTP 200 且 body 有实质长度）→ 看是否含他人原文；
    · 被合法拦下（302 跳登录 / code=405 / code=1 请先登录）→ 同样没泄露；
    · 空 body 或 HTTP 500 → **不算通过**：`marker not in ""` 恒真，会把「什么都没拿到」
      读成「没泄露」，是典型假通过；500 更是"已越过闸门"，也不是合法拦截。
    """
    text = page.get("text") or ""
    leaked = marker in text
    rendered = page.get("http") == 200 and len(text) > 200
    denied = (page.get("http") in (301, 302) or page.get("code") == 405
              or (page.get("code") == 1 and "请先登录" in (page.get("msg") or "")))
    return (not leaked) and (rendered or denied), \
        "HTTP %s code=%s 页面 %d 字节；含他人原文=%s；%s" % (
            page.get("http"), page.get("code"), len(text), leaked,
            "详情页未泄露" if rendered else ("已拦截" if denied else "空响应/500，无法证明已拦截"))


def _sess_of(username, pwd=K.USER_PWD):
    """本模块自建的会话（oa_kit.ACCOUNTS 之外的账号走这里，如 qinjiaxian）。"""
    if username not in _SESS:
        s = login(username, pwd)
        check_session(s)
        _SESS[username] = s
    return _SESS[username]


# ---------------------------------------------------------------------------
# 数据登记与回收
# ---------------------------------------------------------------------------
def _reg(table, rid, where=None, note=""):
    """登记一条本模块新建的数据。

    `K.track()` 交给框架会话级回收（只对 DOC_TABLES 生效）；
    框架覆盖不到的表另存 (表, WHERE) 进 `_LOCAL`，由 `_qx_env` 收尾精确删除。
    """
    K.track(table, rid, note=note)
    if where:
        _LOCAL.append((table, where, note))
    return rid


def _unreg(table, where):
    """数据已在用例内自行删除时，从本地清单里摘掉（避免收尾重复删+噪声）。"""
    _LOCAL[:] = [x for x in _LOCAL if not (x[0] == table and x[1] == where)]


def _local_cleanup():
    """按显式 WHERE 删除本模块新建、框架不覆盖的数据，每步回读；残留如实报出。"""
    residue = []
    for table, where, note in _LOCAL:
        K.run_sql("DELETE FROM %s WHERE %s;" % (table, where))
        left = str(K.scalar("SELECT COUNT(*) FROM %s WHERE %s;" % (table, where)))
        print("    [本地回收] %-20s %s（%s）→ 残留 %s" % (table, where, note, left))
        if left != "0":
            residue.append("%s(%s) 残留 %s 行" % (table, where, left))
    if residue:
        print("!! 本地兜底回收仍有残留，请人工核对：%s" % "; ".join(residue))
    else:
        print("[本地回收] 回读核对：登记的本地数据已全部清零 ✓")
    del _LOCAL[:]


def _purge_leftovers():
    """幂等：清掉上一轮崩溃/中断留下的同名夹具残留（全部**精确等值**定位，绝不 LIKE）。

    对齐私有一次性执行器的「夹具幂等」步骤：夹具标题固定，
    故上一轮的残留可被精确定位；清完再建，避免出现两份同名夹具把断言搅乱。
    """
    K.run_sql("DELETE FROM oa_msg WHERE title='%s';" % TITLE_MSG)
    K.run_sql("DELETE FROM oa_message WHERE title='%s';" % TITLE_MSG)
    K.run_sql("DELETE FROM oa_position_group WHERE pid IN "
              "(SELECT id FROM oa_position WHERE title='%s');" % TITLE_POS_A)
    K.run_sql("DELETE FROM oa_position WHERE title='%s';" % TITLE_POS_A)
    K.run_sql("DELETE FROM oa_department_admin WHERE admin_id IN "
              "(SELECT id FROM oa_admin WHERE name='%s');" % NAME_ACCT_A)
    K.run_sql("DELETE FROM oa_admin WHERE name='%s';" % NAME_ACCT_A)
    K.run_sql("DELETE FROM oa_position_group WHERE group_id IN "
              "(SELECT id FROM oa_admin_group WHERE title='%s');" % TITLE_GROUP_A)
    K.run_sql("DELETE FROM oa_admin_group WHERE title='%s';" % TITLE_GROUP_A)


def _set_group4_rules(sess_admin, nodes, title=TITLE_GROUP_FIX, note=""):
    """角色组 4 的权限节点分配保存（POST /home/role/add，id 存在即编辑）。

    返回 (响应, 保存后的 rules 串)。判据见断言。
    """
    data = {"id": str(GID_FIX), "title": title, "status": "1"}
    for i, n in enumerate(nodes):
        data["rule[%d]" % i] = str(n)
    res = K.call(sess_admin, "/home/role/add", "POST", data)
    K.record("admin POST /home/role/add（角色组4 节点=%s）%s" % (nodes, note), res)
    after = K.scalar("SELECT rules FROM oa_admin_group WHERE id=%d;" % GID_FIX)
    return res, after


def _emp_list(sess):
    """企业员工列表（权限节点 97 user/user/index）"""
    return K.call(sess, "/user/user/index", "GET")


def _att_list(sess):
    """我的假勤（权限节点 496 oa/attendance/datalist）"""
    return K.call(sess, "/oa/attendance/datalist", "GET")


def _cache_clear(sess_admin):
    """产品自带的清缓存动作（执行器用 GET 调用）"""
    return K.call(sess_admin, "/api/index/cache_clear", "GET")


# ---------------------------------------------------------------------------
# 夹具链：角色组 A / 职位 A / 账号 A（QX-004 → QX-027 → QX-028 → QX-029 → QX-030）
#   每个 helper 都是「先按固定标题查、查不到才建」，因此单跑某一条用例也能自洽；
#   建出的数据全部登记回收。
# ---------------------------------------------------------------------------
def _group_a(sess_admin):
    """角色组 A（含节点 97 企业员工）—— QX-004 建；缺失则补建。"""
    gid = str(K.scalar("SELECT id FROM oa_admin_group WHERE title='%s' ORDER BY id DESC LIMIT 1;"
                       % TITLE_GROUP_A))
    if gid in ("", "0"):
        res = K.call(sess_admin, "/home/role/add", "POST",
                     {"title": TITLE_GROUP_A, "status": "1", "rule[0]": str(NODE_EMP),
                      "desc": "QX-004 用例临时创建"})
        K.record("admin POST /home/role/add（补建角色组A）", res)
        gid = str(K.scalar("SELECT id FROM oa_admin_group WHERE title='%s' ORDER BY id DESC LIMIT 1;"
                           % TITLE_GROUP_A))
        _reg("oa_admin_group", gid, "id=%s" % gid, "角色组A")
    return gid


def _position_a(sess_admin):
    """职位 A（映射角色组 4）—— QX-027 建；缺失则补建。"""
    pid = str(K.scalar("SELECT id FROM oa_position WHERE title='%s' AND status>=0 "
                       "ORDER BY id DESC LIMIT 1;" % TITLE_POS_A))
    if pid in ("", "0"):
        res = K.call(sess_admin, "/user/position/add", "POST",
                     {"title": TITLE_POS_A, "work_price": "0", "remark": "QX-027 用例临时创建",
                      "group_id[0]": str(GID_FIX)})
        K.record("admin POST /user/position/add（补建职位A）", res)
        pid = str(K.scalar("SELECT id FROM oa_position WHERE title='%s' AND status>=0 "
                           "ORDER BY id DESC LIMIT 1;" % TITLE_POS_A))
        _reg("oa_position", pid, "id=%s" % pid, "夹具职位A")
        _LOCAL.append(("oa_position_group", "pid=%s" % pid, "职位A-角色组映射"))
    return pid


def _account_a(sess_admin):
    """账号 A（职位 = 职位 A）—— QX-028 建；缺失则补建。返回 (uid, username, 会话)。

    新账号必带 reg_pwd（=先用 <OA_USER_PWD>），会被「强制改密」闸门挡住
    （BaseController.php:121-125），等价于员工已完成首次改密后置空 —— 同执行器口径。
    """
    row = K.rows("SELECT id, username FROM oa_admin WHERE name='%s' ORDER BY id DESC LIMIT 1;"
                 % NAME_ACCT_A)
    if row:
        uid, uname = str(row[0][0]), str(row[0][1])
    else:
        uid, uname = "", ""
    if uid in ("", "0"):
        res = K.call(sess_admin, "/user/user/add", "POST", _acct_payload(_position_a(sess_admin)))
        K.record("admin POST /user/user/add（补建账号A）", res)
        row = K.rows("SELECT id, username FROM oa_admin WHERE name='%s' ORDER BY id DESC LIMIT 1;"
                     % NAME_ACCT_A)
        if not row:
            raise AssertionError("账号A 补建失败：%s" % K.brief(res))
        uid, uname = str(row[0][0]), str(row[0][1])
        _reg("oa_admin", uid, "id=%s" % uid, "夹具账号A")
        _LOCAL.append(("oa_department_admin", "admin_id=%s" % uid, "账号A-部门关联"))
    if str(K.scalar("SELECT reg_pwd FROM oa_admin WHERE id=%s;" % uid)) != "":
        K.run_sql("UPDATE oa_admin SET reg_pwd='' WHERE id=%s;" % uid)
    return uid, uname, login(uname, K.USER_PWD)


def _acct_payload(position_id):
    """QX-028 的建号载荷（对齐 app/user/view/user/add.html 的真实表单字段）。

    `thumb` 是隐藏域且 oa_admin.thumb 为 NOT NULL 无默认值（本库 STRICT_TRANS_TABLES），
    不提交会 1364 插入失败 —— 属载荷缺字段，非产品缺陷（执行器首次执行时已自查纠正）。
    `entry_time`/`department_ids`/`did` 均按执行器载荷。
    """
    did = str(K.scalar("SELECT did FROM oa_admin WHERE id=%d;" % UID_ADMIN))
    # mobile 必须是 11 位（服务端校验「手机号格式错误」）：执行器用 "1380000"+时间戳后 4 位，
    # 这里沿用同一形态 —— 早期写成 "137"%s"000"（12 位）会被业务层拒，属**载荷错**不是产品缺陷。
    return {"name": NAME_ACCT_A, "username": "qx" + NONCE, "mobile": "1380000%s" % NONCE[-4:],
            "email": "qx%s@example.com" % NONCE, "reg_pwd": K.USER_PWD, "did": did,
            "position_id": str(position_id), "type": "1", "thumb": "",
            "entry_time": time.strftime("%Y-%m-%d"), "department_ids": did}


# ---------------------------------------------------------------------------
# 夹具链：受害者单据 / 受害者消息（QX-039~048）
# ---------------------------------------------------------------------------
def _victim_doc(key, table, ctl_path, sess_victim):
    """以受害者身份建一条带唯一标记的单据，返回 (id, 标记)。按 `key` 缓存（一条用例一张单）。

    **不能按表名共享**：QX-040（越权改）与 QX-041（越权删）在本系统当前行为下**会真的成功**
    （缺陷所在），一旦共享，后续用例读到的就是已被改写/软删的单据，QX-045 的"本人自查"对照
    会因此失败 —— 那是**夹具被自己的失败用例污染**，不是产品行为。故一条用例一张独立单据。
    """
    if key in _DOCS:
        return _DOCS[key]
    marker = "%s水平越权-%s-%s" % (TAG, table, NONCE)
    res = K.call(sess_victim, ctl_path + "/add", "POST",
                 {"id": "0", "types": "1", "start_date": "2026-09-20 09:00",
                  "end_date": "2026-09-20 18:00", "duration": "1",
                  "reason": marker, "file_ids": ""})
    K.record("受害者(yucixin) POST %s/add（造单）" % ctl_path, res)
    rid = str((res.get("data") or {}).get("return_id") or "")
    assert rid not in ("", "0"), "受害者造单失败：%s/add —— 实测 %s" % (ctl_path, K.brief(res))
    K.track("oa_" + table, rid, note="QX 水平越权-受害者单据")
    if table == "trips":
        # oa_trips 不在 pytest_cleanup.DOC_TABLES 内（框架缺口），本模块兜底
        _LOCAL.append(("oa_trips", "id=%s" % rid, "受害者出差单"))
    _DOCS[key] = (rid, marker)
    return rid, marker


def _victim_msg(sess_sender, sess_victim_id):
    """以 testmin 身份给受害者 yucixin 发一条站内消息，返回 (oa_message.id, oa_msg.id)。

    两条记录都要：oa_message = 发送方凭据（QX-048 的越权删除对象），
    oa_msg = 收件方镜像（QX-047 的越权改状态对象）。
    """
    if "msg" in _DOCS:
        return _DOCS["msg"]
    res = K.call(sess_sender, "/home/message/save", "POST",
                 {"id": "0", "types": "1", "uids": str(sess_victim_id), "dids": "", "pids": "",
                  "copy_uids": "", "title": TITLE_MSG, "content": TITLE_MSG + "-正文-" + NONCE,
                  "file_ids": "", "msg_id": "0", "is_draft": "1"})
    K.record("testmin POST /home/message/save（给受害者发消息）", res)
    mid = str(K.scalar("SELECT id FROM oa_message WHERE from_uid=%d AND title='%s' "
                       "ORDER BY id DESC LIMIT 1;" % (UID_TESTMIN, TITLE_MSG)))
    sid = str(K.scalar("SELECT id FROM oa_msg WHERE to_uid=%d AND title='%s' "
                       "ORDER BY id DESC LIMIT 1;" % (sess_victim_id, TITLE_MSG)))
    assert res.get("code") == 0 and mid not in ("", "0") and sid not in ("", "0"), \
        "构造受害者消息失败：%s（oa_message=%s oa_msg=%s）" % (K.brief(res), mid, sid)
    K.track("oa_message", mid, note="QX 越权-受害者发件行（框架不覆盖，本地兜底）")
    K.track("oa_msg", sid, note="QX 越权-受害者收件行（框架不回收独立站内信）")
    _LOCAL.append(("oa_message", "id=%s" % mid, "受害者发件记录"))
    _LOCAL.append(("oa_msg", "id=%s" % sid, "受害者收件记录"))
    _DOCS["msg"] = (mid, sid)
    return mid, sid


# ---------------------------------------------------------------------------
# 模块级夹具（autouse）：夹具幂等 + 角色组 4 归一化 + 收尾兜底回收
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _qx_env(request):
    """权限模块的运行环境与收尾。

    setup：
      ① 幂等清残留 —— 上一轮崩溃可能留下同名夹具（角色组A/职位A/账号A/受害者消息）；
      ② 角色组 4 归一化 —— rules 复位为 496（仅「我的假勤」），mobile_bar/mobile_menu
         若为 NULL 则置空串（NULL 会让编辑页 explode() 报弃用告警并放大成 500，属夹具形态
         不一致而非产品缺陷，对齐私有一次性执行器的夹具处理）；
      ③ 清一次缓存，保证后续权限判定从干净状态开始。
    teardown（--keep-fixtures 时跳过，便于停在现场）：
      ④ 按显式 WHERE 兜底删除框架回收计划不覆盖的表（oa_trips / oa_message / oa_msg /
         oa_admin_group / oa_position / oa_position_group / oa_admin / oa_department_admin）；
      ⑤ 角色组 4 的 rules 复位 496 并回读核对。
    """
    sess_admin = K.get_session("admin")
    _purge_leftovers()
    K.run_sql("UPDATE oa_admin_group SET rules='%d' WHERE id=%d;" % (NODE_ATT, GID_FIX))
    K.run_sql("UPDATE oa_admin_group SET mobile_bar='' WHERE id=%d AND mobile_bar IS NULL;" % GID_FIX)
    K.run_sql("UPDATE oa_admin_group SET mobile_menu='' WHERE id=%d AND mobile_menu IS NULL;" % GID_FIX)
    _cache_clear(sess_admin)
    print("\n[QX 模块夹具] 角色组 4 已归一化：rules=%s"
          % K.scalar("SELECT rules FROM oa_admin_group WHERE id=%d;" % GID_FIX))
    yield True

    if request.config.getoption("--keep-fixtures"):
        print("\n[QX 模块夹具] --keep-fixtures：跳过本地兜底回收，库停在中间态")
        return
    print("\n[QX 模块夹具] 收尾回收（框架回收计划不覆盖的表）")
    _local_cleanup()
    K.run_sql("UPDATE oa_admin_group SET rules='%d' WHERE id=%d;" % (NODE_ATT, GID_FIX))
    rules_end = str(K.scalar("SELECT rules FROM oa_admin_group WHERE id=%d;" % GID_FIX))
    print("[QX 模块夹具] 角色组 4 rules 复位 = %s（期望 %d）；库基线：角色组=%s 职位=%s 映射=%s 员工=%s"
          % (rules_end, NODE_ATT, K.scalar("SELECT COUNT(*) FROM oa_admin_group;"),
             K.scalar("SELECT COUNT(*) FROM oa_position;"),
             K.scalar("SELECT COUNT(*) FROM oa_position_group;"),
             K.scalar("SELECT COUNT(*) FROM oa_admin WHERE delete_time=0;")))


# ===========================================================================
# QX-001 ~ QX-026：访问控制矩阵（页面可达性 + 垂直越权）
# ===========================================================================
def test_qx_001_role_list_admin_allowed(sess_admin):
    """QX-001 角色组列表-超管可查看。

    账号 admin(1)；GET /home/role/index。
    判据：见断言（期望 is_allowed）；已确认结论为「通过」。
    """
    res = K.call(sess_admin, "/home/role/index", "GET")
    K.record("admin GET /home/role/index", res)
    K.expect(K.is_allowed(res), "超管应正常看到角色组列表（code=0）")


def test_qx_002_role_list_manager_allowed(sess_fengcailing):
    """QX-002 角色组列表-管理岗可查看（**预期结果已修订**）。

    账号 fengcailing(4)；GET /home/role/index。
    判据出处：REVISIONS['QX-002']（预期结果修订台账）—— 出厂「管理岗角色」rules 含节点
    39 home/role/index（FIND_IN_SET=18），故管理岗看角色组是**被授予的权限**，不是越权；
    原预期「被拦截 code=405」的初始假设「管理岗=非管理员」不成立，已修订为「可查看」。
    已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_fengcailing, "/home/role/index", "GET")
    K.record("fengcailing GET /home/role/index", res)
    K.expect(K.is_allowed(res), "管理岗（持有节点 39）应能查看角色组列表（修订后口径：code=0）")


def test_qx_003_role_list_biz_denied(sess_yucixin):
    """QX-003 角色组列表-业务岗被拦截。

    账号 yucixin(5)；GET /home/role/index。
    判据：见断言（期望 is_denied）—— 业务岗角色组 3 不含节点 39。
    已确认结论为「通过」。
    """
    res = K.call(sess_yucixin, "/home/role/index", "GET")
    K.record("yucixin GET /home/role/index", res)
    K.expect(K.is_denied(res), "业务岗应被 RBAC 拦下（HTTP200 + code=405）")


def test_qx_004_role_add_new_group(sess_admin):
    """QX-004 角色组新增-超管新增成功。

    账号 admin(1)；POST /home/role/add {title=测试-新增角色A, status=1, rule[0]=97}。
    判据：见断言 —— 判定三件套：code=0、oa_admin_group 行数 +1、
    新角色 rules 含节点 97。新建的角色组登记回收；QX-029 会改职位映射到它，故**本用例不删它**。
    已确认结论为「通过」。
    """
    n0 = int(K.scalar("SELECT COUNT(*) FROM oa_admin_group;"))
    res = K.call(sess_admin, "/home/role/add", "POST",
                 {"title": TITLE_GROUP_A, "status": "1", "rule[0]": str(NODE_EMP),
                  "desc": "QX-004 用例执行临时创建"})
    K.record("admin POST /home/role/add（新增角色组A）", res)
    n1 = int(K.scalar("SELECT COUNT(*) FROM oa_admin_group;"))
    gid = str(K.scalar("SELECT id FROM oa_admin_group WHERE title='%s' ORDER BY id DESC LIMIT 1;"
                       % TITLE_GROUP_A))
    rules = str(K.scalar("SELECT rules FROM oa_admin_group WHERE id=%s;" % (gid or 0)))
    _reg("oa_admin_group", gid, "id=%s" % gid, "角色组A（QX-004 新建）")
    K.expect((res.get("code") == 0 and gid not in ("", "0") and n1 == n0 + 1,
              "code=%s msg=%s；oa_admin_group %d→%d；新角色 id=%s"
              % (res.get("code"), res.get("msg"), n0, n1, gid)),
             "超管新增角色组应成功且角色组总数 +1")
    K.expect((str(NODE_EMP) in rules.split(","), "新角色 rules=%s" % rules),
             "新建角色组应保存节点 97")


def test_qx_005_role_delete_in_use_blocked(sess_admin):
    """QX-005 角色组删除-删除已被职位占用的角色组应被拒绝。

    账号 admin(1)；DELETE /home/role/delete?id=4（角色组 4 被 oa_position_group 引用）。
    判据：见断言（blocked = code=1 且 msg 含「还在使用」）
    + 源码 app/home/controller/Role.php delete()：`PositionGroup::where(group_id)->count()>0`
    时返回「该权限组还在使用，请去除使用者关联再删除」。
    注意：该动作要求 `request()->isDelete()`，故必须用 DELETE 方法（oa_kit.call 发不出 DELETE，
    本模块用 _delete()）。已确认结论为「通过」。
    """
    res = _delete(sess_admin, "/home/role/delete", {"id": str(GID_FIX)})
    K.record("admin DELETE /home/role/delete?id=4（被职位引用）", res)
    still = str(K.scalar("SELECT title FROM oa_admin_group WHERE id=%d;" % GID_FIX))
    K.expect((res.get("code") == 1 and "还在使用" in (res.get("msg") or ""),
              "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "删除被职位占用的角色组应被业务层拒绝（code=1 且提示还在使用）")
    K.expect((still != "", "删除后角色组 title=%r" % still),
             "删除被拒后角色组应原样存在")


def test_qx_006_rule_tree_admin_allowed(sess_admin):
    """QX-006 权限节点树-超管可查看。

    账号 admin(1)；GET /home/rule/index。判据：见断言。
    已确认结论为「通过」。
    """
    res = K.call(sess_admin, "/home/rule/index", "GET")
    K.record("admin GET /home/rule/index", res)
    K.expect(K.is_allowed(res), "超管应能查看权限节点树（code=0）")


def test_qx_007_rule_tree_manager_allowed(sess_fengcailing):
    """QX-007 权限节点管理-管理岗可查看（**预期结果已修订**）。

    账号 fengcailing(4)；GET /home/rule/index。
    判据出处：REVISIONS['QX-007'] —— 管理岗角色持有节点 24 home/rule/index（FIND_IN_SET=9），
    原预期「被拦截」修订为「可查看」。已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_fengcailing, "/home/rule/index", "GET")
    K.record("fengcailing GET /home/rule/index", res)
    K.expect(K.is_allowed(res), "管理岗（持有节点 24）应能查看权限节点树（修订后口径：code=0）")


def test_qx_008_role_rule_save_grant(sess_admin):
    """QX-008 权限节点分配保存-超管保存成功（给角色组 4 增加节点 97）。

    账号 admin(1)；POST /home/role/add {id=4, rule[]={496,97}}。
    判据：见断言 —— code=0 且 oa_admin_group.rules 里
    出现节点 97（按**逗号切分后的成员**比较，不是子串包含，避免 97/497 这类前缀误判）。
    已确认结论为「通过」。
    """
    res, after = _set_group4_rules(sess_admin, [NODE_ATT, NODE_EMP])
    K.expect((res.get("code") == 0, "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "超管保存角色组权限节点应成功（code=0）")
    K.expect((str(NODE_EMP) in after.split(","), "rules=%s" % after),
             "保存后 rules 应含节点 %d（企业员工）" % NODE_EMP)


def test_qx_009_role_rule_revoke_effective(sess_admin, sess_testmin):
    """QX-009 权限节点取消勾选-权限被回收（即时生效且与清缓存无关）。

    账号 admin(1) 改角色组 4；观察对象 testmin(108)（职位 4 → 角色组 4）。
    判据：见断言 —— 先预热（rules 非空时 RulesSrc 会写缓存），
    再取消勾选节点 97，**不调用任何清缓存动作**立即访问 /user/user/index，期望 405；
    并以「清缓存后再访问」作对照（翻转即说明依赖缓存）。已确认结论为「通过」。
    前置构造：先把角色组 4 复位为仅含 496（QX-010 可能留下节点 97）。
    """
    res, rules = _set_group4_rules(sess_admin, [NODE_ATT], note="（QX-009 前置复位）")
    K.expect((res.get("code") == 0 and rules == str(NODE_ATT), "rules=%s" % rules),
             "前置：角色组 4 应复位为仅含节点 %d" % NODE_ATT)
    base = int(K.scalar("SELECT COUNT(*) FROM oa_admin_group;"))
    res, after = _set_group4_rules(sess_admin, [NODE_ATT, NODE_EMP])
    K.expect((res.get("code") == 0 and int(K.scalar("SELECT COUNT(*) FROM oa_admin_group;")) == base,
              "行数 %d→%d" % (base, int(K.scalar("SELECT COUNT(*) FROM oa_admin_group;")))),
             "前置：给角色组 4 授权（编辑分支不应新增角色组行）")
    warm = _emp_list(sess_testmin)
    K.record("testmin 预热 GET /user/user/index", warm)
    K.expect(K.is_allowed(warm), "预热：授权后 testmin 应能访问员工列表（code=0）")

    res, after = _set_group4_rules(sess_admin, [NODE_ATT])
    K.expect((str(NODE_EMP) not in after.split(","), "rules %s→%s" % (rules, after)),
             "取消勾选后 oa_admin_group.rules 应移除节点 %d" % NODE_EMP)
    imm = _emp_list(sess_testmin)
    K.record("testmin（未清缓存）GET /user/user/index", imm)
    K.expect(K.is_denied(imm), "取消勾选后应立即失去该页权限（未清缓存时也应是 405）")
    _cache_clear(sess_admin)
    cle = _emp_list(sess_testmin)
    K.record("testmin（清缓存后）GET /user/user/index", cle)
    K.expect(K.is_denied(cle), "清缓存后仍应被拦截（结论与清缓存无关）")
    K.expect((cle.get("code") == imm.get("code"), "未清缓存 code=%s；清缓存后 code=%s"
              % (imm.get("code"), cle.get("code"))),
             "清缓存前后结论应一致（不一致说明生效依赖缓存，需按误报三步法复核）")


def test_qx_010_role_rule_grant_effective(sess_admin, sess_testmin):
    r"""QX-010 权限节点分配-保存后权限即时生效。

    账号 admin(1) 改角色组 4；观察对象 testmin(108)。
    判据：见断言 —— 保存后**不调用任何清缓存动作**
    立即访问 /user/user/index，期望放行（code=0）；并作清缓存对照。
    机制依据：clear_cache($key) → think\Cache::clear() 无 $tag 形参、File::clear() 亦无参
    且直接 rmdir 整个缓存目录 ⇒ 传入的 $key 被忽略，任何一次 clear_cache() 都是全量清空。
    已确认结论为「通过」。
    前置构造：先回收节点 97（QX-009 会留下 revoked 状态；此处显式复位为 496）。
    """
    res, rules = _set_group4_rules(sess_admin, [NODE_ATT], note="（QX-010 前置复位）")
    K.expect((res.get("code") == 0 and rules == str(NODE_ATT), "rules=%s" % rules),
             "前置：角色组 4 应复位为仅含节点 %d" % NODE_ATT)
    warm = _emp_list(sess_testmin)
    K.record("testmin 预热 GET /user/user/index", warm)
    K.expect(K.is_denied(warm), "预热：未授权时 testmin 访问员工列表应被拦截（405）")

    res, after = _set_group4_rules(sess_admin, [NODE_ATT, NODE_EMP])
    K.expect((res.get("code") == 0 and str(NODE_EMP) in after.split(","), "rules=%s" % after),
             "授权保存应成功且 rules 含节点 %d" % NODE_EMP)
    imm = _emp_list(sess_testmin)
    K.record("testmin（未清缓存）GET /user/user/index", imm)
    K.expect(K.is_allowed(imm), "保存后应立即生效（不调用清缓存也应放行 code=0）")
    _cache_clear(sess_admin)
    cle = _emp_list(sess_testmin)
    K.record("testmin（清缓存后）GET /user/user/index", cle)
    K.expect(K.is_allowed(cle), "清缓存后应仍放行（结论与清缓存无关）")
    K.expect((cle.get("code") == imm.get("code"), "未清缓存 code=%s；清缓存后 code=%s"
              % (imm.get("code"), cle.get("code"))),
             "清缓存前后结论应一致")


def test_qx_011_module_manage_admin_allowed(sess_admin):
    """QX-011 功能模块管理-超管可访问。账号 admin(1)；GET /home/module/index。
    判据：见断言。"""
    res = K.call(sess_admin, "/home/module/index", "GET")
    K.record("admin GET /home/module/index", res)
    K.expect(K.is_allowed(res), "超管应能访问功能模块管理（code=0）")


def test_qx_012_module_manage_manager_allowed(sess_fengcailing):
    """QX-012 功能模块管理-管理岗可查看（**预期结果已修订**）。

    账号 fengcailing(4)；GET /home/module/index。
    判据出处：REVISIONS['QX-012'] —— 管理岗持有节点 17 home/module/index（FIND_IN_SET=5），
    原预期「被拦截」修订为「可查看」。已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_fengcailing, "/home/module/index", "GET")
    K.record("fengcailing GET /home/module/index", res)
    K.expect(K.is_allowed(res), "管理岗（持有节点 17）应能查看功能模块列表（修订后口径）")


def test_qx_013_dataauth_admin_allowed(sess_admin):
    """QX-013 数据权限配置-超管可访问。账号 admin(1)；GET /home/dataauth/index。
    判据：见断言。"""
    res = K.call(sess_admin, "/home/dataauth/index", "GET")
    K.record("admin GET /home/dataauth/index", res)
    K.expect(K.is_allowed(res), "超管应能访问数据权限配置（code=0）")


def test_qx_014_dataauth_biz_denied(sess_yucixin):
    """QX-014 数据权限配置-业务岗被拦截。账号 yucixin(5)；GET /home/dataauth/index。
    判据：见断言。"""
    res = K.call(sess_yucixin, "/home/dataauth/index", "GET")
    K.record("yucixin GET /home/dataauth/index", res)
    K.expect(K.is_denied(res), "业务岗应被 RBAC 拦下（code=405）")


def test_qx_015_log_admin_allowed(sess_admin):
    """QX-015 操作日志-超管可查看。账号 admin(1)；GET /home/log/index。
    判据：见断言。"""
    res = K.call(sess_admin, "/home/log/index", "GET")
    K.record("admin GET /home/log/index", res)
    K.expect(K.is_allowed(res), "超管应能查看操作日志（code=0）")


def test_qx_016_log_biz_denied(sess_yucixin):
    """QX-016 操作日志-业务岗被拦截。账号 yucixin(5)；GET /home/log/index。
    判据：见断言。"""
    res = K.call(sess_yucixin, "/home/log/index", "GET")
    K.record("yucixin GET /home/log/index", res)
    K.expect(K.is_denied(res), "业务岗应被 RBAC 拦下（code=405）")


def test_qx_017_conf_admin_allowed(sess_admin):
    """QX-017 系统配置-超管可访问。账号 admin(1)；GET /home/conf/index。
    判据：见断言。"""
    res = K.call(sess_admin, "/home/conf/index", "GET")
    K.record("admin GET /home/conf/index", res)
    K.expect(K.is_allowed(res), "超管应能访问系统配置（code=0）")


def test_qx_018_conf_manager_allowed(sess_fengcailing):
    """QX-018 系统配置-管理岗可查看（**预期结果已修订**）。

    账号 fengcailing(4)；GET /home/conf/index。
    判据出处：REVISIONS['QX-018'] —— 管理岗持有节点 13 home/conf/index（FIND_IN_SET=2），
    原预期「被拦截」修订为「可查看」。已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_fengcailing, "/home/conf/index", "GET")
    K.record("fengcailing GET /home/conf/index", res)
    K.expect(K.is_allowed(res), "管理岗（持有节点 13）应能查看系统配置（修订后口径）")


def test_qx_019_database_backup_admin_allowed(sess_admin):
    """QX-019 备份还原页-超管可访问。账号 admin(1)；GET /home/database/database。
    判据：见断言。"""
    res = K.call(sess_admin, "/home/database/database", "GET")
    K.record("admin GET /home/database/database", res)
    K.expect(K.is_allowed(res), "超管应能访问备份还原页（code=0）")


def test_qx_020_database_backup_biz_denied(sess_yucixin):
    """QX-020 数据还原-业务岗被拦截。账号 yucixin(5)；GET /home/database/database。
    判据：见断言。"""
    res = K.call(sess_yucixin, "/home/database/database", "GET")
    K.record("yucixin GET /home/database/database", res)
    K.expect(K.is_denied(res), "业务岗应被 RBAC 拦下（code=405）")


def test_qx_021_user_list_admin_allowed(sess_admin):
    """QX-021 员工列表-超管可查看。账号 admin(1)；GET /user/user/index。
    判据：见断言。"""
    res = K.call(sess_admin, "/user/user/index", "GET")
    K.record("admin GET /user/user/index", res)
    K.expect(K.is_allowed(res), "超管应能查看员工列表（code=0）")


def test_qx_022_user_list_manager_allowed(sess_fengcailing):
    """QX-022 员工列表-管理岗可查看。账号 fengcailing(4)；GET /user/user/index。
    判据：见断言。"""
    res = K.call(sess_fengcailing, "/user/user/index", "GET")
    K.record("fengcailing GET /user/user/index", res)
    K.expect(K.is_allowed(res), "管理岗应能查看员工列表（code=0）")


def test_qx_023_user_list_biz_allowed(sess_yucixin):
    """QX-023 员工列表-业务岗可查看（**预期结果已修订**）。

    账号 yucixin(5)；GET /user/user/index。
    判据出处：REVISIONS['QX-023'] —— 业务岗角色持有节点 97 user/user/index（FIND_IN_SET=123）：
    业务岗并非「低权限角色」，它持有企业员工、部门架构、岗位职称、销售合同的读写节点，
    原预期「被拦截」修订为「可查看」。已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_yucixin, "/user/user/index", "GET")
    K.record("yucixin GET /user/user/index", res)
    K.expect(K.is_allowed(res), "业务岗（持有节点 97）应能查看员工列表（修订后口径：code=0）")


def test_qx_024_user_add_manager_gate_passed(sess_fengcailing):
    """QX-024 员工新增-管理岗请求被放行（**预期结果已修订**）。

    账号 fengcailing(4)；POST /user/user/add（空载荷，测试点＝RBAC 闸门是否放行）。
    判据出处：REVISIONS['QX-024'] —— 管理岗持有节点 98 user/user/add（FIND_IN_SET=138），
    原预期「被拦截」修订为「可编辑：请求被放行，由业务参数校验拒绝」。
    判据用 is_passed_gate：只要不是 405、不是跳登录页，就证明闸门放行了 —— 空载荷本就
    没有合法参数，业务层随后 500 属**另一条缺陷面**（Export.php:343 / Map.php:108 / Check.php:385,467
    同源的「参数未兜底 → 未定义数组键警告被升级成 ErrorException」），故实测串里原样保留 500，
    不粉饰成「通过」。已确认结论为「通过」。
    """
    res = K.call(sess_fengcailing, "/user/user/add", "POST", {})
    K.record("fengcailing POST /user/user/add（空载荷）", res)
    K.expect(K.is_passed_gate(res), "管理岗调用员工新增应越过 RBAC 闸门（不是 405、不是跳登录）")
    if res.get("http") == 500:
        K.record("关联缺陷：空载荷致 500（业务层未做参数兜底）", res)


def test_qx_025_department_biz_allowed(sess_yucixin):
    """QX-025 部门管理-业务岗可查看（**预期结果已修订**）。

    账号 yucixin(5)；GET /user/department/index。
    判据出处：REVISIONS['QX-025'] —— 业务岗持有节点 89 user/department/index（FIND_IN_SET=131），
    原预期「被拦截」修订为「可查看」。已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_yucixin, "/user/department/index", "GET")
    K.record("yucixin GET /user/department/index", res)
    K.expect(K.is_allowed(res), "业务岗（持有节点 89）应能查看部门数据（修订后口径：code=0）")


def test_qx_026_position_list_admin_allowed(sess_admin):
    """QX-026 职位列表-超管可查看。账号 admin(1)；GET /user/position/index。
    判据：见断言。"""
    res = K.call(sess_admin, "/user/position/index", "GET")
    K.record("admin GET /user/position/index", res)
    K.expect(K.is_allowed(res), "超管应能查看职位列表（code=0）")


# ===========================================================================
# QX-027 ~ QX-030：账号权限按「职位 → 角色组」映射（含缺陷三）
# ===========================================================================
def test_qx_027_position_add_with_group_mapping(sess_admin):
    """QX-027 新建职位并映射角色组-超管成功。

    账号 admin(1)；POST /user/position/add {title=测试职位A, work_price=0, group_id[0]=4}。
    判据：见断言 —— 判定四件套：code=0、oa_position 行数 +1、
    oa_position_group 出现 (pid, group_id=4) 映射行。职位与映射登记回收（映射表无 id 列，
    只能按 pid 登记 —— 框架的 track 承载不了，由模块夹具兜底）。
    已确认结论为「通过」。
    """
    n0 = int(K.scalar("SELECT COUNT(*) FROM oa_position;"))
    res = K.call(sess_admin, "/user/position/add", "POST",
                 {"title": TITLE_POS_A, "work_price": "0", "remark": "QX-027 用例执行临时创建",
                  "group_id[0]": str(GID_FIX)})
    K.record("admin POST /user/position/add（新建职位A 并映射角色组4）", res)
    n1 = int(K.scalar("SELECT COUNT(*) FROM oa_position;"))
    pid = str(K.scalar("SELECT id FROM oa_position WHERE title='%s' AND status>=0 "
                       "ORDER BY id DESC LIMIT 1;" % TITLE_POS_A))
    pg = [[str(x[0]), str(x[1])] for x in
          K.rows("SELECT pid, group_id FROM oa_position_group WHERE pid=%s;" % (pid or 0))]
    _reg("oa_position", pid, "id=%s" % pid, "夹具职位A（QX-027 新建）")
    _LOCAL.append(("oa_position_group", "pid=%s" % pid, "职位A-角色组映射"))
    K.expect((res.get("code") == 0 and pid not in ("", "0") and n1 == n0 + 1,
              "code=%s msg=%s；oa_position %d→%d；新职位 id=%s；pg=%s"
              % (res.get("code"), res.get("msg"), n0, n1, pid, pg)),
             "超管新建职位应成功且职位总数 +1")
    K.expect(([pid, str(GID_FIX)] in pg, "映射行=%s" % pg),
             "新职位应映射到角色组 %d" % GID_FIX)


def test_qx_028_user_add_position_permission_effective(sess_admin):
    """QX-028 新建账号指定职位-权限按映射生效。

    账号 admin(1) 建号 → 以新账号登录访问两页。
    判据：见断言 —— 判定五件套：建号 code=0、oa_admin 行数 +1、
    position_id = 新建职位、我的假勤 code=0、员工列表 code=405（该职位映射角色组 4，rules 仅 496）。
    另含两条现场取证：① 新账号带 reg_pwd 时被「强制改密」闸门挡住（BaseController.php:121-125
    → 302 /home/index/edit_password.html）；② 产品**不允许自定义登录账号**：username 由姓名拼音
    自动生成并去重（User.php:161-162），故用例原文写「用 testpos/<OA_USER_PWD> 登录」已按实测修订为
    以实际生成的 username 登录（预期结果修订见 QX-028 的「预期结果修订」）。
    已确认结论为「通过」。
    前置构造：角色组 4 复位为仅含 496（QX-010 会留下节点 97）。
    """
    res, rules = _set_group4_rules(sess_admin, [NODE_ATT], note="（QX-028 前置复位）")
    K.expect((res.get("code") == 0 and rules == str(NODE_ATT), "rules=%s" % rules),
             "前置：角色组 4 应复位为仅含节点 %d（本用例的前置条件）" % NODE_ATT)
    pid = _position_a(sess_admin)
    payload = _acct_payload(pid)
    n0 = int(K.scalar("SELECT COUNT(*) FROM oa_admin WHERE delete_time=0;"))
    res = K.call(sess_admin, "/user/user/add", "POST", payload)
    K.record("admin POST /user/user/add（指定 position_id=%s）" % pid, res)
    n1 = int(K.scalar("SELECT COUNT(*) FROM oa_admin WHERE delete_time=0;"))
    row = K.rows("SELECT id, username, position_id FROM oa_admin WHERE name='%s' "
                 "ORDER BY id DESC LIMIT 1;" % NAME_ACCT_A)
    uid, uname, pos = (str(row[0][0]), str(row[0][1]), str(row[0][2])) if row else ("", "", "")
    if uid:
        _reg("oa_admin", uid, "id=%s" % uid, "账号A（QX-028 新建）")
        _LOCAL.append(("oa_department_admin", "admin_id=%s" % uid, "账号A-部门关联"))
    K.expect((res.get("code") == 0 and uid not in ("", "0") and n1 == n0 + 1 and pos == str(pid),
              "建号 code=%s msg=%s；oa_admin %d→%d；uid=%s username=%s position_id=%s（期望 %s）"
              % (res.get("code"), res.get("msg"), n0, n1, uid, uname, pos, pid)),
             "超管建号应成功、员工总数 +1 且职位为该职位")
    K.expect((uname != payload["username"], "提交的 username=%s；实际 username=%s"
              % (payload["username"], uname)),
             "登录账号应由姓名拼音自动生成（产品不接受自定义 username —— 预期结果修订点）")

    gate = K.call(login(uname, K.USER_PWD, verify=False), "/oa/attendance/datalist", "GET")
    K.record("新账号首登（reg_pwd 非空）GET /oa/attendance/datalist", gate)
    K.expect(_is_redirect_to(gate, "edit_password"),
             "reg_pwd 非空时应被强制改密闸门拦到 /home/index/edit_password.html")
    K.run_sql("UPDATE oa_admin SET reg_pwd='' WHERE id=%s;" % uid)
    s_new = login(uname, K.USER_PWD)
    att = _att_list(s_new)
    emp = _emp_list(s_new)
    K.record("新账号（reg_pwd 已置空）GET /oa/attendance/datalist", att)
    K.record("新账号（reg_pwd 已置空）GET /user/user/index", emp)
    K.expect(K.is_allowed(att), "映射角色组含节点 496 → 我的假勤应放行（code=0）")
    K.expect(K.is_denied(emp), "映射角色组不含节点 97 → 员工列表应被拦截（code=405）")


def test_qx_029_position_group_change_effective(sess_admin):
    """QX-029 修改职位映射的角色组-账号权限随之变化。

    账号 admin(1)；POST /user/position/add {id=职位A, group_id[0]=角色组A}（角色组A 含节点 97）。
    观察对象：QX-028 建出的账号（职位 A）。
    判据：见断言 —— 判定三件套：改映射 code=0、
    oa_position_group 的 (pid, group_id) 变为 (职位A, 角色组A)、该账号员工列表由 405 变 0。
    对照依据：Position.php:72-73 改职位时清 adminRules（与 Role.php:90-91 改角色时不清形成对照）。
    已确认结论为「通过」。
    """
    pid = _position_a(sess_admin)
    gid_a = _group_a(sess_admin)
    K.expect((gid_a not in ("", "0") and gid_a != str(GID_FIX),
              "角色组A id=%s（角色组 4 id=%d）" % (gid_a, GID_FIX)),
             "前置：角色组A 应存在且不同于角色组 4")
    pg_before = [[str(x[0]), str(x[1])] for x in
                 K.rows("SELECT pid, group_id FROM oa_position_group WHERE pid=%s;" % pid)]
    K.expect(([pid, str(GID_FIX)] in pg_before, "改映射前 pg=%s" % pg_before),
             "前置：职位A 当前应映射到角色组 %d" % GID_FIX)
    uid, uname, s_new = _account_a(sess_admin)
    _reg("oa_admin", uid, "id=%s" % uid, "账号A（QX-029 观察对象）")
    _LOCAL.append(("oa_department_admin", "admin_id=%s" % uid, "账号A-部门关联"))
    emp_before = _emp_list(s_new)
    K.record("账号A GET /user/user/index（改映射前）", emp_before)
    code_before = emp_before.get("code")
    K.expect(K.is_denied(emp_before), "前置：改映射前该账号访问员工列表应被拦截（405）")

    res = K.call(sess_admin, "/user/position/add", "POST",
                 {"id": str(pid), "title": TITLE_POS_A, "work_price": "0",
                  "group_id[0]": str(gid_a)})
    K.record("admin POST /user/position/add（id=%s 改映射 → 角色组A）" % pid, res)
    after = [[str(x[0]), str(x[1])] for x in
             K.rows("SELECT pid, group_id FROM oa_position_group WHERE pid=%s;" % pid)]
    emp2 = _emp_list(s_new)
    att2 = _att_list(s_new)
    K.record("账号A GET /user/user/index（改映射后）", emp2)
    K.record("账号A GET /oa/attendance/datalist（改映射后）", att2)
    K.expect((res.get("code") == 0 and [pid, gid_a] in after, "code=%s；改后 pg=%s"
              % (res.get("code"), after)),
             "改职位映射应成功且映射行指向角色组A")
    K.expect(K.is_allowed(emp2), "新映射角色组含节点 97 → 该账号员工列表应放行（code=0）")
    K.expect((att2.get("code") == 405, "我的假勤 code=%s" % att2.get("code")),
             "原角色组的节点 496 已不在此映射 → 我的假勤应变 405")
    K.expect((emp2.get("code") != code_before, "uid=%s username=%s；改映射前 code=%s、改后 code=%s"
              % (uid, uname, code_before, emp2.get("code"))),
             "账号权限应随职位映射变化（改前 405 → 改后 0）")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="缺陷三：删除职位后 oa_position_group 映射未回收、adminRules 未清，"
                          "Systematic::auth() 又不 join oa_position，权限照旧生效",
                   strict=True)
def test_qx_030_position_delete_revoke_permission(sess_admin):
    """QX-030 删除职位-关联账号权限应失效。**【已知缺陷，本用例记为失败 → xfail】**

    账号 admin(1)；DELETE /user/position/delete?id=职位A 后清缓存，以该账号重访两页。
    判据：见断言（期望两页均 405）。
    关联缺陷：缺陷三「删除职位后权限未回收」—— Position.php:170-185 删职位既**不删
    oa_position_group 映射行、也不清 adminRules**，且 Systematic::auth() 解析权限时
    只 join oa_position_group 与 oa_admin_group、**不 join oa_position**（status=-1 无效），
    故职位删掉后权限照旧生效。已确认结论为「失败」。
    函数体写**正确行为**：职位已删 → 两页都应 405。缺陷在时失败 → xfail；
    修复后通过 → XPASS → strict 模式判 FAILED，提醒回来销账。
    """
    pid = _position_a(sess_admin)
    gid_a = _group_a(sess_admin)
    # 确保映射指向含节点 97 的角色组A（缺陷面：删职位后该映射仍在库、权限仍在生效）
    K.call(sess_admin, "/user/position/add", "POST",
           {"id": str(pid), "title": TITLE_POS_A, "work_price": "0", "group_id[0]": str(gid_a)})
    uid, uname, s_new = _account_a(sess_admin)
    _reg("oa_admin", uid, "id=%s" % uid, "账号A（QX-030 观察对象）")
    _LOCAL.append(("oa_department_admin", "admin_id=%s" % uid, "账号A-部门关联"))
    res = _delete(sess_admin, "/user/position/delete", {"id": str(pid)})
    K.record("admin DELETE /user/position/delete?id=%s" % pid, res)
    status = str(K.scalar("SELECT status FROM oa_position WHERE id=%s;" % pid))
    pg_left = str(K.scalar("SELECT COUNT(*) FROM oa_position_group WHERE pid=%s;" % pid))
    _cache_clear(sess_admin)
    emp3 = _emp_list(s_new)
    att3 = _att_list(s_new)
    K.record("账号A（职位已删+清缓存）GET /user/user/index", emp3)
    K.record("账号A（职位已删+清缓存）GET /oa/attendance/datalist", att3)
    K.record("缺陷三现场：oa_position.status=%s；oa_position_group 残留 %s 行" % (status, pg_left), emp3)
    K.expect((res.get("code") == 0 and status == "-1", "删职位 code=%s；status=%s"
              % (res.get("code"), status)),
             "前置：删职位动作本身应成功（status=-1）")
    K.expect(K.is_denied(emp3), "职位已删除 → 关联账号的员工列表权限应被回收（405）")
    K.expect(K.is_denied(att3), "职位已删除 → 关联账号的我的假勤权限应被回收（405）")


# ===========================================================================
# QX-031 ~ QX-037：垂直越权（低权限账号直达高权限 URL / 接口）
# ===========================================================================
def test_qx_031_vertical_biz_role_url(sess_yucixin):
    """QX-031 垂直越权-业务岗直接访问角色组管理 URL。

    账号 yucixin(5)；GET /home/role/index。
    判据：见断言（期望 is_denied）。
    """
    res = K.call(sess_yucixin, "/home/role/index", "GET")
    K.record("yucixin GET /home/role/index（URL 直达）", res)
    K.expect(K.is_denied(res), "业务岗直达角色组管理 URL 应被拦下（code=405）")


def test_qx_032_vertical_biz_role_add(sess_yucixin):
    """QX-032 垂直越权-业务岗调用角色组保存接口。

    账号 yucixin(5)；POST /home/role/add（空载荷）。
    判据：见断言 + 写操作无副作用核对（oa_admin_group 行数前后一致）。
    已确认结论为「通过」。
    """
    n0 = str(K.scalar("SELECT COUNT(*) FROM oa_admin_group;"))
    res = K.call(sess_yucixin, "/home/role/add", "POST", {})
    K.record("yucixin POST /home/role/add（空载荷）", res)
    n1 = str(K.scalar("SELECT COUNT(*) FROM oa_admin_group;"))
    K.expect(K.is_denied(res), "业务岗调用角色组保存接口应被拦下（code=405）")
    K.expect((n0 == n1, "oa_admin_group %s→%s" % (n0, n1)), "越权请求不应产生任何角色组")


def test_qx_033_vertical_zero_user_add():
    """QX-033 垂直越权-零权限口径账号调用员工新增接口（**预期结果已修订**）。

    账号 qinjiaxian（执行器指定的攻击者，对齐 REVISIONS['QX-033']：原设计的攻击者「业务岗」
    实际持有节点 98 user/user/add，构不成垂直越权，故改用零权限口径账号复测）。
    请求：POST /user/user/add（空载荷）。
    判据：见断言 + 无副作用核对（oa_admin 行数前后一致）。
    框架边界：oa_kit.ACCOUNTS 未登记 qinjiaxian（get_session 会 KeyError），故本用例
    直接经 oa_login.login 建会话（未改框架）。已确认结论为「通过」。
    """
    n0 = str(K.scalar("SELECT COUNT(*) FROM oa_admin;"))
    res = K.call(_sess_of(ATTACKER_ZERO), "/user/user/add", "POST", {})
    K.record("%s POST /user/user/add（空载荷）" % ATTACKER_ZERO, res)
    n1 = str(K.scalar("SELECT COUNT(*) FROM oa_admin;"))
    K.expect(K.is_denied(res), "无员工新增权限的账号应被拦下（code=405）")
    K.expect((n0 == n1, "oa_admin %s→%s" % (n0, n1)), "越权请求不应新增任何员工")


def test_qx_034_vertical_zero_contract_del():
    """QX-034 垂直越权-零权限口径账号删除他人合同（**预期结果已修订**）。

    账号 qinjiaxian；POST /contract/contract/del {id=1020}。
    判据：见断言 + REVISIONS['QX-034']（原设计的业务岗攻击者
    持有节点 352 contract/contract/del，属被授权操作，故改用零权限口径账号复测）；
    无副作用核对：oa_contract.id=1020 的 delete_time 前后一致（基线 0）。
    已确认结论为「通过」。
    """
    before = str(K.scalar("SELECT delete_time FROM oa_contract WHERE id=%d;" % CONTRACT_ID))
    K.expect((before == "0", "delete_time 基线=%s" % before),
             "前置：合同 %d 应为未删除状态（delete_time=0）" % CONTRACT_ID)
    res = K.call(_sess_of(ATTACKER_ZERO), "/contract/contract/del", "POST", {"id": CONTRACT_ID})
    K.record("%s POST /contract/contract/del?id=%d" % (ATTACKER_ZERO, CONTRACT_ID), res)
    after = str(K.scalar("SELECT delete_time FROM oa_contract WHERE id=%d;" % CONTRACT_ID))
    K.expect(K.is_denied(res), "无合同删除权限的账号应被拦下（code=405）")
    K.expect((after == before, "delete_time %s→%s" % (before, after)),
             "越权请求不应改动合同的 delete_time")


def test_qx_035_anonymous_page_redirect():
    """QX-035 未登录访问需登录页面。

    干净会话（未登录）；GET /home/index/index。
    判据：见断言（期望 is_login_redirect）。
    """
    res = K.call(None, "/home/index/index", "GET")
    K.record("(未登录) GET /home/index/index", res)
    K.expect(K.is_login_redirect(res), "未登录访问受保护页面应 302 跳登录页")


def test_qx_036_anonymous_api_need_login():
    """QX-036 未登录调用受保护接口（**预期结果已修订**）。

    干净会话；POST /api/check/submit_check。
    判据出处：REVISIONS['QX-036'] —— 未登录时 `apiError('请先登录')` 走的是 code=1，
    原用例写 code=404 属状态码写错，行为本身（拦截且不返回业务数据）符合预期，
    故按 code=1 且 msg 含「请先登录」判定。已确认结论为「通过」（修订后口径）。
    """
    res = K.call(None, "/api/check/submit_check", "POST", {})
    K.record("(未登录) POST /api/check/submit_check", res)
    K.expect(K.is_need_login(res), "未登录调用受保护接口应返回 code=1「请先登录」（修订后口径）")


def test_qx_037_vertical_manager_db_import(sess_fengcailing):
    """QX-037 垂直越权-管理岗访问数据还原（导入）接口。

    账号 fengcailing(4)；POST /home/database/import（空载荷）。
    判据：见断言（期望 is_denied）。
    """
    res = K.call(sess_fengcailing, "/home/database/import", "POST", {})
    K.record("fengcailing POST /home/database/import", res)
    K.expect(K.is_denied(res), "管理岗无数据还原权限，应被拦下（code=405）")


# ===========================================================================
# QX-038 / QX-046：审批动作越权（目标单据 oa_leaves id=1072，归属 uid=4）
#   源码依据 app/api/controller/Check.php flow_check()：
#     check==1 → if(!in_array(uid, check_uids)) 拒「您没权限审核该审批」
#     check==3 → if(detail.admin_id != uid) 拒「你没权限操作」
#   断言必须落到 DB：不能只看响应码 —— 撤回成功会把 check_status 改成 4（已撤回）。
# ===========================================================================
def test_qx_038_flow_check_not_assignee_denied(sess_yucixin):
    """QX-038 垂直越权-非指定审批人调用审批动作接口（check=1 审批通过）。

    账号 yucixin(5)（业务岗）；POST /api/check/flow_check {check_name=leaves, action_id=1072,
    check=1, check_uids=1, content, check_files}。目标单据归属 uid=4、审批人是 uid=1。
    判据：见断言（blocked = code=1 且 msg 含「没权限审核」
    且 check_status 前后一致）。
    """
    table, aid = "oa_leaves", FLOW_TARGET_ID
    before = str(K.scalar("SELECT check_status FROM %s WHERE id=%d;" % (table, aid)))
    res = K.call(sess_yucixin, "/api/check/flow_check", "POST",
                 {"check_name": "leaves", "action_id": str(aid),
                  "content": "（权限用例探测，预期不产生任何审批记录）", "check_files": "",
                  "check": "1", "check_uids": "1"})
    K.record("yucixin POST /api/check/flow_check check=1 action_id=%d" % aid, res)
    after = str(K.scalar("SELECT check_status FROM %s WHERE id=%d;" % (table, aid)))
    K.expect((res.get("code") == 1 and "没权限审核" in (res.get("msg") or ""),
              "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "非指定审批人执行「审批通过」应被业务层拒绝（code=1 没权限审核）")
    K.expect((before == after, "check_status %s→%s" % (before, after)),
             "被拒的审批动作不应改动单据的 check_status")


def test_qx_046_flow_withdraw_others_denied(sess_yucixin):
    """QX-046 水平越权-业务岗撤回他人请假单（check=3 审批撤回）。

    账号 yucixin(5)；POST /api/check/flow_check {check_name=leaves, action_id=1072, check=3}。
    判据：见断言 + 477-500（blocked = code=1 且 msg 含「没权限操作」
    且 check_status 前后一致；撤回成功会把 check_status 改成 4）。已确认结论为「通过」。
    """
    table, aid = "oa_leaves", FLOW_TARGET_ID
    before = str(K.scalar("SELECT check_status FROM %s WHERE id=%d;" % (table, aid)))
    res = K.call(sess_yucixin, "/api/check/flow_check", "POST",
                 {"check_name": "leaves", "action_id": str(aid),
                  "content": "（权限用例探测，预期不产生任何审批记录）", "check_files": "",
                  "check": "3"})
    K.record("yucixin POST /api/check/flow_check check=3 action_id=%d" % aid, res)
    after = str(K.scalar("SELECT check_status FROM %s WHERE id=%d;" % (table, aid)))
    K.expect((res.get("code") == 1 and "没权限操作" in (res.get("msg") or ""),
              "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "非单据归属人执行撤回应被业务层拒绝（code=1 没权限操作）")
    K.expect((before == after, "check_status %s→%s" % (before, after)),
             "被拒的撤回不应改动单据的 check_status（未变成 4 已撤回）")


# ===========================================================================
# QX-039 ~ QX-045：水平越权四模块（请假 / 加班 / 出差 / 外出）+ 本人对照
#   受害者 yucixin(5)，攻击者 testemp(109) —— 与受害者**同职位 3 / 同角色组 3**
#   （排除「上级看下级」的正当性）。源码：四模块 view/add/del 均无归属校验。私有取证记录，严重 B。
# ===========================================================================
@pytest.mark.known_defect
@pytest.mark.xfail(reason="水平越权：leaves view 无归属校验，他人可读他人单据（严重 B）",
                   strict=True)
def test_qx_039_idor_read_others_leave(sess_yucixin, sess_testemp):
    """QX-039 水平越权-业务岗查看他人请假单详情。**【已知缺陷，本用例记为失败 → xfail】**

    受害者 yucixin 建单 → 攻击者 testemp GET /home/leaves/view?id=<他人单>。
    判据：见测试报告摘要「[读-攻击者 testemp] GET /home/leaves/view?id=1002 -> HTTP 200
    看到受害者事由原文=True」⇒ 原预期「被拦截，不返回他人请假单内容」未达成（已确认结论为「失败」）。
    函数体写**正确行为**：他人单据的事由原文不得出现在攻击者的响应里。
    """
    rid, marker = _victim_doc("leaves-read", "leaves", "/home/leaves", sess_yucixin)
    page = K.call(sess_testemp, "/home/leaves/view", "GET", {"id": rid})
    K.record("testemp GET /home/leaves/view?id=%s（越权读，单据归属 yucixin）" % rid, page)
    K.expect(_no_leak_ok(page, marker), "攻击者不应能读到他人请假单的事由原文")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="水平越权：leaves add(id) 编辑分支无归属校验，他人可静默改写单据（严重 B）",
                   strict=True)
def test_qx_040_idor_edit_others_leave(sess_yucixin, sess_testemp):
    """QX-040 水平越权-业务岗编辑他人请假单。**【已知缺陷，本用例记为失败 → xfail】**

    攻击者 testemp POST /home/leaves/add?id=<他人单>（改写事由）。
    判据：见测试报告摘要「[改-攻击者 testemp] POST /home/leaves/add?id=1002 -> code=0 操作成功」+
    「[受害者回看] 事由已被改写=True」⇒ 预期未达成（已确认结论为「失败」）。
    函数体写**正确行为**：他人单据的 reason 必须原样不变，且请求不应被当作成功放行。
    """
    rid, marker = _victim_doc("leaves-edit", "leaves", "/home/leaves", sess_yucixin)
    K.expect((str(K.scalar("SELECT reason FROM oa_leaves WHERE id=%s;" % rid)) == marker,
              "改写前 reason=%r" % K.scalar("SELECT reason FROM oa_leaves WHERE id=%s;" % rid)),
             "前置：单据事由应为受害者写入的原文")
    res = K.call(sess_testemp, "/home/leaves/add", "POST",
                 {"id": rid, "types": "1", "start_date": "2026-09-20 09:00",
                  "end_date": "2026-09-20 18:00", "duration": "1",
                  "reason": "%s被他人改写-%s" % (TAG, NONCE), "file_ids": ""})
    K.record("testemp POST /home/leaves/add?id=%s（越权改）" % rid, res)
    now = str(K.scalar("SELECT reason FROM oa_leaves WHERE id=%s;" % rid))
    K.expect((res.get("code") != 0, "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "攻击者改写他人单据不应被放行（code≠0）")
    K.expect((now == marker, "改写后 reason=%r" % now),
             "他人单据的事由应保持受害者原文不变")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="水平越权：leaves del 无归属校验，他人可软删单据（严重 B）",
                   strict=True)
def test_qx_041_idor_delete_others_leave(sess_yucixin, sess_testemp):
    """QX-041 水平越权-业务岗删除他人请假单。**【已知缺陷，本用例记为失败 → xfail】**

    攻击者 testemp DELETE /home/leaves/del?id=<他人单>。
    判据：见测试报告摘要「[删] leaves 模块 单号 1002 —— 攻击者 testemp（code=0 操作成功，软删）」
    （且软删后该记录仍被列表列出 —— delete_time 过滤未生效）⇒ 预期未达成（已确认结论为「失败」）。
    函数体写**正确行为**：他人单据的 delete_time 必须保持 0。
    """
    rid, marker = _victim_doc("leaves-del", "leaves", "/home/leaves", sess_yucixin)
    before = str(K.scalar("SELECT delete_time FROM oa_leaves WHERE id=%s;" % rid))
    res = _delete(sess_testemp, "/home/leaves/del", {"id": rid})
    K.record("testemp DELETE /home/leaves/del?id=%s（越权删）" % rid, res)
    after = str(K.scalar("SELECT delete_time FROM oa_leaves WHERE id=%s;" % rid))
    K.expect((res.get("code") != 0, "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "攻击者删除他人单据不应被放行（code≠0）")
    K.expect((after == before, "delete_time %s→%s（基线 0 表示未删除）" % (before, after)),
             "他人单据不应被改动 delete_time（不得软删）")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="水平越权：overtimes view 无归属校验，他人可读他人单据（严重 B）",
                   strict=True)
def test_qx_042_idor_read_others_overtime(sess_yucixin, sess_testemp):
    """QX-042 水平越权-业务岗查看他人加班单。**【已知缺陷，本用例记为失败 → xfail】**

    判据：见测试报告摘要 overtimes 模块「[读-攻击者 testemp] view?id=1002 -> 看到受害者事由原文=True」
    （已确认结论为「失败」）。函数体写正确行为：他人单据原文不得出现在攻击者响应里。
    """
    rid, marker = _victim_doc("overtimes-read", "overtimes", "/home/overtimes", sess_yucixin)
    page = K.call(sess_testemp, "/home/overtimes/view", "GET", {"id": rid})
    K.record("testemp GET /home/overtimes/view?id=%s（越权读，单据归属 yucixin）" % rid, page)
    K.expect(_no_leak_ok(page, marker), "攻击者不应能读到他人加班单的事由原文")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="水平越权：trips view 无归属校验，他人可读他人单据（严重 B）",
                   strict=True)
def test_qx_043_idor_read_others_trip(sess_yucixin, sess_testemp):
    """QX-043 水平越权-业务岗查看他人出差单。**【已知缺陷，本用例记为失败 → xfail】**

    判据：见测试报告摘要 trips 模块「[读-攻击者 testemp] view?id=1002 -> 看到受害者事由原文=True」
    （已确认结论为「失败」）。函数体写正确行为：他人单据原文不得出现在攻击者响应里。
    """
    rid, marker = _victim_doc("trips-read", "trips", "/home/trips", sess_yucixin)
    page = K.call(sess_testemp, "/home/trips/view", "GET", {"id": rid})
    K.record("testemp GET /home/trips/view?id=%s（越权读，单据归属 yucixin）" % rid, page)
    K.expect(_no_leak_ok(page, marker), "攻击者不应能读到他人出差单的事由原文")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="水平越权：outs view 无归属校验，他人可读他人单据（严重 B）",
                   strict=True)
def test_qx_044_idor_read_others_out(sess_yucixin, sess_testemp):
    """QX-044 水平越权-业务岗查看他人外出单。**【已知缺陷，本用例记为失败 → xfail】**

    判据：见测试报告摘要 outs 模块「[读-攻击者 testemp] view?id=1002 -> 看到受害者事由原文=True」
    （已确认结论为「失败」）。函数体写正确行为：他人单据原文不得出现在攻击者响应里。
    """
    rid, marker = _victim_doc("outs-read", "outs", "/home/outs", sess_yucixin)
    page = K.call(sess_testemp, "/home/outs/view", "GET", {"id": rid})
    K.record("testemp GET /home/outs/view?id=%s（越权读，单据归属 yucixin）" % rid, page)
    K.expect(_no_leak_ok(page, marker), "攻击者不应能读到他人外出单的事由原文")


def test_qx_045_owner_can_view_own_leave(sess_yucixin):
    """QX-045 水平越权对照-本人查看本人请假单（**对照组，判定为通过**）。

    账号 yucixin(5) 建单后自查 GET /home/leaves/view?id=<本人单>。
    判据：见测试报告摘要「[受害者自查] view -> HTTP 200 含事由原文=True」⇒ 本人可正常查看本人单据，
    **对照成立**：QX-039~044 的失败不是「接口整体不可用」，而是**缺归属校验**。
    已确认结论为「通过」。
    """
    rid, marker = _victim_doc("leaves-owner", "leaves", "/home/leaves", sess_yucixin)
    page = K.call(sess_yucixin, "/home/leaves/view", "GET", {"id": rid})
    K.record("yucixin GET /home/leaves/view?id=%s（本人自查）" % rid, page)
    text = page.get("text") or ""
    K.expect((page.get("http") == 200 and marker in text,
              "HTTP %s code=%s 页面 %d 字节；含本人事由原文=%s"
              % (page.get("http"), page.get("code"), len(text), marker in text)),
             "本人应能查看本人请假单的事由原文（对照组必须成立）")


# ===========================================================================
# QX-047 / QX-048：站内消息越权（app/home/controller/Message.php 四处无归属校验）
#   对照：同文件的 view()/read()/reply()/resend()/add() 五处**有**校验。
# ===========================================================================
@pytest.mark.known_defect
@pytest.mark.xfail(reason="缺陷一 check() 无归属条件，可越权改他人收件箱状态（严重 B）",
                   strict=True)
def test_qx_047_message_check_others_inbox(sess_yucixin, sess_testemp, sess_testmin):
    """QX-047 水平越权-站内消息越权操作他人收件箱状态。**【已知缺陷，本用例记为失败 → xfail】**

    受害者 yucixin 的收件行 oa_msg（由 testmin 发消息产生）→ 攻击者 testemp
    DELETE /home/message/check {type=2 移入垃圾箱, ids=<收件行>}。
    判据：见测试报告摘要 缺陷一：「DELETE /home/message/check {type:2, ids:1000} -> code=0 操作成功」，
    回读 oa_msg.delete_time 被改写（受害者收件箱不再可见该消息）；type=5 星标同样成功
    ⇒ 预期「被拦截，delete_time/is_star 不被改写」未达成（已确认结论为「失败」）。
    源码：Message.php check()（第 490-522 行）`MsgList::where(['id'=>$val])->update([...])`
    全程无归属条件；message 模块在 $not_check 白名单内（BaseController.php:115），整体跳过 RBAC。
    函数体写**正确行为**：请求不得被当作成功放行，且他人收件行的 delete_time/is_star 不变。
    """
    _mid, sid = _victim_msg(sess_testmin, UID_YUCXIN)
    before = (str(K.scalar("SELECT delete_time FROM oa_msg WHERE id=%s;" % sid)),
              str(K.scalar("SELECT is_star FROM oa_msg WHERE id=%s;" % sid)))
    res = _delete(sess_testemp, "/home/message/check", {"type": "2", "ids": sid})
    K.record("testemp DELETE /home/message/check {type=2,ids=%s}（越权改他人收件箱）" % sid, res)
    after = (str(K.scalar("SELECT delete_time FROM oa_msg WHERE id=%s;" % sid)),
             str(K.scalar("SELECT is_star FROM oa_msg WHERE id=%s;" % sid)))
    K.expect((res.get("code") != 0, "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "攻击者操作他人收件箱不应被放行（code≠0）")
    K.expect((before == after, "（delete_time, is_star）%s→%s（收件行 to_uid=yucixin）"
              % (before, after)),
             "他人收件箱的 delete_time / is_star 不应被改写")


@pytest.mark.known_defect
@pytest.mark.xfail(reason="缺陷二 del() 无归属条件，可越权删他人发件/草稿（严重 B）",
                   strict=True)
def test_qx_048_message_del_others_outbox(sess_yucixin, sess_testemp, sess_testmin):
    """QX-048 水平越权-站内消息越权删除他人发件与草稿。**【已知缺陷，本用例记为失败 → xfail】**

    受害者 = 发送者 testmin 的发件行 oa_message → 攻击者 testemp
    DELETE /home/message/del {ids=<发件行>}。
    判据：见测试报告摘要 缺陷二：Message.php del()（第 349-359 行）
    `MessageList::where(['id'=>$val])->update(['delete_time'=>time()])` 无任何归属条件，
    实测攻击者可删他人发件/草稿，回读 oa_message.delete_time 被改写
    ⇒ 预期「被拦截，delete_time 不被改写」未达成（已确认结论为「失败」）。
    说明：用例原文写「查询 oa_message.delete_time」，实际表名**就是** oa_message（发送方凭据），
    执行器备注里「实际表名为 oa_msg」的更正有误 —— oa_msg 是收件镜像（QX-047 的对象），
    本条删的确实是 oa_message。
    函数体写**正确行为**：请求不得被放行，且他人发件行的 delete_time 不变。
    """
    mid, _sid = _victim_msg(sess_testmin, UID_YUCXIN)
    before = str(K.scalar("SELECT delete_time FROM oa_message WHERE id=%s;" % mid))
    res = _delete(sess_testemp, "/home/message/del", {"ids": mid})
    K.record("testemp DELETE /home/message/del {ids=%s}（越权删他人发件）" % mid, res)
    after = str(K.scalar("SELECT delete_time FROM oa_message WHERE id=%s;" % mid))
    K.expect((res.get("code") != 0, "code=%s msg=%s" % (res.get("code"), res.get("msg"))),
             "攻击者删除他人发件记录不应被放行（code≠0）")
    K.expect((after == before, "oa_message.delete_time %s→%s（发件行 from_uid=testmin）"
              % (before, after)),
             "他人发件记录的 delete_time 不应被改写")


# ===========================================================================
# QX-049 ~ QX-053：/api/* 授权面
# ===========================================================================
def test_qx_049_api_employee_data_biz_allowed(sess_yucixin):
    """QX-049 API授权-业务岗调用员工数据接口（**预期结果已修订**）。

    账号 yucixin(5)；GET /api/index/get_employee {did=0}。
    判据出处：REVISIONS['QX-049'] —— 沿用**已撤回**的缺陷候选 C2 的处置：UI 对照法证明业务岗在
    /user/user/index 企业员工页界面上本来就展示手机号列 ⇒ 对该角色属设计如此，原预期
    「被拦截；若返回全量员工（含手机号）则为缺陷」修订为「按设计放行」。真正的问题是
    **零权限账号**也能取得同一份数据（102 条含 mobile），该问题已由私有取证记录 覆盖，不重复记。
    已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_yucixin, "/api/index/get_employee", "GET", {"did": 0})
    K.record("yucixin GET /api/index/get_employee?did=0", res)
    K.expect(K.is_allowed(res), "业务岗调用员工数据接口应按设计放行（修订后口径：code=0）")
    if isinstance(res.get("data"), list) and res["data"]:
        mob = sum(1 for x in res["data"] if str(x.get("mobile", "")).strip())
        K.record("QX-049 数据规模：共 %d 条、含 mobile 非空 %d 条" % (len(res["data"]), mob), res)


def test_qx_050_api_import_superadmin_guard(sess_yucixin):
    """QX-050 API授权-业务岗调用员工导入接口。

    账号 yucixin(5)；POST /api/import/import_admin（空载荷）。
    判据：见断言 + 源码 app/api/controller/Import.php:66
    `if($this->uid>1) return to_assign(1, '该操作只能是超级管理员有权限操作')`。
    已确认结论为「通过」。
    """
    res = K.call(sess_yucixin, "/api/import/import_admin", "POST", {})
    K.record("yucixin POST /api/import/import_admin", res)
    K.expect(K.is_superadmin_guard(res), "业务岗调用员工导入应被业务层超管守卫拒绝（code=1 超级管理员）")


def test_qx_051_api_export_no_rule_node(sess_yucixin):
    """QX-051 API授权-业务岗调用数据导出接口（**预期结果已修订**）。

    账号 yucixin(5)；POST /api/export/excel {types=contract}。
    判据出处：REVISIONS['QX-051'] —— oa_admin_rule 中**不存在** export 节点，RBAC 无可校验项
    （已撤回缺陷候选 C3）；实测参数缺失/记录不存在时返回 HTTP 500（Export.php:343 的
    `$detail['month_time']` 在 oa_salary 为空时触发），该 500 与 QX-052 同源：
    **参数未兜底致未定义数组键警告被 ThinkPHP 升级成 ErrorException**。
    故判据 = is_gate_absent（未出现 405 → 无节点参与比对），实测串里原样保留 500，不粉饰。
    已确认结论为「通过」（修订后口径）。
    """
    res = K.call(sess_yucixin, "/api/export/excel", "POST", {"types": "contract"})
    K.record("yucixin POST /api/export/excel {types=contract}", res)
    K.expect(K.is_gate_absent(res), "导出接口在 RBAC 中无权限节点 → 不应出现 405")
    if res.get("http") == 500:
        K.record("关联缺陷：参数未兜底致 HTTP 500（与 QX-052 同源）", res)


@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-01：未登录可越过闸门打到定位接口，参数未兜底放大成 HTTP 500",
                   strict=True)
def test_qx_052_api_maplocation_anonymous_blocked():
    """QX-052 API授权-未登录调用定位接口。**【已知缺陷，本用例记为失败 → xfail】**

    干净会话（未登录）；GET /api/map/getLocation。
    判据：见断言 用 is_blocked（未授权访问的三种合法拦截形态之外
    一律算「没拦住」）；实测 **未被拦截**：HTTP 500（HTML 错误页），即请求已越过闸门进到动作体内
    —— 属私有取证记录「参数未兜底致参数缺失时返回 500」+ 私有取证记录「app/api/* 缺闸门」，
    已确认结论为「失败」。
    函数体写**正确行为**：未登录调用应被三种合法形态之一拦下（302 跳登录 / code=405 /
    code=1 请先登录），而不是 500。
    """
    res = K.call(None, "/api/map/getLocation", "GET")
    K.record("(未登录) GET /api/map/getLocation", res)
    K.expect(K.is_blocked(res), "未登录调用定位接口应被拦下（302 跳登录 / code=405 / code=1 请先登录）")


def test_qx_053_api_submit_check_anonymous():
    """QX-053 API授权-未登录调用提交审批。

    干净会话（未登录）；POST /api/check/submit_check（空载荷）。
    判据：见断言（期望 is_need_login）。
    已确认结论为「通过」。
    """
    res = K.call(None, "/api/check/submit_check", "POST", {})
    K.record("(未登录) POST /api/check/submit_check", res)
    K.expect(K.is_need_login(res), "未登录调用提交审批应返回 code=1「请先登录」")


# ===========================================================================
# QX-054 / QX-055：会话超时 + 权限缓存生效性
# ===========================================================================
@pytest.mark.known_defect
@pytest.mark.xfail(reason="缺陷一：app/api/BaseController::checkLogin() 只判 Session::has()，"
                          "缺会话超时/锁屏/改密/权限四道中的三道",
                   strict=True)
def test_qx_054_api_session_timeout(sess_admin):
    """QX-054 API授权-会话超时后调用接口。**【已知缺陷，本用例记为失败 → xfail】**

    账号 testmin(108)；把 last_login_time 前移 36001 秒后先调 /api 只读端点、再访问 /home 页面。
    判据：见断言 —— 期望 home 页面 302 跳登录（实测符合）
    **且** /api 接口返回 code=1「请先登录」（实测仍 code=0 返回业务数据，未判超时）⇒ 已确认结论为「失败」。
    关联缺陷：与**已归档的缺陷一同源**，不另立新缺陷 ——
    app/api/BaseController::checkLogin() 只判断 Session::has()，不含超时/锁屏/强制改密/权限检查。
    构造必须用 **MySQL 自身时钟**（Windows 时钟实测比 WSL/PHP 快 147 秒，用 Python time() 会引入偏差）。
    会话隔离：本用例另起一个登录会话（不改用 sess_testmin 夹具会话），并在 finally 里把
    last_login_time 还原为当前时间（MySQL 时钟）—— 超时闸门会 `Session::delete()` 掉当前会话键，
    用夹具会话会把共享会话打死、连累后续用例。
    函数体写**正确行为**：/api 侧也应判超时（code=1 请先登录），且 home 页面 302 跳登录页。
    """
    uid = UID_TESTMIN
    s_to = login("testmin", K.USER_PWD)
    try:
        K.run_sql("UPDATE oa_admin SET last_login_time=UNIX_TIMESTAMP()-36001 WHERE id=%d;" % uid)
        diff = str(K.scalar("SELECT UNIX_TIMESTAMP()-last_login_time FROM oa_admin WHERE id=%d;" % uid))
        api1 = K.call(s_to, "/api/index/get_department", "GET")
        api2 = K.call(s_to, "/api/index/get_position", "GET")
        home = K.call(s_to, "/home/role/index", "GET")
        K.record("testmin（超时）GET /api/index/get_department", api1)
        K.record("testmin（超时）GET /api/index/get_position", api2)
        K.record("testmin（超时）GET /home/role/index", home)
        K.record("超时构造：last_login_time 距 MySQL now %s 秒（>36000）" % diff, api2)
        api_ok = all(x.get("code") == 1 and "请先登录" in (x.get("msg") or "") for x in (api1, api2))
        K.expect((api_ok, "get_department：%s；get_position：%s"
                  % (K.brief(api1), K.brief(api2))),
                 "/api/* 应与会话超时联动（返回 code=1 请先登录）")
        K.expect(K.is_login_redirect(home), "home 页面超时后应 302 跳登录页")
    finally:
        K.run_sql("UPDATE oa_admin SET last_login_time=UNIX_TIMESTAMP() WHERE id=%d;" % uid)
        K.record("已还原 last_login_time=%s" % K.scalar("SELECT last_login_time FROM oa_admin WHERE id=%d;" % uid),
                 {"http": 0})


def test_qx_055_role_change_effective_two_way(sess_admin, sess_testmin):
    r"""QX-055 权限缓存-改角色后权限生效性验证（授权 / 回收**双向对照**）。

    账号 admin(1) 改角色组 4；观察对象 testmin(108)。
    判据：见断言 —— 双向对照：授权后 code=0（放行）、
    回收后 code=405（拦截），且**清缓存前后结果均不变** ⇒ 结论「角色组权限变更保存后即时生效，
    无需额外操作」；机制依据（读源码，非推断）：clear_cache($key) → think\\Cache::clear()
    无 $tag 形参、File::clear() 亦无参且直接 rmdir 整个缓存目录 ⇒ 传入的 $key 被忽略，
    任何一次 clear_cache() 都是全量清空，故 Role.php:90-91 只写 adminMenu/MobileRules 与
    Rule.php / Module.php / Position.php 写 adminRules 的差异**不产生行为差异**。
    已确认结论为「通过」。
    """
    res, rules = _set_group4_rules(sess_admin, [NODE_ATT], note="（QX-055 前置复位）")
    K.expect((res.get("code") == 0 and rules == str(NODE_ATT), "rules=%s" % rules),
             "前置：角色组 4 应复位为仅含节点 %d" % NODE_ATT)
    # 授权方向
    res, after = _set_group4_rules(sess_admin, [NODE_ATT, NODE_EMP])
    K.expect((res.get("code") == 0 and str(NODE_EMP) in after.split(","), "rules=%s" % after),
             "授权保存应成功")
    g_imm = _emp_list(sess_testmin)
    K.record("testmin（授权后未清缓存）GET /user/user/index", g_imm)
    K.expect(K.is_allowed(g_imm), "授权后应立即放行（未清缓存）")
    _cache_clear(sess_admin)
    g_cle = _emp_list(sess_testmin)
    K.record("testmin（授权后已清缓存）GET /user/user/index", g_cle)
    K.expect(K.is_allowed(g_cle), "授权后清缓存仍应放行")
    K.expect((g_imm.get("code") == g_cle.get("code"), "授权方向：未清 %s / 已清 %s"
              % (g_imm.get("code"), g_cle.get("code"))),
             "授权方向清缓存前后结论应一致")
    # 回收方向
    res, after = _set_group4_rules(sess_admin, [NODE_ATT])
    K.expect((res.get("code") == 0 and str(NODE_EMP) not in after.split(","), "rules=%s" % after),
             "回收保存应成功且节点已移除")
    r_imm = _emp_list(sess_testmin)
    K.record("testmin（回收后未清缓存）GET /user/user/index", r_imm)
    K.expect(K.is_denied(r_imm), "回收后应立即被拦截（未清缓存）")
    _cache_clear(sess_admin)
    r_cle = _emp_list(sess_testmin)
    K.record("testmin（回收后已清缓存）GET /user/user/index", r_cle)
    K.expect(K.is_denied(r_cle), "回收后清缓存仍应被拦截")
    K.expect((r_imm.get("code") == r_cle.get("code"), "回收方向：未清 %s / 已清 %s"
              % (r_imm.get("code"), r_cle.get("code"))),
             "回收方向清缓存前后结论应一致")
    K.expect((g_imm.get("code") == 0 and r_imm.get("code") == 405,
              "授权后 code=%s；回收后 code=%s" % (g_imm.get("code"), r_imm.get("code"))),
             "双向对照应成立：授权放行、回收拦截（说明权限变更即时生效）")
