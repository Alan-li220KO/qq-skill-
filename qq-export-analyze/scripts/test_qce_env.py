"""qce_env.py 的单元测试：定位、版本解析、asset 选择与校验相关逻辑。

直接运行：python scripts/test_qce_env.py
"""

import importlib.util
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "qce_env.py"
spec = importlib.util.spec_from_file_location("qce_env", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules["qce_env"] = module
spec.loader.exec_module(module)

FIXTURES = SCRIPT.parent.parent / ".test-fixtures"


def make_fake_root(base: Path) -> Path:
    """在 base 的 Documents 下造一个最小可识别的 QCE 目录（模拟上游解压布局）。"""
    root = base / "Documents" / "NapCat-QCE-Windows-x64-v9.9.9" / "NapCat-QCE-Windows-x64"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "launcher-user.bat").write_text("@echo off\n", encoding="utf-8")
    (root / "config" / "webui.json").write_text(json.dumps({"token": "x"}), encoding="utf-8")
    (root / "README.txt").write_text("QCE 版本: 9.9.9\n", encoding="utf-8")
    return root


def test_is_qce_root():
    shutil.rmtree(FIXTURES, ignore_errors=True)
    home = FIXTURES / "home"
    home.mkdir(parents=True)
    (FIXTURES / "empty").mkdir()
    assert module.is_qce_root(make_fake_root(home))
    assert not module.is_qce_root(FIXTURES / "empty")
    assert not module.is_qce_root(None)
    assert not module.is_qce_root(FIXTURES / "missing")
    print("PASS is_qce_root 只认含启动器的目录")


def test_locate_prefers_explicit_then_env_then_config_then_common():
    home = FIXTURES / "home"
    common = module.candidate_roots(home=home, env={})
    assert common, "候选路径应包含 Documents 下的安装目录"
    assert module.is_qce_root(common[0]), common[0]

    explicit = FIXTURES / "explicit"
    explicit.mkdir(parents=True)
    (explicit / "launcher-user.bat").write_text("", encoding="utf-8")

    root, source = module.locate(explicit=explicit, home=home, env={})
    assert (root, source) == (explicit, "--qce-root"), (root, source)

    env = {"QCE_ROOT": str(explicit)}
    root, source = module.locate(home=home, env=env)
    assert (root, source) == (explicit, "QCE_ROOT"), (root, source)

    root, source = module.locate(home=home, env={})
    assert source == "常见安装路径" and root == common[0], (root, source)
    print("PASS locate 优先级：显式 > 环境变量 > 常见路径")


def test_locate_reports_stale_config():
    skill_root = FIXTURES / "skill"
    skill_root.mkdir(parents=True)
    module.config_path(skill_root).write_text(
        json.dumps({"qceRoot": str(FIXTURES / "gone")}), encoding="utf-8"
    )
    root, source = module.locate(skill_root=skill_root, home=FIXTURES / "no-such-home", env={})
    assert root is None and "已失效" in source, (root, source)
    print("PASS 配置指向失效目录时给出明确说明")


def test_write_config_roundtrip():
    skill_root = FIXTURES / "skill2"
    skill_root.mkdir(parents=True)
    target = FIXTURES / "explicit"
    path = module.write_config(target, skill_root=skill_root)
    assert json.loads(path.read_text(encoding="utf-8"))["qceRoot"] == str(target)
    assert module.locate(skill_root=skill_root, home=FIXTURES / "nope", env={}) == (target, module.CONFIG_NAME)
    print("PASS 安装路径可写入并再被 locate 读到")


def test_version_parsing():
    assert module.parse_qce_version("QCE 版本: 6.3.0\n") == "6.3.0"
    assert module.parse_qce_version("QCE版本：6.10.1") == "6.10.1"
    assert module.parse_qce_version("没有版本信息") is None
    assert module.parse_qq_build("9.9.19-34740") == 34740
    assert module.parse_qq_build("9.9.29-47354") == 47354
    assert module.parse_qq_build("乱七八糟") is None
    print("PASS QCE 版本与 QQ 构建号解析")


def test_pick_asset_only_windows_x64():
    assets = [
        {"name": "NapCat-QCE-Linux-x64-v6.3.0.tar.gz"},
        {"name": "NapCat-QCE-Windows-x64-v6.3.0.zip", "size": 1},
        {"name": "QQChatExporter-Installer-v6.3.0.exe"},
    ]
    picked = module.pick_asset(assets, system="Windows", machine="AMD64")
    assert picked["name"] == "NapCat-QCE-Windows-x64-v6.3.0.zip", picked
    for system, machine in (("Linux", "x86_64"), ("Darwin", "arm64")):
        try:
            module.pick_asset(assets, system=system, machine=machine)
            raise AssertionError("非 Windows 平台应当明确拒绝")
        except RuntimeError as error:
            assert "只支持 Windows x64" in str(error), error
    print("PASS asset 选择锁定 Windows x64 并给出跨平台说明")


def test_expected_sha256_sources():
    asset = {"name": "NapCat-QCE-Windows-x64-v6.3.0.zip", "digest": "sha256:AbC123"}
    assert module.expected_sha256(asset) == "abc123"
    assert module.expected_sha256({"name": "NapCat-QCE-Windows-x64-v6.3.0.zip"}) == module.KNOWN_SHA256[asset["name"]]
    assert module.expected_sha256({"name": "unknown.zip"}, override=" DEADbeef ") == "deadbeef"
    assert module.expected_sha256({"name": "unknown.zip"}) is None
    print("PASS 校验和来源：参数 > 上游 digest > 已知兜底")


def test_validate_members_rejects_escape():
    module.validate_members(["NapCat/launcher-user.bat", "NapCat/config/webui.json"])
    for bad in ("../evil.exe", "C:/Windows/system32/evil.dll", "/etc/passwd"):
        try:
            module.validate_members([bad])
            raise AssertionError(f"应当拒绝 {bad}")
        except RuntimeError as error:
            assert "不安全路径" in str(error), error
    print("PASS 拒绝压缩包路径逃逸")


def test_find_launcher_dir():
    shutil.rmtree(FIXTURES / "extract", ignore_errors=True)
    base = FIXTURES / "extract"
    nested = base / "NapCat-QCE-Windows-x64" / "inner"
    nested.mkdir(parents=True)
    (nested / "launcher-user.bat").write_text("", encoding="utf-8")
    assert module.find_launcher_dir(base) == nested
    assert module.find_launcher_dir(FIXTURES / "empty") is None
    print("PASS 能在解压产物里找到启动器目录")


def test_install_dry_run_uses_release_metadata():
    calls = {}
    module.fetch_release = lambda version=None: calls.update(version=version) or {
        "tag_name": "v6.3.0",
        "assets": [{"name": "NapCat-QCE-Windows-x64-v6.3.0.zip", "size": 42334765,
                    "browser_download_url": "https://example.invalid/pkg.zip",
                    "digest": "sha256:" + "a" * 64}],
    }
    target = FIXTURES / "dry-run-target"
    result = module.install(version="6.3.0", target=target, dry_run=True)
    assert result == target and not target.exists(), target
    assert calls["version"] == "6.3.0"
    print("PASS install --dry-run 只解析上游元数据，不落盘")


def test_install_verifies_checksum_and_extracts():
    shutil.rmtree(FIXTURES / "fake-release", ignore_errors=True)
    staging = FIXTURES / "fake-release"
    staging.mkdir(parents=True)
    payload = staging / "NapCat-QCE-Windows-x64-v6.3.0.zip"
    with zipfile.ZipFile(payload, "w") as bundle:
        bundle.writestr("NapCat-QCE-Windows-x64/launcher-user.bat", "@echo off\n")
        bundle.writestr("NapCat-QCE-Windows-x64/config/webui.json", "{}")

    digest = module.sha256_file(payload)
    module.fetch_release = lambda version=None: {
        "tag_name": "v6.3.0",
        "assets": [{"name": payload.name, "size": payload.stat().st_size,
                    "browser_download_url": payload.as_uri(), "digest": f"sha256:{digest}"}],
    }
    skill_root = FIXTURES / "skill3"
    skill_root.mkdir(parents=True)
    target = FIXTURES / "installed"
    module.install(target=target, env={}, work_dir=FIXTURES / "work", skill_root=skill_root)
    assert module.is_qce_root(target), list(target.iterdir())
    assert module.locate(skill_root=skill_root, home=FIXTURES / "nope", env={})[0] == target
    assert module.parse_qce_version("QCE 版本: 6.3.0") == "6.3.0"
    print("PASS install 校验并通过后解压出可识别的 QCE 目录")

    bad = dict(digest="sha256:" + "0" * 64)
    module.fetch_release = lambda version=None: {
        "tag_name": "v6.3.0",
        "assets": [{"name": payload.name, "size": payload.stat().st_size,
                    "browser_download_url": payload.as_uri(), "digest": bad["digest"]}],
    }
    try:
        module.install(target=FIXTURES / "installed-bad", force=True, env={},
                       work_dir=FIXTURES / "work", skill_root=skill_root)
        raise AssertionError("校验和不匹配时必须中止")
    except RuntimeError as error:
        assert "校验和不匹配" in str(error), error
    print("PASS 校验和不匹配时中止安装")


def test_install_from_local_archive_offline():
    """GitHub 下载节点不可达时的离线通道：--archive 不应触碰网络。"""
    payload = FIXTURES / "fake-release" / "NapCat-QCE-Windows-x64-v6.3.0.zip"
    assert payload.is_file(), "依赖前一个用例生成的假安装包"
    digest = module.sha256_file(payload)

    def explode(*_args, **_kwargs):
        raise AssertionError("--archive 模式下不应访问网络")

    original = module.fetch_release
    module.fetch_release = explode
    try:
        skill_root = FIXTURES / "skill4"
        skill_root.mkdir(parents=True)
        target = FIXTURES / "offline-installed"
        module.install(archive=payload, target=target, sha_override=digest,
                       env={}, work_dir=FIXTURES / "offline-work", skill_root=skill_root)
        assert module.is_qce_root(target), list(target.iterdir())

        module.install(archive=payload, target=FIXTURES / "offline-bad",
                       sha_override="0" * 64, force=True,
                       env={}, work_dir=FIXTURES / "offline-work", skill_root=skill_root)
        raise AssertionError("校验和不匹配时必须中止")
    except RuntimeError as error:
        assert "校验和不匹配" in str(error), error
    finally:
        module.fetch_release = original
    print("PASS 离线 --archive 安装：不联网、仍强制校验")


def test_install_from_archive_without_sha_is_allowed_but_reported():
    payload = FIXTURES / "fake-release" / "NapCat-QCE-Windows-x64-v6.3.0.zip"
    unknown = FIXTURES / "fake-release" / "NapCat-QCE-Windows-x64-v9.9.9.zip"
    shutil.copyfile(payload, unknown)  # 文件名不在 KNOWN_SHA256 表里，才走"未提供校验和"分支
    target = FIXTURES / "offline-nosha"
    module.install(archive=unknown, target=target, env={},
                   work_dir=FIXTURES / "offline-work2", skill_root=FIXTURES / "skill4")
    assert module.is_qce_root(target)
    print("PASS 未提供校验和时安装仍成功，但会打印实际 sha256 供核对")


def test_collect_checks_reports_missing_root():
    results = module.collect_checks(None, None)
    levels = {name: level for level, name, _ in results}
    assert levels["QCE 目录"] == "fail", results
    assert "平台" in levels and "Python" in levels and "QQ 客户端" in levels
    print("PASS 体检在缺少 QCE 目录时判为失败")


if __name__ == "__main__":
    try:
        test_is_qce_root()
        test_locate_prefers_explicit_then_env_then_config_then_common()
        test_locate_reports_stale_config()
        test_write_config_roundtrip()
        test_version_parsing()
        test_pick_asset_only_windows_x64()
        test_expected_sha256_sources()
        test_validate_members_rejects_escape()
        test_find_launcher_dir()
        test_install_dry_run_uses_release_metadata()
        test_install_verifies_checksum_and_extracts()
        test_install_from_local_archive_offline()
        test_install_from_archive_without_sha_is_allowed_but_reported()
        test_collect_checks_reports_missing_root()
    finally:
        shutil.rmtree(FIXTURES, ignore_errors=True)
    print("全部依赖管理测试通过")

