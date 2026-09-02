# Alarkive Package v0.2 格式规范

状态：当前实现规范　版本：`0.2`

Alarkive Package 是 Web Content Manager 与 CLI Publisher 之间的不可变内容交换格式。
Package 保存 **Content Variant**，平台目标通过程序内的 routing 映射消费这些内容；发布运行状态另存于 `publish-state.json`，不会写回 Package。

## 1. 目录布局

```text
<package-id>/
├── manifest.json
├── content/
│   ├── public_long.md       # 按需存在
│   ├── wechat_long.md       # 按需存在
│   ├── wechat_short.md      # 按需存在
│   └── toutiao_short.md     # 按需存在
└── images/
    ├── 01.png
    ├── 02.png
    └── ...
```

只为 manifest 中实际存在的 Content Variant 生成 Markdown 文件。图片是一组共享资产；每个变体
可以在 manifest 中保存自己的图片路径和顺序，当前 Web Writer 默认让所有启用模块引用同一组图片。

## 2. manifest.json

根目录下的 [`package.schema.json`](package.schema.json) 是 v0.2 的机器可读 Schema。顶层必须包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 固定为 `"0.2"`。 |
| `id` | string | `YYYYMMDD-HHMMSS-xxxx`，且必须与目录名相同。 |
| `name` | string | 非空任务名称。 |
| `created_at` | string | 带时区的 ISO 8601 时间。 |
| `content` | object | 至少包含一个支持的 Content Variant，不能为空。 |

当前支持的 Content Variant：

- `public_long`：公域长文（百家号 + 今日头条）
- `wechat_long`：微信长文
- `wechat_short`：微信图文 / 小绿书
- `toutiao_short`：微头条

每个已存在的变体都必须包含完整的 `title`、`content_file` 和非空 `images`。变体键可以只出现
一个、两个、三个或四个；缺少变体表示没有为该内容类型准备内容，不应创建空文件。

示例：

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
      "images": ["images/01.png", "images/02.png", "images/03.png"]
    },
    "wechat_short": {
      "title": "NASA 下一台重要望远镜要来了",
      "content_file": "content/wechat_short.md",
      "images": ["images/01.png", "images/02.png", "images/03.png"]
    }
  }
}
```

一个只包含 `public_long` 的合法 Package：

```json
{
  "schema_version": "0.2",
  "id": "20260902-180000-a7c3",
  "name": "生活资讯测试",
  "created_at": "2026-09-02T18:00:00+08:00",
  "content": {
    "public_long": {
      "title": "测试标题",
      "content_file": "content/public_long.md",
      "images": ["images/01.png"]
    }
  }
}
```

双平台或三平台 Package 同理，例如可同时包含 `public_long`、`wechat_short`，或再加上
`wechat_long`。不要求四个模块全部存在。

## 3. 正文与图片

正文文件必须是 Package 内实际存在的 UTF-8 Markdown 文件，不能使用绝对路径或 `..` 路径穿越，
并且不能为空。图片必须是 Package 内实际存在的 PNG 文件；当前 Web 限制为单任务 1–20 张、单张
不超过 20 MiB、总量不超过 100 MiB。

### 3.1 长文图片 marker

`public_long` 与 `wechat_long` 都允许使用独立成行的 `[[image:N]]`：

```markdown
第一段正文。

[[image:1]]

第二段正文。
```

`N` 从 1 开始，对应该变体的 `images` 数组。marker 不能重复、不能越界、必须独占一行（允许
缩进和行尾空白），格式错误会在启动浏览器前被拒绝。两个长文变体各自拥有独立图片编排；图片
资产可以共享，但插入位置不共享。

`wechat_short` 与 `toutiao_short` 使用有序图片列表，不要求正文包含 marker。

## 4. Routing 与 Publisher

Package 不保存 `targets` 字段。程序统一定义：

```text
public_long  → baijiahao, toutiao_article
wechat_long  → wechat_article
wechat_short → wechat_image
toutiao_short → toutiao_micro
```

v0.2.0 当前已接入的 Publisher 只有：

- `baijiahao`：消费 `public_long`，可发布准备
- `wechat_image`：消费 `wechat_short`，可发布准备

`toutiao_article`、`wechat_article`、`toutiao_micro` 的内容可以保存和展示，但 Publisher 尚未接入，
详情页显示“待接入”，不会启动空 workflow。一键发布只执行当前任务中存在且已接入的目标，并复用
同一个浏览器生命周期；不会点击任何平台最终发布按钮。

## 5. Legacy Package v0.1

旧格式仍由 Loader 兼容读取，旧 Schema 保留在 [`schemas/package-v0.1.schema.json`](schemas/package-v0.1.schema.json)。
旧 `platforms` 会在内存中转换为：

- `baijiahao` → `public_long`
- `wechat` → `wechat_short`
- `xiaohongshu` 保留为 legacy-only 内容，不进入 v0.2 Web UI routing

旧三平台 Package 无需迁移、无需修改，仍可按“百家号 → 微信图文”执行。Web Content Manager 从
v0.2.0 起只写新的 v0.2 格式。

## 6. 校验职责

- JSON Schema 检查 v0.2 结构、至少一个变体和未知键。
- `alarkive_publisher/content.py` 检查路径、文件、PNG、正文和长文 marker，并兼容 v0.1。
- `alarkive_publisher/web/storage.py` 检查 Web 输入并原子写入 v0.2 Package。
- `publish-state.json` 只记录运行状态，不是 Package 内容的一部分。
