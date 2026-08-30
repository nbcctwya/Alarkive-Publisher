# Alarkive Publisher v0.1.4

当前完整工作流为：

```text
Web Content Manager
        ↓
生成 Alarkive Package v0.1
        ↓
Markdown Renderer
        ↓
CLI Publisher
        ↓
小红书 → 百家号 → 微信公众号贴图
```

Web Content Manager 负责创建 Package；CLI Publisher 负责读取 Package 并执行三个平台的 Playwright Dry Run。Package 内容仍然只由 `manifest.json`、`content/` 和 `images/` 组成；v0.1.4 的运行状态另存为 Publisher sidecar。当前仍然不会点击任何平台的最终“发布/发表”按钮。

v0.1.4 继续由 Web Content Manager 在任务详情页启动后台准备流程，并在网页中查看离散步骤、登录/人工检查等待点和失败信息。每个进程同时只允许一个 Web Publisher 使用共享的 `.browser-data/` profile。

## v0.1.4 Changelog

v0.1.4 focuses on bug fixes and workflow hardening.

- 修复平台最终 `ready` 状态在用户继续后被错误改成 `running`。
- 兼容百家号只接受单文件选择的图片输入，支持按顺序追加多张图片。
- 增强百家号图片弹窗对不同版本本地上传控件和 file input 的兼容性。
- 修复微信公众号正文换行结构被编辑器以文本节点保留时的误报失败。
- 修复失败后用户手工关闭浏览器导致 Publisher 永久占用的问题。
- 用进程级 active workflow 状态统一 SSR、轮询和发布按钮行为。
- 创建 Package 时自动去除三个平台标题的首尾空白，正文原样保留。
- 增强 PNG 签名校验，并限制单张图片 20 MB、单任务总计 100 MB、最多 20 张。
- 统一 Package `created_at` 必须包含 timezone 的校验。
- 新增回归测试和 GitHub Actions CI。

## 安装

建议使用 Python 虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

默认使用电脑中已安装的 Google Chrome，因此不需要额外安装 Playwright Chromium。程序会在项目根目录使用独立的 `.browser-data/` 持久化 profile，登录状态会保存在这里，不会和日常 Chrome profile 混用。

如果 PowerShell 不允许激活脚本，也可以直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果电脑没有安装 Google Chrome，可以安装 Playwright Chromium，并设置：

```powershell
python -m playwright install chromium
$env:ALARKIVE_BROWSER_CHANNEL = "chromium"
```

如需把自动化 profile 放到其他目录，可设置：

```powershell
$env:ALARKIVE_BROWSER_DATA_DIR = "D:\Alarkive\browser-data"
```

## 创建 Alarkive Package

启动 Web Content Manager：

```powershell
python -m alarkive_publisher.web.app
```

访问：

```text
http://127.0.0.1:8000
```

在网页中填写任务名称、三个平台的标题和正文，选择 PNG 图片并调整顺序，然后点击“保存图文”。任务会保存到项目根目录的 `posts/`，不需要创建或复制任何旧格式目录。上传内容必须是实际 PNG 文件（包含 PNG signature），每张不超过 20 MB，每个任务不超过 20 张且图片总大小不超过 100 MB；混合选择时，非 PNG 文件会被忽略并提示。

Package 目录结构如下：

```text
posts/
└── 20260829-153400-a7c3/
    ├── manifest.json
    ├── publish-state.json       # Publisher Runtime sidecar，可选
    ├── content/
    │   ├── xiaohongshu.md
    │   ├── baijiahao.md
    │   └── wechat.md
    └── images/
        ├── 01.png
        ├── 02.png
        └── 03.png
```

`manifest.json` 是 Package v0.1 的唯一元数据来源，包含任务 ID、名称、带时区的 `created_at`，以及每个平台自己的（已去除首尾空白）`title`、`content_file` 和有序 `images` 列表。

`publish-state.json` 不属于 Package 内容，也不是 `manifest.json` 的一部分。旧任务没有这个文件时，Web 界面按“未发布 / workflow idle”处理；Publisher 会在需要时以原子方式创建或更新它。Package Loader 不要求该文件存在。

正文使用 UTF-8 Markdown 原文保存。Package Loader 不会删除 `**`、转换 HTML、解析富文本或修改换行、空行、中文和 Emoji。Publisher 运行时才根据平台渲染 Markdown，Package 文件本身不会被改写。

## 使用 CLI Publisher

直接把 Web 生成的 Package 目录传给 `main.py`：

```powershell
python main.py ".\posts\20260829-153400-a7c3"
```

也可以使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe main.py ".\posts\20260829-153400-a7c3"
```

Publisher 会在启动浏览器前完整读取并验证：

- `manifest.json` 和 `schema_version`
- Package ID 与目录名
- 三个平台的标题和 Markdown 正文
- manifest 指定的每个平台图片列表及顺序
- 所有正文和图片文件是否存在、是否位于 Package 内

验证通过后，Publisher 会在运行时使用 Markdown Renderer：小红书和微信公众号贴图使用可读的纯文本，百家号使用受控 HTML 富文本。三个标题仍然直接使用 manifest 中的普通字符串。

## Markdown Renderer

Package 始终保存 Markdown Source，例如：

```markdown
**64GB：甜点**
```

Publisher 根据平台处理：

- 小红书：`64GB：甜点`，不带 `**`
- 百家号：使用真正的 `<strong>` 粗体语义
- 微信公众号贴图：`64GB：甜点`，不带 `**`

当前支持段落、标题、粗体、斜体、无序/有序列表、引用、行内代码和链接。复杂 Markdown 会安全降级为可读文本，不保证完整视觉样式。原始 Markdown、manifest 和图片不会被 Renderer 修改。

验证通过后才会启动 persistent Chrome profile，并按以下顺序运行：

```text
小红书
↓
人工检查（按 Enter 继续）
↓
百家号
↓
人工检查（按 Enter 继续）
↓
微信公众号贴图
↓
人工检查（按 Enter 关闭浏览器）
```

CLI 仍通过控制器使用 Enter 暂停。三个平台仍然都只填写内容、上传图片并停在最终发布按钮之前。

## 使用 Web Publisher

在图文详情页点击“发布”后，任务会立即显示“已发布”，并在后台启动一个共享浏览器流程。这里的“已发布”只是 Alarkive 本地内容管理状态，表示用户点击过 Web 页面里的“发布”；它不表示 Alarkive 已确认小红书、百家号和微信公众号真正发布成功。

每个平台会依次经历检查登录、打开编辑器、上传图片、填写内容和“已准备完成”。需要扫码登录、选择公众号或人工检查时，页面会显示等待状态和“继续”按钮。三个平台共用同一个浏览器窗口：小红书或百家号准备完成后请保持浏览器打开，并在网页点击“继续”；微信公众号准备完成后，点击“结束流程并关闭浏览器”，流程才会变为 `completed`。如果用户提前手工关闭浏览器，流程会记录为 `failed`，后台 worker 会检测到浏览器已退出并释放 Publisher，不会自动重新打开或恢复。

网页通过每秒轮询 `GET /api/posts/{id}/publish-state` 获取状态。整体状态包括 `idle`、`running`、`waiting`、`completed`、`failed` 和 `interrupted`；平台状态包括 `pending`、`running`、`waiting`、`ready` 和 `failed`。接口另提供来源于进程内 `PublishManager` 的 `publisher_active`，用于确保存在任意 active workflow 时不会显示新的“发布”按钮。`completed` 只表示三个编辑器的内容准备流程已完成，不表示平台真正发布成功。服务重启后，找不到对应后台任务的 `running`/`waiting` 状态会显示为 `interrupted`，不会自动恢复。

点击“重新置为未发布”只会把 `published` 改为 `false`、把 `published_at` 改为 `null`。它不会删除 Package 或状态文件、重跑/停止 Publisher、关闭浏览器、清空 workflow、修改 manifest/Markdown/图片、撤回平台内容或调用平台。即使 Publisher 正在运行，也只改变这两个本地标记，后台流程继续执行。

## 发布安全边界

v0.1.4 仍然绝对不会自动点击小红书、百家号或微信公众号的最终按钮，包括“发布”“发表”“立即发布”“确认发布”“群发”等。Publisher 只打开编辑器、上传图片、填写标题和正文，并停在最终发布页。若用户确实要发布，仍需在打开的浏览器中手工点击平台按钮。

### Package 错误

如果传入的目录不是 Package v0.1，Publisher 会在浏览器启动前报错。例如缺少 manifest 时：

```text
Error: manifest.json not found.
This folder is not a valid Alarkive Package v0.1.
```

Package Loader 是只读的，不会修改 `manifest.json`、Markdown 或图片文件。旧版 `xiaohongshu/*.txt`、`baijiahao/*.txt`、`wechat/*.txt` 目录格式不再是主流程，也不由 v0.1.4 的 `main.py` 读取。

## 手工登录

登录全部由用户手工完成，程序不会读取或保存用户名、密码、手机号或验证码。

如果自动化 profile 尚未登录，程序会在终端提示，并保持浏览器打开等待人工登录。微信可能还需要手工选择目标公众号。登录状态会保存在 `.browser-data/` 中。

## 调试

中途失败时会输出失败平台和具体步骤，并尽量保存截图：

```text
debug/xiaohongshu-failure.png
debug/baijiahao-failure.png
debug/wechat-failure.png
```

浏览器会保持打开，方便人工检查；按 Enter 后才会关闭。
