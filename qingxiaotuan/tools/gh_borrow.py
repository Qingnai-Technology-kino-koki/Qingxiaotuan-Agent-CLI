"""GitHub 代码借鉴引擎 —— 让青小团自主发现相似开源项目并「借鉴」(非全抄)。

设计目标 (用户明确要求「自主去找开源相似项目借鉴代码, 注意不是全抄」):
  * 优先使用本机 `gh` CLI; 无 gh 时回退 GitHub REST API (匿名 60req/h, 有
    GITHUB_TOKEN/GH_TOKEN 则提额)。
  * 每次借鉴都带署名: 仓库 URL + 许可证 (SPDX) + 文件路径, 绝不隐去出处。
  * 默认只读 + 截断 (单文件 ≤ 20KB), 绝不执行从 GitHub 拉取的任意代码。
  * 显式 pledge: 仅参考思路/接口/边界处理, 落地时按本项目规范重写。

该模块对外部依赖做了可注入设计 (runner / http_get), 方便离线测试。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

# 借鉴原则: 对外暴露, 任何返回结果都会携带, agent/skill 强制遵守。
BORROW_PLEDGE = (
    "借鉴原则: 仅参考其实现思路 / 接口设计 / 边界处理, 绝不整段照搬; "
    "引用须保留原作者署名与许可证; 落地时按青小团项目规范重写。"
)

DEFAULT_LIMIT = 5
MAX_SNIPPET_BYTES = 20000


# ================================================================ 底层执行封装

def _default_runner(args: List[str], timeout: int = 25) -> Tuple[int, str, str]:
    """执行外部命令 (默认 subprocess), 返回 (rc, stdout, stderr)。可注入用于测试。"""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


# ================================================================ GitHub 客户端

class GitHubClient:
    """统一 GitHub 访问: 优先 gh CLI, 回退 REST API。"""

    def __init__(
        self,
        token: Optional[str] = None,
        runner: Optional[Callable[[List[str]], Tuple[int, str, str]]] = None,
        http_get: Optional[Callable[[str, Dict[str, str]], Tuple[int, str]]] = None,
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self._runner = runner or _default_runner
        self._http_get = http_get  # (url, headers) -> (status, text)

    # ---- 能力探测 ----
    def has_gh(self) -> bool:
        return shutil.which("gh") is not None

    # ---- gh 后端 ----
    def _gh_api(self, path: str, params: Optional[Dict[str, str]] = None) -> dict:
        args = ["gh", "api", path, "-H", "Accept: application/vnd.github+json"]
        if self.token:
            args += ["-H", f"Authorization: Bearer {self.token}"]
        if params:
            for k, v in params.items():
                args += ["-f", f"{k}={v}"]
        rc, out, err = self._runner(args)
        if rc != 0:
            raise RuntimeError(f"gh api {path} 失败: {err.strip()[:200]}")
        return cast(dict, json.loads(out))

    # ---- REST 回退 ----
    def _rest(self, url: str, params: Optional[Dict[str, str]] = None) -> dict:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "qingxiaotuan-cli"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        full = url
        if params:
            full = url + "?" + urllib.parse.urlencode(params)
        if self._http_get:
            status, text = self._http_get(full, headers)
        else:
            import httpx
            r = httpx.get(full, headers=headers, timeout=25, follow_redirects=True)
            status, text = r.status_code, r.text
        if status != 200:
            raise RuntimeError(f"REST {status}: {text.strip()[:200]}")
        return cast(dict, json.loads(text))

    # ---- 搜索 ----
    def search_repos(self, query: str, limit: int = DEFAULT_LIMIT) -> List[dict]:
        if self.has_gh():
            data = self._gh_api(
                "search/repositories",
                {"q": query, "per_page": str(limit), "sort": "stars", "order": "desc"},
            )
        else:
            data = self._rest(
                "https://api.github.com/search/repositories",
                {"q": query, "per_page": str(limit), "sort": "stars", "order": "desc"},
            )
        return cast(List[dict], data.get("items", []))

    def search_code(self, query: str, limit: int = DEFAULT_LIMIT) -> List[dict]:
        if self.has_gh():
            data = self._gh_api("search/code", {"q": query, "per_page": str(limit)})
        else:
            data = self._rest(
                "https://api.github.com/search/code",
                {"q": query, "per_page": str(limit)},
            )
        return cast(List[dict], data.get("items", []))

    # ---- 取文件内容 (只读, 截断) ----
    def get_file_content(self, repo: str, path: str, ref: Optional[str] = None) -> str:
        """返回仓库文件文本 (base64 解码 / raw 回退), 截断到 MAX_SNIPPET_BYTES。"""
        if self.has_gh():
            api = f"repos/{repo}/contents/{path}"
            if ref:
                api += f"?ref={urllib.parse.quote(ref)}"
            try:
                data = self._gh_api(api)
                if isinstance(data, dict) and data.get("content"):
                    return base64.b64decode(data["content"]).decode("utf-8", "replace")
            except Exception:
                pass
        # 回退: raw.githubusercontent.com
        branch = ref or "main"
        raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
        if self._http_get:
            status, text = self._http_get(raw, {"User-Agent": "qingxiaotuan-cli"})
        else:
            import httpx
            r = httpx.get(raw, headers={"User-Agent": "qingxiaotuan-cli"}, timeout=25,
                          follow_redirects=True)
            status, text = r.status_code, r.text
        if status != 200:
            raise RuntimeError(f"取文件失败 {status}: {repo}/{path}")
        return text

    def get_license(self, repo: str) -> str:
        try:
            if self.has_gh():
                data = self._gh_api(f"repos/{repo}/license")
            else:
                data = self._rest(f"https://api.github.com/repos/{repo}/license")
            return (data.get("license") or {}).get("spdx_id") or "NOASSERTION"
        except Exception:
            return "?"


# ================================================================ 高层: 借鉴编排

@dataclass
class BorrowItem:
    repo: str
    url: str
    path: str = ""
    snippet: str = ""
    desc: str = ""
    stars: int = 0
    license: str = "?"


def borrow(
    query: str,
    *,
    mode: str = "code",
    limit: int = DEFAULT_LIMIT,
    max_bytes: int = MAX_SNIPPET_BYTES,
    token: Optional[str] = None,
    client: Optional[GitHubClient] = None,
) -> Dict[str, object]:
    """自主发现相似开源项目并借鉴。

    mode="code": 搜代码匹配, 取每个匹配文件的片段 (带署名);
    mode="repo": 搜仓库, 返回高星仓库清单 (带许可证)。
    返回结构: {query, mode, pledge, items:[BorrowItem...]}
    """
    client = client or GitHubClient(token=token)
    items: List[BorrowItem] = []

    if mode == "repo":
        for it in client.search_repos(query, limit):
            items.append(BorrowItem(
                repo=it.get("full_name", "?"),
                url=it.get("html_url", ""),
                desc=(it.get("description") or "")[:160],
                stars=int(it.get("stargazers_count", 0) or 0),
                license=(it.get("license") or {}).get("spdx_id") or "?",
            ))
    else:  # code
        for m in client.search_code(query, limit):
            repo = (m.get("repository") or {}).get("full_name", "?")
            path = m.get("path", "")
            branch = (m.get("repository") or {}).get("default_branch") or "main"
            snippet = ""
            try:
                raw = client.get_file_content(repo, path, branch)
                if raw:
                    snippet = raw[:max_bytes]
            except Exception:
                snippet = ""
            items.append(BorrowItem(
                repo=repo,
                url=m.get("html_url", f"https://github.com/{repo}"),
                path=path,
                snippet=snippet,
                license=client.get_license(repo),
            ))

    return {
        "query": query,
        "mode": mode,
        "pledge": BORROW_PLEDGE,
        "items": [vars(it) for it in items],
    }


# ================================================================ 渲染

def format_borrow(result: Dict[str, object]) -> str:
    """把借鉴结果渲染为终端可读的 Markdown。"""
    lines: List[str] = []
    lines.append(f"# GitHub 借鉴: `{result['query']}` (mode={result['mode']})")
    lines.append("")
    lines.append(f"> {result['pledge']}")
    lines.append("")
    items = cast(List[Dict[str, Any]], result.get("items") or [])
    if not items:
        lines.append("_未找到匹配的开源项目 (可换关键词或降低限制)。_")
        return "\n".join(lines)
    for i, it in enumerate(items, 1):
        lines.append(f"## {i}. {it['repo']}")
        if it.get("desc"):
            lines.append(f"   {it['desc']}")
        if it.get("stars"):
            lines.append(f"   ⭐ {it['stars']}  ·  许可证: {it.get('license','?')}")
        if it.get("path"):
            lines.append(f"   文件: `{it['path']}`  —  {it['url']}")
        if it.get("snippet"):
            lines.append("")
            lines.append("   ```")
            for ln in it["snippet"].splitlines()[:200]:
                lines.append("   " + ln)
            lines.append("   ```")
        lines.append("")
    lines.append("---")
    lines.append("署名要求: 借鉴上述实现时, 请在注释/文档中注明来源仓库与许可证。")
    return "\n".join(lines)
