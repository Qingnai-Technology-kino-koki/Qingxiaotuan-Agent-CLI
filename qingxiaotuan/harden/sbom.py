"""SBOM 生成器 (SPDX 2.3 JSON, 纯标准库)。

生成当前运行环境的软件物料清单 (Software Bill of Materials), 用于供应链可视化与审计。

- 默认从 ``importlib.metadata`` 枚举已安装发行包 (运行时真实组件);
- 同时把青小团本体作为根 Package 加入;
- 可选: 若环境中安装了 ``cyclonedx`` 库, 额外提供 CycloneDX 格式 (``generate_cyclonedx_sbom``)。

纯标准库实现, 可离线运行, 不依赖网络。
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

# SPDXID 合法字符: 字母/数字/连字符/点
_SPDX_ID_SAFE = re.compile(r"[^A-Za-z0-9.-]")

try:
    from .. import __version__ as _QXT_VERSION
except Exception:  # pragma: no cover
    _QXT_VERSION = "0.0.0"


def _spdx_id(name: str, idx: int) -> str:
    safe = _SPDX_ID_SAFE.sub("-", name)
    return f"SPDXRef-Package-{safe}-{idx}"


def _iter_installed() -> List[Dict[str, str]]:
    """枚举已安装发行包 (name, version)。"""
    out: List[Dict[str, str]] = []
    try:
        dists = list(importlib.metadata.distributions())
    except Exception:  # pragma: no cover
        return out
    for dist in dists:
        name = dist.metadata.get("Name") or getattr(dist, "name", "") or ""
        version = dist.version or ""
        if not name:
            continue
        out.append({"name": name, "version": version})
    # 去重 (同名取第一个)
    seen = set()
    unique = []
    for pkg in out:
        if pkg["name"] in seen:
            continue
        seen.add(pkg["name"])
        unique.append(pkg)
    return unique


def generate_sbom(
    project_name: str = "qingxiaotuan",
    project_version: Optional[str] = None,
    include_installed: bool = True,
) -> dict:
    """生成 SPDX 2.3 SBOM 字典。"""
    project_version = project_version or _QXT_VERSION
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc_ns = f"https://qingnai.tech/sbom/{project_name}-{project_version}-{ts}"

    packages: List[dict] = []
    relationships: List[dict] = []

    # 根组件: 项目本体
    root_id = "SPDXRef-Package-qingxiaotuan-0"
    packages.append({
        "SPDXID": root_id,
        "name": project_name,
        "versionInfo": project_version,
        "supplier": "NOASSERTION",
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "MIT",
        "copyrightText": "NOASSERTION",
    })

    idx = 1
    if include_installed:
        for pkg in _iter_installed():
            pid = _spdx_id(pkg["name"], idx)
            idx += 1
            packages.append({
                "SPDXID": pid,
                "name": pkg["name"],
                "versionInfo": pkg["version"],
                "supplier": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            })
            relationships.append({
                "spdxElementId": root_id,
                "relatedSpdxElement": pid,
                "relationshipType": "DEPENDS_ON",
            })

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project_name}-sbom",
        "documentNamespace": doc_ns,
        "creationInfo": {
            "created": ts,
            "creators": ["Tool: qingxiaotuan-harden-sbom"],
            "licenseListVersion": "3.20",
        },
        "packages": packages,
        "relationships": relationships,
    }


def write_sbom(path: str, **kwargs) -> str:
    """生成并写入 SBOM JSON 文件, 返回路径。"""
    data = generate_sbom(**kwargs)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def sbom_hash(data: dict) -> str:
    """SBOM 内容指纹 (用于校验 SBOM 未被篡改)。"""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def generate_cyclonedx_sbom(project_name: str = "qingxiaotuan",
                            project_version: Optional[str] = None) -> dict:
    """若安装了 ``cyclonedx`` 库则生成 CycloneDX 格式; 否则给出清晰错误。"""
    try:
        from cyclonedx.model.bom import Bom  # type: ignore
        from cyclonedx.model.component import Component, ComponentType  # type: ignore
    except Exception as exc:  # pragma: no cover - 可选依赖
        raise RuntimeError(
            "CycloneDX 输出需要 cyclonedx 库 (pip install cyclonedx-python-lib)。"
        ) from exc
    project_version = project_version or _QXT_VERSION
    bom = Bom()
    bom.add_component(Component(name=project_name, version=project_version,
                                type=ComponentType.APPLICATION))
    for pkg in _iter_installed():
        bom.add_component(Component(name=pkg["name"], version=pkg["version"],
                                    type=ComponentType.LIBRARY))
    # cyclonedx 序列化需额外依赖, 这里仅返回对象供上层使用
    return bom
