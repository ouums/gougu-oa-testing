# 勾股OA 6.1.2 接口自动化测试（pytest）

把 `02_测试用例/测试用例.xlsx` 里的 **135 条功能用例**（权限 55 / 审批流 35 / 合同 30 / 薪资 15）
用 pytest + requests 重新实现：**同一条用例、同一套判定口径、独立的真实断言**。

> 说明：项目最初用一次性执行器产出结果并回填 Excel；本仓库只发布**可重复的 pytest 版本**。
> 一次性执行器及其结果 JSON 属于私有交付物，未随仓库分发；本目录代码不依赖它们，
> 直接按「快速开始」安装依赖、配置环境即可运行。
> `oa_kit.py` 的断言口径沿用当时的规则：**一律解析响应体 JSON 的 `code`，不看 HTTP 状态码**。

---

## 快速开始

```bash
cd 03_接口测试/接口自动化

# 前置：WSL2 三服务必须在跑（WSL2 闲置会休眠，先唤醒再启服务）
wsl -d Ubuntu -u root -e systemctl start nginx php8.2-fpm mysql

# 首次运行：创建 pytest 依赖的最小权限账号 testmin/testemp 与角色组 4
python ../setup_test_account.py

# 跑全套（135 条）
python -m pytest

# 只跑某个模块
python -m pytest -m qx        # 权限体系
python -m pytest -m sp        # 审批流
python -m pytest -m ht        # 合同管理
python -m pytest -m xz        # 薪资管理

# 停在现场排查（跳过夹具回收与数据回收，库停在中间态）
python -m pytest --keep-fixtures

# Allure 只要失败证据（默认通过用例也附，见「呈现层」一节）
python -m pytest --allure-brief

# 看 Allure 报告 —— 必须带 ALLURE_NO_ANALYTICS=true，否则浏览器会卡死（原因见「呈现层」第 5 条）
#   PowerShell：
$env:ALLURE_NO_ANALYTICS='true'; allure serve allure-results
#   cmd：
set ALLURE_NO_ANALYTICS=true && allure serve allure-results

# 报告已经生成过时，直接服务现成目录即可（不重新生成，更快）
allure open allure-report

# 想要「双击就能打开」的单文件版（数据全部内联，无需服务、可单独发给别人）
#   PowerShell：$env:ALLURE_NO_ANALYTICS='true'; 再执行下面这行
allure generate allure-results -o <输出目录> --clean --single-file
```

> 公开仓库不提交 `allure-report/`、`allure-results/`：它们是本地生成物，附件含完整请求 / 响应。
> 仓库只保留 `results.xml` 摘要；需要看报告时在本地生成。

**生成 `results.xml`（交付凭证）**：`--junitxml` **故意不写进 `pytest.ini`** —— 它钉住交付周期
与数字口径，只能在定稿那一轮显式加，**复跑一律不加**（时间戳一变，全套周期口径作废）：

```bash
python -m pytest --junitxml=results.xml
```

### 运行前配置

所有脚本通过 `03_接口测试/oa_config.py` 读取配置。把仓库根目录的 `.env.example`
复制为 `.env`，填入本机实际值（`.env` 已在 `.gitignore` 中，不会提交）：

```powershell
# PowerShell
Copy-Item ..\..\.env.example ..\..\.env
```

```bash
# bash
cp ../../.env.example ../../.env
```

至少需要设置 `OA_HOME`、`OA_ADMIN_PWD`、`OA_USER_PWD`；数据库无口令时 `OA_DB_PASSWORD` 可留空。

---

## 本次执行结果（2026-09-21 定稿）

```bash
python -m pytest --junitxml=results.xml
```

| | |
|---|---|
| 用例总数 | **135**（权限 55 / 审批流 35 / 合同 30 / 薪资 15） |
| 通过 | **112** |
| 已知缺陷（`xfail(strict=True)`） | **23** |
| 失败 / 错误 | **0 / 0** |
| 墙钟耗时 | **120.9 s** |
| 执行时刻 | 2026-09-21 03:50:15 +08:00 |
| 交付凭证 | `results.xml`（JUnit XML）｜`allure-report/index.html`（HTML） |

### ⚠ 读 Allure 报告时注意：`xfail` 在报告里显示为 `skipped`，**不是「没跑」**

allure-pytest 把 `xfail` **一律记成 `skipped`**，所以 Allure 首页会写「23 skipped」。
这 23 条**全部执行过了**，是「按设计应当失败、实测确实失败」的已知缺陷。判据看
`statusDetails.message`，里面带着完整的「应有行为 vs 实测」原文：

```
XFAIL 缺陷三：删除职位后 oa_position_group 映射未回收…
AssertionError: 职位已删除 → 关联账号的员工列表权限应被回收（405）
       —— 实测：http=200 code=0 msg=
```

`results.xml` 里同样能区分：这 23 条带 **`type="pytest.xfail"`**，而真正的跳过会是
`type="pytest.skip"` —— 本套件**一条 `pytest.skip` 都没有**（见「判定口径」第 3 节：
skip 会丢掉失败证据，也掩盖「系统当前是坏的」这一事实）。

> 另有一处口径差异：Allure 显示的耗时（71.8 s）是**用例本体**时间之和，
> `results.xml` 的 **120.9 s** 才是含夹具重建/回收的**墙钟**时间。
> 对交付报周期口径，**用 120.9 s**。

### Allure 报告的呈现层（中文标题 / 功能 / 场景 / 级别 / 步骤 / 证据）

报告不是"跑完顺带生成的日志"，它是给人逐条复核用的，所以呈现层也是被设计过的：

| 报告上的位置 | 来源 | 说明 |
|---|---|---|
| 用例标题 | 用例 docstring 首行 | 如 `HT-001 新增销售合同（普通类型）`，**不再是 `test_ht_001_sales_contract_add`** |
| 功能（Features） | 用例所在文件 | 权限体系 / 审批流 / 合同管理 / 薪资管理 |
| 场景（Stories） | 源码里的 `# ====` 段落横幅 | 沿用作者自己的分组（22 段 → 短名 18 个，映射见 `conftest.STORY_ALIAS`） |
| 严重级别 | 用例表「优先级」列 | 高→critical / 中→normal / 低→minor，由 `gen_case_meta.py` 生成 `case_meta.py` |
| 步骤 | `oa_kit.call()` / `oa_kit.expect()` 自动落 | `请求 POST /contract/contract/add`、`断言 新增销售合同应保存成功（code=0）` |
| 附件 | `oa_kit.record()` 登记的每一次请求 | 一问一答：请求方法/路径/参数 + 实测串 + 响应体原文 |

四条设计约定：

1. **标签从既有事实推导，不手写装饰器。** 135 条 × 4 行 `@allure.xxx` 是 540 行复制粘贴，
   新增用例极易漏标。改成在 `pytest_collection_modifyitems` 里从 docstring / 所在文件 /
   段落横幅 / 用例表推导 —— 改一处不会漂移。
2. **证据**默认**通过用例也附**（`--allure-brief` 可回到"只在失败时附"）。
   早期只在失败时附，理由是"断言 405 拦截本就是预期结果，全附会把报告灌满"；
   实际拿报告复核时才发现代价更大：112 条通过的页面什么都不显示，
   而本项目的结论恰恰最需要**可复核**。单条用例最多附 12 条请求。
3. **附件类型必须是 `attachment_type.TEXT`。** allure-python-commons 2.16 的枚举里
   **没有 `MARKDOWN` 成员** —— 早期代码写的 `allure.attachment_type.MARKDOWN` 抛
   `AttributeError`，又被 `except Exception: pass` 吞掉，**失败附件其实从来没挂上去过**。
   也不用字符串 `"text/markdown"`：报告前端 bundle 里没有 markdown 渲染器，那只会变成下载链接。
4. **`__allure_display_name__` 在收集期写入。** allure 的 listener 是在
   `pytest_runtest_setup`（fixture 装配之后）才读它的，收集期写入来得及。
   ⚠️ 标题会经 allure 的 `SafeFormatter().format()`，**含花括号会被当占位符**；
   `_title_of()` 对这种首行直接退回函数名，不冒险。
5. **🔴 生成报告必须设 `ALLURE_NO_ANALYTICS=true`。** Allure 2.46.1 默认**往报告里注入
   Google Tag Manager 埋点**（模板 `tpl/index.html.ftl` 里的 `<#if analyticsDisable == false>` 分支），
   `allure generate`/`serve` **生成端自己**也会往 `google-analytics.com` 发一次（`GaPlugin`，10 秒超时）。
   本机网络是**代理分流**状态：`google-analytics.com` 通（0.43 s / 204），
   但 **`www.google.com` 是黑洞 —— 每次请求要干等 21.3 秒才失败**，而 GTM 会持续往它发心跳并重试。
   结果是报告页本身 0.4 s 就渲染完了，**整个标签页却会因为网络栈被这些"挂着不返回"的请求占满而失去响应**
   —— 表现为"打开 Allure 报告浏览器一直卡死"。
   设了 `ALLURE_NO_ANALYTICS=true` 后 `index.html` 里不再有该 `<script>`（实测命中数 1 → 0），
   控制台从"满屏 GA 报错"变为**零报错**。**这份报告是要给别人看的，不设的话对方一打开同样卡死。**
   ⚠️ 该变量是**环境变量**（`io.qameta.allure.Constants.ALLURE_NO_ANALYTICS`），**不是命令行参数**。

### 跑完之后的库状态（「夹具自包含」的验收点）

一条 `pytest` 跑完，夹具建立 → 用例执行 → 夹具回收全在同一会话内完成，库应回到基线。
定稿这一轮实测：

| 检查项 | 实测 |
|---|---|
| 薪资夹具残留（`teststaff` / 组 5 / 职位 5 / 批次 1000~1009 / 关联明细） | 全部 **0 行** ✓ |
| `oa_flow` 带 `【测试】` 标记的流程 | **0 行**（回到 19 行基线）✓ |
| 单据表 `oa_leaves` / `trips` / `outs` / `overtimes` | 28 / 2 / 2 / 2 ✓ |
| 合同表 `oa_contract` / `oa_purchase` | 58 / 10 ✓ |
| **勿清**清单：`testmin`(108) / `testemp`(109) / 零权限组(4) | 三者在位 ✓ |
| `oa_admin` 总数 | 106 = 出厂 104 + testmin + testemp ✓ |

### 交付物口径自检（内部脚本，未随仓库发布）

定稿前曾用项目内的只读脚本核对：用例表自洽、用例表与 pytest 模块一致、旧值残留、
缺陷口径等。该脚本依赖私有交付物（一次性执行器结果 JSON、截图目录），因此未包含在
本仓库。本仓库保留 `results.xml` 作为当轮执行的 JUnit 凭证。

---

## 目录

| 文件 | 作用 |
|---|---|
| `conftest.py` | fixture 装配：登录会话、环境前置自检、夹具编排、运行期数据回收、失败附件 |
| `oa_kit.py` | 请求封装 + **9 个断言原语** + SQL 访问 + 数据登记（判定口径的唯一出处） |
| `pytest_cleanup.py` | 运行期数据回收（破坏性代码，单独成文件以便审阅、可 dry-run） |
| `pytest.ini` | `xfail_strict=true`、markers 注册、Allure 输出 |
| `test_qx_permission.py` | 权限体系 55 条 |
| `test_sp_flow.py` | 审批流 35 条 |
| `test_ht_contract.py` | 合同管理 30 条 |
| `test_xz_salary.py` | 薪资管理 15 条 |
| `results.xml` | **交付凭证**：JUnit XML，钉住交付周期与数字口径（只在定稿那轮生成） |
| `allure-report/` | Allure HTML 报告（由 `allure-results/` 生成）。⚠️ **必须走 HTTP 服务打开**（`allure open allure-report`）；**`file://` 双击 `index.html` 不行** —— 报告数据是 `fetch()` 拉的，浏览器禁止从本地文件读，页面会停在 `Failed to fetch`。**能双击打开的单文件版见 `05_测试报告/勾股OA_Allure测试报告_单文件.html`** |
| `05_测试报告/勾股OA_Allure测试报告_单文件.html` | **同一份报告的单文件版**（约 5 MB，数据全内联，**双击即可打开、可直接发给别人**，无需装 Allure、无需起服务）。生成命令见上；**必须带 `ALLURE_NO_ANALYTICS=true`** |
| `allure-results/` | Allure 原始结果（含 `environment.properties`，报告首页「环境」栏的来源） |

---

## 判定口径（**改任何断言前先读这一节**）

### 1. 一律解析响应体 `code`，不看 HTTP 状态码

本系统 `checkAuth()` 失败走 `to_assign(405, ...)` ⇒ **HTTP 200 + body `code=405`**。
只看状态码会把"被权限拦截"读成"接口正常"，整批用例假通过。

| code | 含义 |
|---|---|
| `0` | 成功 |
| `405` | RBAC 拦截（你没有权限） |
| `1` | 通用业务错误（含 msg 为「请先登录」的会话失效） |

### 2. 未授权访问的「三种合法拦截形态」

302 跳登录页 / `code=405` / `code=1` 且 msg 含「请先登录」。
**HTTP 500 不属于任何一类** —— 500 说明请求已越过闸门进到了动作体内，是「没拦住」。

### 3. 三态结果，不混算

`通过 / 失败 / 待执行`。已知缺陷用例标 **`xfail(strict=True)`**，**不标 skip** ——
skip 丢掉失败证据，也掩盖「系统当前是坏的」这一事实；xfail 两者都保留。

`pytest.ini` 开 `xfail_strict=true` ⇒ 缺陷被修复后 xfail 用例会 **XPASS 并直接判 FAILED**，
xfail 因此是**活的回归探针**，而不是一句永不失效的注释。

### 4. 断言必须真的比较

取了两个数却从未比较、或写 `assert data is not None`（空列表也不是 None，恒真），
产出的是**假通过**——比失败更危险。凡「一致 / 相等 / 命中」类结论，代码里必须有真正的比较表达式。

### 5. ⚠️ 本机 WSL guest 时钟不同步 —— 时间类断言必须先排除它

本实例的 WSL guest 与 Windows 宿主**时钟不一致**：`timedatectl` 实测
`System clock synchronized: no`，宿主**快 ~146 秒**且会跳变
（2026-09-21 独立复验：宿主 `1789926125` vs guest `1789925979`，差 **146 秒**）。

后果：数据库时间戳是 PHP（guest 时钟）写的，**后写的行可能带更早的时间**。
全表实测 **12 组「id 升序而 `check_time` 反序」的行对，跳幅全部落在 144~147 秒**，
横跨 leaves / trips / contract / purchase。

⇒ **任何「时间升序 / 时间差 / 时间窗」断言都必须在时间回拨下仍然成立**，否则会偶发假红：

* **顺序判据以 `id` 升序为准**（id 单调，不受时钟影响）；
* 时间不单调时，**只接受 ≥60 秒的回拨**作为环境解释（本机实测跳幅 144~147s），
  **秒级反序一律判失败** —— 那更可能是真的逻辑错乱，不是时钟；
* 用**时间窗**筛数据尤其危险（会漏掉回拨期间写入的行）⇒ 回收一律**显式 id**，这也是本套件
  既有的规矩，此处再得一条独立理由。

> 这是**测试环境缺陷，不是产品缺陷**：上生产（NTP 正常）即不复现。故 SP-031 归因为环境，
> **不计入缺陷报告**；相关的产品观察（审批记录接口无 `ORDER BY`，`oa_flow_record` 仅有
> `PRIMARY(id)` 索引 ⇒ 时钟回拨时界面时间线显示倒序）另立观察项、按轻微计。

---

## 开发约定（新增或修改用例时遵守）

### 用例 → 测试函数的映射

* **一条用例 = 一个测试函数**，函数名 `test_<编号小写>_<英文短名>`，如 `test_qx_001_role_list_visible`。
* 模块级 `pytestmark = [pytest.mark.qx]` 打模块标记。
* 函数 docstring 写：用例标题 + 关联缺陷/证据 + 判据出处（源码位置或实测现象）。
* **数量必须精确等于用例数**（QX 55 / SP 35 / HT 30 / XZ 15），否则与 Excel 口径不符。

### 可用 API

```python
import oa_kit as K

# 请求：sess=None 表示干净会话（未登录），用于「未登录调用」类用例
res = K.call(sess_admin, "/home/role/index", method="GET", data={"page": 1})
# res = {"http":200, "code":0, "msg":"...", "n":3, "count":12, "data":[...]}
#       或 {"http":302, "location":"/home/login/index"}（未跟随重定向）

# 断言原语（返回 (bool, 实测串)，用 K.expect 变成真断言）
K.expect(K.is_denied(res), "零权限账号应被 405 拦截")     # 失败消息自动带实测原文
K.expect(K.is_allowed(res), "超管应正常看到角色组列表")

# 数据访问
K.rows("SELECT ...;")        # -> [[字段,...], ...]
K.scalar("SELECT COUNT(*) ...;")

# 登记本用例新建的数据（供会话结束自动回收，**新建就必须登记**）
K.track("oa_contract", new_id, note="HT-012 新建")

# 登记请求详情（只在用例失败时挂进 Allure 报告）
K.record("fengcailing POST /contract/contract/add", res)
```

### 可用 fixture

`conftest.py` 提供，直接写进函数签名即可：

| fixture | 说明 |
|---|---|
| `sess_admin` | 超管 uid=1 |
| `sess_fengcailing` | 管理岗 uid=4（组 2，**缺节点 219/222**） |
| `sess_yucixin` | 业务岗 uid=5（组 3） |
| `sess_qinjiaxian` | 出厂账号 uid=6，`position_id=4`（与 testmin 同职位）—— QX-033/034 的「攻击者」 |
| `sess_testmin` | 最小权限 uid=108（组 4）—— **零权限对照** |
| `sess_testemp` | 业务岗 uid=109（组 3） |
| `sess_teststaff` | 普通员工 uid=110（组 5，仅 266/267）—— 依赖 `fixture_salary` |
| `sess_of` | 按名取会话的工厂，需要多账号对照时用 |
| `fixture_salary` | 薪资夹具：自动重建 `teststaff`/组 5/职位 5/批次 1000~1009，跑完自动回收（`cleanup_salary_data.py`） |
| `fixture_flow` | 会签/或签夹具：自动重建 `oa_flow` id 20/21，跑完自动回收（`cleanup_flow_fixture.py`） |
| `fixture_contract` | 合同夹具（幂等，已在库则跳过；**刻意不挂 teardown** —— `HT-CASE-*` 与合同主数据属「勿清」清单，跨轮次保留） |

依赖薪资/会签夹具的用例，除函数签名外还要打标记：

```python
@pytest.mark.needs_salary_fixture
def test_xz_001_...(fixture_salary, sess_teststaff):
    ...
```

### 已知缺陷用例的写法

```python
@pytest.mark.known_defect
@pytest.mark.xfail(reason="BUG-17 合同金额与税点缺少服务端范围校验（一般 C）", strict=True)
def test_ht_012_amount_negative(sess_fengcailing):
    """HT-012 合同金额-负数：应被服务端范围校验拒绝，实测被接受（缺陷）。"""
    res = K.call(sess_fengcailing, "/contract/contract/add", data={... "cost": "-100"})
    K.record("...", res)
    # 这里写「**正确行为**」的断言 —— 缺陷存在时它会失败，xfail 把它记成 xfailed；
    # 缺陷修复后它会通过，strict 模式下变成 XPASS → FAILED，正好提醒回来销账。
    K.expect(K.is_validation_error(res), "负数金额应被服务端拒绝")
```

> ⚠️ **xfail 用例里的断言写「正确行为」，不写「当前错误行为」**。
> 写成后者的话，缺陷修复时用例反而变红，语义就反了。

### 数据回收

用例新建的每一条数据都必须 `K.track(表名, id)`，会话结束时 `pytest_cleanup.py` 会按
**显式 id + 分步回读**精确删除（明细行 → `oa_flow_record` → `oa_flow_step` → `oa_msg` → 单据表）。

**绝不用 `LIKE` 批量删**：`mysql` 命令行遇错即停且不回滚，`LIKE` 一旦写宽就会删掉出厂数据。

#### ⚠ **独立脚本（非 pytest 进程）建的数据没有回收入口**

`K.track()` 登记的 id 只对**同一个 pytest 会话**的收尾生效（`conftest._recycle_created_data`
在会话结束时统一回收）。**探针 / 一次性脚本里建的数据不会有人替你回收** ——
内存里登记了，进程一退就没了，库里那几行就一直留着。

实测踩过：一条探针脚本建的 `leaves 1316`（`【用例数据】SP-029-probe`）留在库里，
既不属 pytest 会话、也没人回收。**约定**：独立脚本若建了数据，必须自己显式回收：

```python
import pytest_cleanup
pytest_cleanup.cleanup({"oa_leaves": [1316]}, apply_=True)   # 显式 id，分步回读
```

同理，`【用例数据】SP-*` 这批跨多轮的残渣就是这么攒下来的（见挂起存档 §2.6 ⑥ 第 2(c) 项）。

#### ⚠ 夹具回收器 ≠ 一次性清理脚本：**夹具不在时必须是 rc=0 的空操作**

`conftest._recycle_fixture` 是在 `finally` 里跑的，脚本一非 0 退出就抛 RuntimeError，
**夹具随之留在库里**。而夹具建立失败时回收照样会跑 —— 所以回收器必须对「夹具不存在」
这一情形返回 rc=0，否则它会变成一个「夹具泄漏放大器」。

实测踩过的坑：`fixture_flow` 原先挂的是 `cleanup_flow_data.py`，那是**上一轮的一次性
执行器**，回收清单来自上一轮执行结果里的旧单据 id。那些单据早没了 ⇒
脚本 `sys.exit(2)` ⇒ 会话收尾 rc=2 ⇒ 抛异常 ⇒ `oa_flow` 20/21 泄漏，**每跑一轮多留一次**。

两条判定标准，写新回收器时逐条对：

| | 一次性清理脚本（`cleanup_*_data.py`） | 夹具回收器（`cleanup_*_fixture.py`） |
|---|---|---|
| 回收清单来源 | 上一轮执行结果里的单据 id | **按标记现查**（如 `oa_flow.title LIKE '【测试】%'`） |
| 目标不存在时 | 报错退出（rc=2，提醒你搞错了） | **rc=0 空操作**（本就不该在） |
| 幂等 | 不要求 | **必须**（`finally` 里反复跑） |
| 回读基准 | 本轮新增归零 | 回到**出厂/保留夹具基线**（`oa_flow` 19 行） |

`cleanup_flow_fixture.py` 还多两道闸：id 20/21 若存在却**不带**`【测试】`前缀 ⇒ 拒绝执行
（防误删生产流程）；既有 19 条流程逐条比对 `title`/`check_type`/`flow_list` 的 MD5，
必须逐字节未变。

> 📌 **回收器会为每次「真正执行删除」写一份备份**（`flow_fixture_backup_<时间戳>.sql`，
> 落在 WSL 的 `<OA_HOME>/`）。**空操作与 dry-run 都不写** —— 写备份那一步排在
> 「夹具不在就 return」和「dry-run 就 return」之后，所以干净的会话不会留下备份。
> 本项目备份**只增不删**，连续多轮回归会累积备份文件（截至 2026-09-21 收尾为 **28 份**），属预期而非泄漏；
> 同一分钟内跑两次会共用同一份（时间戳精确到分，且**已存在就不覆盖**）。

#### ⚠ 子表不能只按 `action_id` 定位 —— 它跨表不唯一

`action_id` 是**单据 id**，不同单据表的 id 区间**本来就重叠**。实测本实例：

| | id 区间 |
|---|---|
| `oa_leaves` | 1046 – 1227 |
| `oa_contract` | 1020 – 1336 |

⇒ ids 1131/1133 在**两张表里都真实存在**。只按 `action_id` 删子表，会删掉另一类单据的记录。
三个鉴别键（都是实测得来，不是推测）：

| 子表 | 鉴别键 | 实测取值 |
|---|---|---|
| `oa_flow_record` | **`check_table`** | `leaves` / `trips` / `outs` / `overtimes` / `contract` / `purchase` |
| `oa_flow_step` | 无 `check_table` 列 ⇒ 只能 `(action_id, flow_id)` 组对 | — |
| `oa_msg` | **`template`** | 20=请假 21=出差 22=外出 23=加班 **24=销售合同 25=采购合同** |

`oa_msg` 的实测反例：msg id=1537 的 `action_id=1022` / `template=23`（加班申请），
与合同 1022（`template=24`）同 id —— 只按 `action_id` 删会删掉别人单据的站内信。

**交叠不是边缘情况，是常态**（2026-09-21 实测，量级如下）：

| 观测 | 实测值 |
|---|---|
| `oa_leaves` ∩ `oa_contract` 共有的 id | **67 个** |
| 同一 `action_id` 在 `oa_flow_record` 里跨 `check_table` 的实例 | 遍地都是：**1013 同时有 outs / overtimes / purchase / trips 四类**，1006/1007/1009/1011/1015/1019/1022 等各 2~4 类 |
| `oa_flow_record` 现存行 / 其中软删（`delete_time<>0`） | 397 / **83** |

> ⚠️ 最后一行连着一条**产品行为**：`Check::get_flow_nodes()` 查 `oa_flow_record` **不过滤
> `delete_time`**（`app/api/controller/Check.php`，本套件 test_sp_flow.py 已两处记录）。
> 即**软删的旧记录仍会出现在审批历史里**。再叠加上面的跨表同号 ⇒ 若历史链只按
> `action_id` 取数而不限定 `check_table`，就会混进**别类单据的记录 + 软删旧记录**，
> 表现为「历史条数偏多、`check_time` 非升序」。SP-031 的 `ordered=False` 即按此线索排查。

> `oa_flow_step` 无法精确归属，因此 `pytest_cleanup` **单独打一份「同 id 但 flow_id 对不上」报表**
> 供人工判读；因 id 区间重叠，该报表**非 0 属正常**（那些是同 id 的其他类型单据）。
> 真正要警惕的是「某单据自己有 `check_flow_id`、却一行 step 都没配上」。

#### ⚠ 单据表名以 `information_schema` 为准

早期 `DOC_TABLES` 写的是 `oa_evection` / `oa_business_trip`，**这两个表根本不存在** ——
本实例的出差表叫 **`oa_trips`**。而 `_exists()` 是静默过滤的，于是 SP 模块登记的每一条
出差单**从未被回收且不报任何错**。现在登记了不存在的表会**显式报警**，不再静默。

#### 账号清单同理：`oa_kit.ACCOUNTS` 与 `conftest` 的 `sess_*` fixture 必须同步

`SESS_KINDS` 由 `ACCOUNTS` 派生，且 `conftest` 里有一条 `assert` 守卫：两边不一致就**直接导入失败**，
不会等到用例报一个看起来像「模块写错」的 `fixture not found`。

---
