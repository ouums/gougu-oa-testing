# -*- coding: utf-8 -*-
"""
勾股OA —— 构造「最小权限」测试角色与账号（用于验证 API 模块的授权缺口）

背景：现有 3 个演示角色权限都配得很宽（业务岗 426 个节点、几乎全模块），
      用它们测不出 app/api/BaseController 只验登录不验权限这个结构性问题——
      因为界面上本来就对该角色开放。要证明"界面拒绝、接口放行"，必须有一个
      权限足够窄的账号。

本脚本构造：
  角色组 4  「测试-最小权限角色」  仅 1 个节点：496 oa/attendance/datalist 我的假勤
  职位   4  「测试最小权限岗」     → 映射到角色组 4
  账号      testmin / <OA_USER_PWD>       普通员工，与 yucixin 同部门

幂等：重复执行会先删除同名角色/职位/账号再重建。
用法：python 03_接口测试\\setup_test_account.py
"""
import base64
import hashlib
import subprocess
import sys

import oa_config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

WSL_DISTRO = oa_config.WSL_DISTRO
DB = oa_config.DB_NAME
MYSQL = oa_config.MYSQL

GROUP_ID = 4
POSITION_ID = 4
USERNAME = "testmin"
PASSWORD = oa_config.USER_PWD
SALT = "tmq7x2kw"
# 模板账号：克隆其非关键字段，保证账号结构完整（yucixin, id=5）
TEMPLATE_ID = 5
MINIMAL_RULES = "496"  # 仅「我的假勤」

# --- 同级对照账号（水平越权验证用）---
# yucixin 为 id=5 / 职位 3「普通员工岗」/ 角色组 3。水平越权要求攻击者与受害者
# **同角色同级**，才能排除"上级看下级"的正当性，故这里克隆出一个同岗同角色的同伴账号。
PEER_USERNAME = "testemp"
PEER_POSITION_ID = 3
PEER_SALT = "pe9k3mzt"


def set_password(pwd, salt):
    """复刻 app/common.php:930 的实现"""
    return hashlib.md5(hashlib.md5((pwd + salt).encode()).hexdigest().encode()
                       + salt.encode()).hexdigest()


def run_sql(sql):
    b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    shell = "echo %s | base64 -d > /tmp/oa_setup.sql && %s < /tmp/oa_setup.sql; rc=$?; rm -f /tmp/oa_setup.sql; exit $rc" % (b64, MYSQL)
    out = subprocess.run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", shell],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    # mysql 批处理模式下即使语句报错也可能返回 0，故只要 stderr 有 ERROR 就视为失败
    if out.returncode != 0 or "ERROR" in (out.stderr or ""):
        raise RuntimeError("SQL 执行失败:\nstdout=%s\nstderr=%s" % (out.stdout, out.stderr))
    return out.stdout


def main():
    pwd = set_password(PASSWORD, SALT)

    sql = """
SET NAMES utf8mb4;

-- 清理旧数据（幂等）
DELETE FROM oa_admin WHERE username = '{user}';
DELETE FROM oa_position_group WHERE pid = {pos} OR group_id = {grp};
DELETE FROM oa_position WHERE id = {pos};
DELETE FROM oa_admin_group WHERE id = {grp};

-- 1. 最小权限角色组
INSERT INTO oa_admin_group (id, title, status, rules, layouts, mobile_bar, mobile_menu, `desc`, create_time, update_time)
VALUES ({grp}, '测试-最小权限角色', 1, '{rules}', '1,2,3,4,5,6,7,8,9,10,11,12,13', NULL, NULL,
        '自动化测试构造：仅含我的假勤节点，用于验证接口越权', UNIX_TIMESTAMP(), 0);

-- 2. 职位
INSERT INTO oa_position (id, title, layouts, work_price, remark, status, create_time, update_time)
VALUES ({pos}, '测试最小权限岗', NULL, 500, '自动化测试构造', 1, UNIX_TIMESTAMP(), 0);

-- 3. 职位 -> 角色映射
INSERT INTO oa_position_group (pid, group_id, create_time, update_time)
VALUES ({pos}, {grp}, UNIX_TIMESTAMP(), 0);

-- 4. 账号（克隆模板账号的非关键字段，覆盖身份与权限相关字段）
INSERT INTO oa_admin (
    userid, dingtalk_userid, username, pwd, salt, reg_pwd, name, email, mobile, sex, nickname,
    thumb, theme, did, pid, position_id, position_name, position_rank, type, is_staff, job_number,
    birthday, age, work_date, work_location, native_place, nation, home_address, current_address,
    contact, contact_mobile, resident_type, resident_place, graduate_school, graduate_day, political,
    marital_status, idcard, education, speciality, social_account, medical_account, provident_account,
    bank_account, bank_info, file_ids, `desc`, is_hide, entry_time, create_time, update_time,
    delete_time, last_login_time, login_num, last_login_ip, is_lock, auth_did, auth_dids, son_dids, status
)
SELECT
    userid, dingtalk_userid, '{user}', '{pwd}', '{salt}', '', '最小权限测试员', 'testmin@test.local',
    '13900000000', sex, '最小权限测试员',
    thumb, theme, did, pid, {pos}, 0, position_rank, type, is_staff, 'TESTMIN001',
    birthday, age, work_date, work_location, native_place, nation, home_address, current_address,
    contact, contact_mobile, resident_type, resident_place, graduate_school, graduate_day, political,
    marital_status, idcard, education, speciality, social_account, medical_account, provident_account,
    bank_account, bank_info, file_ids, '自动化测试构造的最小权限账号', is_hide, entry_time,
    UNIX_TIMESTAMP(), 0, 0, 0, 0, '', 0, auth_did, auth_dids, son_dids, 1
FROM oa_admin WHERE id = {tpl};

-- 5. 同级对照账号 testemp（与 yucixin 同岗同角色，不新建角色/职位）
DELETE FROM oa_admin WHERE username = '{user2}';
INSERT INTO oa_admin (
    userid, dingtalk_userid, username, pwd, salt, reg_pwd, name, email, mobile, sex, nickname,
    thumb, theme, did, pid, position_id, position_name, position_rank, type, is_staff, job_number,
    birthday, age, work_date, work_location, native_place, nation, home_address, current_address,
    contact, contact_mobile, resident_type, resident_place, graduate_school, graduate_day, political,
    marital_status, idcard, education, speciality, social_account, medical_account, provident_account,
    bank_account, bank_info, file_ids, `desc`, is_hide, entry_time, create_time, update_time,
    delete_time, last_login_time, login_num, last_login_ip, is_lock, auth_did, auth_dids, son_dids, status
)
SELECT
    userid, dingtalk_userid, '{user2}', '{pwd2}', '{salt2}', '', '同级对照测试员', 'testemp@test.local',
    '13900000001', sex, '同级对照测试员',
    thumb, theme, did, pid, {pos2}, 0, position_rank, type, is_staff, 'TESTEMP001',
    birthday, age, work_date, work_location, native_place, nation, home_address, current_address,
    contact, contact_mobile, resident_type, resident_place, graduate_school, graduate_day, political,
    marital_status, idcard, education, speciality, social_account, medical_account, provident_account,
    bank_account, bank_info, file_ids, '自动化测试构造：与 yucixin 同岗同级，用于验证水平越权', is_hide, entry_time,
    UNIX_TIMESTAMP(), 0, 0, 0, 0, '', 0, auth_did, auth_dids, son_dids, 1
FROM oa_admin WHERE id = {tpl};

-- 6. 回读确认
SELECT '角色组:' AS t, id, title, rules FROM oa_admin_group WHERE id = {grp};
SELECT '职位:' AS t, id, title FROM oa_position WHERE id = {pos};
SELECT '映射:' AS t, pid, group_id FROM oa_position_group WHERE pid = {pos};
SELECT '账号:' AS t, id, username, name, did, position_id, status FROM oa_admin WHERE username = '{user}';
SELECT '同伴账号:' AS t, id, username, name, did, position_id, status FROM oa_admin WHERE username = '{user2}';
""".format(user=USERNAME, pwd=pwd, salt=SALT, grp=GROUP_ID, pos=POSITION_ID,
           rules=MINIMAL_RULES, tpl=TEMPLATE_ID,
           user2=PEER_USERNAME, pwd2=set_password(PASSWORD, PEER_SALT), salt2=PEER_SALT,
           pos2=PEER_POSITION_ID)

    print("密码哈希 pwd(testmin) = %s" % pwd)
    print("密码哈希 pwd(testemp) = %s" % set_password(PASSWORD, PEER_SALT))
    run_sql(sql)
    print("已构造完成：")
    print("  · 最小权限账号 %s / %s （职位 %d，角色组 %d，rules=%s）"
          % (USERNAME, PASSWORD, POSITION_ID, GROUP_ID, MINIMAL_RULES))
    print("  · 同级对照账号 %s / %s （职位 %d，与 yucixin 同岗同角色）"
          % (PEER_USERNAME, PASSWORD, PEER_POSITION_ID))


if __name__ == "__main__":
    main()
