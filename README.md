# Alarkive Publisher v0.2.4

本版接入微信公众号长文，支持五种发布方式：百家号、今日头条文章、微信公众号长文、微信图文、微头条。自动化负责准备内容，最终发布仍由用户手动操作。

Alarkive Publisher 负责把研究内容整理为标准化的 Alarkive Package，并为已经接入的渠道准备发布页面。当前版本的内容模型是：

```text
Research / Content
        ↓
Content Variant
        ↓
Platform Routing
        ↓
Publisher
```

当前真正可运行的 Publisher 是：

- `baijiahao`：消费 `public_long`（公域长文）
- `toutiao_article`：消费 `public_long`（公域长文）
- `wechat_article`：消费 `wechat_long`（微信公众号长文）
- `wechat_image`：消费 `wechat_short`（微信图文 / 小绿书）
- `toutiao_micro`：消费 `toutiao_short`（微头条）

v0.2.3 新增已有 Package 的文章编辑：四种 Content Variant 自动回填，v0.2 原地更新，v0.1 在保存时原地升级为 v0.2；任务 ID、名称、创建时间和发布状态保持不变，未选择替换时沿用原图片。旧小红书正文文件保留但不重新纳入 v0.2 内容模块。

v0.2.2 新增微头条自动化预发布，浏览器逻辑独立放在 `alarkive_publisher/toutiao_micro.py`。只读取微头条自己的标题、正文和有序图片列表；当前微头条页面没有独立标题栏，因此将标题作为文字首段，与正文用空行分隔。正文转为可读文字并保留换行、空段，图片按 Package 顺序上传到独立图片区，不使用 `[[image:N]]`，也不向正文插图。完成文字、图片及重绘后的校验后，停在人工检查步骤，不点击“发布”或“存草稿”，不宣称远程草稿已经保存。

v0.2.1 加固了今日头条文章自动化预发布：复用 `public_long` 标题、正文和图片，通过真实 Chrome 按 `[[image:N]]` 插图，并验证正文持久性、逻辑图片数量和图文顺序。真实 Windows Chrome 中的完整正文、4 图末尾追加及 4 图按标记插入已获用户肉眼确认；最终发布始终由用户操作。

已知限制：此前实测头条自动草稿请求返回业务错误码 `7050`，页面持续显示“草稿保存中”。v0.2.2 中，标题、正文、图片及发布区域检查通过后，保存状态超过 30 秒仍未结束会显示“尚未确认保存成功”的提示，并进入等待人工检查状态。用户在头条页面处理保存状态后，可点击“继续到下一个发布平台”；单平台则可结束流程。程序不会把提示当作保存成功，也不会尝试点击发布来解决。文字、图片或发布区域检查失败仍会中止流程。详见 [版本说明](CHANGELOG.md)。

微信公众号长文的浏览器逻辑独立位于 `alarkive_publisher/wechat_article.py`，仅读取 `wechat_long` 的标题、完整正文和有序图片。通过公众号“文章”入口打开新编辑器，保留 Markdown 段落、标题和强调；按 `[[image:N]]` 插入对应图片，未引用图片按原顺序追加末尾，无标记则全部追加。发现编辑器恢复了已有内容时停止，避免覆盖。预发布完成后停留在编辑器等待人工检查，不点击发表、发布、群发、提交或保存为草稿；页面自动保存不等于本程序已经确认远程草稿持久保存。

小红书已经从 Web UI、内容表单、详情页和完整 workflow 中移除；底层 `xiaohongshu.py` 与 `run_xiaohongshu()` 保留为 legacy publisher，供旧集成使用。

## v0.2 Content Variant

一个任务可以包含以下四种模块中的任意一个或多个：

- `public_long`：公域长文（百家号 + 今日头条）
- `wechat_long`：微信长文
- `wechat_short`：微信图文 / 小绿书
- `toutiao_short`：微头条

每个模块的标题和正文遵循 Optional Content 规则：

- 标题和正文同时留空：不保存该模块；
- 标题和正文同时填写：保存该模块；
- 只填写其中一个：服务端拒绝创建；
- 四个模块全部为空：服务端拒绝创建；
- 一个任务至少需要一个完整模块。

Web Content Manager 新建任务写入 Package v0.2，格式见 [`PACKAGE_FORMAT.md`](PACKAGE_FORMAT.md)，当前 Schema 见 [`package.schema.json`](package.schema.json)。v0.1 Schema 保留在 [`schemas/package-v0.1.schema.json`](schemas/package-v0.1.schema.json)。

## Package 示例

```json
{
  "schema_version": "0.2",
  "id": "20260902-180000-a7c3",
  "name": "Roman 太空望远镜",
  "created_at": "2026-09-02T18:00:00+08:00",
  "content": {
    "public_long": {
      "title": "Roman 太空望远镜到底有多强？",
      "content_file": "content/public_long.md",
      "images": ["images/01.png", "images/02.png"]
    },
    "wechat_short": {
      "title": "NASA 下一台重要望远镜要来了",
      "content_file": "content/wechat_short.md",
      "images": ["images/01.png", "images/02.png"]
    }
  }
}
```

图片是任务级共享资产，但每个 Content Variant 都保存自己的 ordered image list，因此未来可以独立调整图片选择和顺序。`public_long`、`wechat_long` 支持独立成行的 `[[image:N]]` marker；短内容使用有序图片组，不要求 marker。

## 兼容 Package v0.1

Loader 同时支持 Package v0.1 和 v0.2，旧 Package 不需要迁移：

```text
v0.1 baijiahao → public_long
v0.1 wechat    → wechat_short
v0.1 xiaohongshu → legacy-only field
```

因此旧的百家号 + 微信公众号 Package 仍可执行：

```text
百家号 → 今日头条文章 → 微信图文
```

旧 `manifest.json` 不会被 Loader 改写；Web Manager 只会创建新的 v0.2 Package。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

程序默认使用本机 Google Chrome，并在项目根目录使用独立的 `.browser-data/` profile。也可以按需安装 Playwright Chromium 并设置 `ALARKIVE_BROWSER_CHANNEL=chromium`。

公共浏览器启动工具位于 `alarkive_publisher/browser.py`（`start_browser()`），公共异常与步骤执行工具位于 `alarkive_publisher/publisher_common.py`（`PublisherError`、`run_step()`）。各平台发布器直接使用这些公共模块，不再从小红书发布器借用工具；小红书旧导入路径保留兼容引用。

## Web Content Manager

启动：

```powershell
python -m alarkive_publisher.web.app
```

访问 `http://127.0.0.1:8000`。创建页面提供四个 Content Variant 模块和四种对应 AI Prompt：公域长文、微信长文、微信图文、微头条。Prompt 只供复制，不调用 AI API，也不会自动修改已保存的 Package。

详情页将内容按 Content Variant 展示，将发布区域按 Platform Target 展示：

```text
有内容 + Publisher 已接入 → 可发布
有内容 + Publisher 未接入 → 待接入
没有对应 Content Variant → 无内容
```

一键发布的定义是“当前任务中有内容且 Publisher 已接入的所有平台”。当前顺序为百家号 → 今日头条文章 → 微信公众号长文 → 微信图文 → 微头条，共享一个 Chrome 持久化 profile 生命周期，并始终停在最终发布按钮之前，不会自动点击发布、发表或群发。

详情页的“发布微头条”按钮只启动微头条单平台准备流程。微头条最多上传 18 张图片，单图不超过 20MB；若编辑器恢复了已有文字或图片，程序会停止并保留页面，避免覆盖或混入旧内容。用户清空页面后可重新启动。

真实 Chrome 单平台复测（默认按 manifest 的 `created_at` 选取 `posts/` 中最新 Package）：

```powershell
python scripts/debug_toutiao_micro.py
# 或指定一个 Package：
python scripts/debug_toutiao_micro.py ".\posts\20260902-233140-40e3"
```

此脚本调用现有 `run_single_platform_workflow()`，到达预发布状态后保留 Chrome；输入 `state` 可再次截图，按回车结束并关闭浏览器。诊断保存在忽略版本管理的 `debug/` 中，Package 文件保持不变。

微信公众号长文使用详情页“发布微信公众号长文”按钮。真实 Chrome 单平台复测同样默认选择 `posts/` 中创建时间最新的 Package：

```powershell
python scripts/debug_wechat_article.py --append-images  # 第一步：完整正文，所有图片追加末尾
python scripts/debug_wechat_article.py                  # 最终模式：按 [[image:N]] 插图
```

`--append-images` 仅在内存中移除 marker，不改写 Package。图片逐张上传，等待微信素材 ID、加载状态及图片地址稳定，校验全文、图片数量和图文顺序，再复查编辑器重绘后的结果。截图和诊断写入 `debug/wechat_article-时间戳/`；默认保留 Chrome 供检查，输入 `state` 再次截图，回车关闭；自动回归可加 `--auto-close`。

## CLI Publisher

```powershell
python main.py ".\posts\20260902-180000-a7c3"
```

CLI 会在启动浏览器前校验 manifest、正文、图片和长文 marker，然后准备当前 Package 中已接入的发布目标。只有未接入 Publisher 的内容时，流程会明确失败且不会启动浏览器。

## 测试

```powershell
python -m unittest discover -s tests
```

GitHub Actions 使用同一条测试命令。
