# Alarkive Publisher v0.1.2

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

Web Content Manager 负责创建 Package；CLI Publisher 负责读取 Package 并执行三个平台的 Playwright Dry Run。两者只通过 `manifest.json`、`content/` 和 `images/` 通信。当前仍然不会点击任何平台的最终“发布/发表”按钮。

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

在网页中填写任务名称、三个平台的标题和正文，选择 PNG 图片并调整顺序，然后点击“保存图文”。任务会保存到项目根目录的 `posts/`，不需要创建或复制任何旧格式目录。

Package 目录结构如下：

```text
posts/
└── 20260829-153400-a7c3/
    ├── manifest.json
    ├── content/
    │   ├── xiaohongshu.md
    │   ├── baijiahao.md
    │   └── wechat.md
    └── images/
        ├── 01.png
        ├── 02.png
        └── 03.png
```

`manifest.json` 是 Package v0.1 的唯一元数据来源，包含任务 ID、名称、带时区的 `created_at`，以及每个平台自己的 `title`、`content_file` 和有序 `images` 列表。

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

三个平台仍然都只填写内容、上传图片并停在最终发布按钮之前。没有 Web 发布按钮、自动发布选项或 `--publish` 参数。

### Package 错误

如果传入的目录不是 Package v0.1，Publisher 会在浏览器启动前报错。例如缺少 manifest 时：

```text
Error: manifest.json not found.
This folder is not a valid Alarkive Package v0.1.
```

Package Loader 是只读的，不会修改 `manifest.json`、Markdown 或图片文件。旧版 `xiaohongshu/*.txt`、`baijiahao/*.txt`、`wechat/*.txt` 目录格式不再是主流程，也不由 v0.1.2 的 `main.py` 读取。

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
