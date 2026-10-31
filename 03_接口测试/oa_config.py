# -*- coding: utf-8 -*-
"""集中配置：账号、数据库、WSL 路径全部从环境变量读取。

优先级：真实环境变量 > 仓库根目录的 `.env` > 下面的默认值。
仓库里不保存任何真实口令；使用前把 `.env.example` 复制为 `.env` 并填写。
"""
import os


def _load_dotenv():
    """读取仓库根目录的 .env（存在才读，不依赖 python-dotenv）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()


def _env(name, default=""):
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# 被测系统
# ---------------------------------------------------------------------------
BASE_URL = _env("OA_BASE_URL", "http://localhost:8080")

# WSL 发行版名称；脚本通过 `wsl -d <发行版>` 执行 SQL / 读 session
WSL_DISTRO = _env("OA_WSL_DISTRO", "Ubuntu")

# OA 在 WSL 中的安装目录。session、备份、runtime 都在这下面。
OA_HOME = _env("OA_HOME", "/home/oa")

# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
DB_NAME = _env("OA_DB_NAME", "oa60")
DB_USER = _env("OA_DB_USER", "root")
DB_PASSWORD = _env("OA_DB_PASSWORD", "")


def _mysql(extra=""):
    parts = ["mysql", "-u" + DB_USER]
    if DB_PASSWORD:
        parts.append("-p" + DB_PASSWORD)
    if extra:
        parts.append(extra)
    parts.append(DB_NAME)
    return " ".join(parts)


def _mysqldump():
    parts = ["mysqldump", "-u" + DB_USER]
    if DB_PASSWORD:
        parts.append("-p" + DB_PASSWORD)
    parts.append(DB_NAME)
    return " ".join(parts)


MYSQL = _mysql()              # 带库名，供 `mysql < file` 使用
MYSQL_NB = _mysql("-N -B")    # 无表头、制表符分隔，供脚本解析
MYSQLDUMP = _mysqldump()      # 带库名，供 `mysqldump <库> <表...>` 使用

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
SESSION_DIR = _env("OA_SESSION_DIR", OA_HOME + "/office/runtime/session")
# Windows 侧访问同一目录的 UNC 路径；留空则按 WSL 发行版自动推导
SESSION_DIR_WIN = _env("OA_SESSION_DIR_WIN", "")
BACKUP_DIR = _env("OA_BACKUP_DIR", OA_HOME)

# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------
ADMIN_USER = _env("OA_ADMIN_USER", "admin")
ADMIN_PWD = _env("OA_ADMIN_PWD", "")
USER_PWD = _env("OA_USER_PWD", "")

# 演示数据自带账号（可按账号单独覆盖）
USER_ACCOUNTS = {
    "fengcailing": _env("OA_FENGCAILING_PWD", USER_PWD),
    "yucixin": _env("OA_YUCINXIN_PWD", USER_PWD),
    "qinjiaxian": _env("OA_QINJIAXIAN_PWD", USER_PWD),
}

# 由夹具脚本创建的测试账号（可按账号单独覆盖）
TEST_ACCOUNTS = {
    "testmin": _env("OA_TESTMIN_PWD", USER_PWD),
    "testemp": _env("OA_TESTEMP_PWD", USER_PWD),
    "teststaff": _env("OA_TESTSTAFF_PWD", USER_PWD),
}
