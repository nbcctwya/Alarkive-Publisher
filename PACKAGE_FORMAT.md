# Alarkive Package v0.1 格式规范

状态：当前实现规范

版本：`0.1`

本文档定义 Alarkive Publisher 使用的 Alarkive Package v0.1。除非另有说明，
“必须”（MUST）表示不符合即不是有效的 Package；“应该”（SHOULD）表示推荐做法；
“可以”（MAY）表示可选行为。

## 1. Package 的用途

Alarkive Package 是 Web Content Manager 与 CLI Publisher 之间的文件交换格式。
它将一个任务的元数据、一个或多个平台的 Markdown 正文和图片放在同一个目录中。

Package 是内容输入，不是发布结果记录。Publisher 只读取 Package 内容并准备平台编辑器，
不会修改 `manifest.json`、Markdown 或图片。

## 2. 目录布局

一个 Package 必须位于一个以 Package ID 命名的目录中：

```text
<package-id>/
├── manifest.json
├── content/
│   ├── xiaohongshu.md       # 按需存在
│   ├── baijiahao.md         # 按需存在
│   └── wechat.md             # 按需存在
└── images/
    ├── 01.png
    ├── 02.png
    └── ...
```

Web Content Manager 当前生成的标准布局使用以上正文文件名和两位数字图片文件名；只会为 manifest
中实际存在的平台生成正文文件。
`content_file` 和 `images` 中的路径使用 `/` 作为分隔符，并且都是相对于 Package 根目录的路径。

`publish-state.json` 如果存在，是 Publisher 的运行状态 sidecar，不属于 Package v0.1 内容，
也不是 `manifest.json` 的一部分。Package Loader 不要求它存在。

## 3. manifest.json

`manifest.json` 必须是 UTF-8 编码的 JSON 对象，并且必须符合项目根目录下的
[`package.schema.json`](package.schema.json)。Schema 描述 JSON 结构、字段类型和基本约束；
文件是否存在、文件内容是否有效等需要由 Package 实现层继续检查。

### 3.1 顶层字段

| 字段 | 类型 | 必须 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 固定为 `"0.1"`。 |
| `id` | string | 是 | 格式为 `YYYYMMDD-HHMMSS-xxxx`，其中 `xxxx` 是 4 位小写十六进制字符。 |
| `name` | string | 是 | 非空任务名称；不能只包含空白字符。 |
| `created_at` | string | 是 | 带时区的 RFC 3339/ISO 8601 日期时间，例如 `2026-08-29T15:34:00+08:00`。 |
| `platforms` | object | 是 | 至少一个支持平台的内容定义；不要求三个平台全部存在。 |

`id` 必须与 Package 的目录名完全相同。实现不得仅根据 `manifest.json` 中的 `id` 猜测目录。

### 3.2 platforms

`platforms` 至少包含以下支持平台中的一个键，也可以包含其中两个或三个：

- `xiaohongshu`：小红书
- `baijiahao`：百家号
- `wechat`：微信公众号图文（界面中也称“小绿书”）

某个平台键不存在，表示该 Package 没有为该平台准备内容。`platforms` 不能是空对象，也不能包含
未知平台键。

每个平台的值必须是对象，并包含以下字段：

| 字段 | 类型 | 必须 | 说明 |
| --- | --- | --- | --- |
| `title` | string | 是 | 非空平台标题。Web Writer 会去除标题首尾空白。 |
| `content_file` | string | 是 | 指向该平台正文文件的 Package 相对路径，必须位于 `content/` 下。 |
| `images` | array[string] | 是 | 至少包含一项的有序 PNG 路径列表，路径必须位于 `images/` 下。 |

同一张图片可以被多个平台引用；每个平台的图片列表及其顺序独立生效。
Publisher 按对应平台 `images` 数组的顺序上传图片。

### 3.3 manifest 示例

```json
{
  "schema_version": "0.1",
  "id": "20260829-153400-a7c3",
  "name": "Bridge 测试",
  "created_at": "2026-08-29T15:34:00+08:00",
  "platforms": {
    "xiaohongshu": {
      "title": "小红书标题",
      "content_file": "content/xiaohongshu.md",
      "images": ["images/01.png", "images/02.png"]
    },
    "baijiahao": {
      "title": "百家号标题",
      "content_file": "content/baijiahao.md",
      "images": ["images/01.png", "images/02.png"]
    },
    "wechat": {
      "title": "微信公众号标题",
      "content_file": "content/wechat.md",
      "images": ["images/01.png", "images/02.png"]
    }
  }
}
```

单平台 Package 只保留实际存在的平台，例如：

```json
{
  "schema_version": "0.1",
  "id": "20260902-170000-a7c3",
  "name": "生活资讯测试",
  "created_at": "2026-09-02T17:00:00+08:00",
  "platforms": {
    "baijiahao": {
      "title": "测试标题",
      "content_file": "content/baijiahao.md",
      "images": ["images/01.png", "images/02.png"]
    }
  }
}
```

双平台 Package 的 `platforms` 可以同时包含 `baijiahao` 和 `wechat`；缺少的
`xiaohongshu` 不需要创建空的 Markdown 文件。完整三平台 Package 仍使用上面的原有结构。

## 4. 正文文件

每个 `content_file` 必须：

1. 位于 Package 根目录内，不能使用绝对路径、`..` 或其他路径穿越形式；
2. 是实际存在的普通文件；
3. 使用 UTF-8 编码；
4. 包含至少一个非空白字符；
5. 保存 Markdown 原文。

正文中的换行、空行、中文、Emoji 和 Markdown 标记由 Package 原样保存。Publisher 在运行时
根据目标平台渲染正文，不会回写或改写源文件。

当前 Renderer 支持段落、标题、粗体、斜体、无序/有序列表、引用、行内代码和链接；复杂 Markdown
可能安全降级为可读文本，但这不改变 Package 中保存的原文。

### 4.1 百家号图片 marker

百家号正文可以使用独立成行的 `[[image:N]]` marker，其中 `N` 从 `1` 开始，对应该平台
`images` 数组中的第 `N` 项。例如：

```markdown
第一段正文。

[[image:1]]

第二段正文。
```

该 marker 是 Publisher 的控制协议，不是标准 Markdown，也不改变 `manifest.json` 的结构。
它只在 `baijiahao` 正文中生效。marker 必须独占一行（允许缩进和行尾空白），不能重复，且不能
超出该平台图片数组的范围。未被 marker 使用的图片会按原数组顺序追加到正文末尾；没有 marker
时，所有图片沿用末尾追加行为。

## 5. 图片文件

每个 `images` 引用必须：

1. 指向 Package 根目录内实际存在的文件；
2. 使用 `.png` 扩展名（大小写不敏感）；
3. 文件内容具有有效 PNG signature，而不只是改名后的其他格式；
4. 在当前 Web Content Manager 限制下，单张不超过 20 MiB，单个 Package 图片总量不超过 100 MiB；
5. 在当前 Web Content Manager 限制下，单个 Package 最多包含 20 张图片。

`images` 数组的顺序是平台上传顺序，也是 `[[image:N]]` 的编号基础。实现不得因为文件名排序
而覆盖 manifest 中声明的顺序。

## 6. 校验与兼容性

Publisher 在启动浏览器前验证：

- `manifest.json` 存在、可解析且 `schema_version` 为 `0.1`；
- Package ID 合法并与目录名一致；
- manifest 中已存在平台的标题、正文和图片列表完整；
 - `platforms` 至少存在一个平台，缺失的平台被跳过；
- 正文和图片文件存在且位于 Package 内；
- 正文是可读取的 UTF-8 文本；
- 图片引用为 PNG，且百家号 marker 没有重复或越界。

校验失败时，Publisher 必须在浏览器启动前报告错误，并且不得修改 Package 文件。

v0.1 的旧版 `xiaohongshu/*.txt`、`baijiahao/*.txt`、`wechat/*.txt` 目录格式不属于当前主流程。
`main.py` 不把这些旧目录当作 Alarkive Package v0.1 读取。

## 7. 规范与实现的关系

- `package.schema.json` 是 `manifest.json` 的机器可读结构规范。
- 本文档是 Package 目录、文件内容和运行行为的正式说明。
- `alarkive_publisher/content.py` 是 CLI Loader 的运行时安全校验实现。
- `alarkive_publisher/web/storage.py` 是 Web Content Manager 的生成和输入校验实现。

如果实现发现 Schema 与代码行为不一致，应先保持对已有 v0.1 Package 的兼容，并同步更新本文档、
Schema、实现和测试。
