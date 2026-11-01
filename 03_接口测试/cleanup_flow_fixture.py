# -*- coding: utf-8 -*-
r"""
勾股OA 6.1.2 —— 回收「会签 / 或签」夹具（setup_flow_data.py 建的 2 条 oa_flow）

【为什么需要这个脚本，而不是复用 cleanup_flow_data.py】
  `cleanup_flow_data.py` 是**上一轮的一次性脚本**：它的回收清单来自
  上一轮执行结果里那批单据 id。作为「夹具回收器」它有致命问题 ——
  那批单据早已不存在，脚本会直接 `sys.exit(2)`。

  而夹具回收器是在 pytest 的 `finally` 里跑的（`conftest._fixture_lifecycle`），
  它**必须满足两条**：
    ① **夹具不在时是干净的空操作**，不能报错 —— 夹具建立失败时也要能安全回收；
    ② 每次跑完都能把 `oa_flow` 还原到 **19 行基线**，与是哪一轮无关。
  `cleanup_flow_data.py` 两条都不满足（实测：SP 模块会话收尾 rc=2 ⇒ conftest 抛
  RuntimeError ⇒ 夹具流程 20/21 留在库里，每跑一轮多留一次）。

  本脚本与 `cleanup_salary_data.py` 同属「夹具回收器」这一类：显式 id + 幂等 + 空操作安全。

【回收对象（显式 id，不用 LIKE 批量删）】
  · oa_flow        —— 标题带【测试】前缀的流程（setup_flow_data.py 所建，正常为 id 20/21）
  · oa_flow_step   —— flow_id 命中上述流程的行（在这两条流下提交过的单据的步骤）
  · oa_flow_record —— 同上

【安全约束】
  · 只删标题带【测试】前缀的流程；若 id 20/21 存在但**不带**该前缀 ⇒ 拒绝执行并报错
  · 既有 19 条流程逐条回读比对（title / check_type / flow_list 的 MD5），必须逐字节未变
  · 删前备份 oa_flow / oa_flow_step / oa_flow_record，**只增不删**
  · 默认 dry-run，`--apply` 才真删；夹具不在时两种模式都是空操作

【用法】
  python 03_接口测试\cleanup_flow_fixture.py            # dry-run
  python 03_接口测试\cleanup_flow_fixture.py --apply    # 真删（先备份）
"""
import argparse
import base64
import subprocess
import sys
from datetime import datetime
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 78
MYSQL = oa_config.MYSQL_NB
WSL = ["wsl", "-d", oa_config.WSL_DISTRO, "-u", "root", "--", "bash", "-c"]
BACKUP_DIR = oa_config.BACKUP_DIR

FLOW_MARK = "【测试】"          # setup_flow_data.py 建的流程的标题前缀
EXPECT_FIXTURE_FLOWS = 2       # 正常建 2 条（会签 / 或签）
BASELINE_FLOWS = 19            # 出厂基线
FIXTURE_FLOW_IDS = (20, 21)    # 正常落在这两个 id 上；仅用于「id 被占却不带标记」的告警

CHILD_BY_FLOW = ("oa_flow_step", "oa_flow_record")


def run_sql(query):
    """经 base64 传输执行 SQL（绕开 wsl.exe 命令行吞 $ 与引号的问题）"""
    b64 = base64.b64encode(query.encode("utf-8")).decode("ascii")
    shell = ("echo %s | base64 -d > /tmp/oa_flow_cln.sql && %s < /tmp/oa_flow_cln.sql; rc=$?; "
             "rm -f /tmp/oa_flow_cln.sql; exit $rc" % (b64, MYSQL))
    out = subprocess.run(WSL + [shell], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
    if out.returncode != 0 or "ERROR" in (out.stderr or ""):
        raise RuntimeError("SQL 执行失败:\n%s\n%s" % (out.stdout, out.stderr))
    return (out.stdout or "").strip()


def rows(query):
    return [ln.split("\t") for ln in run_sql(query).splitlines() if ln != ""]


def one(query, default="0"):
    r = rows(query)
    return r[0][0] if r else default


def one_in_wsl(cmd):
    out = subprocess.run(WSL + [cmd], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
    return ((out.stdout or "") + (out.stderr or "")).strip()


def backup(tables):
    """只增不删：同名备份已存在就不覆盖。

    前缀用 `flow_fixture_` 而不是泛泛的 `cleanup_` —— 本目录里的备份是按范围命名的
    （`oa60_backup_*` / `doc_tables_backup_*` / `perm_backup_*` / `flow_backup_*`），
    泛名会和别的范围的备份混在一起，过一阵就分不清哪份是哪份了。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = "%s/flow_fixture_backup_%s.sql" % (BACKUP_DIR, ts)
    if one_in_wsl("test -f %s && echo yes || echo no" % path).strip() == "yes":
        print("   备份已存在，不覆盖（只增不删）：%s" % path)
        return path, 0, one_in_wsl("wc -c < %s" % path)
    cmd = ("%s %s > %s 2>/tmp/oa_flow_cln.err; echo rc=$?; wc -c < %s"
           % (oa_config.MYSQLDUMP, " ".join(tables), path, path))
    out = one_in_wsl(cmd)
    rc = 0
    for ln in (out or "").splitlines():
        if ln.startswith("rc="):
            rc = int(ln[3:])
    size = (out or "").strip().splitlines()[-1].strip() if out else "0"
    return path, rc, size


def snapshot_baseline():
    """既有（非夹具）流程的快照 —— 收尾逐条比对，证明没被波及"""
    return {r[0]: r[1:] for r in rows(
        "SELECT id, title, check_type, MD5(IFNULL(flow_list,'')) FROM oa_flow "
        "WHERE title NOT LIKE '%s%%';" % FLOW_MARK)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只干跑）")
    args = ap.parse_args()

    print(SEP)
    print("勾股OA 会签/或签夹具回收  ——  模式：%s" % ("** --apply 真删 **" if args.apply else "dry-run 干跑"))
    print(SEP)

    before = snapshot_baseline()

    # ---- 识别夹具流程：只认标题前缀，不认固定 id ----
    fx = rows("SELECT id, title, check_type, status FROM oa_flow WHERE title LIKE '%s%%' "
              "ORDER BY id;" % FLOW_MARK)
    fx_ids = [int(x[0]) for x in fx]

    # id 被占却不带标记 ⇒ 拒绝（防止误删生产流程）
    occupied = [i for i in FIXTURE_FLOW_IDS if one("SELECT COUNT(*) FROM oa_flow WHERE id=%d AND "
                 "title NOT LIKE '%s%%';" % (i, FLOW_MARK)) != "0"]
    if occupied:
        print("!! oa_flow id %s 存在但不带【测试】前缀 —— 夹具 id 被占用，拒绝执行" % occupied)
        sys.exit(2)

    if not fx_ids:
        # ★ 关键：夹具不在时必须是干净的空操作（本脚本在 finally 里跑）
        print("库里没有带【测试】前缀的流程 —— 夹具本就不在，**空操作结束（rc=0）**")
        print("   oa_flow 现有 %s 行（基线 %d）" % (one("SELECT COUNT(*) FROM oa_flow;"), BASELINE_FLOWS))
        return

    print("识别到夹具流程 %d 条：" % len(fx_ids))
    for x in fx:
        print("    %s" % " | ".join(str(v) for v in x))
    if len(fx_ids) != EXPECT_FIXTURE_FLOWS:
        print("   ⚠ 预期 %d 条、实测 %d 条（多跑过 setup_flow_data.py？一并回收，无妨）"
              % (EXPECT_FIXTURE_FLOWS, len(fx_ids)))

    ids_csv = ",".join(map(str, fx_ids))

    # ---- 子表：flow_id 命中夹具流程的行 ----
    plan = {}
    for t in CHILD_BY_FLOW:
        plan[t] = [int(x[0]) for x in
                   rows("SELECT id FROM %s WHERE flow_id IN (%s) ORDER BY id;" % (t, ids_csv))]
        print("    %-16s 命中 %d 行" % (t, len(plan[t])))
    plan["oa_flow"] = fx_ids

    print("")
    print("将删除：%s（合计 %d 行）"
          % ("，".join("%s %d" % (t, len(plan[t])) for t in CHILD_BY_FLOW + ("oa_flow",)),
             sum(len(v) for v in plan.values())))

    if not args.apply:
        print("")
        print("dry-run 结束，未做任何修改。确认无误后加 --apply 执行。")
        return

    path, rc, size = backup(("oa_flow", "oa_flow_step", "oa_flow_record"))
    print("")
    print("已备份 → %s（%s 字节）" % (path, size))
    if rc != 0 or not size or size == "0":
        print("!! 备份失败（rc=%s）；为安全起见终止，不做任何删除" % rc)
        sys.exit(2)

    # ---- 子表先删，流程后删；每步立即回读 ----
    print("")
    print("分步删除（每步立即回读）：")
    for t in CHILD_BY_FLOW + ("oa_flow",):
        ids = plan[t]
        if not ids:
            print("   %-16s 无行可删" % t)
            continue
        n = int(one("SELECT COUNT(*) FROM %s;" % t))
        run_sql("DELETE FROM %s WHERE id IN (%s);" % (t, ",".join(map(str, ids))))
        left = one("SELECT COUNT(*) FROM %s WHERE id IN (%s);" % (t, ",".join(map(str, ids))))
        print("   %-16s 删除 %-3d 行 / 表内 %d → %s，残留 %s 行"
              % (t, len(ids), n, one("SELECT COUNT(*) FROM %s;" % t), left))
        if left != "0":
            print("!! %s 仍有 %s 行未删净，终止（后续表未动）" % (t, left))
            sys.exit(2)

    # ---- 回读核对 ----
    print("")
    print(SEP)
    print("回读核对")
    print(SEP)
    ok = True

    n_flow = int(one("SELECT COUNT(*) FROM oa_flow;"))
    tag = "OK " if n_flow == BASELINE_FLOWS else "!! "
    ok &= n_flow == BASELINE_FLOWS
    print("   %soa_flow = %d（基线 %d）" % (tag, n_flow, BASELINE_FLOWS))

    for t in CHILD_BY_FLOW:
        c = one("SELECT COUNT(*) FROM %s WHERE flow_id IN (%s);" % (t, ids_csv))
        tag = "OK " if c == "0" else "!! "
        ok &= c == "0"
        print("   %s%s 中属于夹具流程的行 = %s" % (tag, t, c))

    after = snapshot_baseline()
    diff = [k for k in before if before[k] != after.get(k)]
    tag = "OK " if not diff else "!! "
    ok &= not diff
    print("   %s既有 %d 条流程逐条比对（title/check_type/flow_list 的 MD5），不一致 %d 条%s"
          % (tag, len(before), len(diff), ("：%s" % diff) if diff else ""))

    print("")
    print("结果：%s" % ("回收完成，回读全部通过" if ok else "**有核对项未通过，请人工复核**"))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
