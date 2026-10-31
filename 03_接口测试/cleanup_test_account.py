# -*- coding: utf-8 -*-
"""
勾股OA —— 回收本会话构造的测试账号与关联数据

回收对象
  · 账号 testmin / testemp（oa_admin）
  · 角色组 4「测试-最小权限角色」、职位 4「测试最小权限岗」、职位-角色映射 pid=4
  · 上述账号（含历史 id 105）产生的操作日志 oa_admin_log
  · WSL 家目录下的失效备份 testmin_row_backup.sql（旧 testmin id=105 的单行备份）
  · 03_接口测试 下的空临时文件 _tmp_insert.sql

【基线核对 —— 为什么删职位/角色组 4 是"还原"而不是"破坏"】
  动手前先用两份独立备份核对了权限相关表的原始状态：

    备份 A  oa60_backup_20260911.sql        全库，项目早期（09-11）
    备份 B  perm_backup_20260918_0153.sql   权限四表，建号脚本执行前（09-18 01:53）

  两份**一致**：oa_position 只有 id 1/2/3，oa_admin_group 只有 id 1/2/3，
  oa_position_group 只有 (1,1)(2,2)(3,3) —— **职位 4 / 角色组 4 原本不存在**。
  setup_test_account.py 是"先 DELETE id=4 再 INSERT"，因原表无该行，DELETE 命中 0 行，
  即建号脚本并未破坏演示数据；它**新建**了 4，恰好填补了下述悬空引用。
  update_sql/ 里也没有任何版本脚本插入过职位 4（只有一条 ALTER TABLE 加列）。

【副作用（如实记录，不隐藏）】
  本机部署实例的初始演示数据里，oa_admin 的 qinjiaxian(id=6) 与 zhangsan2(id=102)
  **本来就带 position_id=4，而 oa_position 里没有 id=4** —— 一个悬空引用（两份备份均如此）。
  extend/systematic/Systematic.php:30-34 取 PositionGroup.pid = position_id 来汇总规则，
  无映射 → $ids 为空 → $rules 为空 → checkAuth() 对**所有**路径返回 false（零权限）。
  删除职位 4 后这两个账号回到该零权限状态，即**基线状态**，非本次新引入的问题。

设计（按测试项目质量红线）
  · **默认 dry-run**，只打印将删除的内容；加 --apply 才真正执行（红线 14）
  · 执行前先做**全库残留扫描**：凡带 admin_id/uid/from_uid/to_uid/user_id 列的表，
    统一统计引用被清理账号的行数，非 0 的全部列出（红线 12：结论须来自完整结果集）
  · 每个清理目标命中 0 行时**显式告警**，不静默报"没有需要清理的数据"（红线 14）
  · 执行后逐项回读校验

注意：本脚本不做"鉴权自检"，因为它直接在数据库层操作、不经过被测系统接口；
      对应的自检等价物是上面的"残留扫描 + 0 行告警"。

保留（安全网与证据，本次不删）
  <OA_HOME>/oa60_backup_20260911.sql
  <OA_HOME>/perm_backup_20260918_0153.sql

用法：
    python 03_接口测试\\cleanup_test_account.py            # 干跑
    python 03_接口测试\\cleanup_test_account.py --apply    # 真删
"""
import argparse
import base64
import os
import subprocess
import sys

import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 78
MYSQL = oa_config.MYSQL
WSL = ["wsl", "-d", oa_config.WSL_DISTRO, "-u", "root", "--", "bash", "-c"]

# 本会话构造的测试账号（历史 id 105 的旧 testmin 账号已不存在，仅残留日志）
TEST_USERNAMES = ["testmin", "testemp"]
HISTORICAL_UIDS = [105]
GROUP_ID, POSITION_ID = 4, 4
# 失效备份：旧 testmin(id=105) 的单行 dump，该账号早已不存在
FAILED_BACKUP_FILE = oa_config.BACKUP_DIR + "/testmin_row_backup.sql"
TMP_SQL_FILES = ["_tmp_insert.sql"]
# 保留的安全网 / 本次基线结论的证据
KEEP_FILES = [oa_config.BACKUP_DIR + "/oa60_backup_20260911.sql",
              oa_config.BACKUP_DIR + "/perm_backup_20260918_0153.sql"]
# 演示数据里带悬空 position_id=4 的两个账号（删职位 4 后回到零权限基线）
AFFECTED_USERNAMES = ["qinjiaxian", "zhangsan2"]
# 权限表的原始基线（两份备份一致）
BASELINE_POSITIONS = [1, 2, 3]
BASELINE_GROUPS = [1, 2, 3]
BASELINE_MAPPINGS = [(1, 1), (2, 2), (3, 3)]
# 账号表原始基线：oa60_backup_20260911.sql 与 perm_backup_20260918_0153.sql 均为 104 个账号，
# 且 09-11 / 09-18 / 清理后三者**逐 id 逐 username 完全一致**。回收后应回到 104。
BASELINE_ADMIN_COUNT = 104

HERE = os.path.dirname(os.path.abspath(__file__))
UID_COLUMNS = ("uid", "admin_id", "from_uid", "to_uid", "user_id")


# ------------------------------------------------------------------ SQL 工具
def sql(query, err=True):
    """经 base64 传输执行 SQL（绕开 wsl.exe 命令行吞 $ 的问题）"""
    b64 = base64.b64encode(query.encode("utf-8")).decode("ascii")
    shell = "echo %s | base64 -d | %s %s" % (b64, MYSQL, "2>&1" if err else "2>/dev/null")
    out = subprocess.run(WSL + [shell], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
    text = ((out.stdout or "") + (out.stderr or "")).strip()
    return "\n".join(l for l in text.split("\n") if "Warning" not in l)


def rows(query):
    """返回结果行（跳过表头），每行按制表符切分"""
    out = sql(query)
    lines = [l for l in out.split("\n") if l.strip()]
    return [l.split("\t") for l in lines[1:]] if lines else []


def scalar(query):
    r = rows(query)
    return r[0][0].strip() if r else ""


# ------------------------------------------------------------- 残留扫描
def uid_tables():
    q = ("SELECT table_name, column_name FROM information_schema.columns "
         "WHERE table_schema='oa60' AND column_name IN (%s) ORDER BY table_name;"
         % ",".join("'%s'" % c for c in UID_COLUMNS))
    return [(t, c) for t, c in rows(q)]


def scan_residue(uids):
    """全库统计引用这些 uid 的行数，返回 [(表.列, 行数)]，只保留非 0"""
    inlist = ",".join(str(u) for u in uids)
    parts = ["SELECT '%s.%s' k, count(*) v FROM %s WHERE %s IN (%s)"
             % (t, c, t, c, inlist) for t, c in uid_tables()]
    out = sql(" UNION ALL ".join(parts) + ";")
    hits = []
    for line in out.split("\n")[1:]:
        if "\t" not in line:
            continue
        k, v = line.split("\t")[:2]
        if v.strip() not in ("0", ""):
            hits.append((k.strip(), int(v)))
    return hits


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只干跑）")
    args = ap.parse_args()
    apply = args.apply

    print(SEP)
    print("勾股OA 测试账号回收  ——  模式：%s" % ("** --apply 真删 **" if apply else "dry-run 干跑"))
    print(SEP)

    # ---------- 1. 定位账号 ----------
    names = ",".join("'%s'" % u for u in TEST_USERNAMES)
    acct = rows("SELECT id,username,name,position_id,status FROM oa_admin "
                "WHERE username IN (%s);" % names)
    uids = [int(r[0]) for r in acct] + list(HISTORICAL_UIDS)
    print("\n[1] 待回收账号：")
    if acct:
        for r in acct:
            print("    id=%-4s %-10s %-12s 职位=%s" % (r[0], r[1], r[2], r[3]))
    else:
        print("    !! 告警：未找到任何测试账号 —— 若已回收过请忽略，否则请检查账号名")

    # ---------- 2. 日志：找出孤立 uid（账号已不在 oa_admin 的日志行）----------
    orphan = rows("SELECT uid, count(*) c FROM oa_admin_log "
                  "WHERE uid NOT IN (SELECT id FROM oa_admin) GROUP BY uid;")
    print("\n[2] 操作日志中 uid 已不存在于 oa_admin 的（孤立日志）：")
    if orphan:
        for r in orphan:
            print("    uid=%-6s %s 行" % (r[0], r[1]))
    else:
        print("    （无）")

    all_uids = sorted(set(uids) | {int(r[0]) for r in orphan})
    print("    本次将清理的 uid 集合：%s" % all_uids)

    # ---------- 3. 权限表基线核对 ----------
    # 删职位/角色组 4 之前，先证明它们确实是本次构造的：原表只应有 1/2/3
    print("\n[3] 权限表基线核对（原始基线应为 id 1/2/3）:")
    pos_ids = [int(r[0]) for r in rows("SELECT id FROM oa_position ORDER BY id;")]
    grp_ids = [int(r[0]) for r in rows("SELECT id FROM oa_admin_group ORDER BY id;")]
    maps = [(int(r[0]), int(r[1])) for r in
            rows("SELECT pid, group_id FROM oa_position_group ORDER BY pid;")]
    ok = True
    for label, cur, base, expect in (
            ("oa_position.id", pos_ids, BASELINE_POSITIONS, [POSITION_ID]),
            ("oa_admin_group.id", grp_ids, BASELINE_GROUPS, [GROUP_ID]),
            ("oa_position_group", maps, BASELINE_MAPPINGS, [(POSITION_ID, GROUP_ID)])):
        extra = [x for x in cur if x not in base]
        print("    %-20s 现有 %-26s 基线外(本次构造) %s" % (label, cur, extra))
        if extra != expect:
            print("      !! 告警：基线外的值应为 %s，实测 %s，请人工确认后再删"
                  % (expect, extra))
            ok = False
    if not ok:
        print("      !! 基线核对未通过 —— 建议先停下人工核对，不要直接 --apply")

    # 记录副作用：这两个账号的 position_id 本来就指向不存在的职位 4
    aff = rows("SELECT id,username,position_id FROM oa_admin WHERE username IN (%s);"
               % ",".join("'%s'" % u for u in AFFECTED_USERNAMES))
    print("\n    受影响账号（演示数据自带 position_id=%d 的悬空引用）：" % POSITION_ID)
    for r in aff:
        print("      id=%-4s %-12s position_id=%s -> 删职位 %d 后回到零权限基线"
              % (r[0], r[1], r[2], POSITION_ID))

    # ---------- 4. 全库残留扫描 ----------
    print("\n[4] 全库残留扫描（凡带 %s 列的表）:" % "/".join(UID_COLUMNS))
    hits = scan_residue(all_uids)
    if hits:
        for k, v in hits:
            print("    %-32s %s 行" % (k, v))
    else:
        print("    !! 告警：未扫描到任何引用行。这通常意味着扫描本身有问题，请先核实")
        print("       （对照：oa_admin_log 中 uid=%s 至少应有记录）" % HISTORICAL_UIDS)

    # ---------- 4. 汇总将执行的动作 ----------
    plan = [
        ("oa_admin", "oa_admin", "username IN (%s)" % names),
        ("oa_admin_group", "oa_admin_group", "id = %d" % GROUP_ID),
        ("oa_position", "oa_position", "id = %d" % POSITION_ID),
        ("oa_position_group", "oa_position_group",
         "pid = %d OR group_id = %d" % (POSITION_ID, GROUP_ID)),
        ("oa_admin_log", "oa_admin_log",
         "uid IN (%s)" % ",".join(str(u) for u in all_uids)),
    ]
    print("\n[5] 将执行的数据库动作（干跑阶段只做 count，不构造删除语句）：")
    for label, table, cond in plan:
        n = scalar("SELECT count(*) FROM %s WHERE %s;" % (table, cond))
        print("    %-18s 命中 %-4s 行   %s WHERE %s" % (label, n, table, cond))
        if str(n).strip() == "0":
            print("      !! 告警：该目标命中 0 行")

    print("\n[6] 将删除的文件：")
    for f in [FAILED_BACKUP_FILE]:
        ex = subprocess.run(WSL + ["test -f %s && wc -c < %s" % (f, f)],
                            capture_output=True, text=True).stdout.strip()
        note = "（旧 testmin id=105 的单行 dump，账号已不存在）" if ex else ""
        print("    %-46s %s %s" % (f, ("存在 %s 字节" % ex) if ex else "!! 不存在", note))
    for f in TMP_SQL_FILES:
        p = os.path.join(HERE, f)
        size = os.path.getsize(p) if os.path.exists(p) else -1
        print("    %-46s %s" % (p, ("存在 %d 字节" % size) if size >= 0 else "!! 不存在"))

    print("\n[7] 保留（安全网与本次基线结论的证据）：")
    for f in KEEP_FILES:
        ex = subprocess.run(WSL + ["test -f %s && wc -c < %s" % (f, f)],
                            capture_output=True, text=True).stdout.strip()
        print("    %-46s %s" % (f, ("保留 %s 字节" % ex) if ex else "!! 未找到"))

    if not apply:
        print("\n" + SEP)
        print("dry-run 结束，未做任何修改。确认无误后加 --apply 执行。")
        print(SEP)
        return

    # ---------- 6. 执行 ----------
    print("\n" + SEP)
    print("开始执行删除 ...")
    for label, table, cond in plan:
        sql("DELETE FROM %s WHERE %s;" % (table, cond))
        print("    [OK] %s" % label)
    for f in [FAILED_BACKUP_FILE]:
        subprocess.run(WSL + ["rm -f %s" % f], capture_output=True, text=True)
        print("    [OK] 删除文件 %s" % f)
    for f in TMP_SQL_FILES:
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            os.remove(p)
            print("    [OK] 删除文件 %s" % p)

    # ---------- 7. 回读校验 ----------
    print("\n" + SEP)
    print("回读校验：")
    print("    oa_admin 中测试账号剩余   : %s 行（期望 0）"
          % scalar("SELECT count(*) FROM oa_admin WHERE username IN (%s);" % names))
    print("    oa_admin_group id=%d 剩余  : %s 行（期望 0）"
          % (GROUP_ID, scalar("SELECT count(*) FROM oa_admin_group WHERE id=%d;" % GROUP_ID)))
    print("    oa_position id=%d 剩余     : %s 行（期望 0）"
          % (POSITION_ID, scalar("SELECT count(*) FROM oa_position WHERE id=%d;" % POSITION_ID)))
    print("    oa_position_group pid=%d   : %s 行（期望 0）"
          % (POSITION_ID, scalar("SELECT count(*) FROM oa_position_group WHERE pid=%d;" % POSITION_ID)))
    print("    权限表已回到基线           : position=%s  group=%s  mapping=%s"
          % (scalar("SELECT GROUP_CONCAT(id ORDER BY id) FROM oa_position;"),
             scalar("SELECT GROUP_CONCAT(id ORDER BY id) FROM oa_admin_group;"),
             scalar("SELECT GROUP_CONCAT(CONCAT(pid,'-',group_id) ORDER BY pid) "
                    "FROM oa_position_group;")))
    print("    oa_admin_log 本次 uid 剩余 : %s 行（期望 0）"
          % scalar("SELECT count(*) FROM oa_admin_log WHERE uid IN (%s);"
                   % ",".join(str(u) for u in all_uids)))
    print("    全库残留复扫               : %s" % (scan_residue(all_uids) or "无残留"))
    print("    演示账号抽查（admin/fengcailing/yucixin/qinjiaxian）: %s 行（期望 4）"
          % scalar("SELECT count(*) FROM oa_admin WHERE username IN "
                   "('admin','fengcailing','yucixin','qinjiaxian');"))
    print("    账号总数                   : %s 行（期望 %d，与 09-11 备份逐 id 一致）"
          % (scalar("SELECT count(*) FROM oa_admin;"), BASELINE_ADMIN_COUNT))
    print("    悬空引用账号仍在（2 行）   : %s 行"
          % scalar("SELECT count(*) FROM oa_admin WHERE username IN (%s);"
                   % ",".join("'%s'" % u for u in AFFECTED_USERNAMES)))
    print(SEP)


if __name__ == "__main__":
    main()
