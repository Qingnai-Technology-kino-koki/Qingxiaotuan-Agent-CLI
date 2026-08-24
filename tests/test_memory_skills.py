"""三层记忆 + 技能系统测试。"""

from qingxiaotuan.memory import MemoryStore
from qingxiaotuan.skills import SkillManager


def test_memory_append_and_read(qxt_home):
    store = MemoryStore(qxt_home)
    store.append_memory("用户偏好使用 TypeScript", section="偏好")
    text = store.read_memory()
    assert "TypeScript" in text
    assert "偏好" in text


def test_user_profile_update(qxt_home):
    store = MemoryStore(qxt_home)
    store.update_user("Name", "小团")
    store.update_user("Name", "小团2")  # 覆盖而非追加
    text = store.read_user()
    assert "小团2" in text
    assert text.count("Name:") == 1


def test_fts_search(qxt_home):
    store = MemoryStore(qxt_home)
    store.append_memory("项目使用 pnpm 管理依赖")
    store.index("skill", "部署流程: build push restart", source="deploy.md")
    hits = store.search("pnpm")
    assert any("pnpm" in h["content"] for h in hits)
    hits2 = store.search("部署")
    assert any(h["kind"] == "skill" for h in hits2)


def test_skill_save_load_refine(qxt_home):
    mgr = SkillManager(qxt_home)
    mgr.save("Deploy App", "部署到测试环境的流程", "1. build\n2. push")
    skill = mgr.load("deploy-app")
    assert skill is not None
    assert "build" in skill.body
    # 同名保存 = 改进, use_count 递增
    mgr.save("Deploy App", "部署到测试环境的流程", "1. build\n2. push\n3. restart")
    refined = mgr.load("deploy-app")
    assert "restart" in refined.body
    assert refined.use_count == 1


def test_skill_search_and_prompt_render(qxt_home):
    mgr = SkillManager(qxt_home)
    mgr.save("Git Workflow", "团队 git 分支规范", "feature 分支从 develop 切出")
    mgr.save("Docker Tips", "镜像构建优化", "使用多阶段构建")
    hits = mgr.search("git 分支")
    assert hits[0].name == "Git Workflow"
    rendered = mgr.render_for_prompt(hits)
    assert "可复用技能" in rendered


def test_skill_indexed_into_fts(qxt_home):
    store = MemoryStore(qxt_home)
    mgr = SkillManager(qxt_home, memory_store=store)
    mgr.save("Backup DB", "数据库备份流程", "mysqldump 全量导出")
    hits = store.search("备份")
    assert any(h["kind"] == "skill" for h in hits)
