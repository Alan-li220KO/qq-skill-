# qq-export-analyze

把 QQ 群聊变成一张能执行的表：**谁定了什么事、谁跟进、截止到哪天**。

不逐条复述闲聊，只留事件和结论。

## 长什么样

下面是真实产物（群「2026羽协纳新咨询群」140 条消息，截取一部分）：

| 截至日期/时间 | 事件 | 谁提到/跟进 | 结论与当前状态 |
| --- | --- | --- | --- |
| 国庆前（原话，未给具体日期） | 面试安排 | 南柯一梦、Doll 提问；内建部-吴国荣 答复 | 国庆前安排面试，会陆续以短信通知；后续补充为"估计开学的一到两周内" |
| 未提及 | 面试形式与内容 | 浅、Doll 提问；内建部-吴国荣 答复 | 面试不用打比赛，就是正常面试形式；具体内容"不能剧透" |
| 未提及 | 群内骗子清理 | ^-^ 举报；宣传部部长-林靖桉 处置 | 已确认是骗子，骗子及拉其入群者已踢出（09-12 08:58 处理完毕） |

报告分「已明确事项」和「待确定事项」两块，完整产物见
`reports/qq-chat/{群号}_{群名}/{时间}_事件分析.md`。

三个刻意的设计，用之前先知道：

- **"截至日期"只填聊天里明说的时间**。发送时间不算截止时间，没说就写"未提及"
- **"国庆前"这类相对时间保留原话**，不替你换算成具体日期
- **图片内容不识别**，报告里如实标注，不猜

## 怎么用

直接在对话里说就行，比如：

> 分析一下「2026羽协纳新咨询群」
>
> 帮我导出「摘星班」的聊天记录
>
> 分析这个文件：D:\xxx\group_xxx.json

它会自己完成**定位群 → 导出 JSON → 读消息 → 写报告**，最后只回你报告路径。

第一次用会弹出一个扫码窗口，**手机 QQ 扫一下**就行（二维码过期会自动换新码）。
之后 QQ 一直登着的话，再喊它就直接干活。

## 装在哪

把 `qq-export-analyze` 整个目录放进任一 skill 根目录，**不用放多份**：

| 位置 | 生效范围 |
| --- | --- |
| `<工作区>/.dsh/skills/` | 只在那个工作区（推荐） |
| `~/.dsh/skills/` | 本机所有会话 |
| `~/.agents/skills/` | 兼容其他 Agent 运行时 |

## 第一次的配置

需要 Windows 10/11 x64，以及装了 QQNT（**34606+**，推荐 `9.9.19-34740`，[下载](https://im.qq.com/)）。
NapCat / QCE 不用自己准备，下面第 2 步会从上游官方 Release 拉（带 sha256 校验）。

```powershell
cd <skill>/scripts

python qce_env.py check      # 体检：QQ 版本、运行库、端口、令牌，缺什么会直接说
python qce_env.py install    # 没装 QCE 就装（装到 %LOCALAPPDATA%\NapCat-QCE）
python qce_qr_login.py       # QQ 没在运行时用这条；已经在跑就加 --no-launch
```

`check` 输出里的 `[X]` 是要处理的，`[!]` 按需处理。全 `[OK]` 就可以直接用了。

## 命令行

不想走对话时，脚本可以单独用：

```powershell
python qce_env.py locate                       # 现在用的是哪个 QCE 目录
python qce_env.py install --dry-run            # 先看要下载什么，不落盘
python qce_env.py install --archive pkg.zip    # 下载节点连不上时：手动下 zip 后离线安装
python qce_env.py check --online               # 顺带比对上游最新版本

python qce_automation.py groups                # 列出群聊与群号
python qce_automation.py export --group-code 1055176754 --format JSON --wait

python test_login_flow.py                      # 自检：登录流程（11 条）
python test_qce_env.py                         # 自检：依赖管理（15 条）
```

## 出问题

| 现象 | 处理 |
| --- | --- |
| 提示找不到 QCE 目录 | `python qce_env.py check`；还不行就 `install`，或设 `QCE_ROOT` 环境变量 |
| 扫码窗口一闪就没了 | 正常，登录成功后会短暂显示"登录成功"再自动关闭 |
| 扫码没反应 | 脚本会自动换新码；连续失败会写 `qce-qr-login.log` 并关窗，去日志看原因 |
| 窗口一直不动 | 等 300 秒会自己超时关窗并在日志写明原因，不会一直挂着 |
| `check` 报 QQ 版本过低 | 升级 QQNT 到 34606+ |
| 启动报缺少 DLL | 装 [VC++ 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| `install` 下载超时/被重置 | 会自动重试并断点续传；仍失败说明访问不了 GitHub 下载节点，改成 `--archive <zip>` |
| 端口 40653 没监听 | 还没登录成功，QCE 是登录后才启动的 |
| `403 无效的访问令牌` | 删掉环境里过期的 `QCE_TOKEN`，脚本会自己从本机配置读新的 |

## 文件说明

```
qq-export-analyze/
├── SKILL.md                 给 Agent 的指令（用法规则都在这里）
├── scripts/
│   ├── qce_env.py           依赖定位 / 体检 / 安装
│   ├── qce_automation.py    QCE 接口封装（群列表、导出）
│   ├── qce_qr_login.py      隐藏启动器 + 扫码登录窗口
│   ├── test_*.py            两个回归测试
│   └── vendor/              扫码用的两个库（BSD，随包携带）
└── agents/openai.yaml       跨运行时接口描述
```

导出产物在 `%USERPROFILE%\Documents\QQChatExporter\exports\`，报告在工作区的 `reports\qq-chat\`。

## 依赖与许可

本 skill 只通过本机 HTTP 接口调用你自己安装的
[QQ Chat Exporter](https://github.com/shuakami/qq-chat-exporter)（GPL-3.0）和
[NapCat](https://github.com/NapNeko/NapCatQQ)（仅限非商业再分发），
**不打包也不修改它们的二进制**——`install` 只做官方 Release 下载与校验。
自用无需关心；要公开分发请看 [THIRD-PARTY.md](THIRD-PARTY.md)。

NapCat 属于非官方 QQ 客户端改造，账号风险自行评估。
