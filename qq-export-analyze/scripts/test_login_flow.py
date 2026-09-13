"""qce_qr_login.py 的无窗口流程测试：验证过期二维码会被刷新而不是照旧显示。

直接运行：python scripts/test_login_flow.py
"""

import importlib.util
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "qce_qr_login.py"
spec = importlib.util.spec_from_file_location("qce_qr_login", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules["qce_qr_login"] = module
spec.loader.exec_module(module)


class Status:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class Canvas:
    def __init__(self):
        self.painted = 0

    def delete(self, _):
        pass

    def create_rectangle(self, *args, **kwargs):
        self.painted += 1


class Root:
    def __init__(self):
        self.pending = []

    def after(self, delay, callback):
        self.pending.append((delay, getattr(callback, "__name__", str(callback))))

    def destroy(self):
        self.pending.append((0, "destroy"))


def build_window(responses, scan_timeout=300, log_path=None, elapsed=0.0):
    """构造一个不创建 Tk 窗口的 LoginWindow，post_json 按脚本返回预设响应。"""
    window = object.__new__(module.LoginWindow)
    window.qce_root, window.api_base, window.timeout = Path("."), "http://x", 45
    window.scan_timeout, window.log_path = scan_timeout, log_path
    window.started_at, window.last_qr, window.refreshes = time.monotonic() - elapsed, None, 0
    window.finished = False
    window.status, window.canvas, window.root = Status(), Canvas(), Root()
    window.headers = {"Authorization": "Bearer test"}
    calls = []

    def fake_post(url, body, headers=None):
        endpoint = url.rsplit("/", 1)[-1]
        calls.append(endpoint)
        if endpoint != "CheckLoginStatus":
            return {}
        return responses.pop(0)

    module.post_json = fake_post
    module.credential = lambda *_: {"Authorization": "Bearer test"}
    return window, calls


def test_expired_qr_is_refreshed():
    expired = {"isLogin": False, "qrcodeurl": "https://old", "loginError": "二维码已过期，请刷新"}
    fresh = {"isLogin": False, "qrcodeurl": "https://new", "loginError": ""}
    window, calls = build_window([expired, fresh])
    window.poll()
    assert "RefreshQRcode" in calls, calls
    assert "已自动刷新" in window.status.value, window.status.value
    assert window.canvas.painted == 0, "过期码不应被画出来"
    window.poll()
    assert window.last_qr == "https://new", window.last_qr
    assert window.canvas.painted > 0, "刷新后应画出新二维码"
    assert window.status.value == "请使用手机 QQ 扫描", window.status.value
    print("PASS 过期码触发刷新，只渲染新码:", window.status.value)


def test_login_success_closes_window():
    window, _ = build_window([{"isLogin": True}])
    window.poll()
    assert window.status.value == "登录成功", window.status.value
    assert window.root.pending[-1] == (1200, "destroy"), window.root.pending
    print("PASS 登录成功后关闭窗口")


def test_refresh_is_bounded():
    expired = {"isLogin": False, "qrcodeurl": "https://old", "loginError": "二维码已过期，请刷新"}
    window, calls = build_window([expired] * 5)
    for _ in range(5):
        window.poll()
    assert calls.count("RefreshQRcode") == module.MAX_QR_REFRESHES, calls
    assert window.status.value.startswith("无法登录"), window.status.value
    print("PASS 刷新次数受限并报错:", window.status.value)


def test_non_expiry_error_is_reported():
    window, calls = build_window([{"isLogin": False, "qrcodeurl": "", "loginError": "登录失败：账号被冻结"}])
    window.poll()
    assert window.status.value == "无法登录：登录失败：账号被冻结", window.status.value
    assert "RefreshQRcode" not in calls, calls
    print("PASS 非过期错误直接展示:", window.status.value)


def test_fresh_qr_renders_without_refresh():
    window, calls = build_window([{"isLogin": False, "qrcodeurl": "https://ok", "loginError": ""}])
    window.poll()
    assert calls == ["CheckLoginStatus"], calls
    assert window.canvas.painted > 0 and window.status.value == "请使用手机 QQ 扫描"
    print("PASS 正常二维码直接渲染，不触发刷新")


def test_scan_timeout_closes_window():
    window, calls = build_window([], scan_timeout=300, elapsed=400)
    window.poll()
    assert window.status.value.startswith("扫码超时"), window.status.value
    assert calls == [], calls
    assert window.root.pending[-1] == (module.FAILURE_LINGER_SECONDS * 1000, "destroy"), window.root.pending
    print("PASS 扫码超时后提示并自动关窗:", window.status.value)


def test_scan_timeout_can_be_disabled():
    window, calls = build_window(
        [{"isLogin": False, "qrcodeurl": "https://ok", "loginError": ""}], scan_timeout=0, elapsed=99999
    )
    window.poll()
    assert window.status.value == "请使用手机 QQ 扫描", window.status.value
    assert calls == ["CheckLoginStatus"], calls
    print("PASS scan-timeout=0 时不超时")


def test_failures_are_logged_without_secrets():
    log_file = Path(tempfile.gettempdir()) / "qce-qr-login-test.log"
    log_file.unlink(missing_ok=True)
    expired = {"isLogin": False, "qrcodeurl": "https://secret-qr-key", "loginError": "二维码已过期，请刷新"}
    window, _ = build_window([expired] * 5, log_path=log_file)
    for _ in range(5):
        window.poll()
    text = log_file.read_text(encoding="utf-8")
    assert "第 3 次自动刷新" in text, text
    assert "无法登录：二维码已过期，请刷新" in text, text
    assert "secret-qr-key" not in text, "二维码内容不得写入日志"
    print("PASS 失败原因落盘且不含二维码内容")

    ok_file = Path(tempfile.gettempdir()) / "qce-qr-login-ok.log"
    ok_file.unlink(missing_ok=True)
    window, _ = build_window([{"isLogin": True}], log_path=ok_file)
    window.poll()
    assert "登录成功" in ok_file.read_text(encoding="utf-8")
    ok_file.unlink(missing_ok=True)
    log_file.unlink(missing_ok=True)
    print("PASS 登录成功写入日志")


def test_unwritable_log_path_does_not_crash():
    window, _ = build_window(
        [{"isLogin": True}], log_path=Path("Z:/definitely-missing/qce-qr-login.log")
    )
    window.poll()
    assert window.finished and window.log_path is None
    print("PASS 日志不可写时静默降级")


def test_resolve_log_file_prefers_writable():
    explicit = Path(tempfile.gettempdir()) / "qce-qr-explicit.log"
    assert module.resolve_log_file(Path(tempfile.gettempdir()), explicit) == explicit
    assert not explicit.exists(), "显式路径不应被提前创建"
    picked = module.resolve_log_file(Path("Z:/definitely-missing"), None)
    assert picked is not None and picked.is_absolute(), picked
    picked.unlink(missing_ok=True)
    print("PASS 日志位置回退到可写路径:", picked)


if __name__ == "__main__":
    test_expired_qr_is_refreshed()
    test_login_success_closes_window()
    test_refresh_is_bounded()
    test_non_expiry_error_is_reported()
    test_fresh_qr_renders_without_refresh()
    test_scan_timeout_closes_window()
    test_scan_timeout_can_be_disabled()
    test_failures_are_logged_without_secrets()
    test_unwritable_log_path_does_not_crash()
    test_resolve_log_file_prefers_writable()
    print("全部登录流程测试通过")
