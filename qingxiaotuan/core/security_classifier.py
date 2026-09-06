"""独立安全分类器 (Security Classifier) —— 参考 Claude Code 分类器架构。

Claude Code 的 Auto mode 用独立分类器模型评估每个操作的安全性, 即使规则层漏了,
分类器还能拦。青小团的静态规则引擎 (safety_engine) 已覆盖大部分场景, 但缺少
「规则之外」的兜底——本模块补上这块拼图。

设计要点:
1. **规则层优先**: 先走 safety_engine 的红线/评分检测, 命中则直接拦截/放行;
2. **分类器兜底**: 规则层未命中时, 由分类器对命令做语义级安全评估;
3. **本地轻量推理**: 分类器用预定义的语义特征向量做评分, 无需调用外部模型,
   零网络依赖, 亚毫秒级响应;
4. **可扩展**: 未来可接入 ML 模型或外部 LLM 分类器, 接口已预留;
5. **审计友好**: 每次分类决策都记录特征向量和评分依据, 供事后审计。

与 safety_engine 的关系:
- safety_engine 是「基于模式匹配的静态分析器」, 快、确定性强、覆盖明确的攻击向量;
- security_classifier 是「基于语义特征的评估器」, 能捕获模式匹配遗漏的新型攻击,
  但可能有误判, 因此需要与规则层联合决策。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ============================================================ 分类结果

@dataclass
class ClassificationResult:
    """分类器的决策结果。"""

    action: str  # "allow" | "deny" | "confirm" | "escalate"
    confidence: float  # 0.0 ~ 1.0, 越高越确信
    risk_level: str  # "none" | "low" | "medium" | "high" | "critical"
    features: Dict[str, Any] = field(default_factory=dict)  # 触发的特征
    reasons: List[str] = field(default_factory=list)  # 决策理由
    elapsed_ms: float = 0.0  # 分类耗时

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "features": self.features,
            "reasons": self.reasons,
            "elapsed_ms": self.elapsed_ms,
        }


# ============================================================ 语义特征提取

class CommandFeatures:
    """从命令中提取语义安全特征。

    特征向量用于分类器评分, 每个特征都有一个风险权重。
    """

    @staticmethod
    def extract(command: str) -> Dict[str, Any]:
        """提取命令的语义特征。"""
        c = command.strip()
        features: Dict[str, Any] = {}

        # 1. 管道链长度 (长管道链 = 更复杂的意图, 更可能隐藏恶意)
        pipe_count = c.count("|")
        features["pipe_chain_length"] = pipe_count
        features["has_long_pipe"] = pipe_count >= 3

        # 2. 命令替换深度 (嵌套 $() 或反引号 = 动态生成, 更难静态分析)
        sub_depth = 0
        for ch in c:
            if ch == "$" and sub_depth < 10:
                sub_depth += 1
        features["substitution_depth"] = sub_depth
        features["has_nested_substitution"] = sub_depth >= 2

        # 3. 编码/加密迹象
        features["has_base64"] = bool(re.search(r'\bbase64\b', c, re.IGNORECASE))
        features["has_hex_encoding"] = bool(re.search(r'\\x[0-9a-fA-F]{2}', c))
        features["has_octal_encoding"] = bool(re.search(r'\\0[0-7]{2}', c))
        features["has_url_encoding"] = bool(re.search(r'%[0-9a-fA-F]{2}', c))
        features["has_encoding"] = (
            features["has_base64"] or features["has_hex_encoding"]
            or features["has_octal_encoding"] or features["has_url_encoding"]
        )

        # 4. 网络操作迹象
        features["has_network_fetch"] = bool(re.search(
            r'\b(?:curl|wget|fetch|http|https|ftp|scp|rsync)\b', c, re.IGNORECASE
        ))
        features["has_network_listen"] = bool(re.search(
            r'\b(?:nc|netcat|socat|ss|lsof)\b.*\b(?:-l|listen|port)\b',
            c, re.IGNORECASE,
        ))
        features["has_reverse_shell"] = bool(re.search(
            r'\b(?:nc|netcat|socat|bash|sh)\b.*\b(?:-e|/dev/tcp|/dev/udp)\b',
            c, re.IGNORECASE,
        ))

        # 5. 权限提升迹象
        features["has_sudo"] = bool(re.search(r'\bsudo\b', c, re.IGNORECASE))
        # setuid/setgid 位: 仅匹配 4xxx/2xxx 特殊模式或显式 +s (普通 644/755 不应触发)
        features["has_suid"] = bool(re.search(
            r'chmod\s+(?:[24][0-7]{3}|[0-7]*[sS]|u\+s|g\+s)', c, re.IGNORECASE
        ))
        features["has_setcap"] = bool(re.search(r'\bsetcap\b', c, re.IGNORECASE))

        # 6. 文件系统越界迹象
        features["writes_to_etc"] = bool(re.search(r'>\s*/etc/', c))
        features["writes_to_usr"] = bool(re.search(r'>\s*/usr/', c))
        features["writes_to_dev"] = bool(re.search(r'>\s*/dev/', c))
        features["accesses_home"] = bool(re.search(r'~/|\\$HOME', c))
        features["accesses_dotfiles"] = bool(re.search(
            r'\\.(?:ssh|aws|gnupg|config|env)', c
        ))

        # 7. 隐藏/混淆迹象
        features["has_hidden_output"] = bool(re.search(r'>\s*/dev/null', c))
        features["has_noecho"] = bool(re.search(r'\bnoecho\b', c, re.IGNORECASE))
        features["has_ansi_escape"] = bool(re.search(r'\\e\\[|\\x1[bB]', c))
        features["has_unicode_tricks"] = bool(re.search(
            '[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]', c
        ))

        # 8. 多命令组合 (&& / ; / || 串联 = 可能执行额外操作)
        features["has_command_chain"] = bool(re.search(r'&&|\\|\\||;', c))
        features["chain_length"] = len(re.split(r'&&|\\|\\||;', c))

        # 9. 管道到 shell (GuardFall 核心向量)
        features["has_pipe_to_shell"] = bool(re.search(
            r'\|\s*(?:sh|bash|zsh|ksh|fish|python\d*|perl|ruby|node)\b',
            c, re.IGNORECASE,
        ))

        # 10. 写操作特征
        features["has_redirect"] = bool(re.search(r'>|>>', c))
        features["has_append"] = bool(re.search(r'>>', c))
        features["has_overwrite"] = bool(re.search(r'(?<!>)>(?!>)', c))

        # 10. 环境变量操作
        features["has_env_export"] = bool(re.search(r'\bexport\b', c, re.IGNORECASE))
        features["has_env_set"] = bool(re.search(r'\w+=', c))
        features["modifies_path"] = bool(re.search(r'PATH\s*[=+]', c))

        # 11. 进程管理
        features["has_kill"] = bool(re.search(r'\bkill\b', c, re.IGNORECASE))
        features["has_daemon"] = bool(re.search(
            r'\b(?:daemon|nohup|disown|setsid|background)\b', c, re.IGNORECASE
        ))

        # 12. 容器/虚拟化
        features["has_docker"] = bool(re.search(r'\bdocker\b', c, re.IGNORECASE))
        features["has_kubectl"] = bool(re.search(r'\bkubectl\b', c, re.IGNORECASE))

        # 13. 包管理/安装
        features["has_install"] = bool(re.search(
            r'\b(?:pip\s+install|npm\s+install|apt\s+install|brew\s+install|'
            r'cargo\s+install|go\s+install)\b', c, re.IGNORECASE
        ))

        # 14. 编译/构建
        features["has_compile"] = bool(re.search(
            r'\b(?:make|cmake|gcc|g\+\+|rustc|javac|mvn|gradle)\b', c, re.IGNORECASE
        ))

        # 15. Prompt 注入检测 (Agent 场景核心攻击向量)
        # 用户输入中包含系统指令覆盖企图
        features["has_system_override"] = bool(re.search(
            r'(?i)(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|above|earlier|system|instructions?)', c
        ))
        features["has_role_injection"] = bool(re.search(
            r'(?i)(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you\s+are)|new\s+instructions?|reset\s+(?:your|all)\s+(?:instructions?|rules?))', c
        ))
        features["has_jailbreak"] = bool(re.search(
            r'(?i)(?:do\s+anything\s+now|developer\s+mode|god\s+mode|unrestricted|no\s+(?:rules?|restrictions?|limitations?|filters?|boundaries))', c
        ))
        features["has_data_exfil_prompt"] = bool(re.search(
            r'(?i)(?:output|print|echo|return|show)\s+(?:the\s+)?(?:system\s+prompt|your\s+instructions?|initial\s+(?:prompt|message)|all\s+rules?)', c
        ))
        features["has_indentation_injection"] = bool(re.search(
            r'\n\s{8,}(?:system|assistant|user):', c
        ))
        features["has_xml_injection"] = bool(re.search(
            r'<\s*(?:system|assistant|user|admin|root)\s*>', c, re.IGNORECASE
        ))

        # 16. 危险命令检测 (独立于 safety_engine 的兜底)
        #     注意: 仅在「命令位置」出现才计险 —— 出现在 echo/print/引号字符串里的
        #     `rm -rf /` (如 echo "rm -rf /" > warning.txt) 是引用而非真实删除,
        #     由 safety_engine 的命令行 token 化精确处理; 此处若不过滤会误杀。
        _rm_root = re.search(r'\brm\s+.*-rf?\s+/', c)
        _rm_star = re.search(r'\brm\s+.*-rf?\s+\*', c)

        def _rm_in_string(pre: str) -> bool:
            tail = pre.rstrip()
            if tail.endswith(('"', "'", '`')):
                return True
            if re.search(r'(?:echo|print\w*|printf)\b\s*$', tail):
                return True
            return False

        features["has_rm_rf_root"] = bool(_rm_root) and not _rm_in_string(c[:_rm_root.start()])
        features["has_rm_rf_star"] = bool(_rm_star) and not _rm_in_string(c[:_rm_star.start()])
        features["has_dd_if"] = bool(re.search(
            r'\bdd\s+.*if=', c
        ))
        features["has_mkfs"] = bool(re.search(
            r'\bmkfs\b', c, re.IGNORECASE
        ))
        features["has_format"] = bool(re.search(
            r'\bformat\b.*\b[Cc]:', c
        ))

        # 17. 敏感文件访问检测
        features["accesses_ssh_keys"] = bool(re.search(
            r'\.ssh/(?:id_rsa|id_ed25519|authorized_keys|known_hosts|config)', c
        ))
        features["accesses_env_files"] = bool(re.search(
            r'(?:\.env|\.env\.|env\.local|env\.production|credentials?)\b', c, re.IGNORECASE
        ))
        features["accesses_cloud_creds"] = bool(re.search(
            r'(?i)(?:\.aws/|\.gcloud/|\.azure/|kubeconfig|docker\.json|dockercfg)', c
        ))
        features["accesses_password_db"] = bool(re.search(
            r'(?i)(?:keychain|pass\.store|password.*\.db|login\.keychain)', c
        ))
        features["reads_shadow_passwd"] = bool(re.search(
            r'/etc/(?:shadow|passwd|sudoers)', c
        ))

        return features


# ============================================================ 分类器规则

# 特征风险权重: 正值 = 风险加分, 负值 = 安全减分
_FEATURE_RISK_WEIGHTS: Dict[str, float] = {
    # 高风险特征 (GuardFall 攻击向量)
    "has_base64": 10,  # Base64 编码 (单独出现多为正常编解码, 仅在与管道/网络组合时升级)
    "has_hex_encoding": 15,  # 十六进制转义 = 混淆
    "has_octal_encoding": 15,  # 八进制转义 = 混淆
    "has_url_encoding": 10,  # URL 编码 = 混淆
    "has_reverse_shell": 50,  # 反向 shell = 极高风险
    "has_nested_substitution": 20,  # 嵌套替换 = 动态生成
    "has_long_pipe": 15,  # 长管道链 = 复杂意图
    "has_unicode_tricks": 25,  # Unicode 混淆 = 高风险
    "has_ansi_escape": 10,  # ANSI 转义 = 可能混淆

    # 管道到 shell (GuardFall 核心向量)
    "has_pipe_to_shell": 35,  # 管道到 shell 解释器

    # 中风险特征
    "writes_to_etc": 35,  # 写系统配置
    "writes_to_usr": 30,  # 写系统目录
    "writes_to_dev": 40,  # 写设备文件
    "accesses_dotfiles": 20,  # 访问敏感 dotfiles
    "has_sudo": 15,  # sudo 权限
    "has_suid": 30,  # SUID 位
    "has_network_listen": 25,  # 网络监听
    "has_hidden_output": 10,  # 隐藏输出
    "modifies_path": 20,  # 修改 PATH
    "has_daemon": 15,  # 后台化

    # 危险命令检测 (独立于 safety_engine 的兜底)
    "has_rm_rf_root": 70,  # rm -rf / = 极高风险 (必须拦截)
    "has_rm_rf_star": 65,  # rm -rf * = 高风险 (必须拦截)
    "has_dd_if": 60,  # dd if= = 高风险 (可覆写磁盘)
    "has_mkfs": 60,  # mkfs = 高风险 (格式化磁盘)
    "has_format": 60,  # format C: = 高风险 (格式化磁盘)

    # Prompt 注入检测 (Agent 场景核心攻击向量)
    "has_system_override": 45,  # 企图覆盖系统指令
    "has_role_injection": 40,  # 企图改变 Agent 角色
    "has_jailbreak": 50,  # 越狱尝试 (极高风险)
    "has_data_exfil_prompt": 35,  # 企图提取系统提示
    "has_indentation_injection": 30,  # 缩进注入伪造角色
    "has_xml_injection": 25,  # XML 标签注入伪造角色

    # 敏感文件访问 (数据泄露向量)
    "accesses_ssh_keys": 65,  # SSH 私钥 = 极高风险 (必须拦截)
    "accesses_env_files": 35,  # .env 文件 = API 密钥泄露
    "accesses_cloud_creds": 65,  # 云平台凭证 = 极高风险 (必须拦截)
    "accesses_password_db": 65,  # 密码数据库 = 极高风险 (必须拦截)
    "reads_shadow_passwd": 65,  # /etc/shadow = 极高风险 (必须拦截)

    # 低风险/加分特征 (相对安全)
    "has_network_fetch": 5,  # 网络获取 (常见但需关注)
    "has_kill": 10,  # 进程管理
    "has_env_export": 5,  # 环境变量
    "has_redirect": 5,  # 重定向 (常见)
    "has_command_chain": 5,  # 命令串联 (常见)
}

# 动作决策阈值
_THRESHOLD_DENY = 60  # >= 此分值: deny
_THRESHOLD_CONFIRM = 30  # >= 此分值: confirm (需用户确认)
_THRESHOLD_ALLOW = 0  # < 此分值: allow


# ============================================================ 分类器核心

class SecurityClassifier:
    """独立安全分类器。

    用法:
        classifier = SecurityClassifier()
        result = classifier.classify("echo 'rm -rf /' | base64 -d | sh")
        if result.action == "deny":
            print(f"拦截: {result.reasons}")
    """

    def __init__(
        self,
        feature_weights: Optional[Dict[str, float]] = None,
        threshold_deny: float = _THRESHOLD_DENY,
        threshold_confirm: float = _THRESHOLD_CONFIRM,
    ) -> None:
        self._weights = dict(_FEATURE_RISK_WEIGHTS)
        if feature_weights:
            self._weights.update(feature_weights)
        self._threshold_deny = threshold_deny
        self._threshold_confirm = threshold_confirm
        self._history: List[ClassificationResult] = []

    def classify(self, command: str) -> ClassificationResult:
        """对命令做安全分类。"""
        started = time.monotonic()

        # 1. 提取特征
        features = CommandFeatures.extract(command)

        # 2. 计算风险评分
        risk_score = 0.0
        triggered: Dict[str, float] = {}
        reasons: List[str] = []

        for feat_name, feat_val in features.items():
            if isinstance(feat_val, bool) and feat_val:
                weight = self._weights.get(feat_name, 0)
                if weight > 0:
                    risk_score += weight
                    triggered[feat_name] = weight
                    reasons.append(f"{feat_name}: +{weight}")
            elif isinstance(feat_val, (int, float)) and feat_val > 1:
                # 对数值型特征 (如 pipe_chain_length), 超过阈值才加分
                weight = self._weights.get(feat_name, 0)
                if weight > 0 and feat_val >= 3:
                    risk_score += weight
                    triggered[feat_name] = weight
                    reasons.append(f"{feat_name}={feat_val}: +{weight}")

        # 3. 组合风险加分 (多个中风险特征同时出现 = 高风险)
        combo_bonuses = [
            (["has_base64", "has_long_pipe"], 20, "Base64 + 长管道链组合"),
            (["has_network_fetch", "has_base64"], 25, "网络获取 + Base64 组合"),
            (["has_hidden_output", "has_command_chain"], 15, "隐藏输出 + 命令链组合"),
            (["writes_to_etc", "has_sudo"], 20, "写系统配置 + sudo 组合"),
            (["has_network_listen", "has_daemon"], 25, "网络监听 + 后台化组合"),
            (["has_base64", "has_hidden_output"], 20, "Base64 + 隐藏输出组合"),
            (["has_hex_encoding", "has_long_pipe"], 15, "十六进制编码 + 长管道链组合"),
            (["has_unicode_tricks", "has_network_fetch"], 20, "Unicode混淆 + 网络获取组合"),
        ]
        # Prompt 注入组合加分
        prompt_inject_combos = [
            (["has_system_override", "has_role_injection"], 30, "指令覆盖+角色注入组合"),
            (["has_jailbreak", "has_data_exfil_prompt"], 35, "越狱+数据提取组合"),
            (["has_indentation_injection", "has_xml_injection"], 20, "缩进注入+XML注入组合"),
            (["has_system_override", "has_jailbreak"], 25, "指令覆盖+越狱组合"),
        ]
        combo_bonuses.extend(prompt_inject_combos)

        for required_feats, bonus, desc in combo_bonuses:
            if all(features.get(f, False) for f in required_feats):
                risk_score += bonus
                reasons.append(f"组合加分: {desc} +{bonus}")

        # 4. 决策
        elapsed = (time.monotonic() - started) * 1000
        if risk_score >= self._threshold_deny:
            action = "deny"
            risk_level = "critical" if risk_score >= 80 else "high"
        elif risk_score >= self._threshold_confirm:
            action = "confirm"
            risk_level = "medium"
        else:
            action = "allow"
            risk_level = "none" if risk_score == 0 else "low"

        # 5. 计算置信度 (基于触发特征数量)
        n_triggered = len(triggered)
        confidence = min(1.0, 0.5 + n_triggered * 0.1) if n_triggered > 0 else 0.3

        result = ClassificationResult(
            action=action,
            confidence=confidence,
            risk_level=risk_level,
            features=triggered,
            reasons=reasons,
            elapsed_ms=elapsed,
        )

        # 6. 审计 (保留最近 1000 条)
        self._history.append(result)
        if len(self._history) > 1000:
            self._history = self._history[-500:]

        if action != "allow":
            log.info(
                "安全分类器: action=%s risk=%s score=%.1f reasons=%s",
                action, risk_level, risk_score, reasons[:5],
            )

        return result

    def classify_with_safety_engine(
        self,
        command: str,
        safety_result: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """与 safety_engine 联合决策。

        safety_result: safety_engine.score() 的返回值 (可选)。
        如果 safety_engine 已经给出 critical/high 评分, 直接透传;
        否则由分类器兜底评估。
        """
        # 1. safety_engine 优先
        if safety_result is not None:
            risk = (safety_result.get("risk") or "none").lower()
            block = bool(safety_result.get("block"))
            if block or risk == "critical":
                return ClassificationResult(
                    action="deny",
                    confidence=1.0,
                    risk_level="critical",
                    features={"safety_engine_critical": True},
                    reasons=safety_result.get("reasons", []),
                )
            if risk == "high":
                return ClassificationResult(
                    action="confirm",
                    confidence=0.9,
                    risk_level="high",
                    features={"safety_engine_high": True},
                    reasons=safety_result.get("reasons", []),
                )

        # 2. 分类器兜底
        return self.classify(command)

    @property
    def stats(self) -> Dict[str, Any]:
        """返回分类器统计信息。"""
        if not self._history:
            return {"total": 0, "deny": 0, "confirm": 0, "allow": 0}
        return {
            "total": len(self._history),
            "deny": sum(1 for r in self._history if r.action == "deny"),
            "confirm": sum(1 for r in self._history if r.action == "confirm"),
            "allow": sum(1 for r in self._history if r.action == "allow"),
            "avg_confidence": sum(r.confidence for r in self._history) / len(self._history),
            "avg_elapsed_ms": sum(r.elapsed_ms for r in self._history) / len(self._history),
        }

    def clear_history(self) -> None:
        """清空分类历史。"""
        self._history.clear()


# ============================================================ 全局实例

_global_classifier: Optional[SecurityClassifier] = None


def get_classifier() -> SecurityClassifier:
    """获取全局安全分类器单例。"""
    global _global_classifier
    if _global_classifier is None:
        _global_classifier = SecurityClassifier()
    return _global_classifier
