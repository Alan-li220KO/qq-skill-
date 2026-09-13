"""以独立二维码窗口启动并登录本机 NapCat-QCE，不显示启动终端。"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "vendor"))
sys.path.insert(0, str(SCRIPT_DIR))
import qce_env  # noqa: E402
import qrcode  # noqa: E402


def post_json(url, body, headers=None):
    request = Request(url, data=json.dumps(body).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    with urlopen(request, timeout=3) as response:
        payload = json.loads(response.read().decode())
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message") or "NapCat API 请求失败")
    return payload.get("data") or {}


def credential(qce_root, api_base):
    config = json.loads((qce_root / "config" / "webui.json").read_text(encoding="utf-8"))
    token = config.get("token")
    if not token:
        raise RuntimeError("未找到 NapCat WebUI 令牌")
    digest = hashlib.sha256(f"{token}.napcat".encode()).hexdigest()
    data = post_json(f"{api_base}/api/auth/login", {"hash": digest})
    value = data.get("Credential")
    if not value:
        raise RuntimeError("未取得 NapCat 临时凭证")
    return {"Authorization": f"Bearer {value}"}


def launch_hidden(qce_root):
    launcher = qce_root / "launcher-user.bat"
    if not launcher.is_file():
        raise RuntimeError(f"未找到启动器：{launcher}")
    subprocess.Popen(
        ["cmd.exe", "/d", "/c", f'call "{launcher}"'],
        cwd=qce_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


EXPIRED_HINTS = ("过期", "失效", "刷新", "expired", "refresh")

MAX_QR_REFRESHES = 3

FAILURE_LINGER_SECONDS = 15


def resolve_log_file(qce_root, override):
    """选取第一个可写的日志位置，顺序为 QCE 日志目录、QCE 用户目录、系统临时目录。"""
    if override:
        return override
    candidates = [
        qce_root / "logs" / "qce-qr-login.log",
        Path.home() / ".qq-chat-exporter" / "logs" / "qce-qr-login.log",
        Path(tempfile.gettempdir()) / "qce-qr-login.log",
    ]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8"):
                pass
        except OSError:
            continue
        return path
    return None


class LoginWindow:
    def __init__(self, qce_root, api_base, timeout, scan_timeout=300, log_path=None):
        self.qce_root, self.api_base, self.timeout = qce_root, api_base, timeout
        self.scan_timeout, self.log_path = scan_timeout, log_path
        self.started_at, self.headers, self.last_qr = time.monotonic(), None, None
        self.refreshes, self.finished = 0, False
        self.root = tk.Tk()
        self.root.title("QQ 扫码登录")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.after(200, self.root.focus_force)
        self.status = tk.StringVar(value="正在获取二维码…")
        self.canvas = tk.Canvas(self.root, width=360, height=360, bg="white", highlightthickness=0)
        self.canvas.pack(padx=20, pady=(20, 8))
        tk.Label(self.root, textvariable=self.status, fg="#555555").pack(pady=(0, 20))
        self.log(f"登录窗口已打开，扫码超时 {self.scan_timeout} 秒，日志：{self.log_path or '未启用'}")
        self.root.after(100, self.poll)

    def log(self, message):
        """把登录过程写入日志；令牌与二维码内容绝不记录。"""
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [qce-qr-login] {message}"
        try:
            print(line, flush=True)
        except (OSError, ValueError, AttributeError):
            pass
        if not self.log_path:
            return
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            self.log_path = None

    def finish(self, message, delay=FAILURE_LINGER_SECONDS * 1000):
        """结束登录流程：提示、写日志，并在 delay 毫秒后关闭窗口，不留无人看的窗口。"""
        if self.finished:
            return
        self.finished = True
        self.status.set(message)
        self.log(message)
        self.root.after(delay, self.root.destroy)

    def render(self, value):
        qr = qrcode.QRCode(border=4)
        qr.add_data(value)
        qr.make(fit=True)
        grid = qr.get_matrix()
        size = len(grid)
        unit, offset = max(1, 340 // size), 10
        self.canvas.delete("all")
        for row, values in enumerate(grid):
            for column, black in enumerate(values):
                if black:
                    x, y = offset + column * unit, offset + row * unit
                    self.canvas.create_rectangle(x, y, x + unit, y + unit, outline="", fill="black")

    def refresh(self, message):
        """二维码过期时向 NapCat 申请新码，避免让用户对着废码反复扫码。"""
        self.refreshes += 1
        try:
            post_json(f"{self.api_base}/api/QQLogin/RefreshQRcode", {}, self.headers)
        except (OSError, URLError, RuntimeError, ValueError) as error:
            self.finish(f"刷新二维码失败：{error}")
            return False
        self.last_qr = None
        self.status.set(f"{message}，已自动刷新，请使用手机 QQ 扫描")
        self.log(f"二维码过期（{message}），第 {self.refreshes} 次自动刷新")
        return True

    def poll(self):
        if self.finished:
            return
        if self.scan_timeout and time.monotonic() - self.started_at >= self.scan_timeout:
            self.finish(f"扫码超时：{self.scan_timeout} 秒内未完成登录，已关闭窗口")
            return
        try:
            if self.headers is None:
                self.headers = credential(self.qce_root, self.api_base)
            data = post_json(f"{self.api_base}/api/QQLogin/CheckLoginStatus", {}, self.headers)
            if data.get("isLogin"):
                self.finish("登录成功", 1200)
                return
            value = data.get("qrcodeurl")
            message = str(data.get("loginError") or "").strip()
            if message:
                expired = any(hint in message.lower() for hint in EXPIRED_HINTS)
                if not expired or self.refreshes >= MAX_QR_REFRESHES:
                    self.finish(f"无法登录：{message}")
                    return
                if not self.refresh(message):
                    return
                self.root.after(1200, self.poll)
                return
            if value and value != self.last_qr:
                self.last_qr = value
                self.render(value)
                self.status.set("请使用手机 QQ 扫描")
                self.log("已显示二维码，等待手机 QQ 扫码")
            elif not value:
                self.status.set("正在等待二维码…")
        except (OSError, URLError, RuntimeError, ValueError) as error:
            if time.monotonic() - self.started_at >= self.timeout:
                self.finish(f"无法获取二维码：{error}")
                return
        self.root.after(1500, self.poll)

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="显示本机 NapCat-QCE 的独立扫码登录窗口")
    parser.add_argument("--qce-root", type=Path, help="含 launcher-user.bat 的 QCE Shell 包目录；省略则自动定位")
    parser.add_argument("--api-base", default="http://127.0.0.1:6099", help="NapCat WebUI 地址")
    parser.add_argument("--timeout", type=int, default=45, help="等待服务启动的秒数")
    parser.add_argument("--scan-timeout", type=int, default=300, help="等待扫码的秒数，0 表示不超时")
    parser.add_argument("--log-file", type=Path, help="登录日志路径；省略则自动选第一个可写位置")
    parser.add_argument("--no-launch", action="store_true", help="不启动 QCE，只连接已运行实例")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit(
            "错误：扫码登录仅支持 Windows（NapCat Shell 包的启动器是 launcher-user.bat）。"
            "已有 JSON 的分析不受平台限制。"
        )
    root = args.qce_root
    if root is None:
        root, source = qce_env.locate()
        if root is None:
            raise SystemExit(f"错误：未找到 QCE 目录（{source or '请先运行 scripts/qce_env.py check'}）。")
        print(f"[qce-qr-login] 自动定位 QCE 目录（来源：{source}）：{root}", flush=True)
    elif not qce_env.is_qce_root(root):
        raise SystemExit(f"错误：{root} 不是有效的 QCE 目录（缺少 launcher-user.bat）。")
    if not args.no_launch:
        launch_hidden(root)
    LoginWindow(
        root,
        args.api_base.rstrip("/"),
        args.timeout,
        args.scan_timeout,
        resolve_log_file(root, args.log_file),
    ).run()


if __name__ == "__main__":
    main()
