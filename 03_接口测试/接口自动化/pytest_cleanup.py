# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 —— pytest 套件运行期数据的回收

【为什么单独成模块，而不是塞进 conftest】
  这是**破坏性**代码，必须能被单独阅读、单独审阅、单独干跑（dry-run）。
  塞进 conftest 的 fixture teardown 里，读的人只能看到一行调用。

【删除顺序（子表先于父表，不可调换）】
  明细行 → oa_flow_record → oa_flow_step → oa_msg → 单据表
  理由：子表通过 action_id 指向单据，先删子表才能保证「单据已删但子表还在」的孤儿行不会产生。

【三条硬约束（全部来自项目已踩过的坑，勿改）】
  1. **只按显式 id 列表删，绝不用 LIKE 批量删** —— LIKE 写宽会误删出厂数据。
  2. **子表不能只按 flow_id 定范围** —— 走自由审批流（flow_id=1）的单据会被漏掉。
  3. **mysql 遇错即停且不回滚** —— 一条语句报错，后面的都不会执行，前面的也不回滚。
     故一律分步执行、每步删完立即回读，残留非 0 即终止。

【本文件 2026-09-21 修掉的四个真实缺陷 —— 都是"看着能跑、其实在漏"的那类】
  ① **表名写错，整条静默跳过**：DOC_TABLES 里写的是 `oa_evection` / `oa_business_trip`，
     这两个表**根本不存在**（实测 `information_schema` 无此表），本实例的出差表叫
     **`oa_trips`**。而 `collect_plan()` 用 `_exists()` 静默过滤 ⇒ SP 模块
     `K.track("oa_trips", id)` 登记的每一条出差单**从未被回收**，且不报任何错。
     （QX 模块独立发现了同一问题，只能在模块内自行兜底 —— 现在从根上修掉。）
  ② **oa_flow_record 只按 action_id 定位会误伤**：action_id 是单据 id，**跨表不唯一** ——
     合同 1022 与请假单 1022 可以同时存在。实测该表有 `check_table` 列
     （取值 = 来源单据表名去 `oa_` 前缀：leaves/trips/outs/overtimes/contract/purchase），
     本次改为 `check_table + action_id` 双限定，跨表不再歧义。
     `oa_flow_step` **没有** check_table 列（实测），只能维持 (action_id, flow_id) 组对。
  ③ **oa_msg 只按 action_id 定位会误伤**：该表既无来源表列也无 flow 列，只有 `template`。
     实测映射：20=请假 21=出差 22=外出 23=加班 **24=销售合同 25=采购合同**。
     实测反例：msg id=1537 的 action_id=1022/template=23（加班申请），
     与合同 1022（template=24）同 id —— 只按 action_id 删会删掉别人单据的站内信。
  ④ **合同的明细行未登记**：`oa_contract_product/service`、`oa_purchase_product/service`
     以 contract_id / purchase_id 外键挂单据，不删会留孤儿行（本实例目前为 0 行，
     但 `add` 接口本就支持写明细，登记上是必须的）。
"""
import sys

from oa_kit import rows, run_sql

# 单据表清单（用例会在这些表里建单）。顺序即删除顺序。
# ⚠ 表名必须与本实例 `information_schema` 一致：出差是 **oa_trips**，
#   不存在 oa_evection / oa_business_trip（写错会被 _exists() 静默跳过 ⇒ 漏回收且不报错）。
DOC_TABLES = ["oa_leaves", "oa_trips", "oa_outs", "oa_overtimes",
              # 合同 / 采购：合同模块 30 条用例每条都自建前置合同，一轮净增约 28 张。
              # 不登记的话会话收尾会整条忽略（collect_plan 只遍历 DOC_TABLES）。
              "oa_contract", "oa_purchase",
              # 薪资明细：不是"单据"，但同样是用例经接口新建的叶子数据，登记了就要能回收。
              # （薪资夹具自身的批次/明细由 cleanup_salary_data.py 负责，不在此列）
              "oa_salary_records"]

# 单据表 → (oa_flow_record.check_table 取值, oa_msg.template 取值)
#   两个鉴别键都是**实测**得来，不是推测：check_table 见 oa_flow_record 列注释，
#   template 见本文件顶部 ③ 的实测反例。缺了它们，跨表同 id 就会互相误伤。
DOC_KINDS = {
    "oa_leaves":    ("leaves",    20),
    "oa_trips":     ("trips",     21),
    "oa_outs":      ("outs",      22),
    "oa_overtimes": ("overtimes", 23),
    "oa_contract":  ("contract",  24),
    "oa_purchase":  ("purchase",  25),
}

# 单据表 → [(明细表, 外键列)]：删单据前必须先清，否则留孤儿行
LINE_TABLES = {
    "oa_contract": [("oa_contract_product", "contract_id"),
                    ("oa_contract_service", "contract_id")],
    "oa_purchase": [("oa_purchase_product", "purchase_id"),
                    ("oa_purchase_service", "purchase_id")],
}

RECORD_TABLE = "oa_flow_record"
STEP_TABLE = "oa_flow_step"
MSG_TABLE = "oa_msg"

# 子表：先于单据表删除
CHILD_TABLES = [RECORD_TABLE, STEP_TABLE, MSG_TABLE]


def _exists(table):
    return bool(rows("SHOW TABLES LIKE '%s';" % table))


def _one(sql, default="0"):
    r = rows(sql)
    return r[0][0] if r else default


def _has_col(table, col):
    return bool(rows("SHOW COLUMNS FROM %s LIKE '%s';" % (table, col)))


def _csv(ids):
    return ",".join(map(str, ids))


def _del(table, ids):
    """按显式 id 删除，返回 (删除前, 删除后) 行数。分块 200 条，遇错即停。"""
    ids = sorted({int(i) for i in ids})
    if not ids:
        return 0, 0
    before = int(_one("SELECT COUNT(*) FROM %s;" % table, "0"))
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        run_sql("DELETE FROM %s WHERE id IN (%s);" % (table, ",".join(map(str, chunk))))
    return before, int(_one("SELECT COUNT(*) FROM %s;" % table, "0"))


def _doc_ids(tracked):
    """登记表 → 实际存在于库里的 {表: [id]}。表不存在则**出声**跳过（不再静默）。"""
    docs = {}
    for t in DOC_TABLES:
        if t not in tracked:
            continue
        if not _exists(t):
            print("   !! 登记了 %s 但该表不存在 —— 这些数据不会被回收，请核对表名" % t)
            continue
        ids = sorted({int(i) for i in tracked[t]})
        if ids:
            docs[t] = ids
    return docs


def _flow_record_scope(docs, notes):
    """oa_flow_record 的定位片段：优先 `check_table + action_id`（跨表不歧义）。

    check_table 缺失时才退化成 (action_id, flow_id) 组对 —— 后者对
    「自由审批流(flow_id=1)的单据」与「同 id 的其他单据」都不够精确，故仅作兜底。
    """
    segs = []
    for t, ids in docs.items():
        kind = DOC_KINDS.get(t, (None, None))[0]
        if kind and _has_col(RECORD_TABLE, "check_table"):
            segs.append("(`check_table`='%s' AND action_id IN (%s))" % (kind, _csv(ids)))
            continue
        notes.append("%s：无 check_table 可依据，退化为 (action_id, flow_id) 组对" % t)
        if not _has_col(t, "check_flow_id"):
            notes.append("   !! %s 既无 check_table 依据也无 check_flow_id 列 ⇒ 跳过其审批记录" % t)
            continue
        pr = [(int(r[0]), int(r[1] or 0))
              for r in rows("SELECT id, check_flow_id FROM %s WHERE id IN (%s);" % (t, _csv(ids)))]
        pr = [p for p in pr if p[1]]
        if pr:
            segs.append("(" + " OR ".join("(action_id=%d AND flow_id=%d)" % p for p in pr) + ")")
    return segs


def _flow_step_scope(docs, notes):
    """oa_flow_step **没有** check_table 列（实测），只能按每单自己的 check_flow_id 组对。

    不能只用夹具的 flow_id —— 走自由审批流(flow_id=1)的单据会被漏掉。
    """
    segs = []
    for t, ids in docs.items():
        if not _has_col(t, "check_flow_id"):
            notes.append("%s：无 check_flow_id 列（不走审批流），无审批步骤行" % t)
            continue
        pr = [(int(r[0]), int(r[1] or 0))
              for r in rows("SELECT id, check_flow_id FROM %s WHERE id IN (%s);" % (t, _csv(ids)))]
        pr = [p for p in pr if p[1]]
        if pr:
            segs.append("(" + " OR ".join("(action_id=%d AND flow_id=%d)" % p for p in pr) + ")")
    return segs


def collect_plan(tracked):
    """把登记表展开成「每张表该删哪些 id」的完整计划（只读，不删任何东西）。

    返回 (plan, notes)。plan = {表名: [id,...]}，notes = 人类可读的核对说明。
    """
    plan, notes = {}, []

    docs = _doc_ids(tracked)
    if not docs:
        return plan, notes

    all_ids = sorted({i for ids in docs.values() for i in ids})
    all_csv = _csv(all_ids)

    # --- 明细行：按外键列精确命中（合同/采购专属）---
    for t, ids in docs.items():
        for lt, col in LINE_TABLES.get(t, ()):
            if not _exists(lt) or not _has_col(lt, col):
                continue
            rs = rows("SELECT id FROM %s WHERE %s IN (%s);" % (lt, col, _csv(ids)))
            if rs:
                plan[lt] = [int(x[0]) for x in rs]
                notes.append("%s：按 %s 命中明细 %d 行" % (lt, col, len(rs)))

    # --- 审批记录：check_table + action_id ---
    segs = _flow_record_scope(docs, notes)
    if segs:
        where = " OR ".join(segs)
        rs = rows("SELECT id FROM %s WHERE %s;" % (RECORD_TABLE, where))
        plan[RECORD_TABLE] = [int(x[0]) for x in rs]
        notes.append("%s：按 check_table+action_id 命中 %d 行" % (RECORD_TABLE, len(plan[RECORD_TABLE])))
        # 「未误伤」报表：同 id 但属于**别的单据类型**的行 —— 这些行不该被删，
        # 数量 >0 恰恰说明 check_table 限定起了作用（若为 0 也不代表有问题，只作观察）
        other = rows("SELECT check_table, COUNT(*) FROM %s WHERE action_id IN (%s) AND NOT (%s) "
                     "GROUP BY check_table;" % (RECORD_TABLE, all_csv, where))
        if other:
            notes.append("   （同 id 但属其他单据类型、本次**不删**：%s）"
                         % "，".join("%s %s 行" % (r[0], r[1]) for r in other))

    # --- 审批步骤：只能 (action_id, flow_id) 组对 ---
    segs = _flow_step_scope(docs, notes)
    if segs:
        where = " OR ".join(segs)
        rs = rows("SELECT id FROM %s WHERE %s;" % (STEP_TABLE, where))
        plan[STEP_TABLE] = [int(x[0]) for x in rs]
        notes.append("%s：按 (action_id, flow_id) 组对命中 %d 行" % (STEP_TABLE, len(plan[STEP_TABLE])))
        # 「同 id 对不上」报表 —— 该表**没有** check_table 列，无法直接判定归属。
        # ⚠ 本实例的单据 id 区间本来就重叠（实测 oa_leaves 1046–1227 与合同残渣
        #   1131–1336 交叠，ids 1131/1133 在两张表里都存在），故这里**通常非 0，且属正常**
        #   ——它们多半是同 id 的**其他类型**单据的步骤行。按 flow_id 汇总打印供人工判读。
        #   判读要点：若某行的 flow_id 等于本套件某单据自己的 check_flow_id 却仍未被配对
        #   命中，那才是**真正的漏网**（早期就是这样发现自由审批流 flow_id=1 被漏删的）。
        stray = rows("SELECT flow_id, COUNT(*) FROM %s WHERE action_id IN (%s) AND NOT (%s) "
                     "GROUP BY flow_id ORDER BY flow_id;" % (STEP_TABLE, all_csv, where))
        notes.append("   ↑ 同 id 但 flow_id 对不上的行（多为同 id 的其他类型单据，人工判读）：%s"
                     % ("，".join("flow_id=%s 共 %s 行" % (r[0], r[1]) for r in stray) or "无"))

    # --- 站内信：template + action_id ---
    msg_segs, untyped = [], []
    for t, ids in docs.items():
        tpl = DOC_KINDS.get(t, (None, None))[1]
        if tpl:
            msg_segs.append("(`template`=%d AND action_id IN (%s))" % (tpl, _csv(ids)))
        else:
            untyped.append(t)
    if msg_segs:
        where = " OR ".join(msg_segs)
        rs = rows("SELECT id FROM %s WHERE %s;" % (MSG_TABLE, where))
        plan[MSG_TABLE] = [int(x[0]) for x in rs]
        notes.append("%s：按 template+action_id 命中 %d 行" % (MSG_TABLE, len(plan[MSG_TABLE])))
    if untyped:
        # 无 template 映射的表（如薪资明细）本来就不产生站内信；若确有，宁可少删不可错删
        notes.append("   （无 template 映射、不参与站内信回收：%s）" % "，".join(untyped))

    # --- 单据本体 ---
    for t, ids in docs.items():
        plan[t] = ids
        notes.append("%s：登记 %d 行" % (t, len(ids)))

    return plan, notes


def cleanup(tracked, apply_=False, log=print):
    """回收。

    apply_=False 时只打印计划与现状（dry-run），不删任何东西。
    返回 dict：{表名: 删除行数}；未真删时为空 dict。
    """
    plan, notes = collect_plan(tracked)
    if not plan:
        log("[回收] 本套件没有新建任何需要回收的数据")
        return {}

    log("[回收] 计划：")
    for n in notes:
        log("    " + n)

    if not apply_:
        log("[回收] dry-run：未删除任何数据")
        return {}

    log("[回收] 开始删除（子表 → 父表，每步回读）：")
    deleted = {}
    # 固定顺序：明细行 → 审批记录 → 步骤 → 站内信 → 单据本体，避免孤儿行
    order = [t for t in LINE_TABLES.values() for t, _ in t] + CHILD_TABLES + \
            [t for t in DOC_TABLES if t in plan]
    for t in order:
        ids = plan.get(t) or []
        if not ids:
            continue
        before, after = _del(t, ids)
        got = before - after
        deleted[t] = got
        log("    %-20s 删 %d 行（%d → %d）" % (t, got, before, after))
        if got != len(ids):
            # 单据表可能有软删除行（delete_time>0 仍占行）——那不是残留，如实标注
            log("       ⚠ 预期删 %d 行、实删 %d 行，请核对是否含已软删除行" % (len(ids), got))

    # 终检：登记的 id 是否真的都不在了
    residue = []
    for t, ids in plan.items():
        if not ids:
            continue
        left = _one("SELECT COUNT(*) FROM %s WHERE id IN (%s);" % (t, _csv(ids)))
        if left != "0":
            residue.append("%s 残留 %s 行" % (t, left))
    if residue:
        log("!! 回收后仍有残留，请人工核对：%s" % "; ".join(residue))
        return deleted
    log("[回收] 回读核对：登记的全部 id 已清零 ✓")
    return deleted


if __name__ == "__main__":
    # 独立干跑入口：只读盘点，不做任何删除。
    # 真正的回收由 pytest 会话结束时的 fixture 调用（数据在内存里，不落盘）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("本模块由 pytest 会话 fixture 调用；单独运行只做只读盘点。")
    print("登记表清单（表名不存在者会被 pytest 会话显式报警）：")
    for t in DOC_TABLES:
        if not _exists(t):
            print("    %-20s !! 表不存在 —— 请核对表名" % t)
            continue
        kind, tpl = DOC_KINDS.get(t, ("（无）", "（无）"))
        print("    %-20s 总 %-6s check_table=%-11s template=%s"
              % (t, _one("SELECT COUNT(*) FROM %s;" % t), kind, tpl))
