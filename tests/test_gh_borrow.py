"""GitHub 借鉴引擎的离线测试: 注入假客户端, 不触网。"""

import json

from qingxiaotuan.tools import gh_borrow
from qingxiaotuan.tools.gh_borrow import GitHubClient, borrow, format_borrow


class _FakeClient(GitHubClient):
    """模拟 gh 后端: 返回固定 JSON, 不执行任何外部命令/网络。"""

    def __init__(self, have_gh=True):
        super().__init__(token="test")
        self._have_gh = have_gh

    def has_gh(self):
        return self._have_gh

    def _gh_api(self, path, params=None):
        if path == "search/repositories":
            return {"items": [
                {"full_name": "octo/demo", "html_url": "https://github.com/octo/demo",
                 "description": "a demo", "stargazers_count": 123,
                 "license": {"spdx_id": "MIT"}},
            ]}
        if path == "search/code":
            return {"items": [
                {"repository": {"full_name": "octo/demo", "default_branch": "main",
                                "license": {"spdx_id": "MIT"}},
                 "path": "src/x.py", "html_url": "https://github.com/octo/demo/blob/main/src/x.py"},
            ]}
        if path.startswith("repos/octo/demo/contents/"):
            return {"content": json.dumps("print('hello')").encode("utf-8").decode("utf-8") and
                    __import__("base64").b64encode(b"print('hello')\n").decode()}
        if path == "repos/octo/demo/license":
            return {"license": {"spdx_id": "MIT"}}
        raise AssertionError(f"unexpected gh api: {path}")

    def get_file_content(self, repo, path, ref=None):
        return "print('hello')\n"


def test_borrow_code_with_attribution():
    res = borrow("demo", mode="code", limit=3, client=_FakeClient())
    assert res["pledge"] == gh_borrow.BORROW_PLEDGE
    items = res["items"]
    assert len(items) == 1
    it = items[0]
    assert it["repo"] == "octo/demo"
    assert it["path"] == "src/x.py"
    assert it["license"] == "MIT"
    assert "print('hello')" in it["snippet"]


def test_borrow_repo_mode():
    res = borrow("demo", mode="repo", limit=3, client=_FakeClient())
    items = res["items"]
    assert items[0]["repo"] == "octo/demo"
    assert items[0]["stars"] == 123
    assert items[0]["license"] == "MIT"


def test_rest_fallback_when_no_gh():
    class _RestClient(_FakeClient):
        def _rest(self, url, params=None):
            if "search/repositories" in url:
                return {"items": [{"full_name": "octo/rest", "html_url": "u",
                                  "description": "d", "stargazers_count": 5,
                                  "license": {"spdx_id": "Apache-2.0"}}]}
            raise AssertionError(url)
    c = _RestClient(have_gh=False)
    assert c.has_gh() is False
    rows = c.search_repos("x")
    assert rows[0]["full_name"] == "octo/rest"


def test_format_borrow_includes_pledge_and_source():
    res = borrow("demo", mode="code", client=_FakeClient())
    text = format_borrow(res)
    assert "借鉴原则" in text
    assert "octo/demo" in text
    assert "src/x.py" in text
