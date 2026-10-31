# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 —— 回收「薪资管理」测试构造的人设、夹具与运行时残留

回收对象（**全部走显式 id 列表，不用 LIKE 批量删**）
  · 工资明细 oa_salary_records   批次 1000~1009 下的全部明细 + 夹具明细 id 1000/1001/1002
  · 工资批次 oa_salary           id 1000~1009（10 个）
  · 账号   teststaff（id=110，oid 由本次构造）
  · 角色组 5「测试-普通员工角色（薪资）」  rules="266,267"
  · 职位   5「测试普通员工岗（薪资）」      + 职位→角色组映射 (pid=5, group_id=5)
  · 操作日志 oa_admin_log 中 uid=110 的行（本次登录产生，8 行）
  · 运行时缓存 RulesSrc110（权限规则缓存，内容恰为本次构造的 2 条规则）
  · 运行时会话文件 runtime/session/sess_* 中 gougu_admin=110 的 8 个

【为什么这两类运行时残留必须清（不是可选项）】
  ① runtime/cache 下的 RulesSrc110 —— app/base/BaseController.php:152-153 的 checkAuth() 直接读
     Cache::get('RulesSrc' . $uid)，而 :148 那行 `//Cache::delete('RulesSrc' . $uid);` 是**注释掉的**；
     extend/systematic/Systematic.php:26 只在缓存缺失时才重建、:55-56 以 36000 秒 TTL 写入。
     即该缓存**不会因删账号而自动失效**，会以 10 小时的 TTL 独立存活 —— 属真实残留，必须显式清理。
     识别方式：内容恰为 `a:2:{i:0;s:18:"oa/salary/datalist";i:1;s:14:"oa/salary/view";}`
     （本次构造的角色组 5 的 rules 就是这两条节点，全库唯一，无误删风险）。
  ② runtime/session 下的会话文件 —— 内容恰为 `a:1:{s:11:"gougu_admin";i:110;}`，全库唯一匹配，
     8 个文件即 teststaff 的 8 次登录。**清理理由**：账号被删后其会话已不可用
     （checkLogin() → get_admin($uid) 按 id 重新查库，行已不存在则返回 null，
     随后取 $login_admin['did'] 会报错 → 得错误页而非访问权），与其留一个不可用的残留，
     不如显式清掉。**只删匹配 uid=110 的那 8 个文件**，不清空整个 session 目录 ——
     清空会连带登出用户本地浏览器的既有会话（那是本项目之外的状态，不属可回收范围）。

【为什么明细/批次是**硬删**而不是软删】
  oa_salary 与 oa_salary_records 的**原始基线是 0 行**（两份独立备份均可证：两表在
  夹具构造前不存在任何行，AUTO_INCREMENT=1000）。软删（置 delete_time）只会在表里留下
  10 个批次 + 18 条明细的墓碑行，**回不到 0 行基线**，回收就不算完成。
  硬删同时与夹具构造脚本自身的幂等清理口径一致（setup_salary_data.py 的 build_sql()
  第一段就是 `DELETE FROM oa_salary_records WHERE salary_id IN (...) OR id IN (...)`）。
  安全网：<OA_HOME>/oa60_backup_20260911.sql（全库，项目早期）—— 只增不删。

【本脚本不动的对象（边界）】
  · 角色组 4 / 职位 4 / 映射 (4,4) / 账号 testmin(108) / testemp(109)
    —— 属**上一个测试账号集**，由 cleanup_test_account.py 负责；两个脚本的回收顺序可任意，
       但都不要代对方清理。本脚本在前后各打印一次这些 id，用于证明「没被顺手删掉」。
  · ceshiyuangong(id=104)、qinjiaxian、zhangsan2 等演示数据 —— 只读核对，不写
  · 会话目录、日志文件、备份文件 —— 除上面点名的 8 个会话文件外一律不动
  · <OA_HOME>/office/update_sql/ —— 产品自带的版本脚本，与本次测试无关

【前置（本次已满足，脚本会复核）】
  · 权限五表已备份 → <OA_HOME>/perm_backup_20260919_0128.sql（测试计划 §3.2）
  · cleanup_salary_data.py 与 setup_salary_data.py 的常量必须保持一致（批次 id、账号、组/职位 id）

设计（按测试项目质量红线）
  · **默认 dry-run**，只打印将删除的内容；加 --apply 才真正执行
  · 分步执行 + **每步回读**（mysql CLI 遇错即停不回滚，不做大事务）
  · 执行前做**归属核对**：oa_salary / oa_salary_records 中凡不在本次构造 id 列表内的行一并列出，
    证明"只删本次构造的，不误伤既有数据"（红线：结论须来自完整结果集）
  · **全库残留扫描**：凡带 uid/admin_id/from_uid/to_uid/user_id/salary_id 列的表，
    统一统计引用被清理 uid 与批次 id 的行数，非 0 的全部列出
  · 每个清理目标命中 0 行时**显式告警**，不静默报"没有需要清理的数据"
  · 执行后逐项回读，并核对**负向证据基线**（财务流水 0 行、站内信未被写）

注意：本脚本不做"鉴权自检"，因为它直接在数据库层操作、不经过被测系统接口；
      对应的自检等价物是上面的"归属核对 + 残留扫描 + 0 行告警"。

用法：
    python 03_接口测试\\cleanup_salary_data.py            # 干跑
    python 03_接口测试\\cleanup_salary_data.py --apply    # 真删
"""
import argparse
import base64
import subprocess
import sys

import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 78
MYSQL = oa_config.MYSQL
WSL = ["wsl", "-d", oa_config.WSL_DISTRO, "-u", "root", "--", "bash", "-c"]

# ---------------------------------------------------------------------------
# 本次构造的对象（与 setup_salary_data.py 的常量保持一致）
# ---------------------------------------------------------------------------
BATCH_IDS = [1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009]
FIXTURE_RECORD_IDS = [1000, 1001, 1002]      # 批次 A 的 3 条夹具明细
USERNAME = "teststaff"
UID = 110
GROUP_ID = 5
POSITION_ID = 5
RUNTIME = oa_config.OA_HOME + "/office/runtime"
CACHE_DIR = RUNTIME + "/cache"
SESSION_DIR = RUNTIME + "/session"

# 上一批测试账号集（本脚本**不动**，由 cleanup_test_account.py 负责）
PREV_GROUP_ID, PREV_POSITION_ID = 4, 4
PREV_UIDS = [108, 109]

# 负向证据基线（证明测试全程未触碰外发点）
#
# ⚠ 原先这里写死 `MSG_BASELINE = 273`，现已**删除该常量** —— 它是个迟早会骗人的哨兵：
#   oa_msg 的总数随各轮测试数据的构造/回收不断变化，钉住某个历史绝对值，
#   只会让每次运行都报一条「期望 273、实测 95」的假警报。**假警报会训练人忽略这一行**，
#   这条核对也就废了（本轮就差点被它误导去怀疑新泄漏，见挂起存档 §2.6 ③）。
#   真正的不变量是「回收薪资夹具**不得改变** oa_msg」⇒ 改为**本次运行前后自比**（见 [7] 与收尾）。
#   财务流水仍是全 0，那个绝对值是稳的（该库基线就是 0），故保留。
FINANCE_TABLES = ["oa_finance_log", "oa_finance_injection"]

# 权限表原始基线（两份独立备份一致：oa_position / oa_admin_group 只有 1/2/3，
# oa_position_group 只有 (1,1)(2,2)(3,3)）—— 4 属上一批构造，故本次回收后的期望值是 1/2/3/4
BASELINE_POSITIONS = [1, 2, 3]

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


def shell(cmd):
    """在 WSL 里跑一条 shell 命令，返回 stdout（去除首尾空白）"""
    out = subprocess.run(WSL + [cmd], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    return ((out.stdout or "") + (out.stderr or "")).strip()


def id_list(vals):
    return ",".join(str(v) for v in vals)


# ------------------------------------------------------------- 残留扫描
def uid_tables():
    cols = tuple(UID_COLUMNS) + ("salary_id",)
    q = ("SELECT table_name, column_name FROM information_schema.columns "
         "WHERE table_schema='oa60' AND column_name IN (%s) ORDER BY table_name;"
         % ",".join("'%s'" % c for c in cols))
    return [(t, c) for t, c in rows(q)]


def scan_residue():
    """全库统计引用 uid=110 或本次批次 id 的行数，返回 [(表.列, 行数)]，只保留非 0"""
    parts = []
    for t, c in uid_tables():
        if c == "salary_id":
            cond = "salary_id IN (%s)" % id_list(BATCH_IDS)
        else:
            cond = "%s = %d" % (c, UID)
        parts.append("SELECT '%s.%s' k, count(*) v FROM %s WHERE %s" % (t, c, t, cond))
    out = sql(" UNION ALL ".join(parts) + ";")
    hits = []
    for line in out.split("\n")[1:]:
        if "\t" not in line:
            continue
        k, v = line.split("\t")[:2]
        if v.strip() not in ("0", ""):
            hits.append((k.strip(), int(v)))
    return hits


# ------------------------------------------------------- 运行时残留的识别
# 两个模式都是**全库唯一**的：前者恰为本次构造的角色组 5 的 rules（266 我的薪资-列表 /
# 267 我的薪资-详情）序列化后的形态；后者是 ThinkPHP 会话里 session_admin 键的取值形态。
CACHE_PATTERN = 'a:2:{i:0;s:18:"oa/salary/datalist";i:1;s:14:"oa/salary/view";}'
SESSION_PATTERN = 'gougu_admin";i:%d;' % UID


def _grep_files(pattern, directory):
    """用 grep -rl 列出内容含 pattern 的文件；不依赖 shell 变量展开"""
    out = shell("grep -rl '%s' %s" % (pattern, directory))
    return [l for l in out.split("\n") if l.strip()]


def cache_files():
    return _grep_files(CACHE_PATTERN, CACHE_DIR)


def session_files():
    return _grep_files(SESSION_PATTERN, SESSION_DIR)


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只干跑）")
    args = ap.parse_args()
    apply = args.apply

    print(SEP)
    print("勾股OA 薪资模块回收  ——  模式：%s" % ("** --apply 真删 **" if apply else "dry-run 干跑"))
    print(SEP)

    # ---------- 1. 定位：账号 / 权限对象 ----------
    print("\n[1] 待回收对象（显式 id 列表）:")
    acct = rows("SELECT id, username, name, did, position_id, status FROM oa_admin "
                "WHERE username = '%s' OR id = %d;" % (USERNAME, UID))
    if acct:
        for r in acct:
            print("    账号      id=%-4s %-10s %-14s did=%s 职位=%s"
                  % (r[0], r[1], r[2], r[3], r[4]))
    else:
        print("    !! 告警：未找到账号 %s(id=%d) —— 若已回收过请忽略" % (USERNAME, UID))
    for label, q in (("角色组 %d" % GROUP_ID,
                      "SELECT id, title, rules FROM oa_admin_group WHERE id=%d;" % GROUP_ID),
                     ("职位   %d" % POSITION_ID,
                      "SELECT id, title FROM oa_position WHERE id=%d;" % POSITION_ID),
                     ("映射   (%d,%d)" % (POSITION_ID, GROUP_ID),
                      "SELECT pid, group_id FROM oa_position_group "
                      "WHERE pid=%d AND group_id=%d;" % (POSITION_ID, GROUP_ID))):
        got = rows(q)
        print("    %-14s %s" % (label, " | ".join(got[0]) if got else "!! 告警：未找到"))
    print("    批次           id %s（%d 个）" % (id_list(BATCH_IDS), len(BATCH_IDS)))

    rec_ids = [int(r[0]) for r in rows(
        "SELECT id FROM oa_salary_records WHERE salary_id IN (%s) OR id IN (%s) ORDER BY id;"
        % (id_list(BATCH_IDS), id_list(FIXTURE_RECORD_IDS)))]
    print("    明细           id %s（%d 条）"
          % (id_list(rec_ids) if rec_ids else "-", len(rec_ids)))

    # ---------- 2. 归属核对：只删本次构造的 ----------
    print("\n[2] 归属核对（证明不误伤既有数据）:")
    stray_batches = [int(r[0]) for r in rows(
        "SELECT id FROM oa_salary WHERE id NOT IN (%s) ORDER BY id;" % id_list(BATCH_IDS))]
    stray_recs = [int(r[0]) for r in rows(
        "SELECT id FROM oa_salary_records WHERE salary_id NOT IN (%s) ORDER BY id;"
        % id_list(BATCH_IDS))]
    print("    oa_salary 中不在本次批次列表内的行          : %s"
          % (stray_batches or "无（该表全部 10 行均为本次构造）"))
    print("    oa_salary_records 中不属于本次批次的行      : %s"
          % (stray_recs or "无（该表全部行均属本次批次）"))
    if stray_batches or stray_recs:
        print("      !! 告警：存在非本次构造的数据，请人工确认后再 --apply")

    # ---------- 3. 边界核对：上一批的对象仍在（本脚本不动它们）----------
    print("\n[3] 边界核对（这些**不属本次回收**，删完应逐字不变）:")
    prev = rows("SELECT g.id, g.title, p.id, p.title FROM oa_admin_group g "
                "LEFT JOIN oa_position p ON p.id = %d WHERE g.id = %d;"
                % (PREV_POSITION_ID, PREV_GROUP_ID))
    for r in prev:
        print("    角色组 %s「%s」 / 职位 %s「%s」" % tuple(r))
    prev_accts = rows("SELECT id, username, position_id, status FROM oa_admin "
                      "WHERE id IN (%s) ORDER BY id;" % id_list(PREV_UIDS))
    for r in prev_accts:
        print("    账号   id=%-4s %-10s 职位=%s status=%s" % tuple(r))
    print("    说明：这四项由 cleanup_test_account.py 负责，两个脚本互不代劳")

    # ---------- 4. 全库残留扫描 ----------
    print("\n[4] 全库残留扫描（引用 uid=%d 或批次 %s 的行）:" % (UID, id_list(BATCH_IDS)))
    hits = scan_residue()
    if hits:
        for k, v in hits:
            print("    %-32s %s 行" % (k, v))
    else:
        print("    !! 告警：未扫描到任何引用行。这通常意味着扫描本身有问题，请先核实")

    # ---------- 5. 运行时残留 ----------
    print("\n[5] 运行时残留（权限缓存 / 会话）:")
    cache_hits = cache_files()
    print("    权限缓存 RulesSrc%d（内容恰为本次构造的 2 条规则）: %d 个"
          % (UID, len(cache_hits)))
    for f in cache_hits:
        print("        %s" % f)
    print("      清理理由：checkAuth() 直接读 Cache::get('RulesSrc'.$uid)（BaseController.php:152-153），")
    print("                而 :148 的 Cache::delete 是注释掉的 ⇒ 删账号不会让该缓存失效，")
    print("                它会以 36000 秒 TTL 独立存活（Systematic.php:26 / :55-56）")

    sess_hits = session_files()
    print("    会话文件 gougu_admin=%d: %d 个（只删这些，不清空整个 session 目录）"
          % (UID, len(sess_hits)))
    print("      会话目录总文件数: %s；清空会连带登出用户本地浏览器的既有会话，故不整体清"
          % shell("ls %s | wc -l" % SESSION_DIR))

    # ---------- 6. 命中行数 ----------
    rec_cond = "salary_id IN (%s) OR id IN (%s)" % (id_list(BATCH_IDS), id_list(FIXTURE_RECORD_IDS))
    plan = [
        ("oa_salary_records", "oa_salary_records", rec_cond),
        ("oa_salary", "oa_salary", "id IN (%s)" % id_list(BATCH_IDS)),
        ("oa_admin_log", "oa_admin_log", "uid = %d" % UID),
        ("oa_admin", "oa_admin", "username = '%s' OR id = %d" % (USERNAME, UID)),
        ("oa_position_group", "oa_position_group",
         "pid = %d OR group_id = %d" % (POSITION_ID, GROUP_ID)),
        ("oa_position", "oa_position", "id = %d" % POSITION_ID),
        ("oa_admin_group", "oa_admin_group", "id = %d" % GROUP_ID),
    ]
    print("\n[6] 将执行的数据库动作（干跑阶段只做 count）:")
    for label, table, cond in plan:
        n = scalar("SELECT count(*) FROM %s WHERE %s;" % (table, cond))
        print("    %-20s 命中 %-4s 行   %s WHERE %s" % (label, n, table, cond))
        if str(n).strip() == "0":
            print("      !! 告警：该目标命中 0 行")
    print("    删除顺序：明细 → 批次 → 日志 → 账号 → 映射 → 职位 → 角色组（先子后父）")

    # ---------- 7. 负向证据基线 ----------
    # 取本次运行前的 oa_msg，收尾与之自比 —— 不用写死的绝对值（理由见文件头 MSG_BASELINE 处的注释）
    msg_before = int(scalar("SELECT count(*) FROM oa_msg;"))
    print("\n[7] 负向证据基线（证明测试全程未触碰外发点）:")
    print("    oa_msg                : %d 行（记下此值，收尾须相等 —— 未触发站内信）" % msg_before)
    for t in FINANCE_TABLES:
        n = scalar("SELECT count(*) FROM %s;" % t)
        print("    %-21s : %s 行（期望 0 —— 未调用发放接口 /adm/api/salary_pay）" % (t, n))
    print("    对照：本次唯一会写站内信与财务流水的入口是发放接口，脚本全程未调用")

    if not apply:
        print("\n" + SEP)
        print("dry-run 结束，未做任何修改。确认无误后加 --apply 执行。")
        print(SEP)
        return

    # ---------- 8. 执行（分步 + 每步回读）----------
    print("\n" + SEP)
    print("开始执行删除（分步，每步回读命中行数）...")
    for label, table, cond in plan:
        before = scalar("SELECT count(*) FROM %s WHERE %s;" % (table, cond))
        sql("DELETE FROM %s WHERE %s;" % (table, cond))
        after = scalar("SELECT count(*) FROM %s WHERE %s;" % (table, cond))
        flag = "OK" if after in ("0", "") else "!! 仍有残留"
        print("    [%s] %-20s %s -> %s 行" % (flag, label, before, after))

    print("    清理运行时残留 ...")
    for f in cache_hits:
        shell("rm -f '%s'" % f)
        print("        [OK] 删除权限缓存 %s" % f)
    for f in sess_hits:
        shell("rm -f '%s'" % f)
        print("        [OK] 删除会话文件 %s" % f)

    # ---------- 9. 回读校验 ----------
    print("\n" + SEP)
    print("回读校验：")
    print("    oa_salary 剩余                : %s 行（期望 0，回到构造前基线）"
          % scalar("SELECT count(*) FROM oa_salary;"))
    print("    oa_salary_records 剩余        : %s 行（期望 0，回到构造前基线）"
          % scalar("SELECT count(*) FROM oa_salary_records;"))
    print("    oa_admin 账号 %s 剩余        : %s 行（期望 0）"
          % (USERNAME, scalar("SELECT count(*) FROM oa_admin WHERE username='%s' OR id=%d;"
                              % (USERNAME, UID))))
    print("    oa_admin_group id=%d 剩余     : %s 行（期望 0）"
          % (GROUP_ID, scalar("SELECT count(*) FROM oa_admin_group WHERE id=%d;" % GROUP_ID)))
    print("    oa_position id=%d 剩余        : %s 行（期望 0）"
          % (POSITION_ID, scalar("SELECT count(*) FROM oa_position WHERE id=%d;" % POSITION_ID)))
    print("    oa_position_group pid=%d 剩余 : %s 行（期望 0）"
          % (POSITION_ID, scalar("SELECT count(*) FROM oa_position_group WHERE pid=%d;"
                                 % POSITION_ID)))
    print("    oa_admin_log uid=%d 剩余     : %s 行（期望 0）"
          % (UID, scalar("SELECT count(*) FROM oa_admin_log WHERE uid=%d;" % UID)))
    print("    权限缓存 RulesSrc%d 剩余     : %d 个（期望 0）"
          % (UID, len(cache_files())))
    print("    会话 gougu_admin=%d 剩余     : %d 个（期望 0）"
          % (UID, len(session_files())))

    print("\n    权限表已回基线（4 属上一批，由另一脚本回收）:")
    print("        oa_position.id       = %s  （期望 1,2,3,4）"
          % scalar("SELECT GROUP_CONCAT(id ORDER BY id) FROM oa_position;"))
    print("        oa_admin_group.id    = %s  （期望 1,2,3,4）"
          % scalar("SELECT GROUP_CONCAT(id ORDER BY id) FROM oa_admin_group;"))
    print("        oa_position_group    = %s  （期望 1-1,2-2,3-3,4-4）"
          % scalar("SELECT GROUP_CONCAT(CONCAT(pid,'-',group_id) ORDER BY pid) "
                   "FROM oa_position_group;"))

    print("\n    边界核对（上一批对象应逐字未变）:")
    for r in rows("SELECT id, username, position_id, status FROM oa_admin "
                  "WHERE id IN (%s) ORDER BY id;" % id_list(PREV_UIDS)):
        print("        id=%-4s %-10s 职位=%s status=%s" % tuple(r))
    print("    演示数据抽查（admin/fengcailing/yucixin/ceshiyuangong）: %s 行（期望 4）"
          % scalar("SELECT count(*) FROM oa_admin WHERE username IN "
                   "('admin','fengcailing','yucixin','ceshiyuangong');"))
    print("    账号总数                      : %s 行（回收前 107 = 104 基线 + testmin/testemp/teststaff）"
          % scalar("SELECT count(*) FROM oa_admin;"))

    print("\n    全库残留复扫                  : %s" % (scan_residue() or "无残留"))
    print("    负向证据基线（应仍为原值）:")
    msg_after = int(scalar("SELECT count(*) FROM oa_msg;"))
    print("        oa_msg                    : %d 行（回收前 %d —— %s）"
          % (msg_after, msg_before,
             "相等 ✓ 未触发站内信" if msg_after == msg_before
             else "**不相等（差 %+d），需人工复核：回收不该写站内信**" % (msg_after - msg_before)))
    for t in FINANCE_TABLES:
        print("        %-25s : %s 行（期望 0）" % (t, scalar("SELECT count(*) FROM %s;" % t)))
    print(SEP)
    print("回收完成。薪资模块相关数据与运行时残留均已清除，oa_salary / oa_salary_records 回到 0 行基线。")
    print(SEP)


if __name__ == "__main__":
    main()
