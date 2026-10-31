# -*- coding: utf-8 -*-
"""
勾股OA 6.1.2 本地部署 —— 通用登录模块（接口自动化 / SQL校验 / 性能测试共用）

【验证码处理说明】
被测系统登录强制图形验证码（think-captcha，服务端强校验），是本项目接口自动化的唯一障碍。
config/captcha.php 配置 math=true，验证码为算术题 "x + y = "，其中 x∈[10,30]、y∈[1,9]，
因此正确答案恒为 11~39 的整数（源码 vendor/topthink/think-captcha/src/Captcha.php:114-122）。
Captcha::check() 直接以 password_verify($code, $key) 比对明文，不做 md5。
=> 读取本地 PHP session 文件中存的 bcrypt 哈希，用 11~39 穷举即可在 1 次请求内还原验证码。

该方法仅适用于「本地部署、可读文件系统」的测试环境；对线上系统不适用（也不应使用）。
"""
import os
import re
import subprocess
import sys
import time

import requests

import oa_config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = oa_config.BASE_URL

# WSL 部署路径（由 oa_config 从环境变量推导，见 .env.example）
WSL_DISTRO = oa_config.WSL_DISTRO
WSL_SESSION_DIR = oa_config.SESSION_DIR
WIN_SESSION_DIR = oa_config.SESSION_DIR_WIN or (
    "\\\\wsl.localhost\\" + WSL_DISTRO + WSL_SESSION_DIR.replace("/", "\\")
)

CAPTCHA_MIN, CAPTCHA_MAX = 11, 39  # 算术验证码取值范围


def _read_captcha_hash(sid, retry=20, interval=0.15):
    """从本地 session 文件读取验证码的 bcrypt 哈希

    ThinkPHP 在请求结束时才把 session 落盘，/captcha 响应返回后文件可能尚未写入，
    故这里做重试（实测偶发 FileNotFoundError）。
    """
    path = os.path.join(WIN_SESSION_DIR, "sess_" + sid)
    raw = ""
    for _ in range(retry):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            if "captcha" in raw:
                break
        except FileNotFoundError:
            pass
        time.sleep(interval)
    m = re.search(r'captcha";a:\d+:\{s:3:"key";s:\d+:"([^"]+)"', raw)
    if not m:
        raise RuntimeError("session 文件中未找到 captcha key，内容：%s" % raw[:200])
    return m.group(1)


def _php_brute(hash_value):
    """用被测系统自身运行的 PHP 做 password_verify 穷举（语义与服务器完全一致）

    注意：PHP 代码不能直接经 wsl.exe 命令行传递，`$` 会被 Windows 参数解析吞掉
    （实测报 syntax error, unexpected token "="），故改用 base64 传输。
    """
    php_code = (
        "<?php $h='%s';for($i=%d;$i<=%d;$i++){if(password_verify((string)$i,$h)){echo $i;break;}}"
        % (hash_value, CAPTCHA_MIN, CAPTCHA_MAX)
    )
    import base64
    b64 = base64.b64encode(php_code.encode("utf-8")).decode("ascii")
    shell = "echo %s | base64 -d > /tmp/oa_brute.php && php /tmp/oa_brute.php; rm -f /tmp/oa_brute.php" % b64
    out = subprocess.run(
        ["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", shell],
        capture_output=True, text=True, timeout=60,
    )
    code = (out.stdout or "").strip()
    if not code.isdigit():
        raise RuntimeError("验证码穷举失败: stdout=%r stderr=%r" % (out.stdout, out.stderr))
    return code


def solve_captcha(sess):
    """取验证码图片 -> 读 session -> 还原出验证码明文"""
    r = sess.get(BASE_URL + "/captcha", timeout=10)
    r.raise_for_status()
    sid = sess.cookies.get("PHPSESSID")
    if not sid:
        raise RuntimeError("未拿到 PHPSESSID，cookie: %s" % sess.cookies)
    return _php_brute(_read_captcha_hash(sid))


def check_session(sess):
    """鉴权自检：确认会话真的被服务端认可

    必须做这一步——否则会话失效时，受保护的读接口可能仍返回数据，
    用例会一路"假通过"；也可能全部返回"请先登录"而被误读成权限缺陷。
    """
    r = sess.get(BASE_URL + "/home/index/index", timeout=15, allow_redirects=False)
    if r.status_code in (301, 302) or "/home/login" in r.headers.get("Location", ""):
        raise RuntimeError("会话自检失败：被重定向到登录页（会话未被服务端认可）")
    return True


def login(username, password, sess=None, verify=True):
    """登录并返回已带会话的 requests.Session（后续业务接口直接复用）"""
    sess = sess or requests.Session()
    captcha = solve_captcha(sess)
    r = sess.post(
        BASE_URL + "/home/login/login_submit",
        data={"username": username, "password": password, "captcha": captcha},
        timeout=15,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    res = r.json()
    if res.get("code") != 0:
        raise RuntimeError("登录失败(%s): %s" % (username, res.get("msg")))
    sess.headers.update({"X-Requested-With": "XMLHttpRequest"})
    if verify:
        check_session(sess)
    return sess


def whoami(sess):
    """返回当前登录账号信息（用于验证会话有效性）"""
    r = sess.get(BASE_URL + "/home/index/index", timeout=15)
    m = re.search(r'<title>(.*?)</title>', r.text, re.S)
    return {"status": r.status_code, "title": m.group(1).strip() if m else ""}


if __name__ == "__main__":
    user = sys.argv[1] if len(sys.argv) > 1 else "admin"
    pwd = sys.argv[2] if len(sys.argv) > 2 else oa_config.USER_PWD
    s = login(user, pwd)
    print("[OK] 登录成功: %s  session=%s" % (user, s.cookies.get("PHPSESSID")))
