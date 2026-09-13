#!/usr/bin/env python3
"""QCE 依赖的定位、体检与安装引导。

本 skill 不打包 NapCat 或 QCE 的二进制：`install` 从上游官方 Release 下载并校验
校验和。上游许可见 THIRD-PARTY.md，其中 NapCat 仅允许非商业再分发。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAME = "qce.local.json"

UPSTREAM_REPO = "shuakami/qq-chat-exporter"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
NAPcat_URL = "https://github.com/NapNeko/NapCatQQ"
QQ_DOWNLOAD_URL = "https://im.qq.com/"

LAUNCHER_NAMES = ("launcher-user.bat", "launcher-user.sh")
NAPcat_WEBUI_PORT = 6099
QCE_PORT = 40653
MIN_QQ_BUILD = 34606
RECOMMENDED_QQ = "9.9.19-34740"

WINDOWS_ASSET_PREFIX = "NapCat-QCE-Windows-x64-"

# 上游 Release 未提供 digest 时的已知校验和兜底。
KNOWN_SHA256 = {
    "NapCat-QCE-Windows-x64-v6.3.0.zip": "cfdd77d037677c9920f07757044bd56e8dfb0c73eb823ed40897fb338c246bfe",
}


# --------------------------------------------------------------------------- 基础

def config_path(skill_root: Path = SKILL_ROOT) -> Path:
    return skill_root / CONFIG_NAME


def is_qce_root(path) -> bool:
    """目录含 QCE 启动器即视为有效的 QCE 根目录。"""
    if not path:
        return False
    path = Path(path)
    return path.is_dir() and any((path / name).is_file() for name in LAUNCHER_NAMES)


def read_config(skill_root: Path = SKILL_ROOT) -> dict:
    try:
        data = json.loads(config_path(skill_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_config(root: Path, skill_root: Path = SKILL_ROOT) -> Path:
    target = config_path(skill_root)
    target.write_text(
        json.dumps({"qceRoot": str(root)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def default_install_dir(env=None) -> Path:
    env = os.environ if env is None else env
    local = env.get("LOCALAPPDATA") or env.get("XDG_DATA_HOME")
    base = Path(local) if local else Path.home() / ".local" / "share"
    return base / "NapCat-QCE"


def candidate_roots(home=None, env=None) -> list:
    """按优先级返回候选 QCE 目录；只做有界的 glob，不递归全盘。"""
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    roots: list[Path] = []
    local = env.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "NapCat-QCE")
    for base in (home / "Documents", home / "Downloads", home / "Desktop"):
        if not base.is_dir():
            continue
        for pattern in (
            "NapCat-QCE-Windows-x64-*/NapCat-QCE-Windows-x64",
            "NapCat-QCE-*/NapCat-QCE-Windows-x64",
            "NapCat-QCE-Windows-x64-*",
            "NapCat-QCE-*",
        ):
            roots.extend(sorted(base.glob(pattern)))
    return roots


def locate(explicit=None, skill_root: Path = SKILL_ROOT, home=None, env=None):
    """返回 (QCE 根目录, 来源说明)；找不到返回 (None, None)。"""
    env = os.environ if env is None else env
    candidates = [("--qce-root", explicit)]
    if env.get("QCE_ROOT"):
        candidates.append(("QCE_ROOT", env["QCE_ROOT"]))
    configured = read_config(skill_root).get("qceRoot")
    if configured:
        candidates.append((CONFIG_NAME, configured))
    for source, value in candidates:
        if value and is_qce_root(value):
            return Path(value), source
    for path in candidate_roots(home=home, env=env):
        if is_qce_root(path):
            return path, "常见安装路径"
    if configured:
        return None, f"{CONFIG_NAME} 指向的目录已失效：{configured}"
    return None, None


# --------------------------------------------------------------------------- 版本解析

def parse_qce_version(readme_text: str):
    """从 QCE 的 README.txt 里取 QCE 版本号。"""
    match = re.search(r"QCE\s*版本\s*[:：]\s*([0-9][0-9.]*)", readme_text or "")
    return match.group(1) if match else None


def parse_qq_build(version: str):
    """从 9.9.19-34740 这类版本号里取构建号。"""
    match = re.search(r"-(\d{4,})$", (version or "").strip())
    return int(match.group(1)) if match else None


def qq_install_path(qce_root):
    """优先读启动器写入的 config/qq_path.txt。"""
    if not qce_root:
        return None
    recorded = Path(qce_root) / "config" / "qq_path.txt"
    try:
        value = recorded.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return Path(value) if value else None


def qq_version(qq_path):
    """读取 QQNT 安装目录下的版本号。"""
    if not qq_path:
        return None
    qq_path = Path(qq_path)
    search = []
    versions = qq_path.parent / "versions"
    if versions.is_dir():
        search.extend(sorted((p / "resources" / "app" / "package.json" for p in versions.iterdir()), reverse=True))
    search.append(qq_path.parent / "resources" / "app" / "package.json")
    for manifest in search:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("version"):
            return str(data["version"])
    return None


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


def local_token():
    """读取本机 QCE 写入的访问令牌；只在内存中使用，不打印、不落盘。"""
    token_file = Path.home() / ".qq-chat-exporter" / "security.json"
    try:
        value = json.loads(token_file.read_text(encoding="utf-8")).get("accessToken")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


# --------------------------------------------------------------------------- 体检

def collect_checks(qce_root, source=None, online=False, env=None, runner=None) -> list:
    """返回 [(级别, 名称, 说明)]，级别为 ok / warn / fail / info。"""
    env = os.environ if env is None else env
    results = []

    system = platform.system()
    if system == "Windows":
        results.append(("ok", "平台", f"Windows {platform.release()} {platform.machine()}"))
    else:
        results.append(
            ("warn", "平台", f"{system} {platform.machine()}：导出链路仅支持 Windows x64，"
                             "已有 JSON 的分析不受影响")
        )

    version = platform.python_version()
    level = "ok" if sys.version_info >= (3, 9) else "fail"
    results.append((level, "Python", version))

    try:
        import tkinter  # noqa: F401
        results.append(("ok", "tkinter", "可用，扫码窗口能弹出"))
    except Exception as error:  # noqa: BLE001 - 任何导入失败都只影响扫码窗口
        results.append(("warn", "tkinter", f"不可用（{error}），无法弹出扫码窗口"))

    if qce_root:
        results.append(("ok", "QCE 目录", f"{qce_root}（来源：{source}）"))
        readme = Path(qce_root) / "README.txt"
        try:
            qce_version = parse_qce_version(readme.read_text(encoding="utf-8"))
        except OSError:
            qce_version = None
        results.append(
            ("ok" if qce_version else "warn", "QCE 版本", qce_version or "未在 README.txt 中读到版本号")
        )
        webui = Path(qce_root) / "config" / "webui.json"
        results.append(
            ("ok" if webui.is_file() else "warn", "NapCat WebUI 配置",
             "已生成" if webui.is_file() else "尚未生成，首次运行 launcher 后才会出现")
        )
    else:
        results.append(
            ("fail", "QCE 目录", source or "未找到；先运行 install，或用 --qce-root / QCE_ROOT 指定")
        )

    qq_path = qq_install_path(qce_root) or _probe_common_qq()
    if qq_path and Path(qq_path).is_file():
        qq_ver = qq_version(qq_path)
        build = parse_qq_build(qq_ver)
        if build and build >= MIN_QQ_BUILD:
            results.append(("ok", "QQ 客户端", f"{qq_ver}（{qq_path}）"))
        elif qq_ver:
            results.append(
                ("fail", "QQ 客户端", f"{qq_ver} 低于要求的 {MIN_QQ_BUILD}+（推荐 {RECOMMENDED_QQ}）：{qq_path}")
            )
        else:
            results.append(("warn", "QQ 客户端", f"找到 {qq_path}，但读不到版本号"))
    else:
        results.append(("fail", "QQ 客户端", f"未找到 QQNT，请先安装：{QQ_DOWNLOAD_URL}"))

    if system == "Windows":
        vcruntime = Path(env.get("SystemRoot", r"C:\Windows")) / "System32" / "vcruntime140.dll"
        results.append(
            ("ok" if vcruntime.is_file() else "warn", "VC++ 运行库",
             "已安装" if vcruntime.is_file() else "缺少 vcruntime140.dll，启动报 DLL 错误时需装 VC++ 运行库")
        )

    for port, label in ((NAPcat_WEBUI_PORT, "NapCat WebUI"), (QCE_PORT, "QCE 服务")):
        running = port_open(port)
        results.append(
            ("info", f"端口 {port}", f"{label}{' 正在监听' if running else ' 未监听（登录后才会启动）'}")
        )

    if local_token():
        results.append(("ok", "访问令牌", "已在 ~/.qq-chat-exporter/security.json 中找到，无需手动粘贴"))
    else:
        results.append(("info", "访问令牌", "尚未生成，登录成功后 QCE 会自动写入"))

    if online:
        results.extend(_online_checks(qce_root))
    return results


def _probe_common_qq():
    if platform.system() != "Windows":
        return None
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        candidate = Path(base) / "Tencent" / "QQNT" / "QQ.exe"
        if candidate.is_file():
            return candidate
        programs = Path(base) / "Programs" / "Tencent" / "QQNT" / "QQ.exe"
        if programs.is_file():
            return programs
    return None


def _online_checks(qce_root):
    try:
        release = fetch_release()
    except (OSError, urllib.error.URLError, RuntimeError) as error:
        return [("warn", "上游版本", f"查询失败：{error}")]
    latest = str(release.get("tag_name") or "").lstrip("v")
    local = None
    if qce_root:
        try:
            local = parse_qce_version((Path(qce_root) / "README.txt").read_text(encoding="utf-8"))
        except OSError:
            local = None
    if local and latest and local == latest:
        return [("ok", "上游版本", f"已是上游最新版 v{latest}")]
    if local and latest:
        return [("warn", "上游版本", f"本地 v{local}，上游最新 v{latest}：{UPSTREAM_URL}/releases")]
    return [("info", "上游版本", f"上游最新 v{latest}：{UPSTREAM_URL}/releases")]


# --------------------------------------------------------------------------- 下载与校验

def fetch_json(url: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "qq-export-analyze-skill", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_release(version: str | None = None) -> dict:
    if version:
        tag = version if version.startswith("v") else f"v{version}"
        url = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/tags/{tag}"
    else:
        url = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest"
    return fetch_json(url)


def pick_asset(assets, system: str | None = None, machine: str | None = None) -> dict:
    """按平台挑选官方 asset；目前只支持 Windows x64。"""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if system != "Windows" or machine not in ("amd64", "x86_64"):
        raise RuntimeError(
            f"本 skill 的导出链路只支持 Windows x64，当前为 {system} {machine}；"
            f"其他平台请参考 {UPSTREAM_URL}#下载"
        )
    for asset in assets or []:
        name = str(asset.get("name") or "")
        if name.startswith(WINDOWS_ASSET_PREFIX) and name.endswith(".zip"):
            return asset
    raise RuntimeError(f"上游 Release 中未找到 {WINDOWS_ASSET_PREFIX}*.zip")


def expected_sha256(asset: dict, override: str | None = None) -> str | None:
    if override:
        return override.strip().lower()
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    return KNOWN_SHA256.get(str(asset.get("name") or ""))


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


@contextlib.contextmanager
def work_directory(work_dir=None):
    """下载与解压用的工作目录；--work-dir 可指定到与安装目录同一磁盘。"""
    if work_dir:
        path = Path(work_dir)
        path.mkdir(parents=True, exist_ok=True)
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="qce-download-") as path:
        yield Path(path)


def validate_members(names) -> None:
    """拒绝绝对路径与 .. 逃逸，避免解压写出行外。

    用 anchor 而不是 is_absolute：Windows 下 "/etc/passwd" 这类根路径的
    is_absolute() 为假，但仍会把文件写到当前盘符的根目录。
    """
    for name in names:
        path = Path(name)
        if path.is_absolute() or path.anchor or ".." in path.parts:
            raise RuntimeError(f"压缩包包含不安全路径：{name}")


def find_launcher_dir(base: Path):
    base = Path(base)
    if is_qce_root(base):
        return base
    for pattern in ("*/launcher-user.bat", "*/*/launcher-user.bat"):
        for hit in sorted(base.glob(pattern)):
            return hit.parent
    return None


def download(url: str, target: Path, attempts: int = 3, timeout: int = 120) -> Path:
    """下载到 target；中断后按已有字节续传，最多尝试 attempts 次。"""
    target = Path(target)
    last_error = None
    for attempt in range(1, attempts + 1):
        done = target.stat().st_size if target.exists() else 0
        headers = {"User-Agent": "qq-export-analyze-skill"}
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                partial = getattr(response, "status", 200) == 206
                if done and not partial:
                    done, mode = 0, "wb"  # 服务器不支持续传，重头下载
                else:
                    mode = "ab" if done else "wb"
                total = int(response.headers.get("Content-Length") or 0) + done
                with target.open(mode) as handle:
                    while True:
                        block = response.read(1 << 20)
                        if not block:
                            break
                        handle.write(block)
                        done += len(block)
                        if total:
                            print(f"\r  下载中 {done * 100 // total}%（{done >> 20}/{total >> 20} MB）",
                                  end="", flush=True)
            print()
            return target
        except (urllib.error.URLError, OSError) as error:
            last_error = error
            size = target.stat().st_size >> 20 if target.exists() else 0
            print(f"\n  第 {attempt}/{attempts} 次下载中断：{error}（已获取 {size} MB）")
    raise RuntimeError(f"下载失败，已尝试 {attempts} 次：{last_error}")


def install(version=None, target=None, sha_override=None, dry_run=False, force=False, env=None,
            work_dir=None, skill_root=None, archive=None) -> Path:
    env = os.environ if env is None else env
    skill_root = Path(skill_root) if skill_root else SKILL_ROOT
    target = Path(target) if target else default_install_dir(env)

    if archive:
        local = Path(archive)
        if not local.is_file():
            raise RuntimeError(f"本地安装包不存在：{local}")
        sha = (sha_override or KNOWN_SHA256.get(local.name) or "").lower() or None
        print(f"使用本地安装包：{local}")
        print(f"文件大小：{local.stat().st_size >> 20} MB")
        print(f"校验和：sha256:{sha or '未提供，将跳过校验（可用 --sha256 指定，或核对下方实际值）'}")
        print(f"安装目录：{target}")
        print(f"许可提醒：{UPSTREAM_URL}（GPL-3.0）与 {NAPcat_URL}（仅限非商业再分发）")
        if dry_run:
            print("--dry-run：未做任何改动。")
            return target
        _install_from(local, target, sha, force, work_dir, skill_root)
        return target

    release = fetch_release(version)
    tag = str(release.get("tag_name") or "")
    asset = pick_asset(release.get("assets"))
    url = str(asset.get("browser_download_url") or "")
    sha = expected_sha256(asset, sha_override)
    size = int(asset.get("size") or 0)

    print(f"上游版本：{tag}")
    print(f"下载地址：{url}")
    print(f"文件大小：{size >> 20} MB")
    print(f"校验和：sha256:{sha or '上游未提供，且无已知值（将跳过校验）'}")
    print(f"安装目录：{target}")
    print(f"许可提醒：{UPSTREAM_URL}（GPL-3.0）与 {NAPcat_URL}（仅限非商业再分发）")
    if dry_run:
        print("--dry-run：未下载任何文件。")
        return target

    if target.exists() and any(target.iterdir()) and not force:
        raise RuntimeError(f"目标目录非空：{target}；确认覆盖请加 --force")

    with work_directory(work_dir) as workspace:
        local = Path(workspace) / Path(url).name
        try:
            download(url, local)
        except RuntimeError as error:
            raise RuntimeError(
                f"{error}；若所在网络无法访问 GitHub 下载节点，可手动下载该 zip 后改用 --archive <zip> 安装"
            ) from error
        _install_from(local, target, sha, force, work_dir, skill_root)
    return target


def _install_from(archive: Path, target: Path, sha, force: bool, work_dir, skill_root) -> None:
    """校验、解压并落位；archive 为本地 zip（下载所得或 --archive 指定）。"""
    if target.exists() and any(target.iterdir()) and not force:
        raise RuntimeError(f"目标目录非空：{target}；确认覆盖请加 --force")
    actual = sha256_file(archive)
    if sha and actual != sha:
        raise RuntimeError(f"校验和不匹配：期望 {sha}，实际 {actual}")
    print("校验和一致。" if sha else f"未提供期望校验和，实际 sha256:{actual}（可对照发行页核对）")
    with work_directory(work_dir) as workspace:
        _extract(archive, target, staging=Path(workspace) / "extract")
    config = write_config(target, skill_root=skill_root)
    print(f"已安装到：{target}")
    print(f"已记录到：{config}")
    print("下一步：python scripts/qce_qr_login.py --no-launch（或直接不带参数自动定位），扫码登录。")


def _extract(archive: Path, target: Path, staging=None) -> None:
    staging = Path(staging) if staging else Path(archive).parent / "extract"
    with zipfile.ZipFile(archive) as bundle:
        validate_members(bundle.namelist())
        bundle.extractall(staging)
    source = find_launcher_dir(staging)
    if source is None:
        raise RuntimeError("解压后未找到 launcher-user.bat，压缩包结构可能已变化")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


# --------------------------------------------------------------------------- CLI

LEVEL_MARK = {"ok": "[OK]  ", "warn": "[!]   ", "fail": "[X]   ", "info": "[i]   "}


def print_checks(results) -> None:
    for level, name, detail in results:
        print(f"{LEVEL_MARK.get(level, '[i]   ')}{name}：{detail}")
    worst = {level for level, _, _ in results}
    if "fail" in worst:
        print("\n存在必须处理的问题（[X]），按上面的提示处理后重跑 check。")
    elif "warn" in worst:
        print("\n没有致命问题，[!] 项按需处理。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QCE 依赖的定位、体检与安装引导")
    sub = parser.add_subparsers(dest="command", required=True)

    locate_cmd = sub.add_parser("locate", help="输出解析到的 QCE 目录")
    locate_cmd.add_argument("--qce-root", type=Path, help="显式指定 QCE 目录")

    check = sub.add_parser("check", help="体检本机依赖是否齐备")
    check.add_argument("--qce-root", type=Path, help="显式指定 QCE 目录")
    check.add_argument("--online", action="store_true", help="同时查询上游最新版本")
    check.add_argument("--json", action="store_true", help="以 JSON 输出")

    install_cmd = sub.add_parser("install", help="从上游官方 Release 下载并安装 QCE")
    install_cmd.add_argument("--version", help="指定版本，如 6.3.0；默认最新")
    install_cmd.add_argument("--dir", type=Path, help="安装目录，默认 %%LOCALAPPDATA%%\\NapCat-QCE")
    install_cmd.add_argument("--sha256", help="覆盖期望校验和")
    install_cmd.add_argument("--dry-run", action="store_true", help="只打印计划，不下载")
    install_cmd.add_argument("--force", action="store_true", help="目标目录非空时覆盖")
    install_cmd.add_argument("--work-dir", type=Path, help="下载解压的工作目录，默认系统临时目录")
    install_cmd.add_argument("--archive", type=Path, help="使用已下载的官方 zip 离线安装（GitHub 下载节点不可达时）")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "locate":
            root, source = locate(explicit=args.qce_root)
            if root is None:
                raise SystemExit(f"未找到 QCE 目录。{source or '请先运行 install。'}")
            print(f"{root}\t（来源：{source}）")
        elif args.command == "check":
            root, source = locate(explicit=args.qce_root)
            results = collect_checks(root, source, online=args.online)
            if args.json:
                print(json.dumps([{"level": l, "item": n, "detail": d} for l, n, d in results],
                                 ensure_ascii=False, indent=2))
            else:
                print_checks(results)
            if any(level == "fail" for level, _, _ in results):
                raise SystemExit(1)
        elif args.command == "install":
            install(
                version=args.version,
                target=args.dir,
                sha_override=args.sha256,
                dry_run=args.dry_run,
                force=args.force,
                work_dir=args.work_dir,
                archive=args.archive,
            )
    except (RuntimeError, urllib.error.URLError, OSError) as error:
        raise SystemExit(f"错误：{error}") from error


if __name__ == "__main__":
    main()
