---
name: github-skill-sync
description: 把本地安装的 skill（~/.workbuddy/skills/*）与其 GitHub 上游仓库比对版本并安全更新；同时支持把本地自建/修改后的 skill 发布或增量更新到 GitHub 仓库（绕过被墙的 git 协议，走 api.github.com REST API）。覆盖：扫描 GitHub 来源、git-blob SHA 零下载差异比对、冲突分类（github_updated / local_modified / conflict）、备份精准更新、SHA 校验、以及反向的 Contents API 发布/更新（幂等建库、逐文件 PUT、带 sha 增量更新、tree 验证）。触发词：skill 更新、skill 比对、同步 skill、检查 skill 版本、更新本地技能、skill 是否最新、发布 skill、上传 skill、skill 发布到 github、skill 推送到 github、更新 github 仓库、把 skill 发到 github。
agent_created: true
version: 1.0.0
---

# GitHub Skill 同步

把本地 skill 与 GitHub 上游对齐。**核心原则：本地自定义内容绝不覆盖，先备份，再更新，最后校验。**

## 网络约束（本机实测，务必遵守）

| 端点 | 状态 |
|---|---|
| `api.github.com` | ✅ 可达（未认证 **60 次/小时**） |
| `api.github.com/repos/{r}/tarball/{branch}` | ✅ 可达（重定向 codeload） |
| `github.com:443` | ❌ 连接被重置（git clone 不可用） |
| `raw.githubusercontent.com` | ❌ GET 超时不通 |

**推论**：
- 优先用 **tarball 整包下载**（1 次调用拿全部文件），不要逐个 `git/blobs` 拉取（33 个文件就会超时被杀）。
- 大 blob（>300KB）经 `git/blobs` 常 `IncompleteRead`，必须带指数退避重试，或改走 tarball。
- 谨慎花额度：递归 tree 一次调用即可拿到全部 path+sha，比对靠本地算 SHA，**不消耗下载额度**。

## 工作流

### 1. 枚举本地 skill 并定位 GitHub 来源
```python
import os, re
base = os.path.expanduser("~/.workbuddy/skills")
```
递归扫 `SKILL.md` 及 `.md/.json/.txt/.py/.yaml` 中的 `github.com/[\w.\-]+/[\w.\-]+` 正则。
扫不到仓库地址的：用 `api.github.com/search/repositories?q={name}+skill` 搜；仍 0 结果且 frontmatter 有 `agent_created: true` 的 → **本机自建，跳过**。

### 2. 取上游递归文件树
```python
info  = GET /repos/{repo}                      # 拿 default_branch
tree  = GET /repos/{repo}/git/trees/{branch}?recursive=1
```
本地缓存成 `github_skill_cache.json`，后续比对离线进行。

### 3. 零下载差异比对（关键技巧）
本地按 **git blob SHA** 算法算每个文件，与 tree 中 sha 直接比：
```python
def git_sha(p):
    d = open(p, "rb").read()
    return hashlib.sha1(b"blob " + str(len(d)).encode() + b"\x00" + d).hexdigest()
```
产出三类：**behind**（本地落后）/ **localOnly**（本地独有，勿删）/ **ghOnly**（上游新增）。
排除 `.github/`、`archive/` 前缀。

### 4. 冲突分类 —— 决定能否覆盖
对 behind 文件，逐行集合比较 `local` vs `github`：
- `github_updated`：本地行 ⊆ 上游行 → **可覆盖**
- `local_modified`：上游行 ⊆ 本地行（本地加了东西）→ **保护，不覆盖**
- `conflict`：两边各有对方没有的行 → **默认不覆盖**，需用户拍板
- `identical`：SHA 不同但内容同行（换行/编码差异）→ 覆盖无害

### 5. 备份 → 更新 → 校验（三步不可省）
```python
shutil.copytree(os.path.join(SK, name), os.path.join(BAK, name))
```
只写 behind 文件与 ghOnly 中的**必需**文件（README/CHANGELOG/LICENSE/tests/docs 通常非必需）。
更新后**重算 SHA 与目标 sha 逐一比对**，报告 `N/M 对齐`。

### 6. 路径错位处理（常见坑）
上游把技能放在**子目录**（如 `KKKKhazix/human-writing` 的技能实际在 `human-writing/`，`tanweai/pua` 的根 SKILL.md 对应 `skills/pua/SKILL.md`）。
→ 校验时必须用**正确参照路径**比对，否则会误报 MISMATCH。
→ 展开 tarball 时剥掉顶层目录：`name.split("/", 1)[1]`。

## 合并策略（用户选"合并上游新版"时）
以**上游新版全文为基底**，把本地独有行追加为附录，防丢定制：
```
{上游全文}

---

## 本地保留备注（从旧版保留，新版未包含，请按需整合进正文）

- {本地独有行1}
- {本地独有行2}
```
写完后**必须校验 frontmatter**：`re.match(r"^---\n(.*?)\n---\n", txt, re.S)`，确认 `name:` 正确，否则 skill 无法加载。

## 安全审计（装新 skill 时才需要，更新已有 skill 不需要）
审 `scripts/*.py`：禁网络请求 / subprocess / eval / exec。
审含 JS 的 HTML 模板：禁 fetch / 外部请求 / 数据外传。

## 7. 发布 / 更新本地 skill 到 GitHub（反向同步，新增）

前 6 节解决「GitHub → 本地」。本节解决反向：**本地自建 / 修改后的 skill 首次发布或增量更新到仓库**。本机 `github.com:443` 被墙、无法 `git push`，**只能走 `api.github.com` REST API**（见顶部网络约束）。

### 前置
- 需要 GitHub **经典 PAT**（`repo` 权限），由用户提供；**仅经环境变量 `GITHUB_TOKEN` 传入，不落盘、不回显**，用完提醒用户可在 GitHub 撤销。
- 本机无 `gh` CLI 也不影响——纯 REST API。

### 流程（已实测可用）
1. **验证 token**：`GET /user` 拿 `login`。
2. **幂等探测仓库**：`GET /repos/{login}/{repo}`
   - `404` → `POST /user/repos` 创建（公开 / 私有由用户定，`auto_init:false` 即可）。
   - `200` → 已存在，走更新流程。
   - ⚠️ **经典 PAT 默认无 `delete_repo`**（`DELETE` 返回 403），故**先探测后创建**，绝不依赖「删库重建」。
3. **逐文件上传 / 更新**，用 **Contents API** `PUT /repos/{login}/{repo}/contents/{path}`：
   - **新仓库**：首个 `PUT` 自动初始化 `main` 分支 + 初始提交，无需预先提交。
   - **文件不存在**（`GET` 拿 `sha` 返回 `None`）→ `PUT` 不带 `sha`。
   - **文件已存在** → 必须先 `GET` 拿 `sha` 再 `PUT` 带 `sha`，否则 `409 conflict`。
   - 内容 `base64` 编码；中文 UTF-8 路径与正文经 base64 **无乱码**。
4. **验证**：`GET /repos/{login}/{repo}/git/trees/{branch}?recursive=1`，比对远端 `blob` 数与 `default_branch` / `private` 设置。

### 坑（本次发布实测）
- ❌ **Git Database API**（`blobs → tree → commit → ref`）在**空仓库**报 `409 "Git Repository is empty"`——空仓库必须先有一笔提交。→ 改用 Contents API（首个 `PUT` 自动建分支），绕开此坑。
- ❌ 经典 PAT 无 `delete_repo`，重建仓库时 `DELETE` 被 403。→ 幂等探测，不删库。
- ✅ 中文路径 / 内容经 base64 无乱码；`urllib.parse.quote(path)`（默认 `safe='/'`）保留 `/` 分隔、编码中文，正是 API 所需。

### 脚本
`scripts/publish_skill.py`（纯标准库，封装上述全部逻辑）：
```bash
set GITHUB_TOKEN=ghp_xxx
python scripts/publish_skill.py --skill-dir C:/Users/youqw/.workbuddy/skills/foo --repo foo-skill
python scripts/publish_skill.py --skill-dir ... --repo foo-skill --private    # 私有
python scripts/publish_skill.py ... --dry-run                                # 只列文件，不发请求
```
`--dry-run` 先核对将上传的文件清单再正式发布，避免误传。

## 8. 收尾
- 保留 `skills_backup/` 供回滚，明确告知用户路径。
- 校验用户的本地自定义 skill 与备份零差异（证明未被误改）。
- 若上游 SKILL.md 体量过大（>100KB），提醒用户：加载会吃掉大量 context，建议拆到 `references/` 做渐进式披露。
