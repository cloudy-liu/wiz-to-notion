# Wiz To Notion

把 `D:\notes\wiz-import-complete` 里的 Markdown 笔记一键导入到 Notion。

默认目标页是你给的页面：

```text
https://www.notion.so/Android-3487344d42e7808a9a14c9787050ab7a
```

工具会做这些事：

- 递归扫描 Markdown，默认跳过 `_wiz` 元数据目录。
- 按本地目录结构在 Notion 下创建子页面。
- 使用 Notion `2026-03-11` 的 `markdown` 参数创建笔记页面。
- 去掉导出文件开头的 Wiz frontmatter，不把 YAML 显示到 Notion 正文里。
- 检测本地图片和附件，默认上传到 Notion，并追加到页面末尾的 `Imported local assets` 区域。
- 写入 `_wiz/notion_import_state.json`，中断后再次运行会跳过已经导入且内容未变的笔记。
- 写入 `_wiz/notion_import_report.json`，记录导入结果、失败项和未解析链接。

## 需要你准备

1. 创建一个 Notion integration，并拿到 internal integration token，形如 `ntn_...`。
2. 在 Notion 目标页面右上角 `...` -> `Add connections`，把这个 integration 加进去。
3. integration 需要至少开启 `Insert content` 能力；如果之后要读取/校验页面，再开启 `Read content`。

## 安装

```powershell
python -m pip install -r requirements.txt
```

也可以用当前目录的 `.venv`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env`：

```powershell
Copy-Item .env.example .env
```

填写：

```env
NOTION_TOKEN=ntn_your_integration_secret
NOTION_PARENT_PAGE_ID=https://www.notion.so/Android-3487344d42e7808a9a14c9787050ab7a
WIZ_TO_NOTION_SOURCE_DIR=D:\notes\wiz-import-complete
NOTION_VERSION=2026-03-11
```

## 先预览

不会调用 Notion，也不会写 state：

```powershell
python .\scripts\import_wiz_to_notion.py --dry-run
```

限制前 3 篇用于检查：

```powershell
python .\scripts\import_wiz_to_notion.py --dry-run --limit 3
```

## 一键导入

```powershell
python .\scripts\import_wiz_to_notion.py
```

只导入前 10 篇：

```powershell
python .\scripts\import_wiz_to_notion.py --limit 10
```

不上传本地图片/附件，只导入正文：

```powershell
python .\scripts\import_wiz_to_notion.py --skip-assets
```

重新导入并忽略已有 state：

```powershell
python .\scripts\import_wiz_to_notion.py --no-resume
```

注意：`--no-resume` 会创建新页面，可能产生重复内容。

## 扫描源目录

```powershell
python .\scripts\import_wiz_to_notion.py scan
```

## 说明和限制

- Notion 的 markdown 创建接口可以很好处理正文 Markdown，但不能直接引用本地文件路径，所以工具会把本地图片/附件上传后追加到页面末尾。
- 大于 `--max-upload-mb` 的文件会跳过，默认 20 MB；Notion 小文件直传接口也以 20 MB 为常用边界。
- 相对的 `.md` 内部链接目前只保留原文并写入报告，不会自动改成 Notion 页面链接。
- 若 Notion 返回 429，客户端会按 `Retry-After` 或指数退避重试。

