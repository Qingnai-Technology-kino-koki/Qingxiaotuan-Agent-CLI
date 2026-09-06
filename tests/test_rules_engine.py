"""rules_engine 安全表达式语言回归测试 (无 eval)。

引擎语义: 一条规则的 assert 必须为真, 文件才算通过; assert 为假则记一条违规。
即 matches(subject, pattern) = "subject 是否匹配 pattern"。

重点回归 matches(subject, pattern) / regex_contains(subject, pattern):
旧实现把 args[0](待匹配文本) 误当成正则去编译, 导致:
  - subject 含正则特殊字符(如未闭合的 '[') 时, 旧实现会把它当成正则编译而抛 re.error,
    被 except 吞掉后返回 False → 误杀/误判;
  - 真正该匹配的正则 pattern(args[1]) 从未被使用 → 匹配结果完全错位。
"""

from qingxiaotuan.ext.rules_engine import RuleEngine


def _engine(rules_yaml: str) -> RuleEngine:
    eng = RuleEngine()
    eng.load_yaml_text(rules_yaml)
    return eng


# 规则语义: content 必须包含 api-key 模式 (匹配=通过, 不匹配=违规)
MATCHES_YAML = (
    "- id: require_api_key_ref\n"
    "  severity: error\n"
    '  match:\n    path: "*.txt"\n'
    '  assert: \'matches(content, /api[_-]?key\\s*[:=]/)\'\n'
    "  message: 配置应引用 api key\n"
)

# regex_contains 同语义: content 必须包含密码赋值模式
REGEX_CONTAINS_YAML = (
    "- id: require_password_ref\n"
    "  severity: warn\n"
    '  match:\n    path: "*.txt"\n'
    '  assert: \'regex_contains(content, /password\\s*=\\s*[A-Za-z0-9]{6,}/)\'\n'
    "  message: 配置应含密码\n"
)


def test_matches_subject_matches_pattern_no_violation():
    eng = _engine(MATCHES_YAML)
    # content 含 "api_key =" → 匹配 → assert 为真 → 无违规
    v = eng.check("app.txt", "const api_key = 'x';", kind="file")
    assert not any(x["id"] == "require_api_key_ref" for x in v)


def test_matches_subject_lacks_pattern_is_violation():
    eng = _engine(MATCHES_YAML)
    # content 不含 api-key 模式 → 不匹配 → assert 为假 → 记违规
    v = eng.check("app.txt", "const x = 1;", kind="file")
    assert any(x["id"] == "require_api_key_ref" for x in v)


def test_matches_does_not_compile_subject_as_regex():
    # 回归: subject 含未闭合的正则特殊字符 '[' 时, 旧实现会把它当成正则编译
    # 而抛 re.error (被 except 吞掉返回 False) —— 这是一个无声的误判。
    # 修复后 subject 仅作为被搜索文本, 不再被当作正则。
    eng = _engine(
        "- id: has_b\n"
        "  severity: error\n"
        '  match:\n    path: "*.txt"\n'
        "  assert: 'matches(content, /b/)'\n"
        "  message: 应含 b\n"
    )
    # "a[b" 含有字符 'b' → 匹配 → 无违规 (旧实现会误报违规)
    v = eng.check("app.txt", "a[b", kind="file")
    assert not any(x["id"] == "has_b" for x in v)


def test_regex_contains_subject_matches_pattern_no_violation():
    eng = _engine(REGEX_CONTAINS_YAML)
    v = eng.check("app.txt", "password = s3cr3t", kind="file")
    assert not any(x["id"] == "require_password_ref" for x in v)


def test_regex_contains_subject_lacks_pattern_is_violation():
    eng = _engine(REGEX_CONTAINS_YAML)
    v = eng.check("app.txt", 'username = "alice"', kind="file")
    assert any(x["id"] == "require_password_ref" for x in v)
