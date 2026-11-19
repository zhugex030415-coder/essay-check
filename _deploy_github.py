#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 GitHub API 一次性把 essay-grader 传到 GitHub（不需要装 git）。

用法：
    python _deploy_github.py <token> [repo名]

做的事：
1. 建一个私有仓库（已存在则复用）
2. 上传项目文件（自动跳过 config.json、__pycache__、样本 PDF）
3. 打印仓库地址和 Render 下一步
"""
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SKIP_FILES = {"config.json"}
SKIP_DIRS = {"__pycache__", ".git"}

API = "https://api.github.com"


def call(method, url, token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "essay-grader-deploy",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"HTTP {e.code} {url} :: {body}")


def collect_files():
    out = []
    for p in sorted(HERE.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(HERE)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.name in SKIP_FILES or rel.name.startswith("_test_") or rel.suffix.lower() == ".pdf":
            continue
        out.append(rel)
    return out


def main():
    if len(sys.argv) < 2:
        print("用法: python _deploy_github.py <token> [repo名]")
        sys.exit(1)
    token = sys.argv[1].strip()
    repo = sys.argv[2].strip() if len(sys.argv) > 2 else "essay-grader"

    me = call("GET", f"{API}/user", token)
    owner = me["login"]
    print(f"账号：{owner}")

    try:
        call("POST", f"{API}/user/repos", token, {
            "name": repo, "private": True, "auto_init": False,
            "description": "赤舟 作文批改工作台",
        })
        print(f"已创建私有仓库 {repo}")
    except RuntimeError as e:
        if "422" in str(e):
            print(f"仓库 {repo} 已存在，直接复用")
        else:
            raise

    files = collect_files()
    print(f"\n准备上传 {len(files)} 个文件…")
    ok, fail = 0, 0
    for rel in files:
        path = rel.as_posix()
        try:
            sha = None
            try:
                ex = call("GET", f"{API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}", token)
                sha = ex.get("sha")
            except RuntimeError:
                pass
            payload = {
                "message": f"add {path}",
                "content": base64.b64encode(rel.read_bytes()).decode("ascii"),
            }
            if sha:
                payload["sha"] = sha
                payload["message"] = f"update {path}"
            call("PUT", f"{API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}", token, payload)
            ok += 1
            print(f"  [OK] {path}")
        except Exception as e:
            fail += 1
            print(f"  [FAIL] {path} :: {e}")

    print(f"\n完成：成功 {ok}，失败 {fail}")
    print(f"\n仓库地址：https://github.com/{owner}/{repo}")
    print("\n下一步（Render）：")
    print("  1. https://render.com 用 GitHub 登录")
    print("  2. New + → Blueprint（读 render.yaml）或 Web Service")
    print("  3. 环境变量加 DEEPSEEK_KEY / OCR_KEY / ACCESS_CODE")
    print("  4. Deploy，拿到网址发给助教")


if __name__ == "__main__":
    main()
