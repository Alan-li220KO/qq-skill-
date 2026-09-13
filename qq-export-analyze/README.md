# qq-export-analyze

把 QQ 群聊记录变成可执行的事件摘要：导出群聊 → 提炼事件、参与者和明确截止日期。

这是一个面向 AI Agent 的 **Skill**（技能包），本体只有几百 KB：它通过本机 HTTP API 调用
你**自行安装**的 [QQ Chat Exporter](https://github.com/shuakami/qq-chat-exporter)（QCE），
**不包含、也不修改**其任何二进制文件。

## 能做什么

- 导出指定 QQ 群聊为 JSON（群号或唯一群名均可）
- 从导出结果生成事件分析报告：已明确事项 / 待确定事项两张表，含"截至日期、事件、跟进人、当前状态"
- 只提取明确说出的截止时间，保留"国庆前"这类相对时间原话，不臆造日期
- 图片未识别时如实标注，不猜测图片内容

## 前置要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | **Windows 10 / 11 x64**（导出链路依赖 NapCat 的 Windows Shell 包；仅分析已有 JSON 不受平台限制） |
| QQ 客户端 | QQNT **34606+**，推荐 `9.9.19-34740`（[下载](https://im.qq.com/)） |
| Python | 3.9+，需带 **tkinter**（扫码窗口用；官方 CPython / Anaconda 自带） |
| 其他 | 如启动报缺少 DLL，安装 [VC++ 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe) |

NapCat / QCE 由安装脚本从上游官方 Release 下载，**不需要手动准备**。

## 安装

把整个 `qq-export-analyze` 目录放进任一 skill 根目录：

| 位置 | 生效范围 |
| --- | --- |
| `<项目>/.dsh/skills/qq-export-analyze` | 当前项目（推荐） |
| `~/.dsh/skills/qq-export-analyze` | 本机所有 DSH 会话 |
| `~/.agents/skills/qq-export-analyze` | 兼容多种 Agent 运行时 |

## 首次使用

```powershell
cd <skill>/scripts

# 1. 体检：平台、QQ 版本、VC++ 运行库、端口、访问令牌
python qce_env.py check

# 2. 没有 QCE 就装上（自动挑对应平台的官方 Release，校验 sha256）
python qce_env.py install --dry-run   # 先看要下载什么
python qce_env.py install             # 默认装到 %LOCALAPPDATA%\NapCat-QCE

# 2b. 下载节点连不上时（国内常见）：手动下载 zip 后离线安装
#     官方包：https://github.com/shuakami/qq-chat-exporter/releases
python qce_env.py install --archive .\NapCat-QCE-Windows-x64-v6.3.0.zip

# 3. 扫码登录（QQ 没在运行时去掉 --no-launch）
python qce_qr_login.py --no-launch
```

`check` 报 `[X]` 的项要处理，`[!]` 项按需处理。登录成功后 QCE 服务会监听
`http://127.0.0.1:40653`，访问令牌自动从 `~/.qq-chat-exporter/security.json` 读取，
不需要手动粘贴。

## 常用命令

```powershell
python qce_env.py locate                       # 打印自动定位到的 QCE 目录
python qce_env.py check --online               # 顺带对比上游最新版本
python qce_env.py install --version 6.2.10     # 固定版本安装
python qce_env.py install --archive pkg.zip    # 离线安装（自带断点续传失败时的退路）
python qce_env.py install --work-dir D:\tmp    # 指定下载解压的临时目录

python qce_automation.py groups                # 列出群聊与群号
python qce_automation.py resolve-group --name 摘星班
python qce_automation.py export --group-code 1055176754 --format JSON --wait
python qce_automation.py schedules             # 查看定时导出（只在明确需要时创建）

python test_login_flow.py                      # 登录流程回归测试
python test_qce_env.py                         # 依赖定位/安装回归测试
```

## 目录结构

```
qq-export-analyze/
├── SKILL.md                  # 给 Agent 的指令
├── README.md                 # 本文件
├── LICENSE                   # 本 skill 的许可
├── THIRD-PARTY.md            # 第三方软件与许可声明（发布前请务必阅读）
├── agents/openai.yaml        # 兼容 OpenAI Agent Skill 的接口描述
└── scripts/
    ├── qce_env.py            # 依赖定位 / 体检 / 安装
    ├── qce_automation.py     # QCE HTTP API 封装（导出、群列表、定时任务）
    ├── qce_qr_login.py       # 隐藏启动器 + 扫码登录窗口
    ├── test_qce_env.py       # 依赖管理回归测试
    ├── test_login_flow.py    # 登录流程回归测试
    └── vendor/               # 随包携带的 qrcode / colorama（BSD）
```

导出产物默认落在 `%USERPROFILE%\Documents\QQChatExporter\exports\`；
分析报告默认落在工作区的 `reports/qq-chat/{群号}_{安全群名}/`。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `check` 报 QQ 版本过低 | 升级 QQNT 到 34606+，推荐 `9.9.19-34740` |
| 启动提示缺少 DLL | 安装 VC++ 运行库（见上表） |
| 端口 40653 未监听 | 需要先扫码登录；登录成功后 QCE 才会启动 |
| 二维码扫了没反应 | 脚本会自动调 `RefreshQRcode` 换新码；连续失败会写日志并关窗，读 `qce-qr-login.log` 看原因 |
| `403 无效的访问令牌` | `~/.qq-chat-exporter/security.json` 已重新生成，删掉旧的 `QCE_TOKEN` 环境变量 |
| 提示找不到 QCE 目录 | `python qce_env.py check`，必要时 `install` 或设 `QCE_ROOT` |
| `install` 下载超时/连接被重置 | 脚本会自动重试并断点续传；仍失败说明本机访问不了 GitHub 下载节点，手动下载 zip 后 `install --archive <zip>` |
| 安装时校验和不匹配 | 下载被中间人改写或版本对不上，删掉重下；确认版本可用 `--version` 固定 |
| 扫码窗口一闪而过 | 属正常：登录成功会短暂显示"登录成功"后自动关闭 |

## 许可

本 skill 自身许可见 [LICENSE](LICENSE)。

它依赖并调用第三方软件，**这些软件不包含在本仓库中**，各有自己的许可与限制
（特别是 NapCat 仅允许非商业再分发）。使用与分发前请阅读 [THIRD-PARTY.md](THIRD-PARTY.md)。

NapCat 属于非官方的 QQ 客户端改造，请自行评估账号风险并遵守腾讯相关服务条款。
