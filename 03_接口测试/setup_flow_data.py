# -*- coding: utf-8 -*-
r"""
勾股OA 6.1.2 —— 为 SP-023~026 补配「会签 / 或签」固定审批流（夹具）

【为什么需要这个脚本】
  SP-023~026 原先记「待执行」，理由是"本实例 19 条 oa_flow 的 check_type 全为 1
  （自由审批）、flow_list 全为空，界面上配不出会签/或签"。复核后确认：该理由只说明
  **当前部署没有配**，不等于**产品不支持**——

    · 后台 UI 支持：app/adm/view/flow/add.html:67-69 审批流类型可选「固定审批流(2)」；
      :110「指定人员」为多选（picker-admin data-type="2"）；:116-119 每级有
      「同时有多人时」下拉 = 会签(1) / 或签(2)；:166 可加审批层级
    · 源码判据确证：app/api/controller/Check.php:414
        if((($check_count+1) >= count($flow_count) && $step['check_types']==1) || $step['check_types']==2)
      —— check_types==1（会签）要凑满本级人数才流转；==2（或签）任一人通过即流转
    · 提交路径确证：Check.php:70 的 submit_check **直接取 $param['flow_id']**；
      且**不带 check_uids 参数**时走「非自由审批模式」分支（Check.php:89 的
      !isset($param['check_uids']) 判断，分支体自 :90 起），
      按 flow_list 生成 oa_flow_step —— 所以固定审批流可由调用方显式指定。
  ⇒ 会签/或签是本产品**支持**的能力，只是**当前部署未配置**。故补配后真跑
    （setup_flow_data.py → 接口自动化/ 的 pytest 套件），
    而不是把 4 条用例永久标成「待执行」。

【本脚本建两条流程，均挂在既有审批类型 oa_flow_cate id=1「请假」上（check_table=leaves）】
  【测试】会签流程   check_type=2
      第1级  指定成员 uid=1,4   check_types=1（会签）
      → SP-023（会签全过才通过）、SP-024（会签一人驳回即整单驳回）

  【测试】或签流程   check_type=2
      第1级  指定成员 uid=1,4   check_types=2（或签）
      第2级  指定成员 uid=1
      → SP-025（或签任一通过即流转）、SP-026（或签落选者待办被取消）
      ⚠️ 第 2 级是**刻意**加的：只有流程**流转到下一级**（而不是直接结束），
         才能把「落选者待办被取消」与「整单审批已结束」这两种情况区分开 ——
         否则 check_uids 被清空既可能是"取消"也可能是"结束"，断言没有分辨力。

【写入方式】
  优先走真实后台接口 POST /adm/flow/add（与人工在界面上配流程完全同一条路径）；
  接口返回非 code=0 时**回退直插** oa_flow，并如实打印本次走的是哪条路。
  无论哪条路，落库后都做**三重回读校验**，不过就 sys.exit(1)。

【三重回读校验】
  ① PHP 侧 parse：用与站点同版本的 php CLI 读回 flow_list 并 unserialize，
     逐项比对 check_role / check_types / check_uids —— 证明 serialize 格式可解析
  ② 应用侧 parse：GET /api/check/get_flow_users?id=<flow_id> —— 该接口内部就是
     unserialize(flow_list) 再把每级解析成审批人；能正确返回即证明**应用真能读懂**
  ③ API 列表：GET /adm/flow/datalist（AJAX）能列出新流程且 check_type=2

【产出】
  · 库中新增 2 行 oa_flow（标题带【测试】前缀）
  · 03_接口测试/flow_fixture.json —— 2 条流程的 id/级别定义 + **既有 19 条流程的快照**
    （id → [title, check_type, flow_list 的 md5]），供执行器取 id、供回收脚本核对
    "边界对象逐字节未变"

【不做的事 / 红线】
  · 只**新增** 2 行 oa_flow，不改动既有 19 条的任何字段（快照回读逐条比对）
  · 落库前先备份 oa_flow / oa_flow_cate / oa_flow_step / oa_flow_record 到
    <OA_HOME>/flow_backup_<时间戳>.sql（备份只增不删）
  · 不调 /adm/api/salary_pay、api/task/email_excel、api/index/email_test，不碰第三方
  · 不动 testmin(108) / testemp(109) / ceshiyuangong(104) 与角色组 1~4
  · 用显式 id 定位，**不用 LIKE 批量删**（本脚本不删任何东西，回收见 cleanup_flow_fixture.py）

【用法】
  python 03_接口测试\setup_flow_data.py            # dry-run，只打印计划
  python 03_接口测试\setup_flow_data.py --apply    # 真建（先备份）
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oa_login import BASE_URL, login  # noqa: E402
import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEP = "=" * 78
WSL_DISTRO = oa_config.WSL_DISTRO
DB = oa_config.DB_NAME
MYSQL = oa_config.MYSQL_NB
BACKUP_DIR = oa_config.BACKUP_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
SIDECAR = os.path.join(HERE, "flow_fixture.json")

ADMIN = "admin"
ADMIN_PWD = oa_config.ADMIN_PWD

MARK = "【测试】"
CATE_ID = 1          # oa_flow_cate「请假」（check_table=leaves），无需新建分类
CATE_NAME = "leaves"

# 两条流程的级别定义（check_role=4 = 指定成员，见 flow/add.html:95）
FLOWS = [
    {
        "key": "co_sign",
        "title": MARK + "会签流程",
        "用途": "SP-023 会签全过 / SP-024 会签一人驳回",
        "levels": [
            {"check_role": 4, "check_position_id": 0, "check_uids": "1,4", "check_types": 1},
        ],
    },
    {
        "key": "or_sign",
        "title": MARK + "或签流程",
        "用途": "SP-025 或签任一通过 / SP-026 或签落选者待办取消",
        "levels": [
            {"check_role": 4, "check_position_id": 0, "check_uids": "1,4", "check_types": 2},
            {"check_role": 4, "check_position_id": 0, "check_uids": "1", "check_types": 1},
        ],
    },
]

PROBLEMS = []        # 回读校验的问题清单，非空即失败


def fail(msg):
    PROBLEMS.append(msg)
    print("   !! %s" % msg)


# ---------------------------------------------------------------------------
# SQL 通道（与既有执行器同一套：PHP/shell 不能经 wsl.exe 命令行传，`$` 会被吞）
# ---------------------------------------------------------------------------
def run_sql(sql):
    b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    shell = ("echo %s | base64 -d > /tmp/oa_flow_setup.sql && %s < /tmp/oa_flow_setup.sql; rc=$?; "
             "rm -f /tmp/oa_flow_setup.sql; exit $rc" % (b64, MYSQL))
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


def esc(s):
    """SQL 单引号转义（本脚本写入的字符串都不含引号，仍是防线）"""
    return str(s).replace("\\", "\\\\").replace("'", "''")


# ---------------------------------------------------------------------------
# PHP 侧：serialize / unserialize（用与站点同版本的 php CLI，权威口径）
# ---------------------------------------------------------------------------
PHP_SERIALIZE = """<?php
$raw = file_get_contents('/tmp/oa_flow_php_in.json');
$in = json_decode($raw, true);
if ($in === null) { fwrite(STDERR, "json_decode failed: " . $raw); exit(1); }
echo serialize($in);
"""

PHP_UNSERIALIZE = """<?php
$raw = file_get_contents('/tmp/oa_flow_php_in.txt');
$out = @unserialize($raw);
if ($out === false && trim($raw) !== 'b:0;') {
    fwrite(STDERR, "unserialize failed"); exit(1);
}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""


def _php(code, payload, suffix, ext):
    """把 php 源码与输入都 base64 送进 WSL，避免任何引号/`$` 被外层 shell 吞掉"""
    b_code = base64.b64encode(code.encode("utf-8")).decode("ascii")
    b_in = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    shell = ("echo %s | base64 -d > /tmp/oa_flow_php.php && "
             "echo %s | base64 -d > /tmp/oa_flow_php_in.%s && "
             "php /tmp/oa_flow_php.php; rc=$?; "
             "rm -f /tmp/oa_flow_php.php /tmp/oa_flow_php_in.%s; exit $rc"
             % (b_code, b_in, ext, ext))
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", shell],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120)
    if out.returncode != 0:
        raise RuntimeError("php 调用失败(%s)：stdout=%s\nstderr=%s"
                           % (suffix, out.stdout, out.stderr))
    return (out.stdout or "").strip()


def php_serialize(levels):
    """按 flow_list 的结构生成 PHP serialize 字符串（直插回退时用）"""
    return _php(PHP_SERIALIZE, json.dumps(levels, ensure_ascii=False), "serialize", "json")


def php_unserialize(raw):
    """把库里的 flow_list 反解成 Python 对象（None 表示应用也解析不了）"""
    txt = _php(PHP_UNSERIALIZE, raw, "unserialize", "txt")
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 备份 / 快照
# ---------------------------------------------------------------------------
SNAP_TABLES = ("oa_flow", "oa_flow_cate", "oa_flow_step", "oa_flow_record")


def backup():
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = "%s/flow_backup_%s.sql" % (BACKUP_DIR, ts)
    exists = one_in_wsl("test -f %s && echo yes || echo no" % path)
    if exists == "yes":
        print("   备份已存在，不覆盖（只增不删）：%s" % path)
        return path
    cmd = "%s %s > %s 2>/tmp/oa_flow_dump.err; echo rc=$?; wc -c < %s" \
          % (oa_config.MYSQLDUMP, " ".join(SNAP_TABLES), path, path)
    out = one_in_wsl(cmd)
    print("   已备份：%s（%s）" % (path, out.replace("\n", " ")))
    return path


def one_in_wsl(cmd):
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", cmd],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=300)
    return ((out.stdout or "") + (out.stderr or "")).strip()


def snapshot_flows():
    """既有 oa_flow 的快照：id → [title, check_type, md5(flow_list)]

    ⚠️ 字段数必须**按位置容错**，不能写 `if len(r) < 4: continue`：
    run_sql() 会对整段输出 .strip()，若最后一行末尾的字段是空串（flow_list 为空即如此），
    该行会被 strip 掉尾部制表符 → 只剩 3 个字段 → 整行被丢掉，快照凭空少一条。
    （2026-09-19 实测踩到：id=19 的 flow_list 为空且是末行，快照里丢了它。）

    ⚠️ title 含中文，经 wsl.exe 回传会有编码损耗，故本快照只用于**行数/结构**比对；
    真正的"逐字节未变"判据用 digest_of_flows() —— 全 ASCII，不怕编码。
    """
    out = {}
    for r in rows("SELECT id, title, check_type, IFNULL(flow_list,'') FROM oa_flow "
                  "WHERE delete_time=0 ORDER BY id;"):
        if len(r) < 2:
            continue
        raw = r[3] if len(r) > 3 else ""
        out[r[0]] = [r[1], r[2] if len(r) > 2 else "",
                     hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]]
    return out


def digest_of_flows():
    """全部 oa_flow 行的一条整体指纹（id+title+check_type+flow_list 拼接后 md5）"""
    r = one("SELECT MD5(GROUP_CONCAT(CONCAT_WS('|', id, title, check_type, "
            "IFNULL(flow_list,''), status, delete_time) ORDER BY id SEPARATOR '#')) FROM oa_flow;")
    return r


# ---------------------------------------------------------------------------
# 建流程
# ---------------------------------------------------------------------------
def find_flow_by_title(title):
    return one("SELECT id FROM oa_flow WHERE title='%s' AND delete_time=0 ORDER BY id LIMIT 1;"
               % esc(title))


def create_via_api(sess, flow):
    """走真实后台接口 POST /adm/flow/add（与人工在界面上配流程同一条路径）"""
    n = len(flow["levels"])
    data = {
        "id": 0,
        "title": flow["title"],
        "cate_id": CATE_ID,
        "check_type": 2,                     # 固定审批流
        "department_ids": "",                # 空 = 全部部门
        "copy_uids": "",
        "remark": "SP-023~026 测试夹具，由 setup_flow_data.py 创建，回收见 cleanup_flow_fixture.py",
        "status": 1,
        "check_role[]": [lv["check_role"] for lv in flow["levels"]],
        "check_position_id[]": ["0"] * n,    # 指定成员时该字段不校验，给 0 以免空串进 int 列
        "check_uids_a[]": [lv["check_uids"] for lv in flow["levels"]],
        "check_types[]": [lv["check_types"] for lv in flow["levels"]],
    }
    r = sess.post(BASE_URL + "/adm/flow/add", data=data, timeout=30, allow_redirects=False)
    try:
        return r.json()
    except Exception:
        return {"_raw": (r.text or "")[:300], "_status": r.status_code}


def create_via_sql(flow):
    """回退路径：直插 oa_flow（flow_list 由 php serialize 生成，与 Flow.php:92 同格式）"""
    serialized = php_serialize(flow["levels"])
    sql = ("INSERT INTO oa_flow (title, cate_id, check_type, department_ids, copy_uids, "
           "flow_list, status, remark, admin_id, create_time, update_time, delete_time) VALUES "
           "('%s', %d, 2, '', '', '%s', 1, '%s', 1, UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 0);"
           % (esc(flow["title"]), CATE_ID, esc(serialized),
              esc("SP-023~026 测试夹具（直插），回收见 cleanup_flow_fixture.py")))
    run_sql(sql)
    return find_flow_by_title(flow["title"])


# ---------------------------------------------------------------------------
# 回读校验
# ---------------------------------------------------------------------------
def norm_uids(s):
    return ",".join(sorted(x for x in str(s or "").replace(" ", "").split(",") if x))


def verify_php_parse(flow, fid):
    """① PHP 侧 parse：库里的 flow_list 反解后逐项比对"""
    raw = one("SELECT IFNULL(flow_list,'') FROM oa_flow WHERE id=%d;" % fid, "")
    got = php_unserialize(raw)
    if not isinstance(got, list):
        fail("① %s（id=%d）flow_list 无法被 PHP unserialize 成数组：%r" % (flow["title"], fid, raw[:120]))
        return None
    if len(got) != len(flow["levels"]):
        fail("① %s 级别数 %d ≠ 预期 %d" % (flow["title"], len(got), len(flow["levels"])))
    for i, (g, e) in enumerate(zip(got, flow["levels"])):
        if int(g.get("check_role", -1)) != e["check_role"]:
            fail("① %s 第%d级 check_role=%s ≠ %s" % (flow["title"], i + 1, g.get("check_role"), e["check_role"]))
        if int(g.get("check_types", -1)) != e["check_types"]:
            fail("① %s 第%d级 check_types=%s ≠ %s（会签=1/或签=2）"
                 % (flow["title"], i + 1, g.get("check_types"), e["check_types"]))
        if norm_uids(g.get("check_uids")) != norm_uids(e["check_uids"]):
            fail("① %s 第%d级 check_uids=%s ≠ %s"
                 % (flow["title"], i + 1, g.get("check_uids"), e["check_uids"]))
    print("   ① PHP parse：%d 级，%s" % (len(got), json.dumps(got, ensure_ascii=False)))
    return got


def verify_app_parse(sess, flow, fid):
    """② 应用侧 parse：/api/check/get_flow_users 内部就是 unserialize(flow_list)"""
    r = sess.get(BASE_URL + "/api/check/get_flow_users",
                 params={"id": fid}, timeout=30, allow_redirects=False)
    try:
        j = r.json()
    except Exception:
        fail("② %s（id=%d）get_flow_users 非 JSON：HTTP %d %s"
             % (flow["title"], fid, r.status_code, (r.text or "")[:120]))
        return None
    if j.get("code") != 0:
        fail("② %s（id=%d）get_flow_users code=%s msg=%s"
             % (flow["title"], fid, j.get("code"), j.get("msg")))
        return None
    data = j.get("data") or {}
    got = data.get("flow_data")
    if not isinstance(got, list) or len(got) != len(flow["levels"]):
        fail("② %s（id=%d）应用解析出 %s 级，预期 %d 级"
             % (flow["title"], fid, len(got) if isinstance(got, list) else got, len(flow["levels"])))
        return None
    for i, (g, e) in enumerate(zip(got, flow["levels"])):
        if int(g.get("check_types", -1)) != e["check_types"]:
            fail("② %s 第%d级 check_types=%s ≠ %s"
                 % (flow["title"], i + 1, g.get("check_types"), e["check_types"]))
        if norm_uids(g.get("check_uids")) != norm_uids(e["check_uids"]):
            fail("② %s 第%d级 应用解析出的审批人=%s ≠ %s"
                 % (flow["title"], i + 1, norm_uids(g.get("check_uids")), norm_uids(e["check_uids"])))
    names = [str(u.get("name")) for lv in got for u in (lv.get("check_uids_info") or [])]
    print("   ② 应用 parse：%d 级，审批人 %s" % (len(got), names or "(未返回姓名)"))
    return got


def verify_api_list(sess, flows):
    """③ 后台流程列表接口能列出新流程"""
    r = sess.get(BASE_URL + "/adm/flow/datalist", timeout=30, allow_redirects=False)
    try:
        j = r.json()
    except Exception:
        fail("③ /adm/flow/datalist 非 JSON：HTTP %d %s" % (r.status_code, (r.text or "")[:120]))
        return
    if j.get("code") != 0:
        fail("③ /adm/flow/datalist code=%s msg=%s" % (j.get("code"), j.get("msg")))
        return
    listed = {}
    for it in (j.get("data") or []):
        listed[str(it.get("title"))] = it
    for f in flows:
        it = listed.get(f["title"])
        if it is None:
            fail("③ 流程列表中没有 %s" % f["title"])
        elif int(it.get("check_type", -1)) != 2:
            fail("③ %s 在列表里 check_type=%s ≠ 2" % (f["title"], it.get("check_type")))
    print("   ③ 流程列表共 %d 条，其中本次新建 %s"
          % (len(j.get("data") or []), [f["title"] for f in flows if f["title"] in listed]))


# ---------------------------------------------------------------------------
def main():
    apply = "--apply" in sys.argv
    print(SEP)
    print("勾股OA 6.1.2 —— 补配会签/或签固定审批流（SP-023~026 夹具）")
    print("模式：%s" % ("APPLY（真建）" if apply else "DRY-RUN（只看计划，不落库）"))
    print(SEP)

    n_flow = int(one("SELECT COUNT(*) FROM oa_flow;", "0") or 0)
    n_flow_live = int(one("SELECT COUNT(*) FROM oa_flow WHERE delete_time=0;", "0") or 0)
    print("落库前：oa_flow 共 %d 行（delete_time=0 的 %d 行）" % (n_flow, n_flow_live))
    print("        oa_flow_step %s 行 / oa_flow_record %s 行 / oa_msg %s 行"
          % (one("SELECT COUNT(*) FROM oa_flow_step;", "0"),
             one("SELECT COUNT(*) FROM oa_flow_record;", "0"),
             one("SELECT COUNT(*) FROM oa_msg;", "0")))
    # 幂等：已存在同名流程则复用，不重复建（先查，才能把基线算准）
    existing = {f["key"]: find_flow_by_title(f["title"]) for f in FLOWS}
    want_live = 19 + len([v for v in existing.values() if v])
    if n_flow_live != want_live:
        print("!! 在用流程数 %d ≠ 期望 %d（出厂 19 条%s）—— 请先确认环境状态再继续"
              % (n_flow_live, want_live,
                 " + 本夹具已有 %d 条" % (want_live - 19) if want_live > 19 else ""))

    cate = rows("SELECT id, name, title, check_table FROM oa_flow_cate WHERE id=%d;" % CATE_ID)
    if not cate or cate[0][1] != CATE_NAME:
        print("!! oa_flow_cate id=%d 不是「%s」，脚本中止（不猜分类）" % (CATE_ID, CATE_NAME))
        sys.exit(1)
    print("审批类型：oa_flow_cate id=%s name=%s check_table=%s"
          % (cate[0][0], cate[0][1], cate[0][3]))

    print("\n将新建的流程：")
    for f in FLOWS:
        print("  %s   check_type=2   %d 级" % (f["title"], len(f["levels"])))
        for i, lv in enumerate(f["levels"], 1):
            print("      第%d级  指定成员 uid=%s   同时有多人时=%s"
                  % (i, lv["check_uids"], "会签" if lv["check_types"] == 1 else "或签"))
        print("      用途：%s" % f["用途"])

    snap_before = snapshot_flows()
    digest_before = digest_of_flows()

    if any(existing.values()):
        print("\n!! 已存在同名流程，将**复用**（不重复建）：%s"
              % {k: v for k, v in existing.items() if v})
    if not apply:
        print("\n[DRY-RUN] 未落库、未备份。加 --apply 才真建。")
        return

    if all(existing.values()):
        print("\n两条流程均已存在，无需新建；继续做回读校验。")
        created = {k: int(v) for k, v in existing.items()}
        used = {k: "复用已有" for k in existing}
    else:
        print("\n[备份]")
        backup()
        sess = login(ADMIN, ADMIN_PWD)
        print("   已登录 %s（超管）" % ADMIN)

        created, used = {}, {}
        for f in FLOWS:
            if existing[f["key"]]:
                created[f["key"]] = int(existing[f["key"]])
                used[f["key"]] = "复用已有"
                continue
            res = create_via_api(sess, f)
            fid = None
            path = "接口 POST /adm/flow/add"
            if res.get("code") == 0:
                fid = find_flow_by_title(f["title"])
            else:
                print("   接口创建未成功（code=%s msg=%s），回退直插 —— 如实记录本次走的是直插路径"
                      % (res.get("code"), res.get("msg")))
                fid = create_via_sql(f)
                path = "回退直插 oa_flow"
            if not fid:
                print("!! %s 创建后仍查不到 id，中止" % f["title"])
                sys.exit(1)
            created[f["key"]] = int(fid)
            used[f["key"]] = path
            print("   已建：%s  id=%s  路径=%s" % (f["title"], fid, path))

    print("\n[回读校验]")
    sess = login(ADMIN, ADMIN_PWD)
    for f in FLOWS:
        fid = created[f["key"]]
        raw = one("SELECT CONCAT(check_type, '|', status, '|', delete_time, '|', cate_id) "
                  "FROM oa_flow WHERE id=%d;" % fid, "")
        print("   %s（id=%d）：check_type|status|delete_time|cate_id = %s" % (f["title"], fid, raw))
        if raw.split("|")[0] != "2":
            fail("%s check_type=%s ≠ 2（未落成固定审批流）" % (f["title"], raw.split("|")[0]))
        verify_php_parse(f, fid)
        verify_app_parse(sess, f, fid)
    verify_api_list(sess, FLOWS)

    snap_after = snapshot_flows()
    digest_after = digest_of_flows()
    print("\n[边界对象核对]")
    new_ids = set(snap_after) - set(snap_before)
    changed = [i for i in snap_before if i in snap_after and snap_before[i] != snap_after[i]]
    want_ids = {str(i) for i in created.values()}
    stray = new_ids - want_ids                      # 多出来的行 = 不是我们建的
    missing = want_ids - set(snap_after)            # 我们的流程反而查不到
    print("   既有流程被改动的条数：%d（期望 0）%s" % (len(changed), changed or ""))
    print("   本次比对新增的行 id：%s%s" % (sorted(new_ids) or "[]",
                                      "（复用路径：两条流程在快照前就已存在）" if not new_ids else ""))
    print("   两条夹具流程均在位：%s" % sorted(want_ids))
    if changed:
        fail("既有流程被改动：%s" % changed)
    if stray:
        fail("出现了非本脚本创建的新增流程行：%s" % sorted(stray))
    if missing:
        fail("夹具流程在 oa_flow 中查不到：%s" % sorted(missing))

    sidecar = {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "脚本": os.path.basename(__file__),
        "审批类型": {"cate_id": CATE_ID, "name": CATE_NAME, "check_table": "leaves"},
        "流程": [{"key": f["key"], "title": f["title"], "id": created[f["key"]],
                  "check_type": 2, "levels": f["levels"], "用途": f["用途"],
                  "创建路径": used.get(f["key"], "?")} for f in FLOWS],
        "oa_flow基线与现状": {
            "落库前行数": n_flow, "落库后行数": int(one("SELECT COUNT(*) FROM oa_flow;", "0") or 0),
            "既有19条快照": snap_before, "新增id": sorted(int(x) for x in new_ids),
            "整表指纹前": digest_before, "整表指纹后": digest_after,
        },
        "口径说明": [
            "会签=check_types 1（凑满本级人数才流转），或签=check_types 2（任一人通过即流转），"
            "判据见 app/api/controller/Check.php:414。",
            "【测试】或签流程第 2 级是刻意加的：用于把「落选者待办被取消」与「整单审批已结束」区分开。",
            "两条流程挂在既有 oa_flow_cate id=1「请假」上，未新建审批分类。",
            "本脚本只新增 oa_flow 行；产生的 oa_flow_step/oa_flow_record/oa_msg 由"
            " 接口自动化 pytest 执行时生成，回收见 cleanup_flow_fixture.py。",
        ],
    }
    with open(SIDECAR, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, ensure_ascii=False, indent=2)
    print("\n已写出：%s" % SIDECAR)

    print("\n" + SEP)
    if PROBLEMS:
        print("!! 回读校验未通过（%d 项），请勿继续执行用例：" % len(PROBLEMS))
        for p in PROBLEMS:
            print("   -", p)
        sys.exit(1)
    print("回读校验全部通过：2 条固定审批流已就绪，可执行接口自动化用例（pytest）")
    print(SEP)


if __name__ == "__main__":
    main()
