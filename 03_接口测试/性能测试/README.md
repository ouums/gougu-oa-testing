# 性能测试 —— 核心读接口梯度压测（JMeter 5.6.3）

对本机部署实例的 **3 个核心读接口**做并发梯度压测，看曲线怎么走。

> **结论仅适用于本机实例。** 被测系统是**第三方开源产品**（勾股OA 6.1.2），
> 部署方式是 WSL2 Ubuntu + Nginx + PHP-FPM + MySQL **单机同盘** ——
> 应用、数据库、压测客户端共用一台机器的 CPU/内存/磁盘。
> 因此**任何绝对 TPS 数字都不具备迁移性**，本套压测**不设绝对阈值**，
> 判据只有一条：**加压后 TPS 与响应时间怎么变**。详见 `结果/性能测试结论.md`。

---

## 被测接口

| 接口 | 路径 | 说明 |
|---|---|---|
| 审批列表 | `GET /home/approve/checklist?page=1&limit=20&status=1` | 待我审批；`status=1` 才是「当前步骤审批人是我」 |
| 合同列表 | `GET /contract/contract/datalist?page=1&limit=20` | 销售合同数据表 |
| 员工列表 | `GET /user/user/index?page=1&limit=20` | `isAjax()` 分支，layui 表格 JSON；带 2 个 join + 次部门预载 + 逐行回调，是三个里查询最重的 |

并发梯度 **10 / 20 / 50**；每档爬坡 10 s + 稳态 60 s。

---

## 四个设计决定，以及各自的依据

### 1. 每线程绑定一个**独立会话** —— 这是建模选择，不是绕坑

先做了 `probe_session_lock.py`：PHP 原生 session 对文件**加独占锁**，同 PHPSESSID 的并发请求会被
串行化；若真如此，50 线程共用一个会话测出来的「并发」其实是排队，曲线会平得反常且**看不出来**。
ThinkPHP 用自己的 File 驱动（读 `file_get_contents`、写 `file_put_contents`），推测不加锁 —— 但压测
的地基不能建在推测上，于是实测三臂（同一只读接口，8 次请求）：

| 臂 | 配置 | 墙钟 | 单次中位 |
|---|---|---|---|
| A | 1 会话，**串行** | 291 ms | 35.2 ms |
| B | 1 会话，**并发** | **109 ms** | 74.7 ms |
| C | 8 会话，**并发** | **107 ms** | 72.4 ms |

B ≈ C ⇒ **同一 PHPSESSID 并发共用不会被串行化**。所以「每线程独立会话」不是被迫的，是主动选的：
50 个虚拟用户共用一个登录态不构成真实负载模型，而每个会话的 `RulesSrc{uid}` 权限缓存与
session 文件读写在多用户下才有代表性。**依据是实测，结论两头都站得住** —— 测出来是锁，就按锁设计；
测出来不是锁，就把它当成一次建模选择并说明理由。

### 2. 单接口单跑，三个接口**不在同一轮里混压**

混压时三个接口互相争抢 Nginx / PHP-FPM / MySQL，各自测出来的数字就不再是该接口自身的表现，
TPS 曲线也失去可比性。代价是 9 次运行而非 3 次，值得。

### 3. 指标只统计**稳态窗口**，丢弃爬坡段

爬坡期内并发还在往上加，那段的样本来自轻载服务器，计入会把结果**系统性地看得偏乐观**。
默认爬坡 10 s、总时长 70 s ⇒ 稳态窗口 60 s。
分位数用**最近秩法**（升序取 `ceil(p·n)` 位），不是插值 —— 报出来的数字都真实存在于样本里。

### 4. 判定口径 = 响应体 `code == 0`，**不看 HTTP 状态码**

与本项目功能用例逐字一致：本系统 RBAC 拦截返回 **HTTP 200 + `code=405`**，未登录返回
**HTTP 200 + `code=1`**。用 HTTP 状态码判失败会把「被拦截」当成「成功」，压测会把拦截页
当成正常业务反复压。JMeter 侧用 JSONPathAssertion 断言 `$.code == 0`。
反之 **HTTP 500 不是拦截** —— 它意味着请求已经穿过闸门进了 action 体，是真实的服务端错误。

---

## 文件

| 文件 | 作用 |
|---|---|
| `probe_session_lock.py` | **先跑这个**：会话并发共用实验，决定第 1 条设计 |
| `gen_sessions.py` | 预生成 N 个登录会话 → `sessions.csv`（默认 60，需 ≥ 最大并发） |
| `gougu_oa_read.jmx` | JMeter 测试计划（路径表与断言都在里面） |
| `run_perf.py` | 驱动：前置自检 → 9 次运行 → 解析 JTL → 出报告 |
| `clean_sessions.py` | 回收本次压测的会话文件（默认 dry-run；**不回收业务数据 —— 压测只读**） |
| `sessions.csv` | **生成物**，60 行 `account,sid`；可随时用 `gen_sessions.py` 重建 |
| `结果/` | `*.jtl`（JMeter 原始逐样本 CSV）+ `perf_results.csv` + `性能测试结论.md` |

## 结论摘要

> 完整版见 `结果/性能测试结论.md`。

**吞吐在 10 并发就已到顶**：三个接口 TPS 从 10 → 50 并发只涨 1.01~1.02 倍，
而平均响应时间**严格线性增长约 5 倍**。

| 接口 | TPS@10 | TPS@50 | 单请求服务时间 |
|---|---|---|---|
| 审批列表 | 93.49 | 95.01 | 52.6 ms |
| 合同列表 | 125.34 | 126.20 | 39.6 ms |
| 员工列表 | 284.50 | 287.00 | 17.4 ms |

根因经配置核实：**`pm.max_children = 5`**（PHP-FPM 只开 5 个进程，本机 12 核）。
把「5 个 worker」当**唯一参数**、只用并发 50 那一档反解出服务时间 S，
即可预测全部 9 个测量点，**最大偏差 2.7%**；Little 定律在 9 个点上也全部吻合（最大偏差 1.1%）。

⚠️ 但这是**部署配置**问题，**不是勾股OA 的产品缺陷** ——
那组 FPM 值是 Ubuntu 发行包默认值，`dpkg -V php8.2-fpm` 无输出证明配置文件从未被改动。
**应用自身**的信息是三个接口服务时间相差 3 倍（17.4 / 39.6 / 52.6 ms）。

## 怎么跑

```bat
cd 03_接口测试\性能测试
python probe_session_lock.py     :: 1. 先验证并发假设（约 5 s）
python gen_sessions.py           :: 2. 生成会话（60 个约 60 s）
python run_perf.py --smoke       :: 3. 冒烟：1 接口 × 2 并发 × 8 s
python run_perf.py               :: 4. 正式：9 次运行约 12 分钟
python run_perf.py --report-only :: 5. 只重出报告，不跑 JMeter
python clean_sessions.py         :: 6. 看会话残留（--apply 才真删）
```

`run_perf.py` 默认**跳过已存在的 `.jtl`**（断点续跑），`--force` 全部重跑。
跑完自动写出 `结果/性能测试结论.md` 与 `结果/perf_results.csv`。
`--fpm-workers N` 可改服务时间模型里的进程池宽度（默认 5）。

---

## 踩过的两个坑（都是「参数在命令行上」引起的）

1. **`&` 会被 cmd.exe 吃掉。** JMeter 的启动脚本是 `.bat`，参数要过一次 `cmd.exe`，而 `&` 在
   cmd 里是**命令分隔符**。首版用 `-Jpath=/home/approve/checklist?page=1&limit=20&status=1`，
   实测被截成 `?page=1`，另外两段变成 «'limit' 不是内部或外部命令» / «'status' …»，
   断言 **100% 失败**（响应 200 但 `code=1`）。
   ⇒ 现在命令行**只传 ASCII 标识与数字**（`-Jiface=` / `-Jthreads=` / `-Jramp=` / `-Jduration=`），
   接口路径表写在 JMX 内部的 JSR223 里。
2. **`FileServer.getBaseDir()` 是实例方法，不是静态方法。** Groovy 里写
   `FileServer.getBaseDir()` 报 `MissingMethodException`（提示里甚至列了 `getBaseDir()` 作为
   "possible solution"，很有迷惑性）。正确写法是走静态单例：
   `FileServer.getFileServer().getBaseDir()`。用它把 `sessions.csv` **相对 JMX 自身**解析，
   就不必把带中文的项目路径塞进命令行 —— 顺便躲开了 properties 文件只认 ISO-8859-1 的坑。

---

## 注意

- `sessions.csv` 里是 60 个**本机测试实例**的登录会话，仅对 `http://localhost:8080` 有效，
  且依赖 `oa_login.py` 的本地验证码还原方案（**仅适用于本地可读文件系统的测试环境，绝不用于线上系统**）。
- 压测**只读**：三个接口都是查询，不写库、不留测试数据，因此**不需要数据回收**。
  唯一残留是 WSL `runtime/session/` 下多出的 session 文件（等价于手工登录 60 次，目录已累积 308 个）。
  要清就 `clean_sessions.py --apply` —— 它**只删 `sessions.csv` 里逐个列出的那些 sid**，
  绝不用 `rm sess_*`：那会把浏览器里正在用的 admin 会话一起踢下线。
- 本目录的脚本**不**被 `接口自动化/conftest.py` 引用，是自成一体的套件 ——
  与 `接口自动化/` 平级，不是它的附属。
