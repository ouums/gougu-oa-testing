# 03_接口测试

接口层公共基础设施与两个执行入口：`接口自动化/`（pytest）与 `性能测试/`（JMeter）。

## 配置

所有脚本通过 `oa_config.py` 读取配置，优先级为：真实环境变量 > 仓库根目录 `.env` > 默认值。
仓库不保存真实口令；首次使用先复制 `.env.example` 为 `.env`。

| 变量 | 说明 |
|---|---|
| `OA_BASE_URL` | 被测系统地址，默认 `http://localhost:8080` |
| `OA_WSL_DISTRO` | WSL 发行版名称，默认 `Ubuntu` |
| `OA_HOME` | OA 在 WSL 中的安装目录，默认 `/home/oa` |
| `OA_DB_NAME` / `OA_DB_USER` / `OA_DB_PASSWORD` | 数据库连接 |
| `OA_ADMIN_PWD` / `OA_USER_PWD` | 超管 / 普通测试账号口令 |
| `OA_SESSION_DIR` / `OA_SESSION_DIR_WIN` | PHP session 目录（可选） |
| `OA_BACKUP_DIR` | 备份目录（可选，默认 `OA_HOME`） |

## 登录

`oa_login.py` 处理登录与验证码：

- 验证码是算术题，答案恒为 11~39；
- 本地部署下读取 PHP session 文件里的 bcrypt 哈希，用被测系统自身的 PHP 做
  `password_verify` 穷举；
- **仅适用于可读文件系统的本地测试环境，不适用于线上系统。**

`login()` 之后会调用 `check_session()` 做鉴权自检，避免会话失效时用例“假通过”。

## 夹具

| 脚本 | 作用 |
|---|---|
| `setup_test_account.py` | 创建最小权限角色组 4、职位 4、账号 `testmin` / `testemp` |
| `setup_contract_data.py` | 合同模块主数据 + 销售 / 采购合同 |
| `setup_salary_data.py` | 薪资角色组 5、职位 5、账号 `teststaff`、批次 1000~1009 |
| `setup_flow_data.py` | 会签 / 或签两条固定审批流（id 20/21） |
| `setup_document_data.py` | 假勤四模块单据 |
| `cleanup_*.py` | 对应回收脚本 |

约定：

- **默认 dry-run**，加 `--apply` 才真正写库 / 删库；
- 删除前先备份，备份只增不删；
- 回收按显式 id 或标记查询，不用 `LIKE` 批量删；
- `testmin(108)` / `testemp(109)` / 角色组 4 是 pytest 的环境前置对象，
  不作为测试垃圾清理。

## 两个入口

- `接口自动化/`：135 条用例的 pytest 实现，一条命令完成夹具搭建、执行与回收。
- `性能测试/`：JMeter 工程、会话生成、梯度压测驱动与结果汇总。

各自的使用方法见目录内 README。
