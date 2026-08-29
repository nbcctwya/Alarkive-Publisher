# Alarkive Publisher v0.0.2

一个最小可运行的 Playwright 工具：从固定格式的内容文件夹读取一份内容，依次打开小红书和百家号图文编辑器，填写标题、正文并上传图片，然后停在两个平台最终“发布”按钮之前。

当前版本不会自动登录，也绝不会点击任何平台的最终发布按钮。

## 安装

建议使用 Python 虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

默认使用电脑中已安装的 Google Chrome，因此不需要额外安装 Playwright Chromium。程序会在项目根目录使用独立的 `.browser-data/` 持久化 profile，登录状态会保存在这里，并且不会和日常 Chrome profile 混用。

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
└── images/
    ├── 1.png
    ├── 2.png
    ├── 3.png
    └── 10.png
```

- `xiaohongshu/` 和 `baijiahao/` 中各只能有一个 `.txt` 文件。
- 文件名去掉 `.txt` 后就是对应平台标题。
- TXT 文件内容就是对应平台正文，使用 UTF-8 保存。
- 两个平台共用 `images/` 中的 PNG 图片。
- 图片文件名必须是数字加 `.png`，程序会按数字排序，因此 `10.png` 会排在 `2.png` 后。

## 运行

在项目根目录执行：

```powershell
python main.py "D:\Alarkive\post-folder"
```

也可以直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe main.py .\post-folder
```

程序会先打印两个平台的标题、正文字符数和图片顺序，确认读取成功后才启动浏览器。

## 第一次登录小红书

程序会打开小红书创作中心。如果自动化 profile 尚未登录，终端会提示：

```text
Xiaohongshu is not logged in.
Please complete login manually in the browser.
Press Enter after login is complete...
```

请在浏览器中手工扫码或登录，完成后回到终端按 Enter。程序不会读取或保存用户名、密码、手机号或验证码。

## 第一次登录百家号

小红书填写完成后，程序会暂停并等待检查。按 Enter 后进入百家号。如果百家号尚未登录，终端会提示：

```text
Baijiahao is not logged in.
Please complete login manually in the browser.
Press Enter after login is complete...
```

请在浏览器中手工完成百家号登录，保持浏览器窗口打开，再回到终端按 Enter。登录状态会保存在同一个 `.browser-data/` profile 中。

## 完整 Dry Run

程序严格按下面顺序执行：

```text
小红书
↓
人工检查（按 Enter 继续）
↓
百家号
↓
人工检查（按 Enter 关闭浏览器）
```

小红书会上传图片并填写标题、正文；百家号会填写标题、正文，并通过编辑器的图片上传入口按数字顺序将共享图片插入正文。完成后会显示：

```text
DRY RUN COMPLETE

The final Publish button was NOT clicked.
```

当前版本绝不会点击小红书或百家号的最终发布按钮。中途失败时会输出失败步骤和实际异常，并尽量保存截图到：

```text
debug/xiaohongshu-failure.png
debug/baijiahao-failure.png
```

浏览器会保持打开，方便人工检查；按 Enter 后才会关闭。
