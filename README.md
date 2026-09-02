# Alarkive Publisher v0.2.1.1

Alarkive Publisher 负责把研究内容整理为不可变的 Alarkive Package，并为已经接入的渠道准备发布页面。当前版本的内容模型是：

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
- `wechat_image`：消费 `wechat_short`（微信图文 / 小绿书）

v0.2.1.1 加固了今日头条文章正文插图：使用正文编辑器作用域内的上传控件、明确的上传状态验证，以及插入后的图片数量检查。

内容模型已经预留但尚未接入 Publisher 的目标是：微信公众号长文、微头条。它们可以保存、读取和展示，但不会启动空的浏览器流程。

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

一键发布的定义是“当前任务中有内容且 Publisher 已接入的所有平台”。当前顺序为百家号 → 今日头条文章 → 微信图文，共享一个 Chrome 持久化 profile 生命周期，并始终停在最终发布按钮之前，不会自动点击发布、发表或群发。

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
