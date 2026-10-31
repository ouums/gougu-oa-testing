# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 —— pytest 接口自动化 · 公共装配（fixture 层）

本套件把 02_测试用例/测试用例.xlsx 里 135 条功能用例（权限 55 / 审批流 35 / 合同 30 /
薪资 15）用 pytest 重新实现一遍：**同一条用例、同一套判定口径、独立的真实断言**。
它不是把 run_functional_cases_*.py 的结果 JSON 读一遍打印出来 —— 那样是"假通过"，
断言必须真的发起请求、真的比较返回值（项目质量红线「断言必须真的比较」）。

【分层】请求与判定原语在 oa_kit.py（从 run_functional_cases.py 原样移植，口径逐字一致）；
        本文件只负责 fixture 装配：登录会话、环境前置自检、夹具编排、失败附件。

【xfail 策略】已知缺陷用例标 xfail(strict=True)，**不标 skip** ——
  skip 丢掉失败证据，也掩盖"系统当前是坏的"这一事实；xfail 两者都保留。
  pytest.ini 开 xfail_strict=true ⇒ 缺陷被修复后 xfail 用例会 XPASS 并直接判 FAILED，
  xfail 因此成为**活的回归探针**，而不是一句永不失效的注释。

【夹具策略：会话级自包含】
  薪资夹具（teststaff/组5/批次 1000~1009）与会签夹具（oa_flow id 20/21）上一轮已回收。
  本套件把它们包成 session 级 fixture：开跑自动重建，跑完自动回收并回读校验库回基线。
  一条 `pytest` 命令端到端可重复，不留残留。
  加 `--keep-fixtures` 可停在中间态排查（此时需手动跑对应 cleanup_*.py）。

【账号】按用例「前置条件」列指定，不统一用一个账号（统一了就看不出越权）：
  admin(1)        超管        组1
  fengcailing(4)  管理岗      组2   缺 219/222，故 XZ-015 改用 yucixin
  yucixin(5)      业务岗      组3
  qinjiaxian(6)   出厂账号    组4   执行器 QX-033/034 指定的"攻击者"（position_id=4）
  testmin(108)    最小权限    组4   **零权限对照，BUG-01 的语义承重对象**
  testemp(109)    业务岗      组3   与 teststaff 同部门同级（排除"上级看下级"的正当性）
  teststaff(110)  普通员工    组5   仅 266/267（我的薪资），由薪资夹具构造
"""
import json
import pathlib
import re
import subprocess
import sys
import warnings

import pytest

# ---------------------------------------------------------------------------
# 路径引导：复用 03_接口测试/ 下已验证的基础设施，不重复造轮子
#   oa_login            登录 + 验证码还原 + 会话自检
#   setup_contract_data  run_sql / rows / api（wsl+base64 传 SQL）
# ---------------------------------------------------------------------------
HERE = pathlib.Path(__file__).resolve().parent
SCRIPT_DIR = HERE.parent                      # 03_接口测试
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(HERE))

import oa_kit                                        # noqa: E402
from oa_kit import get_session, rows                 # noqa: E402


# ---------------------------------------------------------------------------
# 会话 fixture
# ---------------------------------------------------------------------------
def _sess_fixture(username):
    @pytest.fixture(scope="session", name="sess_%s" % username)
    def _f():
        return get_session(username)
    return _f


sess_admin = _sess_fixture("admin")
sess_fengcailing = _sess_fixture("fengcailing")
sess_yucixin = _sess_fixture("yucixin")
sess_qinjiaxian = _sess_fixture("qinjiaxian")
sess_testmin = _sess_fixture("testmin")
sess_testemp = _sess_fixture("testemp")
sess_teststaff = _sess_fixture("teststaff")

# 漂移守卫：oa_kit.ACCOUNTS 增了账号、这里忘了加 fixture 的话，
# 用例会以 "fixture not found" 报错而看起来像模块写错。直接在此断言，早失败。
#
# ⚠ 必须先物化成列表再断言。**不能**把遍历 globals() 的生成式写在 assert 里：
#   pytest 会重写 assert，把中间结果存成 `@py_assert0` 之类的名字 —— 而模块作用域的
#   "局部变量" 就是 globals() 本身，于是边遍历边写入 ⇒ RuntimeError:
#   dictionary changed size during iteration。踩过一次，勿改回去。
_FIXTURE_ACCOUNTS = sorted(n[len("sess_"):] for n in list(globals())
                           if n.startswith("sess_") and n != "sess_of")
assert sorted(oa_kit.ACCOUNTS) == _FIXTURE_ACCOUNTS, \
    "conftest 的 sess_* fixture 与 oa_kit.ACCOUNTS 不一致：%s vs %s" % (
        sorted(oa_kit.ACCOUNTS), _FIXTURE_ACCOUNTS)


@pytest.fixture(scope="session")
def sess_of():
    """按名取会话的工厂 —— 需要多账号对照的用例用它（如"换一条路径交叉验证"）"""
    return get_session


# ---------------------------------------------------------------------------
# 环境前置自检：不通过就终止整轮，不产出任何结果
#   （宁可 exit，也不要静默地跑出一堆因环境而失败的结果 —— 那是假阴性）
# ---------------------------------------------------------------------------
def _check_env():
    problems = []

    try:
        get_session("admin")
    except Exception as e:                                  # noqa: BLE001
        problems.append("admin 登录失败（WSL2 是否休眠？三服务是否 active？）：%s" % e)

    if problems:
        pytest.exit("环境前置自检失败：\n  - " + "\n  - ".join(problems), returncode=3)

    # 边界账号：出厂 104 + 刻意保留的 testmin(108) / testemp(109)，缺一即夹具被改动
    got = {r[0]: r[1] for r in rows("SELECT id, username FROM oa_admin WHERE id IN (104,108,109);")}
    for uid, name in (("104", "ceshiyuangong"), ("108", "testmin"), ("109", "testemp")):
        if got.get(uid) != name:
            problems.append("边界账号 oa_admin.id=%s 应为 %s，实测 %r（夹具或在库数据被改动）"
                            % (uid, name, got.get(uid)))

    # 零权限对照（组 4）—— BUG-01 的语义承重对象，缺了它会连累一批用例的结论
    if not rows("SELECT id FROM oa_admin_group WHERE id = 4;"):
        problems.append("角色组 4（测试-最小权限角色）不在库 —— BUG-01 的零权限对照缺失")

    if problems:
        pytest.exit("环境前置自检失败：\n  - " + "\n  - ".join(problems), returncode=3)


@pytest.fixture(scope="session", autouse=True)
def env_check():
    _check_env()
    return True


# ---------------------------------------------------------------------------
# 夹具编排：调用既有 setup_*.py / cleanup_*.py（不重写它们的逻辑）
# ---------------------------------------------------------------------------
def _run_script(name, *args):
    """以子进程方式跑既有夹具脚本 —— 保留其自带的计划打印、落库后回读校验、
    以及「删前自动 mysqldump 备份」等安全动作，比进程内 import 调用更不容易漏步骤。"""
    cmd = [sys.executable, str(SCRIPT_DIR / name)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900, cwd=str(SCRIPT_DIR))


def _apply_fixture(setup_script, verify):
    """建夹具，然后**查库核对**夹具真的落库了。

    verify 是返回 (bool, 描述) 的可调用对象，直接检查数据本身 ——
    早期版本靠匹配 setup 脚本 stdout 里的字符串，踩过两个坑：
      · 全角/半角括号不一致 ⇒ 子串永不命中：脚本 rc=0、数据也落库了，却仍被判失败，
        连带所有用例变 setup error、且夹具数据泄漏在库里；
      · 脚本在落库**之前**也会打印账号名，拿它当标记等于没验证。
    查库拿到的是不变量本身，不受脚本文案与编码影响。
    """
    out = _run_script(setup_script, "--apply")
    if out.returncode != 0:
        raise RuntimeError("%s --apply 失败（rc=%s）\n--- stdout ---\n%s\n--- stderr ---\n%s"
                           % (setup_script, out.returncode, out.stdout, out.stderr))
    ok, desc = verify()
    if not ok:
        raise RuntimeError("%s --apply 返回 rc=0，但查库核对未通过：%s\n--- stdout 尾部 ---\n%s"
                           % (setup_script, desc, (out.stdout or "")[-800:]))
    print("\n[夹具] %s 已就绪（查库核对：%s）" % (setup_script, desc))
    return True


def _recycle_fixture(cleanup_script, keep):
    """回收夹具。keep=True 时跳过（--keep-fixtures 排查用）。"""
    if keep:
        print("\n[夹具] --keep-fixtures 已指定，跳过 %s，库停在中间态，需手动回收" % cleanup_script)
        return
    out = _run_script(cleanup_script, "--apply")
    if out.returncode != 0:
        raise RuntimeError("%s --apply 回收失败（rc=%s）\n%s\n%s"
                           % (cleanup_script, out.returncode, out.stdout, out.stderr))
    print("\n[夹具] %s 已回收（尾部输出）：\n%s"
          % (cleanup_script, (out.stdout or "").strip()[-700:]))


def _fixture_lifecycle(request, name, setup_script, verify, cleanup_script):
    """夹具的统一生命周期：建 → yield → 回收。

    **建立动作也必须在 try 之内** —— 这一点是踩坑后改的。早期写成：

        _apply_fixture(...)          # ← 在 try 之外
        try:
            yield
        finally:
            _recycle(...)

    建立阶段一旦抛异常，`finally` 永远不执行，**已经落库的夹具数据就泄漏在库里**，
    同时 pytest 会把一批用例报成 setup error，看起来还像用例本身的问题。
    现在无论建立成功与否都尝试回收（回收脚本幂等，对空夹具是空操作）。
    """
    keep = request.config.getoption("--keep-fixtures")
    try:
        _apply_fixture(setup_script, verify)
        yield True
    finally:
        _recycle_fixture(cleanup_script, keep)


def _verify_salary():
    n_batch = rows("SELECT COUNT(*) FROM oa_salary WHERE id BETWEEN 1000 AND 1009;")
    n_rec = rows("SELECT COUNT(*) FROM oa_salary_records WHERE salary_id = 1000;")
    n_usr = rows("SELECT COUNT(*) FROM oa_admin WHERE id = 110;")
    g = lambda r: r[0][0] if r else "?"                      # noqa: E731
    got = (g(n_batch), g(n_rec), g(n_usr))
    return got == ("10", "3", "1"), \
        "批次 %s/10、批次A明细 %s/3、teststaff(110) %s/1" % got


def _verify_flow():
    """按**标题 + 类型**核对夹具，**不按固定 id**。

    早期写的是 `SELECT id ... WHERE id IN (20,21)` —— 20/21 是「夹具第一次被建出来时」
    的 id，**不是不变量**。夹具回收器把 20/21 删掉后，InnoDB 的 AUTO_INCREMENT
    **不会回退**，下一轮 `setup_flow_data.py` 建出来就是 22/23，于是这个核对判失败
    ⇒ 所有依赖会签夹具的用例整片变成 setup error（看着像模块写错，其实是夹具核对错了）。

    夹具核对必须查不变量。`setup_flow_data.py` 自己是按标题幂等复用的，核对口径与它对齐。
    """
    got = sorted((r[1], r[2]) for r in rows(
        "SELECT id, title, check_type FROM oa_flow "
        "WHERE title LIKE '【测试】%' AND delete_time = 0;"))
    want = sorted([("【测试】会签流程", "2"), ("【测试】或签流程", "2")])
    return got == want, "带【测试】前缀且未删的流程实测 %r" % (got,)


def _verify_contract():
    n = rows("SELECT COUNT(*) FROM oa_contract WHERE code LIKE 'HT-TEST-%';")
    n = n[0][0] if n else "0"
    return int(n) >= 1, "夹具合同 HT-TEST-* 共 %s 条" % n


@pytest.fixture(scope="session")
def fixture_salary(request):
    """薪资夹具：角色组 5 / 职位 5 / teststaff(110) / 批次 1000~1009 + 明细。

    走 SQL 直接构造「已发放」形态 —— 唯一能置 status=1 的入口 /adm/api/salary_pay
    是本项目**禁止调用**的点（会写站内信 + 注入财务流水，属对外发行为）。
    """
    yield from _fixture_lifecycle(request, "薪资夹具", "setup_salary_data.py",
                                  _verify_salary, "cleanup_salary_data.py")


@pytest.fixture(scope="session")
def fixture_flow(request):
    """会签 / 或签夹具：经真实后台接口建 2 条固定审批流（oa_flow id=20 会签 / 21 或签）。

    回收用 `cleanup_flow_fixture.py`（夹具回收器），**不是** `cleanup_flow_data.py`。
    后者是上一轮的一次性执行器：回收清单来自 `case_results_sp_flow.json` 里的旧单据 id，
    那些单据早已不存在 ⇒ 它直接 `sys.exit(2)`。而本 fixture 的回收是在 `finally` 里跑的，
    一旦非 0 退出，`_recycle_fixture` 抛 RuntimeError，**夹具流程就留在库里**——
    实测踩过一次：SP 模块会话收尾 rc=2 ⇒ oa_flow 20/21 泄漏，每跑一轮多留一次。
    回收器必须「夹具不在时是干净空操作」，`cleanup_flow_fixture.py` 就是按这条写的。
    """
    yield from _fixture_lifecycle(request, "会签夹具", "setup_flow_data.py",
                                  _verify_flow, "cleanup_flow_fixture.py")


@pytest.fixture(scope="session")
def fixture_contract():
    """合同模块夹具：主数据 + 夹具合同（按 code 判重，幂等，已在库则跳过）。

    **刻意不挂 teardown** —— 这不是遗漏，是设计：
      · `HT-CASE-*` 与合同主数据（客户/产品/服务/供应商）都在项目的「刻意保留夹具·
        勿清」清单里，跨轮次保留，本就不该被自动回收；
      · 建夹具是幂等的（按 code 判重），保留不会逐轮膨胀。
    `cleanup_contract_data.py` 是给**需要主动清场时**手工调用的（默认 dry-run，
    主数据还要额外加 `--with-master-data` 才动），不是本 fixture 的日常回收路径。
    """
    _apply_fixture("setup_contract_data.py", _verify_contract)
    return True


# ---------------------------------------------------------------------------
# 运行期数据回收：用例新建的数据经 oa_kit.track() 登记，会话结束时按显式 id 精确删除
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _recycle_created_data(request):
    """会话收尾统一回收本套件新建的单据及其审批子表。

    必须在所有用例跑完后执行（session 级 last），且**即使有用例失败也要跑** ——
    失败恰恰是最可能留下半成品数据的时候。
    加 --keep-fixtures 时跳过，便于停在现场排查。
    """
    yield
    keep = request.config.getoption("--keep-fixtures")
    if keep:
        print("\n[回收] --keep-fixtures 已指定，跳过运行期数据回收，库停在现场")
        return
    import pytest_cleanup
    print("\n" + "=" * 70)
    try:
        pytest_cleanup.cleanup(oa_kit.tracked(), apply_=True)
    except Exception as e:                                  # noqa: BLE001
        # 回收失败必须响亮地报出来：静默失败会留下污染数据，下一轮的结论就不可信了
        print("!! 运行期数据回收失败：%s" % e)
        raise
    finally:
        print("=" * 70)


# ---------------------------------------------------------------------------
# Allure 呈现层：中文标题 / 功能 / 场景 / 严重级别
#
#   135 条用例的「名字」从**既有事实**推导，不手抄 —— 三项来源：
#     · 标题     <- 用例 docstring 首行（135 条本来就用中文写好了首行）
#     · 功能     <- 用例所在文件（4 个模块）
#     · 场景     <- 源码里 `# ====` 段落横幅的首行标题（作者自己的分组）
#     · 严重级别 <- case_meta.SEVERITY（由 gen_case_meta.py 从用例表「优先级」列生成）
#
#   为什么不在每条用例上写 @allure.title/@allure.feature 装饰器：135 条 × 4 行
#   = 540 行复制粘贴，且新增用例极易漏标。从既有事实推导则改一处不漂移。
# ---------------------------------------------------------------------------
FEATURE_BY_FILE = {
    "test_qx_permission.py": "权限体系",
    "test_sp_flow.py": "审批流",
    "test_ht_contract.py": "合同管理",
    "test_xz_salary.py": "薪资管理",
}

# 段落横幅里带内部备注的几个，报告上换成短名（键=清洗后的横幅原文，值是报告用名）。
# 横幅原文刻意保留详细（"目标单据 oa_leaves id=1072"、"需要 fixture_flow…"）——
# 那是给读源码的人看的；报告读者要的是场景名。两者受众不同，故不直接改横幅。
STORY_ALIAS = {
    "账号权限按「职位 → 角色组」映射（含缺陷三）": "账号权限按「职位 → 角色组」映射",
    "审批动作越权（目标单据 oa_leaves id=1072，归属 uid=4）": "审批动作越权",
    "站内消息越权（app/home/controller/Message.php 四处无归属校验）": "站内消息越权",
    "会签 / 或签（需要 fixture_flow：自动重建 oa_flow 夹具流程，跑完自动回收）": "会签 / 或签",
}

_BANNER_OPEN = re.compile(r"^#\s*={10,}\s*$")
_DEF_TEST = re.compile(r"^def (test_\w+)")
_ID_RANGE = re.compile(r"^[A-Z]{2}-\d{3}(\s*[~/、]\s*[A-Z]{2}-\d{3})?\s*[:：]?\s*")
_ACCOUNT_NOTE = re.compile(r"[（(][^）)]*账号[^）)]*[）)]")
_ORDINAL = re.compile(r"^[a-z]\.\s*")
_TAIL_ID_RANGE = re.compile(r"[（(][A-Z]{2}-\d{3}\s*[~]\s*[A-Z]{2}-\d{3}[）)]\s*$")
_CASE_ID = re.compile(r"^([A-Z]{2}-\d{3})\b")
# docstring 首行尾部的台账标记（"（**预期结果已修订**）"）是给读源码的人看的，不进标题
_REVISION_NOTE = re.compile(r"[（(]\*\*[^）)]*\*\*[）)]\s*$")

try:                                                        # 报告装饰，缺了不该拦住跑用例
    from case_meta import SEVERITY as _SEVERITY
except ImportError:                                         # noqa: BLE001
    _SEVERITY = {}


def _title_of(doc):
    """用例 docstring 首行 -> 报告标题。实测首行有三种写法，逐段剥：

      "HT-001 新增销售合同（普通类型）：保存成功且字段正确落库、列表中可见。"
        先按「。」截到第一句，再按「：」截到判据之前  => "HT-001 新增销售合同（普通类型）"
      "QX-011 功能模块管理-超管可访问。账号 admin(1)；GET /home/module/index"
        「。」之后是账号/URL 备注，同样被截掉
      "QX-002 角色组列表-管理岗可查看（**预期结果已修订**）"
        尾部的「（**…**）」是台账标记，不是名字

    被截掉的判据与备注**没有丢** —— Allure 的 description 就是整段 docstring。
    """
    lines = (doc or "").strip().splitlines()
    first = lines[0].strip() if lines else ""
    if not first:
        return ""
    first = first.split("。")[0].strip()
    if "：" in first:
        first = first.split("：")[0].strip()
    first = _REVISION_NOTE.sub("", first).strip()
    # 标题会经 allure 的 SafeFormatter().format()，花括号会被当占位符。
    # 实测 135 条首行都不含花括号；这里只是把"万一"挡在报告外面（宁可用函数名）。
    return "" if ("{" in first or "}" in first) else first


_STORY_CACHE = {}
_ALIAS_CHECKED = [False]        # 别名自检每轮只做一次（可变容器，闭包里改得动）


def _story_of(path):
    """扫源码，返回 {用例函数名: 所属 `# ====` 段落横幅的中文标题}。

    报告里的「场景」直接沿用作者在源码里的分组，不另立一套口径 ——
    两套分组迟早对不上，而"报告说 A 场景、源码说 B 分组"没人会去发现。
    """
    if path not in _STORY_CACHE:
        m, cur = {}, None
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            if _BANNER_OPEN.match(ln):
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if nxt.startswith("#") and not _BANNER_OPEN.match(nxt):
                    cur = nxt.lstrip("#").strip()
                continue
            g = _DEF_TEST.match(ln)
            if g:
                s = _TAIL_ID_RANGE.sub("", _ORDINAL.sub("", _ACCOUNT_NOTE.sub(
                    "", _ID_RANGE.sub("", cur or "（未分组）"))))
                m[g.group(1)] = " ".join(s.split())
        _STORY_CACHE[path] = m
    return _STORY_CACHE[path]


def pytest_collection_modifyitems(config, items):
    """**收集期**给每条用例挂报告用的中文标题与标签。

    `__allure_display_name__` 必须在这里设：allure 的 listener 是在
    pytest_runtest_setup 里（fixture 装配之后）读它的，收集期写入来得及。
    """
    for item in items:
        fn = getattr(item, "obj", None)
        if fn is None or not item.name.startswith("test_"):
            continue

        title = _title_of(fn.__doc__)
        if title:
            fn.__allure_display_name__ = title

        path = str(getattr(item, "path", None) or item.fspath)
        feat = FEATURE_BY_FILE.get(pathlib.Path(path).name)
        if feat:
            item.add_marker(pytest.mark.allure_label(feat, label_type="feature"))

        story = _story_of(path).get(item.name)
        if story:
            item.add_marker(pytest.mark.allure_label(STORY_ALIAS.get(story, story),
                                                     label_type="story"))

        cid = _CASE_ID.match(title)
        lvl = _SEVERITY.get(cid.group(1)) if cid else None
        if lvl:
            item.add_marker(pytest.mark.allure_label(lvl, label_type="severity"))

    # 别名自检：必须拿**套件里所有用例文件**的横幅比对，不能拿本次收集到的 items ——
    # 跑单个文件（-k / 指定文件）时其余模块根本没被收集，按 items 比对会整片误报。
    if not _ALIAS_CHECKED[0]:
        _ALIAS_CHECKED[0] = True
        raw = set()
        for f in sorted(HERE.glob("test_*.py")):
            raw.update(_story_of(str(f)).values())
        unused = set(STORY_ALIAS) - raw
        if unused:
            warnings.warn("conftest.STORY_ALIAS 有 %d 条没命中（源码里的段落横幅文案被改过？）：%s"
                          % (len(unused), "、".join(sorted(unused))), stacklevel=1)


# ---------------------------------------------------------------------------
# 命令行选项
# ---------------------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption("--keep-fixtures", action="store_true", default=False,
                     help="跑完保留薪资/会签夹具（默认自动回收），用于停在中间态排查")
    parser.addoption("--allure-brief", action="store_true", default=False,
                     help="Allure 只在用例失败时附请求证据（默认通过用例也附，便于逐条复核）")


# ---------------------------------------------------------------------------
# 请求证据挂载
#
#   【为什么默认**通过用例也附**】早期版本只在失败时附，理由是"断言 405 拦截本就是
#   预期结果，全附会把报告灌满"。实际拿报告复核时才发现代价更大：135 条里 112 条
#   通过的页面**什么都不显示**，评审者没法逐条看"这条到底发了什么、服务端回了什么"——
#   而本项目的结论恰恰最需要"可复核"。故默认全附，`--allure-brief` 回到仅失败。
# ---------------------------------------------------------------------------
_ATTACH_LIMIT = 12      # 单条用例最多附几条请求，防个别用例把报告撑爆


def _attach_evidence(records, state):
    """把一条用例采集到的请求逐条挂成附件（每条一问一答，纯文本）。

    ⚠️ 附件类型只能用 `attachment_type.TEXT`（text/plain）。
      · `attachment_type` 里**没有 MARKDOWN 这个成员**（allure-python-commons 2.16
        的枚举只有 TEXT/JSON/XML/HTML/CSV/…）。早期本文件写的是
        `allure.attachment_type.MARKDOWN`，AttributeError 被 `except Exception: pass`
        吞掉 ⇒ **失败附件从来就没挂上去过**，而报告上看不出任何异常。改成 TEXT 后
        这个静默黑洞才算堵上；异常处理也改成打印出来，不再 pass。
      · 也不用 `"text/markdown"` 字符串：实测生成的报告前端 bundle 里没有任何
        markdown 渲染器，那个 MIME 只会变成一个下载链接。故正文按**纯文本**排版。

    只附响应体是不够的：看到 code=405 却不知道当时发了什么参数，证据链是断的，
    所以请求参数与响应体在**同一条**附件里成对出现。
    """
    import allure
    for i, r in enumerate(records[:_ATTACH_LIMIT], 1):
        q = r.get("req") or {}
        params = q.get("params")
        tag = "[%d] %s %s" % (i, q.get("method", "?"), q.get("path", "?"))
        body = "\n".join([
            "用例结论：%s" % state,
            "登记标签：%s" % r.get("label", ""),
            "请求方法：%s" % q.get("method", "?"),
            "请求路径：%s" % q.get("path", "?"),
            "请求参数：%s" % ("（无）" if params is None
                             else json.dumps(params, ensure_ascii=False, default=str)),
            "实测结果：%s" % r.get("detail", ""),
            "-" * 78,
            "响应体（oa_kit.call() 结构化结果，已截断至 3000 字符）：",
            r.get("raw", ""),
        ])
        allure.attach(body, name=tag, attachment_type=allure.attachment_type.TEXT)
    if len(records) > _ATTACH_LIMIT:
        allure.attach("另有 %d 条请求未附（单条用例只附前 %d 条）"
                      % (len(records) - _ATTACH_LIMIT, _ATTACH_LIMIT),
                      name="（截断说明）", attachment_type=allure.attachment_type.TEXT)


def _state_of(report):
    """用例结论的人话描述。xfail 要单独说 —— allure 把 xfail 记成 skipped，
    照搬 report.outcome 会把"已知缺陷、已执行且失败"写成"跳过"。"""
    if getattr(report, "wasxfail", None):
        return "XPASS（xfail 用例意外通过 —— 缺陷可能已修复）" if report.passed \
            else "xfail（已知缺陷，已执行且预期失败）"
    if report.passed:
        return "通过"
    if report.failed:
        return "失败"
    return "未通过（%s）" % report.outcome


@pytest.fixture(autouse=True)
def _clear_records():
    oa_kit.clear_records()
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """在 **call 阶段**挂附件 —— 此时用例对象尚未 close，附件才会归属**用例本体**，
    而不是折叠进 fixture 的 Tear down（那里恰恰是失败时最该看到的位置）。

    默认通过/失败都附（理由见上方注释）；`--allure-brief` 则只附没通过的。
    """
    outcome = yield
    report = outcome.get_result()
    records = oa_kit.records()
    if report.when != "call" or not records:
        return
    if item.config.getoption("--allure-brief") and report.passed \
            and not getattr(report, "wasxfail", None):
        return
    try:
        _attach_evidence(records, _state_of(report))
    except Exception as e:                                  # noqa: BLE001
        # 附件挂不上不该让用例变红（报告装饰不是判据），但必须留痕，不许静默
        print("\n[Allure] 请求证据挂载失败（不影响用例结论）：%s" % e)
