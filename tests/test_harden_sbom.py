"""SBOM 生成测试。"""
import json
from pathlib import Path

from qingxiaotuan.harden.sbom import generate_sbom, write_sbom, sbom_hash


def test_generate_sbom_structure():
    data = generate_sbom()
    assert data["spdxVersion"] == "SPDX-2.3"
    assert data["SPDXID"] == "SPDXRef-DOCUMENT"
    # 根组件 + 至少一个已安装包
    names = {p["name"] for p in data["packages"]}
    assert "qingxiaotuan" in names
    assert len(data["packages"]) >= 2
    assert len(data["relationships"]) >= 1
    # 关系指向存在的 SPDXID
    ids = {p["SPDXID"] for p in data["packages"]}
    for rel in data["relationships"]:
        assert rel["spdxElementId"] in ids
        assert rel["relatedSpdxElement"] in ids


def test_sbom_hash_deterministic():
    # generate_sbom() 包含时间戳, 两次调用产生不同 SBOM;
    # sbom_hash 应对同一 dict 产出相同指纹。
    sbom = generate_sbom()
    assert sbom_hash(sbom) == sbom_hash(sbom)
    assert sbom_hash(sbom).startswith("sha256:")


def test_write_sbom(tmp_path):
    out = tmp_path / "sbom.spdx.json"
    path = write_sbom(str(out))
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    assert loaded["spdxVersion"] == "SPDX-2.3"
