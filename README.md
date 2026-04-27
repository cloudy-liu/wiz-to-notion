# Wiz To Notion

把 Markdown 笔记目录导入到 Notion，适合 Wiz 迁移场景。

推荐流程：

1. 用 `wiz-to-obsidian` 把 Wiz 导出为 Markdown。
2. 用本项目把这批 Markdown 导入 Notion。

本项目只负责 `Markdown -> Notion`。只要输入是 Markdown 目录，不要求一定来自 Wiz。

## 功能

- 递归扫描 Markdown，默认跳过 `_wiz` 元数据目录。
- 按本地目录结构在 Notion 下创建子页面。
- 去掉 Wiz 导出 frontmatter，不把 YAML 显示到正文里。
- 识别本地图片和附件，上传到 Notion。
- 写入 `_wiz/notion_import_state.json` 和 `_wiz/notion_import_report.json`，支持断点续跑和结果追踪。
- 失败后优先复用已存在的唯一页面，避免重复导入时反复新建页面。
- 提供 `cleanup-duplicates` 命令，清理历史重复创建的冗余页面。

## 准备

1. 创建 Notion integration，拿到 `NOTION_TOKEN`。
2. 在目标 Notion 页面执行 `... -> Add connections`，把这个 integration 加进去。
3. 准备好导出的 Markdown 目录。

## 配置

复制 `.env.example` 为 `.env`，至少填写：

```env
NOTION_TOKEN=ntn_your_integration_secret
NOTION_PARENT_PAGE_ID=your_notion_page_id_or_url
WIZ_TO_NOTION_SOURCE_DIR=path/to/exported-markdown
NOTION_VERSION=2026-03-11
```

`WIZ_TO_NOTION_SOURCE_DIR` 和 `NOTION_PARENT_PAGE_ID` 需要显式提供，不再内置默认值。

## 使用

源码运行：

```powershell
python -m pip install -r requirements.txt
python .\scripts\import_wiz_to_notion.py --dry-run
python .\scripts\import_wiz_to_notion.py
```

安装成 CLI：

```powershell
python -m pip install -e .
wiz-to-notion scan
wiz-to-notion import --dry-run
wiz-to-notion import
```

常用参数：

- `--limit 10`
- `--skip-assets`
- `--no-resume`

打包后的单文件版本会优先读取当前工作目录的 `.env`；如果当前目录没有 `.env`，会回退到二进制所在目录查找。

## 清理历史重复页

先 dry-run 查看计划：

```powershell
python .\scripts\import_wiz_to_notion.py cleanup-duplicates
```

确认后执行真实清理：

```powershell
python .\scripts\import_wiz_to_notion.py cleanup-duplicates --apply
```

这个命令会：

- 根据本地 Markdown 目录计算每个父页面下的期望子页面数量。
- 结合 `_wiz/notion_import_state.json` 优先保留当前 state 对应的页面。
- 仅把超额重复页移到 Notion 回收站。

## Release

GitHub Release 会自动产出三平台 x64 压缩包：

- `wiz-to-notion-vX.Y.Z-windows-x64.zip`
- `wiz-to-notion-vX.Y.Z-linux-x64.zip`
- `wiz-to-notion-vX.Y.Z-macos-x64.zip`

每个压缩包都包含：

- CLI 二进制
- `.env.example`

下载后解压，复制 `.env.example` 为 `.env`，先执行 `import --dry-run`，确认无误后再正式导入。

## 限制

- 本地资源会上传到 Notion；Notion 不能直接引用你的本地文件路径。
- 相对 `.md` 内部链接目前不会自动改写成 Notion 页面链接。
- 超过 `--max-upload-mb` 的文件会跳过，默认 20 MB。
- 若 Notion 返回 `429`，客户端会按 `Retry-After` 或指数退避重试。
- 某些云主机或代理出口可能被 Notion/Cloudflare 拦截；此时建议换执行环境，而不是重复重试同一出口 IP。
