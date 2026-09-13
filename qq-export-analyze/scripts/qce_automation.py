#!/usr/bin/env python3
"""调用 QQ Chat Exporter（QCE）本机 HTTP API 的小型命令行工具。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "http://127.0.0.1:40653"
DEFAULT_TOKEN_FILE = Path.home() / ".qq-chat-exporter" / "security.json"


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def local_token(token_file: Path = DEFAULT_TOKEN_FILE) -> str | None:
    """读取本机 QCE 写入的访问令牌；令牌只在内存中使用，不打印、不落盘。"""
    try:
        value = json.loads(token_file.read_text(encoding="utf-8")).get("accessToken")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


class QceClient:
    def __init__(self, base_url: str, token: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"无法连接 QCE：{error.reason}") from error
        if not result.get("success", False):
            error_data = result.get("error", {})
            message = error_data.get("message", "未知错误") if isinstance(error_data, dict) else str(error_data)
            raise RuntimeError(message)
        return result["data"]


def resource_options(download_resources: bool) -> dict:
    options = {
        "includeResourceLinks": True,
        "includeSystemMessages": True,
        "preferGroupMemberName": True,
    }
    if not download_resources:
        options["skipDownloadResourceTypes"] = ["image", "video", "audio", "file"]
    return options


def group_name(client: QceClient, group_code: str, supplied_name: str | None) -> str:
    if supplied_name:
        return supplied_name
    groups = client.request("GET", "/api/groups?limit=200").get("groups", [])
    for group in groups:
        if str(group.get("groupCode")) == group_code:
            return str(group.get("groupName") or group_code)
    return group_code


def command_groups(client: QceClient, args: argparse.Namespace) -> None:
    result = client.request("GET", f"/api/groups?limit={args.limit}")
    print_json(result.get("groups", []))


def command_resolve_group(client: QceClient, args: argparse.Namespace) -> None:
    groups = client.request("GET", "/api/groups?limit=200").get("groups", [])
    matches = [group for group in groups if group.get("groupName") == args.name]
    if len(matches) != 1:
        if not matches:
            raise RuntimeError(f"未找到群名为“{args.name}”的群聊；请提供群号。")
        codes = "、".join(str(group.get("groupCode", "未知")) for group in matches)
        raise RuntimeError(f"群名“{args.name}”有多个匹配（群号：{codes}）；请指定群号。")
    print_json(matches[0])


def command_schedules(client: QceClient, _: argparse.Namespace) -> None:
    print_json(client.request("GET", "/api/scheduled-exports"))


def command_export(client: QceClient, args: argparse.Namespace) -> None:
    name = group_name(client, args.group_code, args.group_name)
    payload = {
        "peer": {"chatType": 2, "peerUid": args.group_code},
        "sessionName": name,
        "format": args.format,
        "options": resource_options(args.download_resources),
    }
    task = client.request("POST", "/api/messages/export", payload)
    print_json(task)
    if not args.wait:
        return
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        time.sleep(2)
        task = client.request("GET", f"/api/tasks/{task['taskId']}")
        print_json(task)
        status = str(task.get("status", "")).lower()
        if status == "completed":
            file_path = task.get("filePath")
            if not isinstance(file_path, str) or not file_path:
                raise RuntimeError("导出任务已完成但未返回 filePath。")
            output_path = Path(file_path)
            if args.format == "JSON" and output_path.suffix.lower() != ".json":
                raise RuntimeError(f"导出任务返回的文件不是 JSON：{output_path}")
            try:
                if not output_path.is_file() or not output_path.stat().st_size:
                    raise RuntimeError(f"导出任务返回的文件不可读或为空：{output_path}")
                if args.format == "JSON":
                    with output_path.open("r", encoding="utf-8-sig") as source:
                        json.load(source)
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"导出任务返回的 JSON 不可读或格式错误：{output_path}（{error}）") from error
            return
        if status not in {"queued", "pending", "running"}:
            error_data = task.get("error", {})
            detail = error_data.get("message", "") if isinstance(error_data, dict) else str(error_data)
            suffix = f"：{detail}" if detail else ""
            raise RuntimeError(f"导出任务未完成（状态：{task.get('status', '未知')}）{suffix}")
    raise RuntimeError("等待导出超时；可用任务 ID 稍后再次查询。")


def command_create_daily(client: QceClient, args: argparse.Namespace) -> None:
    name = group_name(client, args.group_code, args.group_name)
    payload = {
        "name": args.name or f"{name}-每日备份",
        "sessionName": name,
        "peer": {"chatType": 2, "peerUid": args.group_code},
        "scheduleType": "daily",
        "executeTime": args.time,
        "timeRangeType": "yesterday",
        "format": args.format,
        "enabled": True,
        "options": resource_options(args.download_resources),
    }
    print_json(client.request("POST", "/api/scheduled-exports", payload))


def command_trigger(client: QceClient, args: argparse.Namespace) -> None:
    print_json(client.request("POST", f"/api/scheduled-exports/{args.schedule_id}/trigger"))


def command_history(client: QceClient, args: argparse.Namespace) -> None:
    print_json(
        client.request("GET", f"/api/scheduled-exports/{args.schedule_id}/history?limit={args.limit}")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QCE 本机 HTTP API 自动化工具")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="QCE 服务地址")
    parser.add_argument("--token", help="临时 QCE Token；优先使用 QCE_TOKEN 环境变量")
    parser.add_argument("--token-env", default="QCE_TOKEN", help="读取 Token 的环境变量名")
    parser.add_argument(
        "--token-file",
        type=Path,
        default=DEFAULT_TOKEN_FILE,
        help="本机 QCE 安全配置路径（默认读取 accessToken 字段）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    groups = subparsers.add_parser("groups", help="列出群聊及群号")
    groups.add_argument("--limit", type=int, default=200)
    groups.set_defaults(handler=command_groups)

    resolve = subparsers.add_parser("resolve-group", help="按群名解析唯一群聊")
    resolve.add_argument("--name", required=True, help="必须唯一的群名")
    resolve.set_defaults(handler=command_resolve_group)

    schedules = subparsers.add_parser("schedules", help="列出现有定时任务")
    schedules.set_defaults(handler=command_schedules)

    export = subparsers.add_parser("export", help="立即导出一个群聊")
    export.add_argument("--group-code", required=True, help="groups 返回的 groupCode")
    export.add_argument("--group-name", help="可选；省略则自动查询")
    export.add_argument("--format", choices=["JSON", "TXT", "HTML", "EXCEL"], default="JSON")
    export.add_argument("--download-resources", action="store_true", help="下载图片、视频等附件")
    export.add_argument("--wait", action="store_true", help="等待任务完成并持续显示状态")
    export.add_argument("--timeout", type=int, default=600, help="--wait 的最长秒数")
    export.set_defaults(handler=command_export)

    daily = subparsers.add_parser("create-daily", help="创建每天导出昨天消息的任务")
    daily.add_argument("--group-code", required=True, help="groups 返回的 groupCode")
    daily.add_argument("--group-name", help="可选；省略则自动查询")
    daily.add_argument("--name", help="定时任务名称")
    daily.add_argument("--time", default="02:30", help="每天执行时间，格式 HH:MM")
    daily.add_argument("--format", choices=["JSON", "TXT", "HTML", "EXCEL"], default="JSON")
    daily.add_argument("--download-resources", action="store_true", help="下载图片、视频等附件")
    daily.set_defaults(handler=command_create_daily)

    trigger = subparsers.add_parser("trigger", help="立即运行一个已有的定时任务")
    trigger.add_argument("schedule_id", help="schedules 返回的 id")
    trigger.set_defaults(handler=command_trigger)

    history = subparsers.add_parser("history", help="查看定时任务的执行历史")
    history.add_argument("schedule_id", help="schedules 返回的 id")
    history.add_argument("--limit", type=int, default=10)
    history.set_defaults(handler=command_history)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    token = os.environ.get(args.token_env) or args.token or local_token(args.token_file)
    if not token:
        token = getpass.getpass("QCE Token（输入不回显）: ")
    try:
        args.handler(QceClient(args.base_url, token), args)
    except RuntimeError as error:
        raise SystemExit(f"错误：{error}") from error


if __name__ == "__main__":
    main()
