# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 —— 构造「薪资管理」测试所需的人设与夹具

【为什么必须新建人设 —— 现有账号一个都用不了】
  验证「员工侧工资条越权」（XZ-001~004）必须满足测试计划 §2.1 的 UI 对照法：
  只有「界面受限而接口不受限」才算确凿缺陷。但本实例的账号都不满足前提：

    账号              职位→角色组            薪资节点                  能否测越权
    admin(id=1)       1 → 组1 超级权限       216~222 全部 + 266,267    ❌ 超级管理员
    fengcailing(4)    2 → 组2 管理岗         216/217/218/220/221       ❌ 本就有权看全员薪资
                                              + 266,267（**缺 219、222**）
    yucixin(5)        3 → 组3 业务岗         216~222 全部 + 266,267    ❌ 同上
    testemp(109)      3 → 组3 业务岗         216~222 全部 + 266,267    ❌ 同上
    testmin(108)      4 → 组4 最小权限       仅 496                    ❌ /oa/salary/* 直接 405，够不到被测接口

  （以上为**实查 oa_admin_group.rules 并按逗号逐项比对**的结果，非凭印象。
     ⚠️ 一处已更正的错记：本节初稿把组 2/3 都写成「216~222」，实测**组 2 缺 219 与 222** ——
     组 2 只持 216/217/218/220/221。这不是细节：XZ-015（删除明细）首轮正是用 fengcailing 发起、
     因组 2 无 222（adm/salary/records_del）而被 405 拒绝，才改由组 3 的 yucixin 执行。
     组 4 的 rules 为 496（我的假勤），与薪资节点完全不相交。）
  ⇒ 必须造一个**仅授 266+267**（我的薪资-列表 / 我的薪资-详情）的「普通员工」人设。

【构造内容】
  角色组 5 「测试-普通员工角色（薪资）」  rules = "266,267"
  职位   5 「测试普通员工岗（薪资）」      → oa_position_group 映射到角色组 5
  账号   teststaff / <OA_USER_PWD>  (id=110)     职位 5，克隆 testemp(109) 的非关键字段 ⇒ did=3
                                          （与 testmin/testemp 同部门同级，排除"上级看下级"的正当性）
  批次 A id=1000  2026-08  status=1 已发放  明细 3 条（teststaff 110 / testmin 108 / testemp 109）
  批次 B id=1001  2026-07  status=0 待发放  → XZ-004
  批次 C~J id=1002~1009    status=0 待发放  → XZ-005~015 各自独占一个批次

【为什么每个用例独占一个批次】
  RecordsValidate.checkOne 的唯一性判据是 (salary_id, uid)，且 records_add/edit/del 都会触发
  update_salary() 重算该批次汇总。若多条用例共用一个批次，前一条用例写入（或本应被拒绝却写入）
  的数据会污染后一条用例的汇总期望值，失败时无法归因。独占批次让每条用例的期望值可独立复算。

【为什么夹具走直接 SQL，而不用接口】
  批次要处于「已发放」形态（oa_salary.status=1 且 oa_salary_records.status=1）。本产品中**唯一**
  能把状态置 1 的入口是 /adm/api/salary_pay（实测源码 app/adm/controller/Api.php:282），
  而该接口是本项目**明确禁止调用**的点：它不仅改状态，还会 event('SendMessage') 写站内信
  oa_msg、并经 FinanceLog 注入四条财务流水 —— 属对外发行为。
  ⇒ 夹具以 SQL **复现「已发放」的数据形态**；**发放动作本身不在本次范围内**，
     这一点会如实写进证据与存档，绝不表述为「已验证发放流程」。
  ⇒ 其余用例（XZ-005~015）的写入动作**全部走真实接口** /adm/salary/records_add 等，不直接 INSERT。

【幂等】先按**显式 id 列表**删除（不用 LIKE 批量删），再重建。
【前置】已备份权限五表 → <OA_HOME>/perm_backup_20260919_0128.sql（测试计划 §3.2）
【用法】
  python 03_接口测试\\setup_salary_data.py            # dry-run：只打印计划与现状
  python 03_接口测试\\setup_salary_data.py --apply    # 真正落库
【回收】cleanup_salary_data.py
"""
import argparse
import hashlib
import sys

import oa_config  # noqa: E402

# 通用封装（wsl+base64 传 SQL、会话缓存、uid 查询）直接复用合同模块脚本里已验证的实现，
# 避免两份实现漂移。它们与合同业务无关，只是基础设施。
from setup_contract_data import (  # noqa: F401
    api, ok, ret_id, rows, run_sql, sess_of, uid_of,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 人设
# ---------------------------------------------------------------------------
GROUP_ID = 5
POSITION_ID = 5
UID = 110                      # 必须显式指定：oa_admin 的 AUTO_INCREMENT=108 < MAX(id)=109，
                               # 不指定 id 会撞上 testmin(108) 的重复键
USERNAME = "teststaff"
PASSWORD = oa_config.USER_PWD
SALT = "ts7k2m9w"
TEMPLATE_ID = 109              # testemp —— 普通员工，did=3
GROUP_TITLE = "测试-普通员工角色（薪资）"
POSITION_TITLE = "测试普通员工岗（薪资）"
RULES = "266,267"              # 仅「我的薪资」列表 + 详情

# 同部门同事（批次 A 明细的 uid；只在这些账号下挂薪资明细，不修改账号本身）
PEER_UIDS = {"testmin": 108, "testemp": 109}

# ---------------------------------------------------------------------------
# 夹具：批次
#   status=1 已发放 / status=0 待发放；month_time 取月中旬 12:00，避免时区把月份挤到上/下月
# ---------------------------------------------------------------------------
BATCH = {
    "A": 1000,   # XZ-001~003  已发放
    "B": 1001,   # XZ-004      待发放
    "C": 1002,   # XZ-005      待发放
    "D": 1003,   # XZ-006
    "E": 1004,   # XZ-007
    "F": 1005,   # XZ-008
    "G": 1006,   # XZ-009
    "H": 1007,   # XZ-010
    "I": 1008,   # XZ-011~013
    "J": 1009,   # XZ-014~015
}
MONTH = {
    "A": "2026-08-15 12:00:00", "B": "2026-07-15 12:00:00", "C": "2026-06-15 12:00:00",
    "D": "2026-05-15 12:00:00", "E": "2026-04-15 12:00:00", "F": "2026-03-15 12:00:00",
    "G": "2026-02-15 12:00:00", "H": "2026-01-15 12:00:00", "I": "2025-12-15 12:00:00",
    "J": "2025-11-15 12:00:00",
}
# 批次 A 的汇总（= Σ实发 + Σ社保 + Σ公积金 + Σ个税，见 app/adm/model/SalaryRecords.php:141-150）
A_SUM_PAYMENT = "28580.00"
A_SUM_SOCIAL = "3650.00"
A_SUM_GONGJIJIN = "3650.00"
A_SUM_TAX = "1020.00"
A_AMOUNT = "36900.00"          # Decimal 复算：28580+3650+3650+1020

# 明细的 22 个金额列（顺序固定，供 _rec_sql 渲染）
AMOUNT_COLS = [
    "salary_basic", "salary_position", "salary_performance", "salary_quanqin",
    "salary_overwork", "salary_meal", "salary_phone", "salary_traffic",
    "salary_house", "salary_fuli", "salary_protecting", "salary_bonus",
    "deduct_belate", "deduct_leave", "deduct_absenteeism",
    "deduct_social", "deduct_gongjijin", "deduct_tax",
    "total_payable", "total_deduction", "total_statutory", "total_payment",
]

# 批次 A 的 3 条已发放明细
#   应发 = Σ12 项工资奖金；代扣代缴 = 社保+公积金+个税；实发 = 应发 - 应扣 - 代扣代缴
#   （与 records_add.html:150-180 的 JS 算式一致）
RECORDS_A = [
    dict(id=1000, uid=UID, bank="中国建设银行", bank_no="6217000000000001",
         v=dict(salary_basic="8000.00", salary_position="2000.00", salary_performance="1500.00",
                salary_quanqin="200.00", salary_overwork="0.00", salary_meal="300.00",
                salary_phone="100.00", salary_traffic="200.00", salary_house="0.00",
                salary_fuli="0.00", salary_protecting="0.00", salary_bonus="0.00",
                deduct_belate="0.00", deduct_leave="0.00", deduct_absenteeism="0.00",
                deduct_social="1200.00", deduct_gongjijin="1200.00", deduct_tax="345.00",
                total_payable="12300.00", total_deduction="0.00",
                total_statutory="2745.00", total_payment="9555.00")),
    dict(id=1001, uid=108, bank="中国工商银行", bank_no="6217000000000002",
         v=dict(salary_basic="7000.00", salary_position="1500.00", salary_performance="1000.00",
                salary_quanqin="200.00", salary_overwork="0.00", salary_meal="300.00",
                salary_phone="100.00", salary_traffic="200.00", salary_house="0.00",
                salary_fuli="0.00", salary_protecting="0.00", salary_bonus="0.00",
                deduct_belate="0.00", deduct_leave="0.00", deduct_absenteeism="0.00",
                deduct_social="1100.00", deduct_gongjijin="1100.00", deduct_tax="270.00",
                total_payable="10300.00", total_deduction="0.00",
                total_statutory="2470.00", total_payment="7830.00")),
    dict(id=1002, uid=109, bank="中国农业银行", bank_no="6217000000000003",
         v=dict(salary_basic="9000.00", salary_position="2500.00", salary_performance="2000.00",
                salary_quanqin="200.00", salary_overwork="0.00", salary_meal="300.00",
                salary_phone="100.00", salary_traffic="200.00", salary_house="0.00",
                salary_fuli="0.00", salary_protecting="0.00", salary_bonus="0.00",
                deduct_belate="0.00", deduct_leave="0.00", deduct_absenteeism="0.00",
                deduct_social="1350.00", deduct_gongjijin="1350.00", deduct_tax="405.00",
                total_payable="14300.00", total_deduction="0.00",
                total_statutory="3105.00", total_payment="11195.00")),
]
RECORD_IDS_A = [r["id"] for r in RECORDS_A]


# ---------------------------------------------------------------------------
def set_password(pwd, salt):
    """复刻 app/common.php 的加盐实现（与 setup_test_account.py 一致）"""
    return hashlib.md5(hashlib.md5((pwd + salt).encode()).hexdigest().encode()
                       + salt.encode()).hexdigest()


def _batch_sql():
    out = []
    for key, bid in sorted(BATCH.items(), key=lambda kv: kv[1]):
        issued = (key == "A")
        out.append(
            "INSERT INTO oa_salary (id, month_time, title, exclude_uids, enterprise_id, account_id,"
            " paytype_id, amount, salary, social, gongjijin, tax, status, pay_time, pay_uid,"
            " admin_id, check_uid, check_time, create_time, update_time, delete_time) VALUES ("
            "{bid}, UNIX_TIMESTAMP('{mon}'), '{title}', '', 0, 0, 0, {amount}, {salary}, {social},"
            " {gjj}, {tax}, {st}, {pay_time}, {pay_uid}, 1, 0, 0, UNIX_TIMESTAMP(), 0, 0);".format(
                bid=bid, mon=MONTH[key],
                title="【测试】%s工资单（%s）" % (MONTH[key][:7], "已发放" if issued else "待发放"),
                amount=A_AMOUNT if issued else "0.00",
                salary=A_SUM_PAYMENT if issued else "0.00",
                social=A_SUM_SOCIAL if issued else "0.00",
                gjj=A_SUM_GONGJIJIN if issued else "0.00",
                tax=A_SUM_TAX if issued else "0.00",
                st=1 if issued else 0,
                pay_time="UNIX_TIMESTAMP('2026-09-05 10:00:00')" if issued else 0,
                pay_uid=1 if issued else 0))
    return out


def _rec_sql(r):
    cols = ", ".join(AMOUNT_COLS)
    vals = ", ".join("'%s'" % r["v"][c] for c in AMOUNT_COLS)
    return (
        "INSERT INTO oa_salary_records (id, salary_id, uid, bank, bank_no, month_time, {cols},"
        " remark, status, pay_time, check_uid, check_time, create_time, update_time, delete_time)"
        " VALUES ({rid}, {sid}, {uid}, '{bank}', '{bank_no}',"
        " (SELECT month_time FROM oa_salary WHERE id = {sid}), {vals},"
        " '【测试】薪资模块夹具，由 setup_salary_data.py 直接构造，可回收',"
        " 1, UNIX_TIMESTAMP('2026-09-05 10:00:00'), 0, 0, UNIX_TIMESTAMP(), 0, 0);".format(
            cols=cols, vals=vals, rid=r["id"], sid=BATCH["A"], uid=r["uid"],
            bank=r["bank"], bank_no=r["bank_no"])
    )


def build_sql():
    parts = ["SET NAMES utf8mb4;",
             "-- 幂等清理：全部走显式 id 列表，不用 LIKE 批量删。",
             "-- 明细这一条同时按 salary_id 清 —— 上一轮执行器经接口在 B~J 批次建过的明细",
             "-- 若不清掉，会残留下来污染本轮各批次的汇总期望值。",
             "DELETE FROM oa_salary_records WHERE salary_id IN (%s) OR id IN (%s);"
             % (", ".join(str(v) for v in sorted(BATCH.values())),
                ", ".join(str(i) for i in RECORD_IDS_A)),
             "DELETE FROM oa_salary WHERE id IN (%s);"
             % ", ".join(str(v) for v in sorted(BATCH.values())),
             "DELETE FROM oa_admin WHERE username = '%s' OR id = %d;" % (USERNAME, UID),
             "DELETE FROM oa_position_group WHERE pid = %d OR group_id = %d;" % (POSITION_ID, GROUP_ID),
             "DELETE FROM oa_position WHERE id = %d;" % POSITION_ID,
             "DELETE FROM oa_admin_group WHERE id = %d;" % GROUP_ID,
             "",
             "-- 1. 角色组：仅 266 我的薪资-列表 + 267 我的薪资-详情",
             "INSERT INTO oa_admin_group (id, title, status, rules, layouts, mobile_bar, mobile_menu,"
             " `desc`, create_time, update_time) VALUES"
             " (%d, '%s', 1, '%s', '1,2,3,4,5,6,7,8,9,10,11,12,13', NULL, NULL,"
             " '自动化测试构造：仅含我的薪资节点，用于验证员工侧工资条越权',"
             " UNIX_TIMESTAMP(), 0);" % (GROUP_ID, GROUP_TITLE, RULES),
             "",
             "-- 2. 职位",
             "INSERT INTO oa_position (id, title, layouts, work_price, remark, status, create_time,"
             " update_time) VALUES (%d, '%s', NULL, 500, '自动化测试构造', 1, UNIX_TIMESTAMP(), 0);"
             % (POSITION_ID, POSITION_TITLE),
             "",
             "-- 3. 职位 → 角色组映射（账号到角色组的唯一通路）",
             "INSERT INTO oa_position_group (pid, group_id, create_time, update_time)"
             " VALUES (%d, %d, UNIX_TIMESTAMP(), 0);" % (POSITION_ID, GROUP_ID),
             ""]

    # 4. 账号：克隆 testemp 的非关键字段，显式 id
    parts.append("""-- 4. 账号（克隆模板账号非关键字段，覆盖身份与权限相关字段）
INSERT INTO oa_admin (
    id, userid, dingtalk_userid, username, pwd, salt, reg_pwd, name, email, mobile, sex, nickname,
    thumb, theme, did, pid, position_id, position_name, position_rank, type, is_staff, job_number,
    birthday, age, work_date, work_location, native_place, nation, home_address, current_address,
    contact, contact_mobile, resident_type, resident_place, graduate_school, graduate_day, political,
    marital_status, idcard, education, speciality, social_account, medical_account, provident_account,
    bank_account, bank_info, file_ids, `desc`, is_hide, entry_time, create_time, update_time,
    delete_time, last_login_time, login_num, last_login_ip, is_lock, auth_did, auth_dids, son_dids, status
)
SELECT
    %d, userid, dingtalk_userid, '%s', '%s', '%s', '', '薪资测试普通员工', 'teststaff@test.local',
    '13900000002', sex, '薪资测试普通员工',
    thumb, theme, did, pid, %d, 0, position_rank, type, is_staff, 'TESTSTAFF001',
    birthday, age, work_date, work_location, native_place, nation, home_address, current_address,
    contact, contact_mobile, resident_type, resident_place, graduate_school, graduate_day, political,
    marital_status, idcard, education, speciality, social_account, medical_account, provident_account,
    bank_account, bank_info, file_ids, '自动化测试构造的普通员工账号（仅我的薪资节点）', is_hide, entry_time,
    UNIX_TIMESTAMP(), 0, 0, 0, 0, '', 0, auth_did, auth_dids, son_dids, 1
FROM oa_admin WHERE id = %d;""" % (UID, USERNAME, set_password(PASSWORD, SALT), SALT,
                                   POSITION_ID, TEMPLATE_ID))
    parts.append("")

    # 5. 批次
    parts.append("-- 5. 工资批次（A 已发放；其余待发放，供各用例独占）")
    parts.extend(_batch_sql())
    parts.append("")

    # 6. 批次 A 的明细
    parts.append("-- 6. 批次 A 的 3 条已发放明细")
    parts.extend(_rec_sql(r) for r in RECORDS_A)
    parts.append("")

    # 7. 回读
    parts.append("""-- 7. 回读确认
SELECT '角色组' AS t, id, title, rules FROM oa_admin_group WHERE id = %d;
SELECT '职位' AS t, id, title FROM oa_position WHERE id = %d;
SELECT '映射' AS t, pid, group_id FROM oa_position_group WHERE pid = %d;
SELECT '账号' AS t, id, username, name, did, position_id, status FROM oa_admin WHERE id = %d;
SELECT '批次' AS t, id, DATE_FORMAT(FROM_UNIXTIME(month_time), '%%Y-%%m') AS mon, status, amount, salary, social, gongjijin, tax FROM oa_salary WHERE id BETWEEN %d AND %d ORDER BY id;
SELECT '明细' AS t, id, salary_id, uid, status, total_payment, deduct_social, deduct_gongjijin, deduct_tax, bank_no FROM oa_salary_records WHERE id IN (%s) ORDER BY id;"""
                 % (GROUP_ID, POSITION_ID, POSITION_ID, UID,
                    min(BATCH.values()), max(BATCH.values()),
                    ", ".join(str(i) for i in RECORD_IDS_A)))
    return "\n".join(parts)


def baseline():
    """夹具相关表的行数基线 + 既有账号未被改动的核对值"""
    keys = ["oa_salary", "oa_salary_records", "oa_admin_group", "oa_position",
            "oa_position_group", "oa_admin"]
    sql = ("SELECT %s;" % ", ".join("(SELECT COUNT(*) FROM %s)" % k for k in keys)
           + " SELECT id, username, position_id, status FROM oa_admin WHERE id IN (1,4,5,108,109)"
             " ORDER BY id;")
    txt = run_sql(sql)
    lines = [l for l in txt.splitlines() if l != ""]
    out = dict(zip(keys, lines[0].split("\t")))
    out["_accounts"] = [l.split("\t") for l in lines[1:]]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = ap.parse_args()

    print("=" * 78)
    print("勾股OA 薪资模块人设与夹具构造%s" % ("" if args.apply else "（dry-run，不写入）"))
    print("=" * 78)

    base = baseline()
    print("构造前基线：")
    for k in ("oa_salary", "oa_salary_records", "oa_admin_group", "oa_position",
              "oa_position_group", "oa_admin"):
        print("    %-20s = %s" % (k, base[k]))
    print("    既有账号核对（不应被改动）：")
    for a in base["_accounts"]:
        print("        id=%-4s %-12s position_id=%-3s status=%s" % tuple(a))

    print("-" * 78)
    print("将构造：")
    print("    角色组 %d  %s  rules=%s" % (GROUP_ID, GROUP_TITLE, RULES))
    print("    职位   %d  %s" % (POSITION_ID, POSITION_TITLE))
    print("    账号   %d  %s / %s  （克隆自 id=%d，did 随之）" % (UID, USERNAME, PASSWORD, TEMPLATE_ID))
    print("    批次   %d 个（id %d~%d）：A=2026-08 已发放；B~J 待发放，供用例独占"
          % (len(BATCH), min(BATCH.values()), max(BATCH.values())))
    print("    明细   %d 条（批次 A：uid %s）"
          % (len(RECORDS_A), "/".join(str(r["uid"]) for r in RECORDS_A)))
    print("    批次 A 汇总：amount=%s = 实发%s + 社保%s + 公积金%s + 个税%s"
          % (A_AMOUNT, A_SUM_PAYMENT, A_SUM_SOCIAL, A_SUM_GONGJIJIN, A_SUM_TAX))

    if not args.apply:
        print("-" * 78)
        print("dry-run 结束：未写入任何数据。加 --apply 执行。")
        print("=" * 78)
        return

    run_sql(build_sql())
    print("-" * 78)
    print("已落库。回读：")
    print(run_sql(build_sql().split("-- 7. 回读确认")[1].strip()) or "")
    after = baseline()
    print("构造后行数：")
    for k in ("oa_salary", "oa_salary_records", "oa_admin_group", "oa_position",
              "oa_position_group", "oa_admin"):
        print("    %-20s %s -> %s" % (k, base[k], after[k]))
    print("    既有账号核对（应与构造前逐字一致）：")
    for a in after["_accounts"]:
        print("        id=%-4s %-12s position_id=%-3s status=%s" % tuple(a))
    print("=" * 78)
    print("人设：%s / %s（uid=%d，仅节点 %s）" % (USERNAME, PASSWORD, UID, RULES))


if __name__ == "__main__":
    main()
