"""内置世界顶级技能 seed 测试 (离线)。"""

from qingxiaotuan.app import build_kernel, seed_builtin_skills
from qingxiaotuan.config import Config


def test_builtin_skills_seeded_on_build(qxt_home):
    build_kernel()  # 首次启动触发 seed
    skills_dir = qxt_home / "skills"
    names = {p.name for p in skills_dir.glob("*.md")}
    for expected in [
        "frontend-mastery.md", "design-aesthetics.md",
        "anthropomorphic-comments.md", "code-review-checklist.md",
        "systematic-debugging.md", "test-driven-dev.md",
        "architecture-reading.md", "safe-refactor.md",
    ]:
        assert expected in names, f"缺失内置技能: {expected}"


def test_seed_is_idempotent(qxt_home):
    config = Config()
    config.ensure_home()  # 只建目录, 不 seed
    n1 = seed_builtin_skills(config)
    n2 = seed_builtin_skills(config)  # 同名不覆盖, 第二次应为 0
    assert n1 >= 8
    assert n2 == 0


def test_seeded_skill_loadable(qxt_home):
    kernel = build_kernel()
    manager = kernel.require("skill_manager")
    skill = manager.load("anthropomorphic-comments")
    assert skill is not None
    assert "AI 腔" in skill.body
