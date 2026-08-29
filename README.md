# Alarkive Publisher v0.1.0

当前项目包含两个相互独立的部分：

1. Web Content Manager：在网页中创建、上传、查看多平台图文任务，并自动生成 Alarkive Package。
2. Existing Publisher Dry Run：沿用 v0.0.3 的 Playwright 流程，读取旧版内容文件夹，依次填写三个平台，然后停在发布按钮之前。

当前版本不会自动登录，也绝不会点击小红书、百家号或微信公众号的最终“发布/发表”按钮。

## Web Content Manager

### 启动

在项目根目录执行：

```powershell
python -m alarkive_publisher.web.app
```

然后访问：

```text
http://127.0.0.1:8000
```

终端也会打印访问地址。根路径会自动跳转到图文列表。Web Content Manager 当前只负责创建、列表和查看，不调用 Playwright，不管理发布状态。

### 创建图文

点击“上传图文”，填写任务名称、小红书/百家号/微信公众号三个平台的标题和正文，选择一张或多张 PNG 图片。图片可以拖动缩略图调整顺序，也提供上下移动按钮。保存后会跳转到任务详情页。

正文统一以 UTF-8 Markdown 原文保存，`**粗体**`、Emoji、换行和空行不会被 Web Content Manager 解析或改写。

所有任务保存在项目根目录的 `posts/` 中。每个任务使用系统生成的安全 ID 作为目录名，不使用标题作为路径：

```text
posts/
└── 20260829-125432-a7c3/
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

`manifest.json` 使用 v0.1 格式，包含 `schema_version`、任务 ID、名称、带时区的 `created_at`、三个平台的标题、正文路径和有序图片路径。列表按 `created_at` 倒序显示；损坏或无法解析的任务会被跳过并在终端记录警告。

### 文件保存位置

```text
posts/
```

Web Content Manager 生成的是新的 Package v0.1 格式。当前旧版 Playwright Publisher 仍读取下面“Existing Publisher Dry Run”中的旧版内容文件夹格式，暂不自动接线。

## 当前支持

- 小红书图文
- 百家号图文
- 微信公众号贴图 / 图片消息轻量图文

微信公众号部分不是传统公众号长文章编辑器，不处理文章作者、封面、摘要、群发或其他声明设置。

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

## 内容文件夹

目录必须是：

```text
post-folder/
├── xiaohongshu/
│   └── 小红书标题.txt
├── baijiahao/
│   └── 百家号标题.txt
├── wechat/
│   └── 微信贴图标题.txt
└── images/
    ├── 1.png
    ├── 2.png
    ├── 3.png
    └── 10.png
```

- 三个平台目录中各只能有一个 `.txt` 文件。
- 文件名去掉 `.txt` 后就是对应平台标题。
- TXT 文件内容就是对应平台正文或描述，使用 UTF-8 保存。
- 三个平台共用 `images/` 中的 PNG 图片。
- 图片文件名必须是数字加 `.png`，程序按数字排序，因此 `10.png` 会排在 `2.png` 后。

## 运行

在项目根目录执行：

```powershell
python main.py "D:\Alarkive\post-folder"
```

也可以直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe main.py .\post-folder
```

程序会先打印三个平台的标题、正文字符数和图片顺序，确认读取成功后才启动浏览器。

## 第一次登录

登录全部由用户手工完成，程序不会读取或保存用户名、密码、手机号或验证码。

### 小红书

如果自动化 profile 尚未登录，终端会提示：

```text
Xiaohongshu is not logged in.
Please complete login manually in the browser.
Press Enter after login is complete...
```

请在浏览器中手工扫码或登录，完成后回到终端按 Enter。

### 百家号

小红书检查完成后，按 Enter 进入百家号。如果尚未登录，终端会提示人工登录。完成后回到终端按 Enter，程序会重新检查登录状态。

### 微信公众号

百家号检查完成后，按 Enter 进入微信公众号。如果尚未登录，终端会提示：

```text
WeChat Official Account is not logged in.

Please complete login manually in the browser.
This may require scanning a QR code in WeChat.

Press Enter after login is complete...
```

请在浏览器中完成微信扫码登录。如果账号需要选择公众号，请在浏览器中手工选择目标账号，再按 Enter。登录状态会保存在同一个 `.browser-data/` profile 中。

## 完整 Dry Run

程序严格按下面顺序执行：

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

小红书会上传共享图片并填写标题、正文；百家号会填写标题、正文，并按数字顺序将图片插入正文；微信公众号会进入“贴图”而不是传统长文章编辑器，上传共享图片并填写贴图标题和描述。

完成后会显示：

```text
DRY RUN COMPLETE

The final Publish button was NOT clicked.
```

当前版本绝不会点击任何平台的最终发布按钮，也没有 `--publish` 或自动发布参数。

## 调试

中途失败时会输出失败平台和具体步骤，并尽量保存截图：

```text
debug/xiaohongshu-failure.png
debug/baijiahao-failure.png
debug/wechat-failure.png
```

浏览器会保持打开，方便人工检查；按 Enter 后才会关闭。
