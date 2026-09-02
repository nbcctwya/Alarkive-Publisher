# Alarkive Publisher v0.1.8

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
百家号 → 微信公众号贴图（按 Package 中实际存在的平台执行）
```

Web Content Manager 负责创建 Package；CLI Publisher 负责读取 Package 并执行 Package 中实际存在的平台 Playwright Dry Run。Package 内容仍然只由 `manifest.json`、`content/` 和 `images/` 组成；运行状态另存为 Publisher sidecar。当前仍然不会点击任何平台的最终“发布/发表”按钮。

v0.1.8 继续由 Web Content Manager 在任务详情页启动后台准备流程，并在网页中查看离散步骤、登录/人工检查等待点和失败信息。每个进程同时只允许一个 Web Publisher 使用共享的 `.browser-data/` profile。

## v0.1.8 — Optional Platform Content

创建任务时，三个平台的内容均为可选，但至少要有一个完整的平台内容：

- 标题和正文同时留空：Package 不包含该平台；
- 标题和正文同时填写：Package 包含该平台；
- 只填写标题或正文：非法，服务端会拒绝创建；
- 至少需要一个平台的标题和正文都填写。

Package v0.1 仍使用 `schema_version: "0.1"`。完整发布流程会自动跳过当前 Package 中不存在的平台；
详情页也不会为缺失平台提供发布按钮。小红书 Publisher 代码保留，但小红书不会加入完整发布流程。

## v0.1.8 Changelog

- Web 创建页面允许平台内容按需留空，并由服务端校验平台成对填写。
- Package v0.1 的 `platforms` 改为至少一个平台，兼容原有三平台 Package。
- Loader、详情页和发布流程只读取并执行 manifest 中实际存在的平台。
- 不存在内容的平台不能启动单平台发布；完整流程只准备存在的百家号和微信公众号内容。
- 发布安全边界不变：Alarkive 仍不会自动点击任何平台真正的最终发布按钮。

## v0.1.7 — Independent Platform Publish

任务详情页在发布状态区保留原有的完整发布入口，并在对应平台内容卡片中提供三个不常用的单平台入口：

- `发布小红书`
- `发布百家号`
- `发布小绿书`（内部平台标识仍为 `wechat`）
- `发布全部`（位于发布状态区）

前三个入口各自只准备对应平台的内容；`发布全部` 保持原有的小红书 → 百家号 → 微信公众号完整 Workflow。单平台 Workflow 复用现有平台 Publisher、共享的 persistent `.browser-data/` profile，并在指定平台准备完成后直接等待人工检查，不会进入其他平台。单平台入口不改变 `published` / `published_at`，因此“重新置为未发布”只对应完整的“发布全部”状态。

单平台入口仍然只负责打开编辑器、上传图片、填写标题和正文。最终平台提交仍由用户手动完成，Alarkive 不会自动点击“发布”“发表”“立即发布”“确认发布”或“群发”等真正提交按钮。Package schema 仍为 `0.1`。

## v0.1.7 Changelog

- 任务详情页新增“发布小红书”“发布百家号”“发布小绿书”三个独立入口。
- 原“发布”入口更名为“发布全部”，完整三平台 Workflow 行为保持不变。
- 单平台 Workflow 复用现有平台 Publisher，只执行指定平台。
- 单平台发布仍使用共享 persistent browser profile，并保持单进程仅一个 active workflow。
- 单平台准备完成后直接进入最终人工检查，不再进入其他平台。
- 单平台按钮移动到对应平台内容卡片；单平台运行不改变完整发布的本地状态标记。
- 发布安全边界不变：Alarkive 仍不会自动点击任何平台真正的最终发布按钮。
- Package schema 仍为 `0.1`。

## v0.1.6 — Multi-platform AI Prompt Copy

创建图文页面现在为三个平台分别提供 Prompt 复制按钮：小红书使用“复制小红书 Prompt”，百家号使用“复制百家号 Prompt”，微信公众号小绿书使用“复制小绿书 Prompt”。这些 Prompt 只负责指导用户在 ChatGPT、Kimi、Claude、Gemini、DeepSeek 等 AI 对话中生成平台文案，不调用任何 AI API。

- 小红书 Prompt 侧重注意力、阅读节奏、自然交流感和精炼表达，不使用图片占位符。
- 百家号 Prompt 保持 v0.1.5 的 `[[image:N]]` 协议、动态图片编号和既有内容不变。
- 小绿书 Prompt 侧重简洁、清晰、手机阅读舒适度和信息获取效率，不使用图片占位符。
- 三个平台复用 Clipboard API、`document.execCommand("copy")` 和手动复制 fallback；小红书和小绿书即使尚未上传图片也可以复制 Prompt。

## v0.1.5 — Baijiahao Inline Images

百家号正文现在支持独立成行的 `[[image:N]]` 图片占位符。创建任务时可以点击“复制 AI 生成 Prompt”，将 Prompt 粘贴到 ChatGPT、Kimi、Claude、Gemini、DeepSeek 等 AI 工具，让 AI 生成可以直接粘贴回百家号正文输入框的内容。Publisher 会在百家号编辑器中按占位符位置插入对应图片。

`[[image:N]]` 是 Alarkive Publisher 的 control marker，不是标准 Markdown。图片编号始终对应创建页面当前显示顺序；未使用的图片会在 Publisher 执行时按原始顺序追加到正文末尾。没有占位符的旧 Package 继续使用 v0.1.4 的“所有图片追加到文末”行为。

## v0.1.5 Changelog

- 新增百家号 `[[image:N]]` 行内图片占位符协议，支持按正文位置插入图片。
- 新增根据当前图片数量和顺序动态生成的“复制 AI 生成 Prompt”按钮。
- 新增百家号正文占位符实时校验：重复、越界和未使用图片会给出提示。
- 百家号 Publisher 在执行前防御性校验占位符；重复或越界 marker 会直接失败，未使用图片追加到正文末尾。
- 没有 marker 的旧 Package 保持 v0.1.4 的末尾追加图片行为，Package schema 仍为 `0.1`。

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
cd "<项目所在目录>"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

默认使用电脑中已安装的 Google Chrome，因此不需要额外安装 Playwright Chromium。程序会在项目根目录使用独立的 `.browser-data/` 持久化 profile，登录状态会保存在这里，不会和日常 Chrome profile 混用。

如果 PowerShell 不允许激活脚本，也可以直接使用虚拟环境中的 Python：

```powershell
cd "<项目所在目录>"
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

Alarkive Package v0.1 的正式格式规范见 [`PACKAGE_FORMAT.md`](PACKAGE_FORMAT.md)，其
`manifest.json` 的机器可读 Schema 见 [`package.schema.json`](package.schema.json)。

### Web Content Manager 使用流程

1. 打开 PowerShell，进入项目所在目录。请将下面的 `<项目所在目录>` 替换为实际路径：

   ```powershell
   cd "<项目所在目录>"
   ```

2. 如果已经创建虚拟环境，先激活它：

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   如果不能激活虚拟环境，可跳过这一步，直接使用后面的 `.\.venv\Scripts\python.exe` 命令。

3. 启动 Web Content Manager：

   ```powershell
   python -m alarkive_publisher.web.app
   ```

   未激活虚拟环境时使用：

   ```powershell
   .\.venv\Scripts\python.exe -m alarkive_publisher.web.app
   ```

4. 浏览器访问 `http://127.0.0.1:8000`，按需填写平台的标题和正文，至少完成一个平台，上传 PNG 图片，然后点击“保存图文”。

5. 进入任务详情页后：

   - 点击“发布全部”，按百家号 → 微信公众号的顺序准备当前 Package 中存在的平台内容；不存在的平台会自动跳过。
   - 点击对应平台内容卡片下方的“发布小红书”“发布百家号”或“发布小绿书”，只准备该平台内容。
   - Publisher 会启动共享 persistent Chrome，并停在平台最终发布按钮之前；登录、选择公众号和最终发布都需要用户手工完成。
   - 完整流程中，百家号准备完成后如果还有微信公众号内容则点击“继续”；最后一个平台准备完成后点击“结束流程并关闭浏览器”。单平台准备完成后直接点击“结束流程并关闭浏览器”。

6. 使用完成后回到运行 Web Manager 的 PowerShell 窗口，按 `Ctrl+C` 停止服务。

任务会保存到项目根目录的 `posts/`，不需要创建或复制任何旧格式目录。上传内容必须是实际 PNG 文件（包含 PNG signature），每张不超过 20 MB，每个任务不超过 20 张且图片总大小不超过 100 MB；混合选择时，非 PNG 文件会被忽略并提示。

三个正文区域旁都提供平台 Prompt：可以点击“复制小红书 Prompt”生成适合小红书的精炼文案，点击“复制百家号 Prompt”生成带 `[[image:N]]` 图片占位符的百家号正文，或点击“复制小绿书 Prompt”生成适合微信公众号图文阅读的简洁文案。Prompt 只提供给用户复制到当前使用的 AI 工具，不会调用 AI API；小红书和小绿书不依赖图片即可复制，百家号仍需先上传图片。百家号页面会实时提示 marker 的有效性、重复引用和未使用图片情况。

Package 目录结构如下：

```text
posts/
└── 20260829-153400-a7c3/
    ├── manifest.json
    ├── publish-state.json       # Publisher Runtime sidecar，可选
    ├── content/
    │   ├── xiaohongshu.md       # 按需存在
    │   ├── baijiahao.md         # 按需存在
    │   └── wechat.md             # 按需存在
    └── images/
        ├── 01.png
        ├── 02.png
        └── 03.png
```

`manifest.json` 是 Package v0.1 的唯一元数据来源，包含任务 ID、名称、带时区的 `created_at`，以及每个已启用平台自己的（已去除首尾空白）`title`、`content_file` 和有序 `images` 列表。`platforms` 至少包含一个平台，不存在的平台不会写入 manifest，也不会生成空正文文件。

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
- manifest 中已存在平台的标题和 Markdown 正文
- manifest 指定的每个平台图片列表及顺序
- 所有正文和图片文件是否存在、是否位于 Package 内

验证通过后，Publisher 会在运行时使用 Markdown Renderer：小红书和微信公众号贴图使用可读的纯文本，百家号使用受控 HTML 富文本。包含 marker 的百家号正文会先拆分为文本块和图片块，再按顺序写入编辑器；已启用平台的标题直接使用 manifest 中的普通字符串。

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
百家号
↓
人工检查（按 Enter 继续；若无微信公众号则结束）
↓
微信公众号贴图
↓
人工检查（按 Enter 关闭浏览器）
```

CLI 仍通过控制器使用 Enter 暂停。完整流程只为 Package 中存在的百家号和微信公众号填写内容，并停在最终发布按钮之前；只有小红书的平台内容不能启动完整流程，但仍保留小红书单平台 Publisher。

## 使用 Web Publisher

在图文详情页点击“发布全部”后，任务会立即显示“已发布”，并在后台启动一个共享浏览器流程。这里的“已发布”只是 Alarkive 本地内容管理状态，表示用户点击过完整发布入口；它不表示 Alarkive 已确认任何平台真正发布成功。点击“发布小红书”“发布百家号”或“发布小绿书”只运行对应平台的准备流程，不改变这个完整发布状态。缺失平台不会显示单平台按钮，也不会被完整流程执行。

完整流程中每个已启用的平台会依次经历检查登录、打开编辑器、上传图片、填写内容和“已准备完成”。百家号准备完成后只有在任务包含微信公众号内容时才需要点击“继续”；最后一个平台准备完成后，点击“结束流程并关闭浏览器”，流程才会变为 `completed`。只有小红书的平台内容不能启动完整流程，但仍可使用小红书单平台入口。单平台流程只执行目标平台，目标平台准备完成后直接点击“结束流程并关闭浏览器”，不会显示或进入下一个平台。如果用户手工关闭浏览器，流程会记录为 `failed`，后台 worker 会检测到浏览器已退出并释放 Publisher，不会自动重新打开或恢复。

网页通过每秒轮询 `GET /api/posts/{id}/publish-state` 获取状态；单平台入口使用 `POST /posts/{id}/publish/{platform}`，其中 `platform` 为 `xiaohongshu`、`baijiahao` 或 `wechat`。整体状态包括 `idle`、`running`、`waiting`、`completed`、`failed` 和 `interrupted`；平台状态包括 `pending`、`running`、`waiting`、`ready` 和 `failed`。状态中的 `workflow_mode` 和 `target_platform` 是向后兼容的轻量运行元数据，旧 sidecar 缺少它们时仍按完整流程处理。接口另提供来源于进程内 `PublishManager` 的 `publisher_active`，用于确保存在任意 active workflow 时不会显示新的发布按钮。`completed` 只表示内容准备流程已完成，不表示平台真正发布成功。服务重启后，找不到对应后台任务的 `running`/`waiting` 状态会显示为 `interrupted`，不会自动恢复。

点击“重新置为未发布”只会把 `published` 改为 `false`、把 `published_at` 改为 `null`。它不会删除 Package 或状态文件、重跑/停止 Publisher、关闭浏览器、清空 workflow、修改 manifest/Markdown/图片、撤回平台内容或调用平台。即使 Publisher 正在运行，也只改变这两个本地标记，后台流程继续执行。

## 发布安全边界

v0.1.8 仍然绝对不会自动点击小红书、百家号或微信公众号的最终按钮，包括“发布”“发表”“立即发布”“确认发布”“群发”等。Publisher 只打开已启用平台的编辑器、上传图片、填写标题和正文，并停在最终发布页。若用户确实要发布，仍需在打开的浏览器中手工点击平台按钮。

### Package 错误

如果传入的目录不是 Package v0.1，Publisher 会在浏览器启动前报错。例如缺少 manifest 时：

```text
Error: manifest.json not found.
This folder is not a valid Alarkive Package v0.1.
```

Package Loader 是只读的，不会修改 `manifest.json`、Markdown 或图片文件。旧版 `xiaohongshu/*.txt`、`baijiahao/*.txt`、`wechat/*.txt` 目录格式不再是主流程，也不由 v0.1.8 的 `main.py` 读取。

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
