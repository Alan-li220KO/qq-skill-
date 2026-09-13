# 第三方软件与许可声明

本 skill **不打包、不修改**下列软件的任何二进制或源码。`scripts/qce_env.py install`
只是从上游官方 Release 下载，并校验 sha256；运行时的登录与导出也全部通过本机 HTTP API 调用。

如果你是**分发本 skill 的人**（发布仓库、打包给别人、写进镜像），请先读完本文。

## 依赖的软件

### QQ Chat Exporter (QCE)

- 项目：<https://github.com/shuakami/qq-chat-exporter>
- 许可：**GPL-3.0**（上游发布说明原文："本项目完全免费且开源，遵循 GPL-3.0 开源协议"）
- 本 skill 的用法：仅调用其本机 HTTP API（`127.0.0.1:40653`），不复制其代码
- 注意：GPL-3.0 是强 copyleft。**若你选择把它的二进制打进自己的仓库**，需履行 GPL 的
  再分发义务（附带许可、提供对应源码等）。本 skill 默认不做这件事，用户从上游下载。

### NapCatQQ

- 项目：<https://github.com/NapNeko/NapCatQQ>
- 许可：**Limited Redistribution License for NapCat**（Copyright © 2024 Mlikowa，
  <https://github.com/NapNeko/NapCatQQ/blob/main/LICENSE>），要点：
  1. 未经主要作者明确许可，禁止未授权的使用、复制、修改或分发；
  2. **允许再分发，但必须附上本许可全文，并清楚注明来源与版权**；
     允许为再分发做小幅修改，但**修改后的代码不得公开发布**；
  3. **仅限非商业用途**；
  4. 本许可未明确授予的权利，须向主要作者申请并获得授权。
- 本 skill 的用法：只启动其官方 Shell 包中的 `launcher-user.bat`，不修改其中任何文件
- `NapCat-QCE-*-vX.Y.Z.zip` 这类上游整包**同时包含 QCE 与 NapCat**。把整包塞进你自己的
  仓库即构成对 NapCat 的再分发，上面第 2、3 条就会适用。

### QQ 客户端（QQNT）

- 归属：腾讯，专有软件，<https://im.qq.com/>
- 本 skill **不下载、不分发** QQ 本体，只读取其安装路径与版本号做兼容性检查
- NapCat 属非官方客户端改造，使用前请自行评估账号风险并遵守腾讯服务条款

## 随包携带的依赖

以下两个 Python 包为纯本地 vendoring，用于扫码窗口，无网络与系统依赖：

| 包 | 版本 | 许可 | 许可文件 |
| --- | --- | --- | --- |
| [qrcode](https://github.com/lincolnloop/python-qrcode) | 8.2 | BSD | `scripts/vendor/qrcode-8.2.dist-info/LICENSE` |
| [colorama](https://github.com/tartley/colorama) | 0.4.6 | BSD | `scripts/vendor/colorama-0.4.6.dist-info/licenses/LICENSE.txt` |

两者均为 BSD 许可，允许随包分发，但**必须保留上述许可文件**。发布前请勿删除。

## 分发前自查清单

- [ ] 仓库里没有 NapCat / QCE 的二进制（不包含 `napcat.mjs`、`qce-server.exe`、
      `NapCatWinBootMain.exe`、`launcher-user.bat` 等）
- [ ] 保留了 `scripts/vendor/` 下两个 BSD 许可文件
- [ ] README 中说明了"本 skill 不包含也不修改其二进制"
- [ ] 若确实要再分发 NapCat，已附上其许可全文、注明来源版权，且用途非商业
- [ ] 没有把访问令牌、二维码内容、聊天记录提交进仓库（见 `.gitignore`）

> 本文档是对上游许可原文的转述，不构成法律意见。正式发布前请自行核对上游最新许可条款。
