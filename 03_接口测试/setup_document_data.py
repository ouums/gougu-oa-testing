# -*- coding: utf-8 -*-
r"""
勾股OA 6.1.2 —— 构造「请假 / 加班 / 出差 / 外出」单据测试数据

【为什么需要这个脚本】
  执行阶段开始时实测：oa_leaves / oa_overtimes / oa_trips / oa_outs **四表全部 0 行**
  （此前 probe_horizontal.py 建的越权验证单据已在取证结束时物理删除，恢复空基线）。
  而下列用例的前置条件都要求库中已存在特定状态的单据：

      权限体系  QX-039~046   水平越权读/改/删/撤回「他人单据」
      审批流    SP-006~012   请假单状态、本人查看、撤回、通过、驳回、驳回后重提
      审批流    SP-015/017/019 加班/出差/外出 的审批通过 / 驳回
      审批流    SP-020~022/031/032 两级审批流转与撤回
      审批流    SP-027/028   非指定审批人、待我审批列表
      审批流    SP-033/034/035 状态筛选 / 分页 / 他人单据隔离

  故按测试计划 §4.5 的要求先构造这批数据。

【与计划 4.5 的偏差（如实记录，不事后回填）】
  计划写的是「请假 / 加班 / 出差 / 外出 每类各 2~3 条，覆盖 待审批/已通过/已驳回/已撤回」。
  实测下来每类 2~3 条不够用：①「他人单据」与本人在同一模块内必须是**两张不同的单**
  （否则 QX-039 读越权和 QX-040 改越权会互相破坏前置）；② 两级审批需要一张独立的单；
  ③ SP-034 分页要求本人单据数 > 每页条数（config/app.php:43 page_size=20），
  请假单必须达到 21 条以上。故实际构造量高于计划值 —— 这一条写进测试报告，不修计划数字。

【构造方式：全程走真实接口，不直接 INSERT】
  建单  POST /home/{leaves|overtimes|trips|outs}/add
  提交  POST /api/check/submit_check   {check_name, action_id, flow_id, check_uids}
  审批  POST /api/check/flow_check     {check_name, action_id, check_flow_id, check, content}
        check=1 通过 / 2 驳回（界面文案为「退回」，同一分支）/ 3 撤回
  走接口才会生成 check_* 字段与 oa_flow_step / oa_flow_record / oa_msg / oa_admin_log 联动数据。

【审批模式说明】
  本实例 19 条 oa_flow 的 flow_list 全为空、check_type 全为 1（自由审批），
  与合同数据构造时一致：submit_check 带 check_uids 即走 Check.php:169 的自由审批分支。
  两级审批在自由审批模式下也能建：第一级通过时带 check_node=2 + check_uids 即追加下一级
  （Check.php:385-398）。

【数据标记与回收】
  每条单据的 reason 以 MARK 开头，回收脚本 cleanup_document_data.py 按 MARK 匹配。

【用法】
  python 03_接口测试\setup_document_data.py            # dry-run：打印计划与现状
  python 03_接口测试\setup_document_data.py --apply    # 真正构造
"""
import argparse
import base64
import subprocess
import sys
import time
from datetime import date, timedelta

import requests

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from oa_login import BASE_URL, login  # noqa: E402
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 78
WSL_DISTRO = oa_config.WSL_DISTRO
DB = oa_config.DB_NAME
MYSQL = oa_config.MYSQL_NB

MARK = "【功能测试数据】"

APPLICANT = "yucixin"      # 业务岗 id=5 —— SP 用例的「本人」、QX 水平越权的「攻击者」
OTHER = "fengcailing"      # 管理岗 id=4 —— QX 水平越权的「他人（受害者）」、SP 的指定审批人
ADMIN = "admin"            # 超管 id=1 —— 两级审批的第二级审批人

# 模块 -> (表名, 审批流程 id, check_name)
MODULES = {
    "leaves":    ("oa_leaves",    1, "leaves"),
    "overtimes": ("oa_overtimes", 4, "overtimes"),
    "trips":     ("oa_trips",     2, "trips"),
    "outs":      ("oa_outs",      3, "outs"),
}

# 各模块必填字段（对齐 probe_horizontal.py 实测通过的报文）
FORM_EXTRA = {
    "leaves":    {"types": 1},   # 事假
    "overtimes": {},
    "trips":     {},
    "outs":      {},
}


# ------------------------------------------------------------------ SQL
def run_sql(sql):
    b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    shell = ("echo %s | base64 -d > /tmp/oa_doc.sql && %s < /tmp/oa_doc.sql; rc=$?; "
             "rm -f /tmp/oa_doc.sql; exit $rc" % (b64, MYSQL))
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", shell],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=180)
    if out.returncode != 0 or "ERROR" in (out.stderr or ""):
        raise RuntimeError("SQL 执行失败:\nstdout=%s\nstderr=%s" % (out.stdout, out.stderr))
    return (out.stdout or "").strip()


def rows(sql):
    return [ln.split("\t") for ln in run_sql(sql).splitlines() if ln != ""]


def one(sql, default=None):
    r = rows(sql)
    return r[0][0] if r and r[0] and r[0][0] != "NULL" else default


def uid_of(username):
    return int(one("SELECT id FROM oa_admin WHERE username='%s';" % username))


def counts():
    parts = ["SELECT '%s' , COUNT(*) FROM %s" % (n, MODULES[n][0]) for n in MODULES]
    return dict((r[0], int(r[1])) for r in rows(" UNION ALL ".join(parts) + ";"))


# ------------------------------------------------------------------ HTTP
def post(sess, path, data):
    r = sess.post(BASE_URL + path, data=data, timeout=30, allow_redirects=False)
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:200], "_status": r.status_code}


def create_doc(sess, module, reason, offset_days=1, hours=8):
    """经被测系统自身的建单接口建单，返回 (新单 id, 响应)"""
    now = int(time.time())
    start = now + 86400 * offset_days
    data = {
        "duration": hours,
        "start_date": time.strftime("%Y-%m-%d %H:%M", time.localtime(start)),
        "end_date": time.strftime("%Y-%m-%d %H:%M", time.localtime(start + 3600)),
        "reason": reason,
        "file_ids": "",
    }
    data.update(FORM_EXTRA[module])
    res = post(sess, "/home/%s/add" % module, data)
    rid = (res.get("data") or {}).get("return_id") if res.get("code") == 0 else None
    return rid, res


def do_submit(sess, module, rid, check_uids):
    _, flow_id, check_name = MODULES[module]
    return post(sess, "/api/check/submit_check", {
        "check_name": check_name, "action_id": rid, "flow_id": flow_id,
        "check_uids": check_uids,
    })


def do_check(sess, module, rid, check, content, next_uids=None):
    """审批动作。

    ⚠ 必须把 check_node / check_uids / check_files / content **四个字段全部带上**，
      即使值为空。原因（本脚本第一版踩过，实测 500）：
        Check.php:385 读 $param['check_node']、Check.php:467 读 $param['content']，
        **两处都没有 isset() 兜底**；缺任一个 → PHP 8 未定义数组键警告 →
        ThinkPHP 的错误处理把警告升级为 ErrorException → 无人捕获 → HTTP 500。
      对照实测（同一张单、只改一个字段的有无）：
        缺 check_node            -> 500
        缺 content               -> 500
        缺 check_uids/check_files -> 200 正常（check_files 在 Check.php:363 有兜底）
      这一条本身也记为一个候选缺陷（缺参未校验直接 500），见存档；但 UI 正常路径
      一定会带上这两个字段，故不属于"界面可触达"的缺陷。
    """
    _, flow_id, check_name = MODULES[module]
    data = {"check_name": check_name, "action_id": rid, "check_flow_id": flow_id,
            "check": check, "content": content, "check_node": 1,
            "check_uids": "", "check_files": ""}
    if next_uids:
        data["check_node"] = 2
        data["check_uids"] = next_uids
    return post(sess, "/api/check/flow_check", data)


def state_of(module, rid):
    table = MODULES[module][0]
    return int(one("SELECT check_status FROM %s WHERE id=%d;" % (table, rid), 0))


# ------------------------------------------------------------------ 计划
def build_plan():
    """返回 [(模块, 标签, 申请人账号, 目标状态, 说明)]，顺序即执行顺序"""
    plan = []
    # 请假：21 条待提交审批（供 SP-034 分页取第二页；page_size=20）
    for i in range(1, 22):
        plan.append(("leaves", "P%02d" % i, APPLICANT, "0", "分页用：待提交审批"))
    # 请假：四状态各 1 条（供 SP-033 筛选）
    plan.append(("leaves", "待审批", APPLICANT, "1", "SP-006/007/008 前置"))
    plan.append(("leaves", "已通过", APPLICANT, "2", "SP-009 前置：已通过不可撤回"))
    plan.append(("leaves", "已驳回", APPLICANT, "3", "SP-012 前置：驳回后重新提交"))
    plan.append(("leaves", "已撤回", APPLICANT, "4", "SP-033 状态筛选"))
    plan.append(("leaves", "两级审批中", APPLICANT, "1@2", "SP-020/021/031/032 前置：已过第一级"))
    plan.append(("leaves", "他人单据", OTHER, "1", "QX-039~041/046、SP-035 前置"))
    # 其余三模块：本人待审批 + 他人单据各 1 条
    for m in ("overtimes", "trips", "outs"):
        plan.append((m, "本人待审批", APPLICANT, "1", "SP-015/017/019、QX-042~044 前置"))
        plan.append((m, "他人单据", OTHER, "1", "SP-035、QX 水平越权对照"))
    return plan


def make_reason(module, label):
    return "%s%s-%s" % (MARK, module, label)


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正落库（默认 dry-run）")
    args = ap.parse_args()

    print(SEP)
    print("勾股OA 6.1.2 —— 单据测试数据构造%s" % ("（APPLY：真实落库）" if args.apply else "（DRY-RUN：不落库）"))
    print(SEP)

    before = counts()
    print("构造前四表行数：%s" % before)

    leftover = int(one("SELECT COUNT(*) FROM oa_leaves WHERE reason LIKE '%s%%';" % MARK, "0") or 0)
    for m in ("overtimes", "trips", "outs"):
        leftover += int(one("SELECT COUNT(*) FROM %s WHERE reason LIKE '%s%%';" % (MODULES[m][0], MARK), "0") or 0)
    if leftover:
        print("!! 已存在 %d 条带标记的旧数据 —— 请先跑 cleanup_document_data.py --apply 回收，本脚本不重复构造。" % leftover)
        sys.exit(1)
    if sum(before.values()) != 0:
        print("!! 四表非空（%s）—— 本脚本要求从空基线开始，避免与手工数据混淆。终止。" % before)
        sys.exit(1)
    print("基线检查通过：四表均为 0 行，无旧标记数据")

    plan = build_plan()
    print("\n构造计划（共 %d 条）：" % len(plan))
    for module, label, who, target, why in plan:
        print("  %-10s %-12s 申请人=%-12s 目标状态=%-4s %s" % (module, label, who, target, why))

    if not args.apply:
        print("\n[DRY-RUN] 未落库。加 --apply 执行。")
        return

    uids = {APPLICANT: uid_of(APPLICANT), OTHER: uid_of(OTHER), ADMIN: uid_of(ADMIN)}
    print("\n账号 uid：%s" % uids)
    sess = {APPLICANT: login(APPLICANT, oa_config.USER_PWD),
            OTHER: login(OTHER, oa_config.USER_PWD),
            ADMIN: login(ADMIN, oa_config.ADMIN_PWD)}

    made = []          # [(module, label, rid)]
    problems = []

    # ---- 1) 建单 ----
    print("\n" + "-" * 78)
    print("1) 建单（POST /home/{module}/add）")
    print("-" * 78)
    for module, label, who, target, _why in plan:
        rid, res = create_doc(sess[who], module, make_reason(module, label))
        if not rid:
            problems.append("建单失败 %s/%s：%s" % (module, label, str(res)[:120]))
            print("  [FAIL] %-10s %-12s %s" % (module, label, str(res)[:100]))
            continue
        made.append([module, label, rid, 0])
        print("  [ ok ] %-10s %-12s id=%-6d 申请人=%s" % (module, label, rid, who))

    # ---- 2) 状态推进 ----
    print("\n" + "-" * 78)
    print("2) 状态推进（submit_check -> flow_check）")
    print("-" * 78)
    by_index = {(m, l): r for m, l, r, _s in made}

    def advance(module, label, target, who=APPLICANT):
        """把已建单据推进到目标状态；target 形如 '1' / '2' / '3' / '4' / '1@2'"""
        rid = by_index.get((module, label))
        if not rid:
            return None
        approver = OTHER if who == APPLICANT else ADMIN
        res = do_submit(sess[who], module, rid, str(uids[approver]))
        if res.get("code") != 0:
            problems.append("提交审批失败 %s/%s：%s" % (module, label, str(res)[:120]))
            return None
        if target == "1":
            return rid
        if target == "1@2":
            # 第一级（OTHER）通过并指定下一级 ADMIN
            r1 = do_check(sess[approver], module, rid, 1, "两级审批-第一级通过", str(uids[ADMIN]))
            if r1.get("code") != 0:
                problems.append("第一级通过失败 %s/%s：%s" % (module, label, str(r1)[:120]))
                return None
            return rid
        if target == "2":
            r1 = do_check(sess[approver], module, rid, 1, "审批通过")
        elif target == "3":
            r1 = do_check(sess[approver], module, rid, 2, "用例测试驳回")
        elif target == "4":
            r1 = do_check(sess[who], module, rid, 3, "用例测试撤回")
        else:
            problems.append("未知目标状态 %s" % target)
            return None
        if r1.get("code") != 0:
            problems.append("状态推进失败 %s/%s→%s：%s" % (module, label, target, str(r1)[:120]))
            return None
        return rid

    for module, label, who, target, _why in plan:
        if target == "0":
            continue
        rid = advance(module, label, target, who)
        if rid:
            st = state_of(module, rid)
            print("  [ ok ] %-10s %-12s id=%-6d 目标=%-4s 实测 check_status=%d" % (module, label, rid, target, st))
        else:
            print("  [FAIL] %-10s %-12s 目标=%s（详见问题清单）" % (module, label, target))

    # ---- 3) 回读校验（真实比较，不打印结论）----
    print("\n" + "-" * 78)
    print("3) 回读校验")
    print("-" * 78)
    after = counts()
    print("构造后四表行数：%s" % after)
    expected = {}
    for module, label, who, target, _why in plan:
        expected[module] = expected.get(module, 0) + 1
    errs = []
    for module, n in expected.items():
        got = after[module]
        if got != n:
            errs.append("模块 %s 期望 %d 行，实测 %d 行" % (module, n, got))
    print("  行数校验：%s" % ("通过" if not errs else "不通过 -> %s" % errs))

    # 每条单据的实际状态是否等于目标
    st_errs = []
    for module, label, who, target, _why in plan:
        rid = by_index.get((module, label))
        if not rid:
            st_errs.append("%s/%s 未建单" % (module, label))
            continue
        st = state_of(module, rid)
        want = {"0": 0, "1": 1, "1@2": 1, "2": 2, "3": 3, "4": 4}[target]
        if st != want:
            st_errs.append("%s/%s(id=%d) 期望 check_status=%d，实测 %d" % (module, label, rid, want, st))
    print("  状态校验：%s" % ("通过" if not st_errs else "不通过"))
    for e in st_errs:
        print("     - %s" % e)

    # 归属校验：本人单据/他人单据的 admin_id 是否与计划一致
    own_errs = []
    for module, label, who, _t, _why in plan:
        rid = by_index.get((module, label))
        if not rid:
            continue
        aid = int(one("SELECT admin_id FROM %s WHERE id=%d;" % (MODULES[module][0], rid), 0))
        if aid != uids[who]:
            own_errs.append("%s/%s(id=%d) 期望 admin_id=%d(%s)，实测 %d"
                            % (module, label, rid, uids[who], who, aid))
    print("  归属校验：%s" % ("通过" if not own_errs else "不通过"))
    for e in own_errs:
        print("     - %s" % e)

    # flow 联动数据（两级审批应有 2 条 step）
    fstep = int(one("SELECT COUNT(*) FROM oa_flow_step WHERE delete_time=0;", "0") or 0)
    frec = int(one("SELECT COUNT(*) FROM oa_flow_record WHERE delete_time=0;", "0") or 0)
    print("  联动数据：oa_flow_step=%d 行，oa_flow_record=%d 行" % (fstep, frec))
    two = by_index.get(("leaves", "两级审批中"))
    if two:
        n = int(one("SELECT COUNT(*) FROM oa_flow_step WHERE action_id=%d AND flow_id=1 AND delete_time=0;" % two, "0") or 0)
        if n != 2:
            errs.append("两级审批单据 id=%d 期望 2 条 flow_step，实测 %d" % (two, n))
        print("  两级审批(id=%d) 的 flow_step 条数 = %d（期望 2）" % (two, n))

    # ---- 4) 汇总 ----
    print("\n" + SEP)
    if problems:
        print("过程中的问题（%d 条）：" % len(problems))
        for p in problems:
            print("  - %s" % p)
    if errs or st_errs or own_errs:
        print("!! 校验未全部通过，数据可能不完整，请勿直接开始用例执行。")
    else:
        print("全部校验通过：%d 条单据已构造完成，状态与归属均与计划一致。" % len(made))
    print(SEP)
    print("回收：python 03_接口测试\\cleanup_document_data.py --apply")


if __name__ == "__main__":
    main()
