#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_skill.py —— 把本地 skill 目录发布/更新到 GitHub（绕过被墙的 git 协议）。

本机 github.com:443 被重置，无法 git push；唯一可用通道是 api.github.com REST API。
本脚本仅用标准库 urllib 实现「本地 -> GitHub」反向同步。

流程（对应 SKILL.md 第 7 节）：
  1. GET /user 验证 token，拿 login
  2. 幂等探测仓库：GET /repos/{login}/{repo}；404 才创建（POST /user/repos）
     —— 经典 PAT 默认无 delete_repo，故「先探测后创建」，不依赖删库重建
  3. 遍历本地 skill 目录，逐文件处理：
     - 新仓库首个 PUT 自动初始化 main 分支 + 初始提交
     - 文件不存在 -> PUT 不带 sha
     - 文件已存在 -> 先 GET 拿 sha 再 PUT 带 sha（否则 409 conflict）
  4. GET tree?recursive=1 验证远端文件数与 default_branch / private

用法：
  set GITHUB_TOKEN=ghp_xxx          # 经典 PAT，需 repo 权限；仅经环境变量传入，不落盘、不回显
  python publish_skill.py --skill-dir C:/Users/youqw/.workbuddy/skills/foo --repo foo-skill
  python publish_skill.py --skill-dir ... --repo foo-skill --private     # 建为私有仓库
  python publish_skill.py ... --dry-run                                # 只列文件，不实际请求
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
EXCLUDE_DIRS = {".git", "__pycache__", ".DS_Store", "node_modules", "archive"}
EXCLUDE_FILES = {".DS_Store"}


def api(method, path, token, data=None):
    """发起一次 GitHub REST 请求。409 指数退避重试；404 返回 (404, {})。"""
    url = API + path
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "workbuddy-skill-publish",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    for attempt in range(4):
        try:
            r = urllib.request.urlopen(req, timeout=30)
            txt = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(txt) if txt else {})
        except urllib.error.HTTPError as e:
            code = e.code
            msg = e.read().decode("utf-8", "replace")
            if code == 409 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if code == 404:
                return 404, {}
            return code, {"error": msg[:300]}
        except Exception as e:  # 网络抖动
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return -1, {"error": str(e)}


def collect_files(skill_dir):
    out = []
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in sorted(files):
            if n in EXCLUDE_FILES:
                continue
            full = os.path.join(root, n)
            rel = os.path.relpath(full, skill_dir).replace(os.sep, "/")
            out.append((rel, full))
    return sorted(out)


def get_sha(token, login, repo, path):
    st, d = api("GET", "/repos/%s/%s/contents/%s" % (login, repo, urllib.parse.quote(path)), token)
    return d.get("sha") if st == 200 else None


def put_file(token, login, repo, path, content_b64, sha, message):
    data = {"message": message, "content": content_b64}
    if sha:
        data["sha"] = sha
    return api("PUT", "/repos/%s/%s/contents/%s" % (login, repo, urllib.parse.quote(path)), token, data)


def main():
    ap = argparse.ArgumentParser(description="发布/更新本地 skill 到 GitHub（绕过 git 协议）")
    ap.add_argument("--skill-dir", required=True, help="本地 skill 目录绝对路径")
    ap.add_argument("--repo", required=True, help="GitHub 仓库名（不含 owner）")
    ap.add_argument("--private", action="store_true", help="建为私有仓库（默认公开）")
    ap.add_argument("--login", default=None, help="仓库 owner，缺省从 /user 获取")
    ap.add_argument("--message", default=None, help="提交信息")
    ap.add_argument("--dry-run", action="store_true", help="只列出将上传的文件，不实际请求")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("错误：请先设置环境变量 GITHUB_TOKEN（经典 PAT，需 repo 权限）。")
        print("       该 token 仅经环境变量传入，不写入任何文件、不回显。")
        sys.exit(1)

    # 1. 验证 token
    st, d = api("GET", "/user", token)
    if st != 200 or "login" not in d:
        print("错误：token 无效或无法获取 login：", d)
        sys.exit(1)
    login = args.login or d["login"]
    print("已认证 GitHub 用户：", login)

    # 2. 幂等探测仓库
    repo = args.repo
    st, d = api("GET", "/repos/%s/%s" % (login, repo), token)
    if st == 404:
        if args.dry_run:
            print("[dry-run] 将创建仓库 %s（%s）" % (repo, "私有" if args.private else "公开"))
        else:
            st2, d2 = api("POST", "/user/repos", token,
                          {"name": repo, "private": args.private, "auto_init": False})
            if st2 != 201:
                print("错误：创建仓库失败", d2)
                sys.exit(1)
            print("已创建仓库：", d2.get("html_url"))
    elif st == 200:
        print("仓库已存在：", d.get("html_url"), "（走增量更新流程）")
    else:
        print("错误：探测仓库异常", d)
        sys.exit(1)

    # 3. 逐文件上传/更新
    files = collect_files(args.skill_dir)
    msg = args.message or ("publish %s via WorkBuddy skill-publish" % repo)
    print("将处理 %d 个文件" % len(files))
    ok = 0
    for rel, full in files:
        with open(full, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        if args.dry_run:
            print("  [dry-run] PUT %s  (%d bytes)" % (rel, os.path.getsize(full)))
            ok += 1
            continue
        sha = get_sha(token, login, repo, rel)  # 不存在返回 None -> 新建
        st, d = put_file(token, login, repo, rel, b64, sha, msg)
        if st in (200, 201):
            ok += 1
            print("  [ok] %s" % rel)
        else:
            print("  [fail] %s  HTTP %s  %s" % (rel, st, d.get("error", "")))
    if args.dry_run:
        print("dry-run 列示完成：%d 个文件将上传（未实际请求）" % ok)
    else:
        print("上传完成：%d/%d 成功" % (ok, len(files)))

    # 4. 验证
    if not args.dry_run:
        st, d = api("GET", "/repos/%s/%s" % (login, repo), token)
        if st == 200:
            branch = d.get("default_branch")
            print("默认分支：%s | 私有：%s" % (branch, d.get("private")))
            st2, t = api("GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (login, repo, branch), token)
            if st2 == 200:
                blobs = [e for e in t.get("tree", []) if e["type"] == "blob"]
                print("远端文件数：%d" % len(blobs))


if __name__ == "__main__":
    main()
