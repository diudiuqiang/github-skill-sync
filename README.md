# github-skill-sync

WorkBuddy 的 **skill 与 GitHub 双向同步**工具 skill。它干两件事：

1. **GitHub → 本地（下载同步）**：把本地已安装的 skill（`~/.workbuddy/skills/*`）与它的 GitHub 上游仓库比对版本并安全更新——零下载的 git-blob SHA 差异比对、冲突分类（`github_updated` / `local_modified` / `conflict`）、精准备份后更新、SHA 校验。
2. **本地 → GitHub（反向发布，新增）**：把本地自建 / 修改后的 skill **首次发布或增量更新到 GitHub 仓库**，绕开被墙的 `git` 协议，走 `api.github.com` REST API。

## 目录结构

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 主入口：网络约束、双向工作流、冲突分类、反向发布流程与坑 |
| `scripts/publish_skill.py` | 反向发布脚本（纯标准库，无第三方依赖） |

## ⚡ 本仓库的发布方式（重点）

> **这个 skill 仓库本身，就是用它自带的 `scripts/publish_skill.py`（新脚本）发布到 GitHub 的——典型的"用新脚本上传自己"。**

为什么这样发、脚本怎么跑：

- **网络约束**：本机 `github.com:443` 被墙，`git push` 不可用；唯一可达通道是 `api.github.com` REST API。
- **发布流程**（`publish_skill.py` 封装）：`GET /user` 验证 token → 幂等探测仓库（`GET /repos/{login}/{repo}`，404 才 `POST /user/repos` 创建，**不删库重建**）→ 遍历 `collect_files()` → 逐文件 `PUT /repos/{login}/{repo}/contents/{path}`（**新仓库首个 `PUT` 自动初始化 `main` 分支**；已存在文件先 `GET` 拿 `sha` 再带 `sha` `PUT` 防 `409`）→ `GET /git/trees/{branch}?recursive=1` 校验远端文件数。
- **中文无乱码**：`.md` / `.py` 的中文路径与正文统一 base64 编码；`urllib.parse.quote(path)` 默认 `safe='/'` 保留分隔符、编码中文，正是 API 所需。

对自己（或任意本地 skill）发布 / 增量更新：

```bash
set GITHUB_TOKEN=你的经典PAT(需 repo 权限，用完建议撤销)
python scripts/publish_skill.py --skill-dir C:/Users/youqw/.workbuddy/skills/github-skill-sync --repo github-skill-sync
```

可选参数：`--private`（建为私有库）、`--dry-run`（只列文件、不发请求）、`--login`（指定 owner）、`--message`（自定义提交信息）。

## 安全约定

- PAT **仅经环境变量 `GITHUB_TOKEN` 传入，不写盘、不回显**；用完可在 GitHub → Settings → Developer settings 撤销。
- 经典 PAT 默认无 `delete_repo`，故脚本一律「先探测后创建」，绝不依赖删库重建。

## 使用方式

- **检查 / 更新本地 skill**：在 WorkBuddy 调用 `github-skill-sync`，按 SKILL.md 第 1~6 节走「扫描来源 → 取上游树 → SHA 比对 → 冲突分类 → 备份更新 → 校验」。
- **发布本地 skill 到 GitHub**：直接跑上面的 `publish_skill.py` 命令。
