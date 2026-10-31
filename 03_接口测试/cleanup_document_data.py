# -*- coding: utf-8 -*-
r"""
勾股OA 6.1.2 —— 回收「请假 / 加班 / 出差 / 外出」单据测试数据

回收范围（全部按 reason 前缀 MARK 匹配，不误伤演示数据 / 手工数据）：
  oa_leaves / oa_overtimes / oa_trips / oa_outs   本次构造的单据本体
  oa_flow_step / oa_flow_record                   与这些单据关联的审批步骤与记录
  oa_msg                                          这些单据触发的站内消息
  oa_admin_log                                    执行阶段产生的操作日志

⚠ 两条必须遵守的清理纪律（本项目已因此踩过一次坑）
  1. **删除 oa_flow_step / oa_flow_record 必须同时用 flow_id 限定**：
     这两张表的 action_id 在 oa_contract 与 oa_purchase 之间会重号
     （本实例两边都从 1000 起），只按 action_id 删会把另一张表的审批记录一并删掉。
     本脚本先按"本次回收的单据 id + 对应 flow_id"成对构造条件。
  2. **默认 dry-run**：不带 --apply 只打印命中行数，不做任何删除。
     若命中数为 0 会明确报警并退出码 1 —— 不能静默地报"没有需要清理的数据"。

用法：
  python 03_接口测试\cleanup_document_data.py            # dry-run
  python 03_接口测试\cleanup_document_data.py --apply    # 真删
"""
import argparse
import base64
import subprocess
import sys

import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 78
WSL_DISTRO = oa_config.WSL_DISTRO
DB = oa_config.DB_NAME
MYSQL = oa_config.MYSQL_NB

MARK = "【功能测试数据】"

# 模块 -> (表名, 审批流程 id)  —— flow_id 用于给 flow 表的删除加限定
MODULES = {
    "leaves":    ("oa_leaves",    1),
    "overtimes": ("oa_overtimes", 4),
    "trips":     ("oa_trips",     2),
    "outs":      ("oa_outs",      3),
}


def run_sql(sql, allow_error=False):
    b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    shell = ("echo %s | base64 -d > /tmp/oa_clr.sql && %s < /tmp/oa_clr.sql; rc=$?; "
             "rm -f /tmp/oa_clr.sql; exit $rc" % (b64, MYSQL))
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", shell],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=300)
    if not allow_error and (out.returncode != 0 or "ERROR" in (out.stderr or "")):
        raise RuntimeError("SQL 执行失败:\nstdout=%s\nstderr=%s" % (out.stdout, out.stderr))
    return (out.stdout or "").strip()


def one(sql, default="0"):
    out = run_sql(sql)
    return out.splitlines()[0] if out else default


def ids_in(table):
    out = run_sql("SELECT GROUP_CONCAT(id) FROM %s WHERE reason LIKE '%s%%';" % (table, MARK))
    return out.splitlines()[0] if out and out.splitlines()[0] != "NULL" else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除（默认 dry-run）")
    args = ap.parse_args()

    print(SEP)
    print("勾股OA 6.1.2 —— 单据测试数据回收%s" % ("（APPLY：真删）" if args.apply else "（DRY-RUN：不删）"))
    print(SEP)

    # ---- 1) 找出范围内的单据 ----
    found = {}
    for module, (table, flow_id) in MODULES.items():
        idlist = ids_in(table)
        n = len([x for x in idlist.split(",") if x]) if idlist else 0
        found[module] = (table, flow_id, idlist, n)
        print("  %-10s %-14s 命中 %2d 条  ids=%s" % (module, table, n, idlist or "-"))

    total = sum(v[3] for v in found.values())
    if total == 0:
        # 自检：这次运行到底是不需要清理，还是脚本自己没跑对？
        print("\n!! 未命中任何待回收数据。")
        print("   请先确认：① 单据确实是用 setup_document_data.py 构造的（reason 前缀 %s）；" % MARK)
        print("             ② 数据库连接正常（本脚本已能连库，否则上面会抛异常）。")
        print("   不做任何删除，退出码 1。")
        sys.exit(1)

    # ---- 2) 统计关联数据（flow / msg / log）----
    flow_where = " OR ".join(
        "(action_id IN (%s) AND flow_id=%d)" % (v[2], v[1])
        for v in found.values() if v[2])
    n_step = one("SELECT COUNT(*) FROM oa_flow_step WHERE delete_time=0 AND (%s);" % flow_where)
    n_rec = one("SELECT COUNT(*) FROM oa_flow_record WHERE delete_time=0 AND (%s);" % flow_where)
    print("\n  关联数据：oa_flow_step=%s 行，oa_flow_record=%s 行（已按 flow_id 限定）" % (n_step, n_rec))

    # ---- 3) 删除 ----
    stmts = []
    for module, (table, flow_id, idlist, n) in found.items():
        if not idlist:
            continue
        stmts.append("DELETE FROM %s WHERE id IN (%s);" % (table, idlist))
    stmts.append("DELETE FROM oa_flow_step   WHERE (%s);" % flow_where)
    stmts.append("DELETE FROM oa_flow_record WHERE (%s);" % flow_where)
    # 站内消息：这些单据触发的审批通知（按模板 20~23 = 请假/出差/外出/加班）
    stmts.append("DELETE FROM oa_msg WHERE template IN (20,21,22,23) AND action_id IN (%s);"
                 % ",".join(v[2] for v in found.values() if v[2]))
    # 操作日志：本阶段 add/edit/check/refue/back 产生的记录
    stmts.append("DELETE FROM oa_admin_log WHERE param_id IN (%s) AND type IN "
                 "('add','edit','check','refue','back','view');"
                 % ",".join(v[2] for v in found.values() if v[2]))

    print("\n  待执行语句：")
    for s in stmts:
        print("    %s" % (s if len(s) < 200 else s[:197] + "..."))

    if not args.apply:
        print("\n[DRY-RUN] 未删除任何数据。加 --apply 执行。")
        return

    print("\n  执行删除 ...")
    run_sql("SET NAMES utf8mb4;\n" + "\n".join(stmts))

    # ---- 4) 回读校验（真实比较）----
    after = {}
    for module, (table, flow_id) in MODULES.items():
        after[module] = int(one("SELECT COUNT(*) FROM %s;" % table))
    f_step = int(one("SELECT COUNT(*) FROM oa_flow_step WHERE delete_time=0;"))
    f_rec = int(one("SELECT COUNT(*) FROM oa_flow_record WHERE delete_time=0;"))
    print("\n  回读：四表行数 = %s" % after)
    print("        oa_flow_step(未删)=%d  oa_flow_record(未删)=%d" % (f_step, f_rec))

    bad = [m for m, c in after.items() if c != 0]
    if bad:
        print("!! 回收不彻底，仍非空的表：%s" % bad)
    elif f_step or f_rec:
        print("!! flow 表仍有未删除的有效行（可能属于其他模块，属正常）")
    else:
        print("\n回收完成：四表已恢复 0 行基线，flow 表已清空。")
    print(SEP)


if __name__ == "__main__":
    main()
