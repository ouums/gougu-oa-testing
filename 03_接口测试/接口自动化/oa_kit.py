# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 —— pytest 接口自动化 · 请求/判定/数据访问工具箱

【为什么要单独有这个文件】
  判定口径必须与私有一次性执行器**逐字一致**，
  否则 pytest 的结论会和已经回填进 Excel 的结论对不上，交付物自相矛盾。
  故这里的 9 个断言原语是从私有一次性执行器原样移植的，
  连 docstring 里的踩坑说明一并保留 —— 它们是"为什么这样判"的唯一权威记录。

【判定口径总纲】
  1. **一律解析响应体 JSON 的 `code`，不看 HTTP 状态码**。
     本系统 checkAuth() 失败走 to_assign(405,...) ⇒ HTTP 200 + body code=405。
     只看状态码会把"被权限拦截"读成"接口正常"，整批用例假通过。
  2. `code` 语义：0=成功 / 405=RBAC 拦截 / 1=通用业务错误（含"请先登录"）
  3. 拦截有三种合法形态：302 跳登录页、body code=405、body code=1 且 msg 含"请先登录"。
     **HTTP 500 不属于任何一类** —— 500 说明请求已越过闸门进到动作体内。
"""
import json
from contextlib import contextmanager

import requests

import setup_contract_data as SCD
from oa_login import BASE_URL as _DEFAULT_BASE, login, check_session
import oa_config  # noqa: E402

# Allure 是**可选**依赖：没装也要能跑（报告装饰不该成为跑用例的前提）
try:
    import allure as _allure
except ImportError:                                         # noqa: BLE001
    _allure = None

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
import os

BASE = oa_config.BASE_URL
ADMIN_PWD = oa_config.ADMIN_PWD
USER_PWD = oa_config.USER_PWD

# 账号表：用户名 -> 密码（uid 见 conftest 模块 docstring）
ACCOUNTS = {
    "admin": ADMIN_PWD,          # uid 1   超管
    "fengcailing": USER_PWD,     # uid 4   管理岗（组2）
    "yucixin": USER_PWD,         # uid 5   业务岗（组3）
    # qinjiaxian 是执行器 QX-033/034 指定的"攻击者"账号（position_id=4）。
    # 早期未登记，那两条用例只能自己 login() 建会话 —— 同一件事出现两种建法。
    "qinjiaxian": USER_PWD,      # uid 6   出厂账号，position_id=4（与 testmin 同职位）
    "testmin": USER_PWD,         # uid 108 最小权限（组4）—— 零权限对照
    "testemp": USER_PWD,         # uid 109 业务岗（组3）
    "teststaff": USER_PWD,       # uid 110 普通员工（组5）—— 薪资夹具构造
}

UID = {"admin": 1, "fengcailing": 4, "yucixin": 5, "qinjiaxian": 6,
       "testmin": 108, "testemp": 109, "teststaff": 110}

_SESS = {}


def get_session(username):
    """登录并做**鉴权自检**，按用户名缓存（每账号每轮只登录一次）。

    鉴权自检不可省：会话失效时受保护的读接口可能仍返回数据，用例会一路"假通过"；
    也可能全部返回"请先登录"而被误读成权限缺陷。两种误判都致命。
    """
    if username not in _SESS:
        if username not in ACCOUNTS:
            raise KeyError("未登记的账号：%s（可选：%s）" % (username, ", ".join(ACCOUNTS)))
        sess = login(username, ACCOUNTS[username])
        check_session(sess)
        _SESS[username] = sess
    return _SESS[username]


# 由 ACCOUNTS 派生，避免两处清单各自漂移（早期就是这么漏掉 qinjiaxian 的）
SESS_KINDS = list(ACCOUNTS)


# ---------------------------------------------------------------------------
# SQL（复用 setup_contract_data 里已验证的 wsl+base64 通道）
# ---------------------------------------------------------------------------
def run_sql(sql):
    return SCD.run_sql(sql)


def rows(sql):
    return SCD.rows(sql)


def scalar(sql, default="0"):
    r = rows(sql)
    return r[0][0] if r else default


# ---------------------------------------------------------------------------
# Allure 步骤
# ---------------------------------------------------------------------------
@contextmanager
def _step(title):
    """给一次请求 / 一次断言套一层 Allure 步骤；allure 未安装时是空操作。

    ⚠️ 退化分支必须**与 with 并列**，不能写成
        try:    with allure.step(t): yield
        except ImportError:         yield
    —— 那样用例体里抛出的 ImportError 会被这里吞掉，用例静默变绿。
    把"报告装饰"变成"假通过"是本项目的红线，宁可没有步骤。

    ⚠️ 步骤只在**用例执行期**有意义。会话夹具的回收器（pytest_cleanup）只用
    rows/run_sql、不走 call()，所以这里不存在"测试之外调 call 产生游离步骤"的情况。
    """
    if _allure is None:
        yield
    else:
        with _allure.step(title):
            yield


# 凭据类参数的键名子串。留档前打码 —— 报告是交付物，不能把口令原样印上去。
# 注意 `secrect`（BUG-04 里那个拼错的降级通道参数）**刻意不在列表内**：
# 它本身就是私有取证记录的物证，打码等于把缺陷证据抹掉。
_SENSITIVE_KEYS = ("password", "passwd", "pwd", "token", "secret")


def _mask_params(data):
    """请求参数留档前脱敏（按**键名**匹配，不看值）"""
    if isinstance(data, dict):
        return {k: ("***（已脱敏）" if any(s in str(k).lower() for s in _SENSITIVE_KEYS) else v)
                for k, v in data.items()}
    return data


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------
def call(sess, path, method="GET", data=None, timeout=30):
    """发一次请求，返回结构化结果。

    `sess=None` 表示**干净会话（未登录）** —— QX-051/052 这类"未登录调用"用例靠它。
    `allow_redirects=False` 是必需的：会话超时/未登录时会 302 跳登录页，
    跟随重定向会拿到登录页的 200，把"没登录"读成"访问成功"。

    每次调用在 Allure 里落成一个「请求 METHOD path」步骤，并把请求侧信息
    （method/path/参数，已脱敏）挂到 `res["_req"]` 供附件留档 —— 只留响应体的话，
    报告上看到 code=405 却不知道当时发了什么参数，证据链是断的。
    """
    method = method.upper()
    kw = dict(timeout=timeout, allow_redirects=False)
    with _step("请求 %s %s" % (method, path)):
        if sess is None:
            r = requests.request(method, BASE + path, data=data, **kw)
        elif method == "GET":
            # GET 的参数只能进查询串；body 会被服务端忽略
            r = sess.get(BASE + path, params=data, **kw)
        elif method == "POST":
            r = sess.post(BASE + path, data=data, **kw)
        else:
            # DELETE / PUT —— 本系统的删除接口是 `DELETE /contract/{contract,purchase}/del?id=N`，
            # 参数走查询串（与 GET 同），故这里用 params 而非 data。
            r = sess.request(method, BASE + path, params=data, **kw)

    res = {"http": r.status_code, "method": method, "path": path,
           "_req": {"method": method, "path": path, "params": _mask_params(data)}}
    if r.status_code in (301, 302):
        res["location"] = r.headers.get("Location", "")
        return res
    try:
        j = r.json()
        res["code"] = j.get("code")
        res["msg"] = str(j.get("msg", ""))
        if isinstance(j.get("count"), int):
            res["count"] = j["count"]
        if isinstance(j.get("data"), list):
            res["n"] = len(j["data"])
            res["data"] = j["data"]
        elif "data" in j and j["data"] is not None:
            # dict 之外还可能是**标量**（如 /api/index/get_codeno 的 data 就是编号字符串本身）。
            # 早期只搬运 list/dict，标量被整条丢掉，`res["data"]` 连键都不出现，
            # 用例只能绕开框架自己解析原始 JSON —— 同一份响应出现两种取法。
            res["data"] = j["data"]
    except Exception:                                       # noqa: BLE001
        # 非 JSON 响应（详情页 HTML、ThinkPHP 报错页…）：body_head 供快速看，
        # text 保留全文供子串检索 —— 早期只留 120 字符，导致详情页类断言无法做。
        text = r.text or ""
        res["body_head"] = text[:120].replace("\n", " ")
        res["text"] = text[:20000]
    return res


def brief(res):
    """把结构化结果渲染成一行可读的实测串（进断言消息与 Allure 报告）"""
    if res.get("http") in (301, 302):
        return "HTTP %s → %s" % (res["http"], res.get("location"))
    parts = ["HTTP %s" % res.get("http")]
    if "code" in res:
        parts.append("code=%s" % res.get("code"))
    if res.get("msg"):
        parts.append("msg=%s" % res["msg"])
    if "count" in res:
        parts.append("count=%s" % res["count"])
    if "n" in res:
        parts.append("n=%s" % res["n"])
    if res.get("body_head"):
        parts.append("body=%s" % res["body_head"])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 断言原语（移植自私有一次性执行器，每种返回 (bool, 实测描述)）
#   返回 (bool, desc) 而非直接 assert，是为了让用例能把实测串带进断言消息 ——
#   失败时报告里要能直接看到"服务端到底回了什么"。
# ---------------------------------------------------------------------------
def is_allowed(res):
    """正常放行：body code=0"""
    ok = res.get("code") == 0
    return ok, "code=%s msg=%s n=%s" % (res.get("code"), res.get("msg"), res.get("n"))


def is_denied(res):
    """被 RBAC 拦截：HTTP 200 + body code=405"""
    ok = res.get("http") == 200 and res.get("code") == 405
    return ok, "http=%s code=%s msg=%s" % (res.get("http"), res.get("code"), res.get("msg"))


def is_login_redirect(res):
    ok = res.get("http") in (301, 302) and "/home/login" in res.get("location", "")
    return ok, "http=%s location=%s" % (res.get("http"), res.get("location"))


def is_need_login(res):
    ok = res.get("code") == 1 and "请先登录" in res.get("msg", "")
    return ok, "code=%s msg=%s" % (res.get("code"), res.get("msg"))


def is_validation_error(res):
    """通过了鉴权、但被业务参数校验拦下（引号内证明"闸门放行了、是业务层拒绝的"）"""
    ok = res.get("http") == 200 and res.get("code") not in (405, None)
    return ok, "http=%s code=%s msg=%s" % (res.get("http"), res.get("code"), res.get("msg"))


def is_passed_gate(res):
    """用例的测试点 = "RBAC 闸门是否放行"，而非"业务层返回什么"。

    闸门在 initialize() 里、动作之前执行，所以**只要不是 405 / 不是跳登录页，
    就证明闸门放行了**——哪怕业务层随后 500（空载荷本就没有合法参数）。
    ⚠ 这里刻意不把 500 当作"通过"来粉饰：500 会在实测串里原样保留，
      并由用例打上"关联缺陷：参数校验缺失→异常放大为 500"的标记。
    """
    denied = res.get("code") == 405
    redirected = res.get("http") in (301, 302)
    ok = not denied and not redirected
    return ok, "http=%s code=%s msg=%s" % (res.get("http"), res.get("code"), res.get("msg"))


def is_blocked(res):
    """未授权访问的判定：被拦截的三种合法形态之外，一律算"没拦住"。

    三种合法拦截形态：302 跳登录页 / body code=405 无权限 / body code=1 请先登录。
    HTTP 500 不属于任何一类 —— 500 说明请求**已经越过闸门进到了动作体内**。

    ⚠ 失败串里必须带**响应体**。早期只报 `http=500 code=None`，看不到 body，
      定位 500 成因得自己再抓一次请求 —— 实测证据不该在报告里丢失。
    """
    if res.get("http") in (301, 302) and "/home/login" in res.get("location", ""):
        return True, "302 → %s" % res.get("location")
    if res.get("code") == 405:
        return True, "code=405 你没有权限"
    if res.get("code") == 1 and "请先登录" in res.get("msg", ""):
        return True, "code=1 请先登录"
    body = (res.get("text") or "")[:200].replace("\n", " ") or \
           (res.get("body_head") or "")[:200] or "(空响应体)"
    return False, "**未被拦截** http=%s code=%s msg=%s body=%s" % (
        res.get("http"), res.get("code"), res.get("msg"), body)


def is_superadmin_guard(res):
    """业务层显式守卫：api/Import.php:66 `if($this->uid>1) return to_assign(1,...)`"""
    ok = res.get("code") == 1 and "超级管理员" in res.get("msg", "")
    return ok, "code=%s msg=%s" % (res.get("code"), res.get("msg"))


def is_gate_absent(res):
    """测试点 = "该动作在 oa_admin_rule 中无权限节点，RBAC 无可校验项"。

    断言：未被 405 拦下（证明确实没有节点参与比对）。
    """
    ok = res.get("code") != 405
    return ok, "http=%s code=%s（未出现 405 → 无节点参与比对）" % (res.get("http"), res.get("code"))


# ---------------------------------------------------------------------------
# 断言助手：把 (bool, desc) 变成真断言，失败消息里带实测原文
# ---------------------------------------------------------------------------
def expect(pair, what):
    """pair=(bool, desc)。断言失败时消息里必须有实测原文，否则报告不可用。

    断言体包在 Allure 步骤里：失败时报告上直接看到**是哪一条判据红的**，
    而不是只有一行 traceback 行号。步骤名用 `what` 原文（本就是中文判据）。
    """
    with _step("断言 %s" % what):
        ok, desc = pair
        assert ok, "%s —— 实测：%s" % (what, desc)
        return desc


# ---------------------------------------------------------------------------
# Allure 附件采集
# ---------------------------------------------------------------------------
_RECORDS = []


def record(label, res):
    """登记一条请求详情，由 conftest 在用例收尾时挂进 Allure 报告。

    本套件的请求全部经 call()，**不用 requests 的隐式 hook** —— session 被多账号
    复用时 hook 的归属容易错乱，显式登记更可控。

    登记内容含**请求侧**（res["_req"]，call() 落的）与**响应侧**（detail/raw）：
    只留响应侧的话，报告上看到 code=405 却不知道当时发了什么参数，证据链是断的。
    """
    _RECORDS.append({"label": label, "detail": brief(res),
                     "req": res.get("_req") or {},
                     "raw": json.dumps(res, ensure_ascii=False, default=str)[:3000]})


def records():
    return list(_RECORDS)


def clear_records():
    _RECORDS.clear()


# ---------------------------------------------------------------------------
# 运行期数据登记：用例新建的每一条数据都要登记，供会话结束时按**显式 id** 精确回收
#
#   为什么不是"跑完用 LIKE 批量删"：项目已踩过坑 —— mysql 遇错即停不回滚，
#   且 LIKE 一旦写宽就会删掉别人（出厂）的数据。唯一被验证安全的姿势是
#   「显式 id + 分步回读核对」。
# ---------------------------------------------------------------------------
_CREATED = {}       # 表名 -> {id: 备注}


def track(table, rid, note=""):
    """登记一条本套件新建的数据。rid 为 None 时忽略（调用点不必写 if）。"""
    if rid is None:
        return None
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return None
    _CREATED.setdefault(table, {})[rid] = note
    return rid


def tracked():
    """返回 {表名: [id, ...]}（已排序，便于回读比对）"""
    return {t: sorted(ids) for t, ids in _CREATED.items() if ids}


def clear_tracked():
    _CREATED.clear()
