# -*- coding: utf-8 -*-
r"""
勾股OA 6.1.2 —— 回收 setup_contract_data.py 构造的合同模块测试数据

【本脚本做什么】
  清掉 setup_contract_data.py --apply 建出来的**可回收**测试数据：

    · oa_contract            12 行  —— code 前缀 HT-TEST-
    · oa_purchase             8 行  —— code 前缀 CG-TEST-
    · oa_contract_product / _service、oa_purchase_product / _service
                             N 行   —— 合同/采购的明细行（按 contract_id / purchase_id 外键）
    · oa_flow_record / oa_flow_step
                             N 行   —— 上述单据的审批记录/步骤
    · oa_msg                 N 行   —— 上述单据的站内信

  这是 setup_contract_data.py 第 49 行声明的回收脚本（存档 §6 第 7 项，此前一直缺失）。

【主数据（【测试】客户/产品/服务/供应商）**默认不删** —— 这是刻意的】
  存档把「合同主数据」与 testmin/testemp、角色组 4、HT-CASE-* 一并列为**勿清夹具**：
  保留的 HT-CASE-* 合同仍以 customer_id / product_id / service_id / supplier_id 指向它们，
  删掉主数据会把那些保留合同变成悬挂引用（详情页会取不到客户名）。
  确需一并清掉时用 `--with-master-data`（届时请确认 HT-CASE-* 也已处置）。

【为什么删除范围能确定是"我造的"——三个鉴别键，都是实测得来】
  ① **code 前缀** HT-TEST- / CG-TEST- 是构造脚本专有命名，出厂/他人数据不会有。
  ② **oa_flow_record.check_table**：该列取值 = 来源单据表名去 `oa_` 前缀
     （实测：leaves / trips / outs / overtimes / contract / purchase）。
     **不能用 action_id 单条件定位** —— action_id 是单据 id，**跨表不唯一**：
     实测 oa_leaves 的 id 区间是 1046–1227，与合同 id 1131–1336 **交叠**，
     ids 1131/1133 在两张表里都真实存在。只按 action_id 删会删掉请假单的审批记录。
  ③ **oa_msg.template**：该表既无来源表列也无 flow 列，只有 template。
     实测映射：20=请假 21=出差 22=外出 23=加班 **24=销售合同 25=采购合同**。
     实测反例：msg id=1537 的 action_id=1022/template=23（加班申请），与合同 1022
     （template=24）同 id —— 只按 action_id 删会删掉别人单据的站内信。
  `oa_flow_step` **没有** check_table 列（实测），只能退化为 (action_id, flow_id) 组对，
  因此**单独打一份"同 id 但对不上"的报表**供人工判读（本实例 id 区间重叠，非 0 属正常）。

【既有教训（本脚本据此写）】
  · mysql CLI 遇错即停且不回滚 ⇒ 分步执行，**每步删完立即回读**，与预期差对不上就终止
  · 绝不用 LIKE 批量删 —— LIKE 只在 SELECT 里做**只读识别**，删除一律走显式行 id 列表
  · 备份**只增不删**：每次运行写一个新的 cleanup_backup_<时间戳>.sql，不覆盖旧备份

【不做什么】
  · 不碰 **HT-CASE-** 前缀的保留夹具（合同模块 30 条用例的前置，存档明确「勿清」）
  · 不碰 oa_admin(104/108/109)、oa_admin_group(1~4)、既有 19 条 oa_flow
  · 不删 oa_admin_log —— 登录/操作日志是审计留痕，不属于"测试数据"
  · 不调任何接口（纯 SQL），不发站内信、不碰第三方
  · 不删"上一轮执行器"留下的 QZ-* / P*-A / 【用例数据】P-* 合同 —— 不在本脚本标记范围内，
    但会在运行结束时**打印出来提醒**（宁可让人看见、由人决定，也不静默忽略）

【用法】
  python 03_接口测试\cleanup_contract_data.py            # 默认 dry-run
  python 03_接口测试\cleanup_contract_data.py --apply    # 真正删除（先备份）
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setup_contract_data as SCD  # noqa: E402  复用其已评审的 wsl+base64 SQL 管道与登录
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 78
BACKUP_DIR = oa_config.BACKUP_DIR
WSL_DISTRO = oa_config.WSL_DISTRO

MARK = SCD.MARK          # HT-TEST  销售合同
MARK2 = SCD.MARK2        # CG-TEST  采购合同
MASTER_MARK = "【测试】"   # 主数据名称前缀

# 单据表 -> (code 前缀, oa_flow_record.check_table 取值, oa_msg.template 取值, 明细表[(表,外键列)])
DOCS = [
    ("oa_contract", MARK,  "contract", 24,
     [("oa_contract_product", "contract_id"), ("oa_contract_service", "contract_id")]),
    ("oa_purchase", MARK2, "purchase", 25,
     [("oa_purchase_product", "purchase_id"), ("oa_purchase_service", "purchase_id")]),
]

# 主数据：(表, 名称列, 显示名)
MASTERS = [("oa_customer", "name", "客户"), ("oa_product", "title", "产品"),
           ("oa_services", "title", "服务"), ("oa_supplier", "title", "供应商")]

# 边界对象：本脚本不应对其发任何 DML，收尾逐项回读确认仍在
BOUNDARY_USERS = (104, 108, 109)
BOUNDARY_GROUPS = (1, 2, 3, 4)
BASELINE_FLOWS = 19
# 保留夹具前缀（存档明确「勿清」）—— 收尾回读确认它们**未被波及**
KEEP_PREFIX = "HT-CASE-"

CHILD_TABLES = ("oa_flow_record", "oa_flow_step", "oa_msg")


def rows(sql):
    return SCD.rows(sql)


def one(sql, dflt=""):
    r = rows(sql)
    return r[0][0] if r else dflt


def one_in_wsl(cmd):
    """在 WSL 里跑一条 shell 命令，合并 stdout/stderr 返回（备份用）"""
    import subprocess
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", cmd],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=300)
    return ((out.stdout or "") + (out.stderr or "")).strip()


def table_exists(t):
    return one("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='oa60' "
               "AND table_name='%s';" % t, "0") == "1"


def collect(with_masters=False):
    """只读盘点：返回 (plan, detail, problems)。
    plan = {表: [行id]}；detail = [(标签, 行, 不合标记数)]；problems = [拒绝继续的理由]

    with_masters=False（默认）时不纳入主数据 —— 存档列为「勿清」夹具，见模块 docstring。
    """
    plan, detail, problems = {}, [], []
    doc_ids, doc_pairs = {}, []          # 表 -> [id]；[(表, id, check_flow_id)]

    # ---- 单据本体：按 code 前缀**只读识别**，并校验前缀确实存在 ----
    for t, mark, ctable, tpl, line_tables in DOCS:
        if not table_exists(t):
            problems.append("表不存在：%s" % t)
            continue
        rs = rows("SELECT id, code, check_flow_id, delete_time FROM %s "
                  "WHERE code LIKE '%s%%' ORDER BY id;" % (t, mark))
        bad = [x for x in rs if not (x[1] or "").startswith(mark)]
        if not rs:
            detail.append(("%s（前缀 %s）" % (t, mark), [], 0))
            continue
        plan[t] = [int(x[0]) for x in rs]
        doc_ids[t] = plan[t]
        for x in rs:
            doc_pairs.append((t, int(x[0]), int(x[2] or 0)))
        detail.append(("%s（前缀 %s）" % (t, mark), rs, len(bad)))
        if bad:
            problems.append("%s 命中 %d 行不含前缀 %s" % (t, len(bad), mark))

    if not doc_ids:
        return {}, detail, problems + ["没有任何单据命中前缀，终止（避免空跑误判）"]

    all_ids = sorted({i for ids in doc_ids.values() for i in ids})
    ids_csv = ",".join(map(str, all_ids))

    # ---- 明细行：按外键列精确命中 ----
    for t, mark, ctable, tpl, line_tables in DOCS:
        if t not in doc_ids:
            continue
        for lt, col in line_tables:
            if not table_exists(lt):
                continue
            rs = rows("SELECT id, %s FROM %s WHERE %s IN (%s) ORDER BY id;"
                      % (col, lt, col, ",".join(map(str, doc_ids[t]))))
            if rs:
                plan[lt] = [int(x[0]) for x in rs]
                detail.append(("%s（按 %s）" % (lt, col), rs, 0))

    # ---- 审批记录：check_table + action_id（check_table 是来源单据表名，跨表不歧义）----
    rec_segs = []
    for t, mark, ctable, tpl, _ in DOCS:
        if t in doc_ids:
            rec_segs.append("(`check_table`='%s' AND action_id IN (%s))"
                            % (ctable, ",".join(map(str, doc_ids[t]))))
    if rec_segs:
        where = " OR ".join(rec_segs)
        rs = rows("SELECT id, action_id, check_table, flow_id, check_uid, check_status "
                  "FROM oa_flow_record WHERE %s ORDER BY id;" % where)
        plan["oa_flow_record"] = [int(x[0]) for x in rs]
        detail.append(("oa_flow_record（check_table+action_id）", rs, 0))
        # 「未误伤」报表：同 id 但属其他单据类型的行 —— 本脚本不删，如实列出
        other = rows("SELECT check_table, COUNT(*) FROM oa_flow_record WHERE action_id IN (%s) "
                     "AND NOT (%s) GROUP BY check_table;" % (ids_csv, where))
        if other:
            detail.append(("   ↑ 同 id 但属其他单据类型、**不删**"
                           "（check_table 限定起了作用）",
                           [[r[0], r[1]] for r in other], 0))

    # ---- 审批步骤：该表无 check_table，只能 (action_id, flow_id) 组对 ----
    pair_segs = ["(action_id=%d AND flow_id=%d)" % (i, f) for _, i, f in doc_pairs if f]
    if pair_segs:
        where = " OR ".join(pair_segs)
        rs = rows("SELECT id, flow_name, action_id, flow_id, sort FROM oa_flow_step "
                  "WHERE %s ORDER BY id;" % where)
        plan["oa_flow_step"] = [int(x[0]) for x in rs]
        detail.append(("oa_flow_step（action_id+flow_id 组对）", rs, 0))
        # 同 id 对不上报表：本实例单据 id 区间重叠（leaves 1046–1227 vs 合同 1131–1336），
        # 故**非 0 属正常** —— 那些是同 id 的其他类型单据的步骤行，不该删。按 flow_id 汇总。
        stray = rows("SELECT flow_id, COUNT(*) FROM oa_flow_step WHERE action_id IN (%s) "
                     "AND NOT (%s) GROUP BY flow_id ORDER BY flow_id;" % (ids_csv, where))
        detail.append(("   ↑ 同 id 但 flow_id 对不上、**不删**（人工判读）",
                       [[r[0], r[1]] for r in stray] or [["（无）", "0"]], 0))
        # 真正要警惕的是：某单据自己的 check_flow_id 有值、却一行 step 都没配上
        miss = [(i, f) for _, i, f in doc_pairs if f
                and not rows("SELECT id FROM oa_flow_step WHERE action_id=%d AND flow_id=%d "
                             "LIMIT 1;" % (i, f))]
        if miss:
            detail.append(("   !! 已提交(有 check_flow_id)却无 step 行的单据", miss, len(miss)))

    # ---- 站内信：template + action_id ----
    msg_segs = []
    for t, mark, ctable, tpl, _ in DOCS:
        if t in doc_ids:
            msg_segs.append("(`template`=%d AND action_id IN (%s))"
                            % (tpl, ",".join(map(str, doc_ids[t]))))
    if msg_segs:
        where = " OR ".join(msg_segs)
        rs = rows("SELECT id, action_id, template, LEFT(title,26) FROM oa_msg "
                  "WHERE %s ORDER BY id;" % where)
        plan["oa_msg"] = [int(x[0]) for x in rs]
        detail.append(("oa_msg（template+action_id）", rs, 0))

    # ---- 主数据：名称前缀【测试】（默认不纳入，见 docstring「勿清」） ----
    if with_masters:
        for t, col, label in MASTERS:
            if not table_exists(t):
                continue
            rs = rows("SELECT id, %s FROM %s WHERE %s LIKE '%s%%' ORDER BY id;"
                      % (col, t, col, MASTER_MARK))
            bad = [x for x in rs if MASTER_MARK not in (x[1] or "")]
            if rs:
                plan[t] = [int(x[0]) for x in rs]
                detail.append(("%s【主数据】" % label, rs, len(bad)))
            if bad:
                problems.append("%s 命中 %d 行不含【测试】前缀" % (t, len(bad)))
    else:
        detail.append(("主数据【测试】*（默认**不删**，存档「勿清」夹具；"
                       "要删加 --with-master-data）", [], 0))

    return plan, detail, problems


def backup(plan):
    """删除前备份本次涉及的**全部**表；只增不删（同名已存在则不覆盖）"""
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = "%s/cleanup_backup_%s.sql" % (BACKUP_DIR, ts)
    if one_in_wsl("test -f %s && echo yes || echo no" % path).strip() == "yes":
        print("   备份已存在，不覆盖（只增不删）：%s" % path)
        return path, 0, one_in_wsl("wc -c < %s" % path)
    tables = " ".join(sorted(plan))
    cmd = ("%s %s > %s 2>/tmp/oa_cleanup_dump.err; echo rc=$?; wc -c < %s"
           % (oa_config.MYSQLDUMP, tables, path, path))
    out = one_in_wsl(cmd)
    rc = 0
    for ln in (out or "").splitlines():
        if ln.startswith("rc="):
            rc = int(ln[3:])
    size = (out or "").strip().splitlines()[-1].strip() if out else "0"
    return path, rc, size


def del_by_ids(table, ids):
    """按显式 id 列表删除，返回受影响行数（分块 200，单条 SQL，遇错即停）"""
    if not ids:
        return 0
    before = int(one("SELECT COUNT(*) FROM %s;" % table, "0"))
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        SCD.run_sql("DELETE FROM %s WHERE id IN (%s);" % (table, ",".join(map(str, chunk))))
    return before - int(one("SELECT COUNT(*) FROM %s;" % table, "0"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只预览）")
    ap.add_argument("--with-master-data", action="store_true",
                    help="连同【测试】主数据一起删（默认保留 —— 存档列为「勿清」夹具）")
    args = ap.parse_args()

    print(SEP)
    print("勾股OA 合同模块测试数据回收%s" % ("" if args.apply else "（dry-run，不删除）"))
    print(SEP)

    plan, detail, problems = collect(with_masters=args.with_master_data)
    print("识别依据：code 前缀 %s / %s，主数据名称前缀 %s" % (MARK, MARK2, MASTER_MARK))
    print("")
    for label, rs, bad in detail:
        print("%s —— %d 行（不合标记 %d）" % (label, len(rs), bad))
        for x in rs[:12]:
            print("      %s" % " | ".join(str(v) for v in x))
        if len(rs) > 12:
            print("      …… 另有 %d 行（略）" % (len(rs) - 12))
    if problems:
        print("")
        for p in problems:
            print("!! %s" % p)
        sys.exit(2)

    # 删除顺序：明细 → 审批记录 → 步骤 → 站内信 → 单据本体 → 主数据
    line_tables = [lt for _, _, _, _, lts in DOCS for lt, _ in lts]
    order = ([t for t in line_tables if t in plan] + [t for t in CHILD_TABLES if t in plan]
             + [t for t, _, _, _, _ in DOCS if t in plan]
             + [t for t, _, _ in MASTERS if t in plan])
    total = sum(len(plan[t]) for t in order)
    print("")
    print("将删除 %d 行：%s" % (total, "，".join("%s %d" % (t, len(plan[t])) for t in order if plan.get(t))))
    if not args.apply:
        print("")
        print("--dry-run：未删除任何行。确认无误后加 --apply 执行。")
        return

    path, rc, size = backup(plan)
    print("")
    print("已备份 → %s（%s 字节）" % (path, size))
    if rc != 0 or not size or size == "0":
        print("!! 备份失败（rc=%s）；为安全起见终止，不做任何删除" % rc)
        sys.exit(2)

    print("")
    print("分步删除（每步立即回读）：")
    for t in order:
        ids = plan.get(t) or []
        if not ids:
            continue
        n = del_by_ids(t, ids)
        left = int(one("SELECT COUNT(*) FROM %s WHERE id IN (%s);"
                       % (t, ",".join(map(str, ids))), "0"))
        print("   %-22s 删除 %-4d 行，残留 %d 行" % (t, n, left))
        if left != 0:
            print("!! %s 仍有 %d 行未删净，终止（后续表未动）" % (t, left))
            sys.exit(2)

    # ---- 回读核对 ----
    print("")
    print(SEP)
    print("回读核对")
    print(SEP)
    ok = True

    for t, mark, ctable, tpl, _ in DOCS:
        c = one("SELECT COUNT(*) FROM %s WHERE code LIKE '%s%%';" % (t, mark), "0")
        tag = "OK " if c == "0" else "!! "
        ok &= c == "0"
        print("   %s%s 中 %s%% 残留 = %s 行" % (tag, t, mark, c))

    if args.with_master_data:
        for t, col, label in MASTERS:
            c = one("SELECT COUNT(*) FROM %s WHERE %s LIKE '%s%%';" % (t, col, MASTER_MARK), "0")
            tag = "OK " if c == "0" else "!! "
            ok &= c == "0"
            print("   %s%s【%s】残留 = %s 行" % (tag, t, label, c))
    else:
        n_master = sum(int(one("SELECT COUNT(*) FROM %s WHERE %s LIKE '%s%%';" % (t, col, MASTER_MARK), "0"))
                       for t, col, _ in MASTERS if table_exists(t))
        tag = "OK " if n_master > 0 else "!! "
        ok &= n_master > 0
        print("   %s主数据【测试】* 保留 = %d 行（存档「勿清」夹具，须 >0）" % (tag, n_master))

    for t, mark, ctable, tpl, _ in DOCS:
        c = one("SELECT COUNT(*) FROM oa_flow_record WHERE check_table='%s' AND action_id NOT IN "
                "(SELECT id FROM %s);" % (ctable, t), "0")
        tag = "OK " if c == "0" else "!! "
        ok &= c == "0"
        print("   %soa_flow_record 中 check_table=%s 的**孤儿行** = %s（应为 0）" % (tag, ctable, c))

    # ---- 边界对象：本脚本只对上述表发 DML，这里逐项回读确认仍在 ----
    print("   边界对象（本脚本未对其发任何 DML，回读确认仍在）：")
    for uid in BOUNDARY_USERS:
        r = rows("SELECT id, username FROM oa_admin WHERE id=%d;" % uid)
        tag = "OK " if r else "!! "
        ok &= bool(r)
        print("      %s账号 %s" % (tag, r[0] if r else "缺失"))
    for gid in BOUNDARY_GROUPS:
        r = rows("SELECT id, title FROM oa_admin_group WHERE id=%d;" % gid)
        tag = "OK " if r else "!! "
        ok &= bool(r)
        print("      %s角色组 %s" % (tag, r[0] if r else "缺失"))

    n_flow = int(one("SELECT COUNT(*) FROM oa_flow;", "0"))
    tag = "OK " if n_flow == BASELINE_FLOWS else "!! "
    ok &= n_flow == BASELINE_FLOWS
    print("      %soa_flow = %d（基线 %d）" % (tag, n_flow, BASELINE_FLOWS))

    # 保留夹具必须仍在（存档「勿清」）
    keep = rows("SELECT COUNT(*) FROM oa_contract WHERE code LIKE '%s%%';" % KEEP_PREFIX)
    n_keep = int(keep[0][0]) if keep else 0
    tag = "OK " if n_keep > 0 else "!! "
    ok &= n_keep > 0
    print("      %s保留夹具 %s* 仍在库 = %d 行（存档「勿清」，须 >0）" % (tag, KEEP_PREFIX, n_keep))

    # ---- 提醒：不在本脚本标记范围内的其他测试前缀 ----
    print("")
    print("提醒：以下合同不属于本脚本的标记范围，**未被删除**，需要时另行处置：")
    others = rows("SELECT SUBSTRING_INDEX(code,'-',1) AS pfx, COUNT(*) FROM oa_contract "
                  "WHERE code NOT LIKE '%s%%' AND code NOT LIKE '%s%%' GROUP BY pfx "
                  "ORDER BY COUNT(*) DESC;" % (MARK, KEEP_PREFIX))
    for r in others:
        print("      %-14s %s 行" % (r[0], r[1]))

    print("")
    print("结果：%s" % ("回收完成，回读全部通过" if ok else "**有核对项未通过，请人工复核**"))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
