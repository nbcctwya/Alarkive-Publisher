# Alarkive Publisher v0.0.1

一个最小可运行的 Playwright 工具：读取固定格式的内容文件夹，打开小红书创作中心，上传 PNG、填写标题和正文，然后停在最终“发布”按钮之前。

本版本不会自动登录，也绝不会点击最终发布按钮。

## 安装

建议使用 Python 虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果 PowerShell 不允许激活脚本，也可以直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

默认使用已安装的 Google Chrome，因此不需要额外安装 Playwright Chromium。

程序默认使用 Windows 当前用户已安装的 Google Chrome，并在项目根目录使用独立的 `.browser-data/` 持久化 profile。这个 profile 不会和你平时打开的 Chrome 冲突，首次运行时在这个窗口中人工登录一次，之后就可以复用登录状态。

当前 Chrome 版本不允许 Playwright 直接接管日常使用的默认 `User Data` profile，因此不能安全地直接复用你主 Chrome 窗口里的登录状态。Chrome 136 起默认数据目录会拒绝远程调试；这是 Chrome 的安全限制。

如需把自动化 profile 放到其他目录，可设置：

```powershell
$env:ALARKIVE_BROWSER_DATA_DIR = "D:\Alarkive\browser-data"
```

如果电脑没有安装 Google Chrome，可以安装 Playwright Chromium，并设置：

```powershell
python -m playwright install chromium
$env:ALARKIVE_BROWSER_CHANNEL = "chromium"
```

## 准备内容文件夹

目录必须是：

```text
test-post/
├── 一个标题.txt
└── images/
    ├── 1.png
    ├── 2.png
    ├── 3.png
    └── 4.png
```

- 只能有一个 `.txt` 文件。
- `.txt` 文件名去掉扩展名后就是标题。
- `.txt` 文件内容就是正文，使用 UTF-8 保存。
- 图片放在 `images/` 中，并使用 `1.png`、`2.png`、`10.png` 这样的数字文件名。
- 图片会按数字顺序上传，而不是按字符串字典序上传。

## 运行

在项目根目录执行：

```powershell
python main.py "D:\Alarkive\test-post"
```

也可以传入相对路径：

```powershell
python main.py .\test-post
```

启动浏览器前，程序会在终端打印读取到的标题、正文字符数和图片顺序。

## 第一次登录

第一次运行时会打开小红书创作中心。如果没有登录，终端会提示：

```text
Xiaohongshu is not logged in.
Please complete login manually in the browser.
Press Enter after login is complete...
```

请在这个自动化 Chrome 窗口中手工扫码或登录，完成后回到终端按 Enter。程序不会读取或保存用户名、密码、手机号或验证码。登录状态保存在项目根目录的 `.browser-data/`，该目录已加入 `.gitignore`，不会提交到 Git。

## Dry Run

程序会进入图文发布页面，按数字顺序上传图片，填写标题和正文，并打印：

```text
DRY RUN COMPLETE

The final Publish button was NOT clicked.
Please inspect the post manually in the browser.
```

此时请在浏览器中人工检查内容。确认无误后回到终端按 Enter，程序才会关闭浏览器。

如果中途失败，程序会输出失败步骤和实际异常，并尽量保存截图到 `debug/failure.png`；浏览器会保持打开，按 Enter 后才关闭。
