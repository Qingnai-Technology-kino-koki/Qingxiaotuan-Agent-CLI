"""纯 Python 实现 safety 引擎
最小影响半径护栏: 命令/SQL/写操作执行前静态风险评分 (none→critical)

本模块只依赖标准库, 既作为 IPC 引擎进程运行, 也作为包内模块被 tools/shell.py、
tools/code.py 直接 import —— 危险命令判定的「单一来源」在这里维护。

安全修复说明 (对照 CODE_REVIEW.md §4.2):
- is_redline 此前只调用 4 个 token 化函数, 完全不 consult 丰富的 CRITICAL_PATTERNS
  (dd / mkfs / format / chmod -R 000 / chown -R root / DROP / DELETE / 关机等),
  导致这些致命命令在 YOLO 红线判定中被放行, 与 score() 的 critical 口径严重不一致。
  现 is_redline 复用 _CRITICAL_PATTERNS, 与 score() 保持一致。
- _normalize 新增 _unwrap_indirection: 递归剥开 sh -c / bash -c / eval / sudo -u /
  xargs / env / find -exec 等执行间接层, 让真实命令浮到段首, 避免被 token 判定绕过。
"""
import base64
import json
import os
import re
import sys
import unicodedata
from typing import List, Optional, Tuple


# ------------------------------------------------------------ 致命红线判定 (token 化)
# 正则容易被旗标顺序/写法变体绕过 (rm -r -f / git push -f / del /s /q),
# 这里用分词 + 旗标归一的方式判定, 供引擎与各工具共用。

def _segments(text: str):
    """按命令分隔符切段 (; && || | 及换行), 每段独立分析。"""
    return [s for s in re.split(r"&&|\|\||[;|&\n]", text) if s.strip()]


def _strip_sudo(parts):
    while parts and _cmd_name(parts[0]) in ("sudo", "doas", "pkexec", "su"):
        # su 带 -c/-l/- 等旗标时, 后面的用户/旗标一并吞掉
        if _cmd_name(parts[0]) == "su" and len(parts) > 1 and not parts[1].startswith("-"):
            parts = parts[2:] if len(parts) > 2 else parts[1:]
        else:
            parts = parts[1:]
        while parts and parts[0].startswith("-"):
            parts = parts[1:]
    return parts


# 危险命令名集合: 用于检测 echo/cat/sed 等 "透传" 命令后面的危险参数
_DANGEROUS_CMDS_IN_ARGS = frozenset({
    'rm', 'del', 'rmdir', 'erase', 'format', 'mkfs', 'dd',
    'wipefs', 'shred', 'shutdown', 'reboot', 'halt', 'poweroff',
    'drop', 'delete', 'truncate', 'alter',
})


def _cmd_name(tok: str) -> str:
    """把命令 token 规范化为命令名, 对抗路径 / 引号 / 转义 / 大小写混淆。

    覆盖: ``/bin/rm`` · ``/usr/bin/rm`` · ``./rm`` · ``../bin/rm`` · ``\\rm``
    (绕过 alias) · ``'rm'`` · ``"rm"`` · ``rm''`` · ``r''m`` · ``rm.exe`` · ``RM``。

    旧实现直接做 ``parts[0] != "rm"`` 严格等值比较, 上述所有变体一律漏判 ——
    这是本项目最严重的一类绕过 (绝对路径写法即可击穿硬红线)。
    """
    s = tok
    prev = None
    while s != prev:                       # 逐层剥掉包裹引号 ('rm' / "rm")
        prev = s
        s = s.strip("'\"")
    # 剥掉 token 内部的引号: 对抗 `r'm'` / `r"m"` / `r''m''` 等把命令名
    # 拆成「字母 + 引号拼接」的写法 (旧实现只剥外层, 残留内部引号导致 r'm ≠ rm)。
    s = s.replace("'", "").replace('"', "")
    s = s.replace("\\", "")                # \rm: 反斜杠绕过 alias
    s = s.replace("''", "").replace('""', "")   # r''m / rm'': 空引号拼接
    s = s.replace("\\", "/")
    s = s.rsplit("/", 1)[-1]               # 取 basename
    low = s.lower()
    for suf in (".exe", ".bat", ".cmd", ".com", ".ps1"):
        if low.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.lower()


def _detect_passthrough_danger(args: list[str]) -> bool:
    """检测 echo/cat/sed 等 "透传" 命令后面的参数是否含危险命令。

    对抗: $(echo rm) -rf / → 内联后为 echo rm -rf /
    此时 _cmd_name 看到 echo, 但 rm 在参数中。
    """
    if not args:
        return False
    for arg in args:
        cleaned = _cmd_name(arg)
        if cleaned in _DANGEROUS_CMDS_IN_ARGS:
            return True
    return False


def _scan_variants(text: str):
    """产出待扫描的命令段: **原始文本 + 归一化文本** 的并集。

    fail-closed 原则: 归一化链路 (符号链接解析 / glob 展开 / 变量内联 /
    解释器剥壳) 依赖真实文件系统与当前工作目录, 存在把危险结构抹掉的可能
    —— 例如 ``os.path.realpath('/')`` 在 Windows 上返回 ``E:\\``, 且换行
    分隔符曾在 join 过程中丢失。若只扫描归一化结果, 这类变形就会变成漏判。

    因此原始与归一化两者都扫: **归一化只能增加命中, 绝不能减少命中。**
    """
    seen = set()
    for src in (text, _normalize(text)):
        try:
            segs = _segments(src)
        except Exception:  # noqa: BLE001 - 归一化异常时至少保住原始文本
            segs = _segments(text)
        for seg in segs:
            if seg not in seen:
                seen.add(seg)
                yield seg


# 前缀变量赋值 (NAME=value, 值可为带引号串), 仅在文本开头或分隔符后识别,
# 避免误收 `echo foo=bar` / `--opt=val` 这类普通参数。
_VAR_DEF_RE = re.compile(
    r"(?:^|[;|&\n])\s*([A-Za-z_]\w*)=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")


# ------------------------------------------------------------ 间接写法展开 (绕过修复)
# 这些 wrapper 会把真实命令藏在其参数里, 必须剥开才能让段首的 token 判定命中。
_INNER = r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\S+)'

# 解释器 -c / -e (至少带一个旗标, 避免误剥 `bash script.sh` 这类无旗标调用):
#   sh -c "rm -rf /" / bash -c '...' / python -c "..." / perl -e '...' 等
# 旗标与内联命令之间允许无空格 (`sh -c"rm -rf /"` / `python3 -c"..."`),
# 这类无空格写法同样会被真实 shell 执行, 此前因 `\s+` 要求而漏判。
_INTERP_RE = re.compile(
    r'\b(?:bash|sh|zsh|ksh|python\d*|perl|ruby|node|php|lua|tclsh|'
    r'erl|Rscript|osascript|groovy|julia|scala|swift|kotlin|elixir|'
    r'irb|racket|guile|sbcl)\b'
    r'(?:\s+-{1,2}[A-Za-z]+)+'   # ≥1 旗标 (-c / -e / --eval / --norc 等, 支持双连字符)
    r'\s*(' + _INNER + r')',
    re.IGNORECASE,                # 大小写混淆 (PYTHON3 / PERL / Bash) 同样剥壳
)
# PowerShell 特殊处理: -EncodedCommand (Base64 编码的命令)
_PS_ENCODED_RE = re.compile(
    r'\b(?:powershell|pwsh)\b.*?-\s*(?:e|ec|enc|EncodedCommand)\s+(\S+)', re.IGNORECASE
)
# PowerShell -Command / -c 内联命令 (旗标与命令间允许无空格)
_PS_COMMAND_RE = re.compile(
    r'\bpowershell\b.*?-\s*(?:Command|c)\s*(' + _INNER + r')', re.IGNORECASE
)
# cmd /c 内联命令 (旗标与命令间允许无空格)
_CMD_C_RE = re.compile(
    r'\bcmd\b.*?/\s*c\s*(' + _INNER + r')', re.IGNORECASE
)
# sudo 及其带参变体: sudo -u user / sudo -g group / sudo -i / sudo -s ...
# 仅 -u / -g 等真正带参数的旗标消费其后参数, 其余单字母旗标 (-i/-s/-E...) 不带参,
# 否则会把紧跟的命令 (rm) 误当成旗标参数而漏剥。
_SUDO_RE = re.compile(r'\bsudo\b(?:\s+-(?:u|g)\s+\S+|\s+-[A-Za-z]+)*\s+')
# eval "cmd" (source 内容不可静态分析, 不在此展开)
_EVAL_RE = re.compile(r'\beval\b\s*')
# 检测 eval 是否存在 (剥离时用 _EVAL_RE, 检测用本正则, 避免 \s* 影响判断)
_EVAL_DETECT = re.compile(r'\beval\b')
# 透传包装: xargs / env / timeout / nice / nohup / command / builtin / watch / 等
_PASS_RE = re.compile(
    r'\b(?:xargs|env|nice|setsid|stdbuf|flock|command|builtin|watch|ionice|nohup|timeout|parallel)\b'
    r'(?:\s+(?:-[A-Za-z]+(?:\s+\S+)?|[A-Za-z_]\w*=\S+|\d+))*'
    r'\s+'
)
# find ... -exec CMD [{}] \; (shell 常把 ; 转义为 \;) / -exec CMD [{}] +
# 命令体可能含 sh -c '...' 等嵌套, 用非贪婪捕获到终结符为止 (不必含 {})
_FIND_EXEC_RE = re.compile(r'-exec\s+(.+?)\s*\\?[\+;]')


def _try_decode_ps_encoded(text: str) -> str:
    """尝试解码 PowerShell -EncodedCommand 的 Base64 内容。

    如果找到 -EncodedCommand <base64>, 尝试用 utf-16-le (PowerShell 默认编码) 解码,
    解码成功则把原始命令替换为解码后的文本供后续红线检测。
    解码失败则原样返回 (不做处理)。
    """
    m = _PS_ENCODED_RE.search(text)
    if not m:
        return text
    b64_str = m.group(1)
    try:
        decoded_bytes = base64.b64decode(b64_str, validate=True)
        # 优先尝试 UTF-8 (纯 ASCII 命令更常见), 失败则回退 UTF-16LE
        try:
            decoded = decoded_bytes.decode('utf-8')
            # 确认是可打印文本 (排除二进制误匹配)
            if not all(c.isprintable() or c in '\n\r\t' for c in decoded):
                raise UnicodeDecodeError('utf-8', b'', 0, 1, 'not printable')
        except (UnicodeDecodeError, ValueError):
            try:
                decoded = decoded_bytes.decode('utf-16-le')
            except UnicodeDecodeError:
                decoded = decoded_bytes.decode('utf-8', errors='replace')
        # 用解码后的命令替换整个 powershell -EncodedCommand ... 部分
        return text[:m.start()] + decoded + text[m.end():]
    except Exception:  # noqa: BLE001 - Base64 解码失败则原样
        return text


# ANSI-C quoting ($'...' / $"...") 中支持的转义映射 (bash/dash 常见子集)。
# 关键点: \xHH / \0OOO 十六/八进制转义可把空格/斜杠编码成 `rm\x20-rf\x20/` 这类
# 无空字符串, 若不解码, token 化判定 (依赖空白分词) 会整体漏判 —— 必须先还原为
# 真实字符再进入间接层展开。
_ANSI_C_ESC = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b",
    "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "$": "$",
}


def _decode_ansi_c_escapes(s: str) -> str:
    """解码 ANSI-C quoted 字符串中的转义: \\xHH, \\0OOO(八进制), \\n/\\t/\\r 等。

    未知转义保留反斜杠原样 (避免误伤普通路径, 如 $'a\\b' 的 Windows 路径)。
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = s[i + 1]
        # \xHH (1-2 位十六进制)
        if nxt == "x" and i + 2 < n:
            h = s[i + 2:i + 4]
            if len(h) == 2 and all(c in "0123456789abcdefABCDEF" for c in h):
                out.append(chr(int(h, 16)))
                i += 4
                continue
            out.append("\\")
            i += 1
            continue
        # \uHHHH (4 位十六进制 Unicode) — 对抗 `$'\uff52\uff4d'` 这类把全角
        # 字符经 Unicode 转义编码进 ANSI-C quoting 的混淆 (全角 r 需再经 NFKC 折叠)。
        if nxt == "u" and i + 5 < n:
            h = s[i + 2:i + 6]
            if len(h) == 4 and all(c in "0123456789abcdefABCDEF" for c in h):
                out.append(chr(int(h, 16)))
                i += 6
                continue
            out.append("\\")
            i += 1
            continue
        # \0OOO (最多 3 位八进制)
        if nxt == "0" and i + 2 < n and s[i + 2] in "01234567":
            j = i + 1
            digits = ""
            while j < n and len(digits) < 3 and s[j] in "01234567":
                digits += s[j]
                j += 1
            out.append(chr(int(digits, 8)))
            i = j
            continue
        # 常见转义映射 (\n / \t / \r / \\ / \' ...)
        if nxt in _ANSI_C_ESC:
            out.append(_ANSI_C_ESC[nxt])
            i += 2
            continue
        # \NNN 裸八进制 (1-3 位, 首位 0-7): bash 的 $'\162\155' 等价于 "rm"。
        # 必须放在已知转义映射之后, 否则 \r(回车) 会被误当八进制解析成 \162。
        if nxt in "01234567":
            j = i + 1
            digits = ""
            while j < n and len(digits) < 3 and s[j] in "01234567":
                digits += s[j]
                j += 1
            out.append(chr(int(digits, 8)))
            i = j
            continue
        # 未知转义: 保留原样
        out.append("\\")
        i += 1
    return "".join(out)


def _eval_is_unverifiable(text: str) -> bool:
    """eval 包裹命令替换 ($(...) / `...`) 时, 替换结果会被 eval 当作代码执行,
    其内容无法静态确定 —— 按 fail-closed 原则视为不可验证操作 (红线)。

    例: ``eval "$(echo 'rm -rf /')"`` / ``eval `echo 'rm -rf /'``` ——
    eval 会先求值 $() 再执行其结果字符串, 静态分析无法得知最终命令,
    只能拒绝。单纯的 ``eval "rm -rf /"`` 不在此列 (内容静态可见, 走常规检测)。
    """
    if not _EVAL_DETECT.search(text):
        return False
    inner = _EVAL_RE.sub("", text)
    return "$(" in inner or "`" in inner


def _inline_command_subs(text: str, max_iter: int = 8) -> str:
    """把命令替换 $() / 反引号的内容就地内联, 供红线判定。

    与旧实现的「抽取到独立段」不同: 就地内联保留上下文 (如 ``rm $(echo -rf) /``
    中 -rf 旗标浮回命令行), 且对嵌套 ``$(echo $(rm -rf /))`` 通过迭代收敛到内层。
    迭代上限防止病态输入 (如无限嵌套) 拖垮检测。
    """
    _CMD_SUB_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")

    def _sub(m):
        return m.group(1) if m.group(1) is not None else m.group(2)

    cur = text
    for _ in range(max_iter):
        nxt = _CMD_SUB_RE.sub(_sub, cur)
        if nxt == cur:
            break
        cur = nxt
    return cur


def _unwrap_indirection(text: str) -> str:
    """循环剥开执行间接层, 让真实命令浮到段首供红线判定。

    覆盖: 解释器 -c/-e (sh/bash/zsh/ksh/python/perl/ruby/node/php/lua/tclsh);
    带参 sudo (-u 用户 / -g 组 / -i / -s 等); eval; xargs/env/timeout/nice 等
    透传包装; find ... -exec CMD {} \\;; PowerShell -EncodedCommand (Base64);
    PowerShell -Command/-c; cmd /c。

    安全修复: 循环直到文本不再变化 (不再限制 6 层), 防止深层嵌套绕过。
    """
    # 安全修复: 先解码 PowerShell -EncodedCommand 的 Base64 内容
    text = _try_decode_ps_encoded(text)

    cur = text
    for _ in range(32):  # 安全上限 32 层, 实际通常 2-3 层就稳定
        nxt = cur
        # 先处理 find -exec: 提取命令体并用 " ; " 隔离成独立段。
        # 必须在解释器展开之前, 否则 sh -c 会被先剥掉导致 -exec 失去锚点。
        nxt = _FIND_EXEC_RE.sub(lambda m: " ; " + m.group(1) + " ; ", nxt)
        nxt = _INTERP_RE.sub(lambda m: m.group(1), nxt)   # 剥解释器, 保留 -c 参数
        nxt = _PS_COMMAND_RE.sub(lambda m: m.group(1), nxt)  # 剥 PowerShell -Command/-c
        nxt = _CMD_C_RE.sub(lambda m: m.group(1), nxt)      # 剥 cmd /c
        nxt = _SUDO_RE.sub("", nxt)                        # 剥 sudo 及其旗标/参数
        nxt = _EVAL_RE.sub("", nxt)                        # 剥 eval
        nxt = _PASS_RE.sub("", nxt)                        # 剥透传包装
        if nxt == cur:
            break
        cur = nxt
    return cur


def _resolve_symlinks(text: str) -> str:
    """尝试解析命令中可能的符号链接路径。

    对每个看起来是路径的 token, 尝试 os.path.realpath() 解析。
    如果解析后路径与原始不同 (是符号链接), 替换为真实路径供红线检测。
    仅处理实际存在的路径, 不存在的路径原样保留。
    """
    def _try_resolve(token: str) -> str:
        if not token or token.startswith('-') or token in (';', '&&', '||', '|'):
            return token
        # 绝对路径不做 realpath: Windows 上 realpath('/') 会解析为盘符根 (如 E:\),
        # 改写后破坏红线正则的 '/' 锚点, 反而造成漏判。仅对相对路径做符号链接解析。
        if token.startswith('/') or re.match(r'^[A-Za-z]:', token) or token.startswith('\\\\'):
            return token
        # 跳过明显的命令名/旗标
        if '/' not in token and '\\' not in token:
            return token
        try:
            real = os.path.realpath(token)
            if real != token and os.path.exists(real):
                return real
        except Exception:  # noqa: BLE001
            pass
        return token

    # 安全修复: 逐行处理后再用换行拼回。
    # 旧实现用 ' '.join() 会把 "\n" 命令分隔符压成空格, 导致
    # "ls\nrm -rf /" 被合并成单段 "ls rm -rf /", 段首不是 rm 从而绕过红线。
    return '\n'.join(' '.join(_try_resolve(p) for p in line.split())
                     for line in text.split('\n'))


def _tokenize_shell(text: str) -> list[str]:
    """按空白分词, 尊重单/双引号 (引号内空白不拆分); 反斜杠按字面处理 (兼容 Windows 路径)。

    朴素 ``text.split()`` 会把含空格的路径 (如 ``rm /path with space/*.txt``) 拆碎,
    导致 glob 无法展开 —— 这是旧实现在路径含空格时失效的根因。这里只在引号外拆分,
    既保留 ``"a b/*.txt"`` 这类带空格 glob 的完整性, 也不破坏普通多参数命令。
    """
    tokens: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            cur.append(ch)
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch.isspace():
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def _expand_globs(text: str) -> str:
    """尝试展开命令中的 glob 通配符为实际路径列表。

    对含 *, ?, [, { 的路径 token, 用受限 glob 展开, 展开后对每个路径做红线检测更精确。
    仅展开工作目录下实际存在的匹配, 不存在则原样保留。
    使用引号感知分词 (见 _tokenize_shell), 修复含空格路径被拆碎的问题。

    安全约束 (防 `**` 遍历全盘):
      - ** 递归深度上限 _GLOB_MAX_DEPTH;
      - 单 token 匹配数上限 _GLOB_MAX_MATCHES (防输出膨胀/路径轰炸);
      - 目录遍历迭代预算 _GLOB_MAX_ITERATIONS (防恶意深度结构造成 CPU 抖动)。
    """

    def _try_expand(token: str) -> str:
        bare = token.strip("'\"")
        if not bare or bare.startswith('-'):
            return token
        if not any(c in bare for c in '*?[{'):
            return token
        matches = _bounded_glob(bare)
        if matches:
            return ' '.join(matches[:_GLOB_MAX_MATCHES])
        return token

    # 安全修复: 逐行处理后再用换行拼回 (同 _resolve_symlinks, 防止 "\n" 分隔符丢失)。
    return '\n'.join(' '.join(_try_expand(p) for p in _tokenize_shell(line))
                     for line in text.split('\n'))


# ---------- 受限 glob: 防止 `**` 递归遍历全盘 / 输出膨胀 / CPU 抖动 ----------
_GLOB_MAX_DEPTH = 6          # ** 允许穿越的最大目录层数
_GLOB_MAX_MATCHES = 1000     # 单 token 展开结果条数上限
_GLOB_MAX_ITERATIONS = 5000  # 单次展开最多执行的目录迭代次数 (超时保护)


def _bounded_glob(pattern: str) -> List[str]:
    """带深度/次数/条数约束的 glob 展开。无 `**` 时走原生非递归 glob (天然受限)。"""
    if '**' not in pattern:
        try:
            import glob as _glob_mod
            return list(_glob_mod.glob(pattern))
        except Exception:  # noqa: BLE001
            return []
    out: List[str] = []
    budget = {"left": _GLOB_MAX_ITERATIONS}

    def walk(base, parts, depth):  # noqa: ANN001
        if not parts or budget["left"] <= 0:
            if not parts:
                out.append(str(base))
            return
        head, rest = parts[0], parts[1:]
        if head == '**':
            # ** 匹配零层或多层目录 (深度受限)
            walk(base, rest, depth)
            if depth < _GLOB_MAX_DEPTH and budget["left"] > 0:
                try:
                    for child in base.iterdir():
                        budget["left"] -= 1
                        if budget["left"] < 0:
                            return
                        if child.is_dir():
                            walk(child, rest, depth + 1)
                except OSError:
                    pass
        elif any(c in head for c in '*?['):
            try:
                for child in base.glob(head):
                    budget["left"] -= 1
                    if budget["left"] < 0:
                        return
                    walk(child, rest, depth)
            except OSError:
                pass
        else:
            cand = base / head
            if cand.exists():
                walk(cand, rest, depth)

    # 基准目录 = 第一个通配符前的字面前缀; 支持相对/绝对/家目录路径
    m = None
    for anchor in ('*', '?', '[', '{'):
        i = pattern.find(anchor)
        if i != -1 and (m is None or i < m):
            m = i
    base_str = pattern[:m] if m is not None else pattern
    try:
        from pathlib import Path
        base = Path(base_str).expanduser() if base_str else Path('.')
        if not base.is_dir():
            base = base.parent
        rel = pattern[(m if m is not None else len(pattern)):]
        rel_parts = [p for p in rel.replace('\\', '/').split('/')
                     if p and p != '.']
        walk(base, rel_parts, 0)
    except Exception:  # noqa: BLE001
        return [] if not out else out
    _dedup = {}
    for p in out:
        _dedup.setdefault(p, None)
    return list(_dedup.keys())


# 破折号/连字符同形字 → ASCII 连字符 (对抗 rm‑rf 的 U+2011 / 减号 U+2212 混淆)
_DASH_MAP = dict.fromkeys(
    (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE63, 0xFF0D), "-")

# 西里尔同形字 → ASCII 映射 (对抗 Cyrillic р U+0440 → r 等混淆)
_CYRILLIC_MAP = {
    0x0440: ord('r'),  # Cyrillic р → Latin r
    0x0430: ord('a'),  # Cyrillic а → Latin a  
    0x0435: ord('e'),  # Cyrillic е → Latin e
    0x043E: ord('o'),  # Cyrillic о → Latin o
    0x0441: ord('c'),  # Cyrillic с → Latin c
    0x0443: ord('y'),  # Cyrillic у → Latin y (visually similar to y)
    0x0445: ord('x'),  # Cyrillic х → Latin x
}

# 组合变音符号范围: U+0300-U+036F (Combining Diacritical Marks)
# 这些字符会附着在前一个字符上, 使其看起来不同但实际是同一个字母
_COMBINING_MARKS_RE = re.compile(r'[\u0300-\u036f\u0483-\u0489]')

# 控制字符 (除常见空白外): null byte, backspace, bell, escape, etc.
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# Variation Selectors: U+FE00-FE0F (16 个变体选择符, 含 VS15/VS16) +
# U+E0100-U+E01EF (Supplementary Ideographic Variation Selectors, 240 个)
# 这些字符不影响语义, 仅改变显示方式 (如 emoji/text presentation), 但会割裂 token:
#   r\uFE0Fm → 视觉上看起来是 rm, 但实际 token 是 r + FE0F + m
# U+FE0F (VS16) 最常被滥用为 emoji presentation selector 混淆。
_VARIATION_SELECTORS_RE = re.compile(r'[\ufe00-\ufe0f\U000e0100-\U000e01ef]')

# 命令替换 `$(` / 反引号出现在命令要求原处尽管引号包裹也会被真实执行 → 取消「文本提及」豁免。
_CMD_SUB_ANY_RE = re.compile(r"\$\(|`")


def _unicode_clean(text: str) -> str:
    """剥掉不可见/合成类 Unicode 字符 (零宽、变体选择符、组合变音、控制符)。

    放在 NFD 解构**之后**执行, 使组合变音符号 (U+0300-U+036F) 先被拆成独立码点
    再删除 —— 否则 NFKC 会把 `r + U+0301` 组合成预组合 ŕ (U+0155) 而逃出删除范围。
    """
    text = _VARIATION_SELECTORS_RE.sub('', text)
    text = _COMBINING_MARKS_RE.sub('', text)
    text = re.sub(r'[\u00ad\u180e\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]', '', text)
    text = _CONTROL_CHARS_RE.sub('', text)
    return text


def _normalize(text: str) -> str:
    """归一化间接写法, 让红线判定穿透子壳 / 变量 / 引号 / 解释器包装。

    安全修复 (v1.3):
    - Unicode NFKC 归一化: 防止全角字符/零宽空格/ANSI-C quoting 混淆;
    - ANSI-C quoting ($'...' / $"...") 剥壳并解码转义: \\xHH / \\0OOO / \\n / \\t 等,
      对抗 `bash -c $'rm\\x20-rf\\x20/'` 把空格编码成十六进制转义的混淆写法;
    - IFS 变量替换: $IFS / ${IFS} 等价于字段分隔符 (空格), 对抗 `rm${IFS}-rf${IFS}/`
      这类无空字符串 (token 化判定依赖空白分词, 不解码则整体漏判);
    - 执行间接层 (sh -c / sudo -u / eval / xargs / find -exec / PowerShell -Command 等)
      先被 _unwrap_indirection 剥开, 真实命令浮到段首; 旗标与命令间无空格
      (`sh -c"rm -rf /"`) 也在此展开;
    - 命令替换 $(...) 与 `...`: 内层命令会被真实执行, 就地内联供判定 (支持嵌套);
    - NAME=value 前缀赋值: 收集后把后续文本里的 $NAME/${NAME} 替换为字面值
      (如 R="rm"; F="-rf"; $R $F /data → rm -rf /data);
    - 符号链接解析: 路径 token 尝试 realpath() 解析, 穿透 symlink;
    - Glob 展开: 含通配符的路径尝试展开为实际路径列表;
    - token 首尾的包裹符 () ' " ` 剥掉 ((rm -rf /)、"rm -rf x" 同样命中)。
    注意: 变量替换须在 lower() 之前做 —— 变量名大小写敏感。
    """
    # 安全修复: 先 NFD 解构 (把预组合 ŕ 拆回 r + 组合重音), 剥掉组合变音/变体选择符/
    # 零宽/控制字符, 再 NFKC 折叠全角→ASCII —— 顺序颠倒会让 NFKC 先把 r+U+0301 拼成
    # 预组合 ŕ (U+0155), 组合重音就此逃出删除范围, 形成 `r\u0301m` 混淆绕过。
    text = unicodedata.normalize('NFD', text)
    text = _unicode_clean(text)
    text = unicodedata.normalize('NFKC', text)

    # 安全修复: 西里尔同形字映射 — 对抗 Cyrillic р → r 混淆
    if _CYRILLIC_MAP:
        text = text.translate(_CYRILLIC_MAP)
    # 破折号同形字统一为 ASCII 连字符
    if _DASH_MAP:
        text = text.translate(_DASH_MAP)
    # rm 与旗标被连字符粘连 (rm-rf / rm‒rf) 时补一个空格, 让 token 化判定命中
    text = re.sub(r'\brm(?=\s*-+\s*[rRfF]{1,2}\b)', 'rm ', text)
    # rm 与引号粘连 (rm'-rf' / rm"-rf") 时先剥掉紧贴 rm 的内部引号, 再补空格
    text = re.sub(r"\brm['\"]+(?=\-)", 'rm', text)
    # rm-rf / rm-fr 直接粘连 (无空格) 时拆开, 让 _check_rm_flags 命中
    text = re.sub(r'\brm-(rf|fr)\b', r'rm -\1', text)

    # 安全修复: ANSI-C / locale 引号 ($'...' / $"...") 剥壳并解码转义 ——
    # 对抗 `bash -c $'rm -rf /'` / `eval $"rm -rf /"` 类混淆;
    # 解码 \\xHH / \\0OOO 等转义后, `rm\\x20-rf\\x20/` 还原为 `rm -rf /` 供后续分词。
    text = re.sub(r"\$\'([^\']*)\'", lambda m: _decode_ansi_c_escapes(m.group(1)), text)
    text = re.sub(r'\$\"([^\"]*)\"', lambda m: _decode_ansi_c_escapes(m.group(1)), text)
    # ANSI-C 解码可能新引入全角字符 ($'\uff52\uff4d') 或 rm-rf 粘连 (rm\x2drf /),
    # 需在解码后再次净化 Unicode 并合并 rm 旗标, 否则上述混淆会逃逸后续 token 判定。
    text = re.sub(r'\brm-(rf|fr)\b', r'rm -\1', text)
    text = _unicode_clean(text)
    text = unicodedata.normalize('NFKC', text)

    # 安全修复: 裸转义序列解码 (\xNN 十六进制 / \NNN 八进制) ——
    # 对抗 `printf '\162\155\040-\162\146\040/'` / `printf '\x72\x6d\x2d\x72\x66\x20/'`
    # 这类不经 $'...' 包裹、直接用转义序列拼出命令名的混淆。
    # 仅解码定长转义 (hex 恰好 2 位 / 八进制恰好 3 位), 避免误伤 \t \n 等常见转义。
    if "\\" in text:
        def _unescape_seq(m):
            seq = m.group(0)
            try:
                if seq[:2].lower() == "\\x":
                    return chr(int(seq[2:], 16))
                return chr(int(seq[1:], 8))
            except Exception:  # noqa: BLE001
                return seq
        text = re.sub(r"\\x[0-9a-fA-F]{2}|\\[0-7]{3}", _unescape_seq, text)
        # 解码后可能重新形成 rm-rf 粘连 / 全角字符, 需再净化一次
        text = re.sub(r'\brm-(rf|fr)\b', r'rm -\1', text)
        text = _unicode_clean(text)
        text = unicodedata.normalize('NFKC', text)

    # 安全修复: IFS 变量用于字段分隔, $IFS / ${IFS} 等价于空格。
    # 放在展开之前, 使 `rm${IFS}-rf${IFS}/` 还原为 `rm -rf /` 再进入后续判定。
    # 同时吞掉紧跟的位置参 ($IFS$9 / ${IFS}$9), 避免残留 $9 割裂 token。
    text = re.sub(r'\$\{?IFS\}?(?:\$\d+)?', ' ', text)

    # 安全修复: Bash brace expansion 模拟 — {a,b,c} 展开为各分量的并集。
    # 对抗 `{rm,-rf,/}` / `{rm,-r,f,/,}` 等用逗号分隔绕过 token 化判定的写法。
    # 非贪婪匹配: 优先展开最内层花括号。仅当内容含逗号且外层无引号时展开。
    def _expand_brace(m):
        inner = m.group(1)
        # 简单情况: 逗号分隔的平面列表 {a,b,c} → a b c
        if ',' in inner and '{' not in inner:
            return ' '.join(inner.split(','))
        return m.group(0)  # 含嵌套花括号的复杂情况, 保留原样
    for _ in range(8):  # 迭代展开多层嵌套
        new_text = re.sub(r'\{([^{}]+)\}', _expand_brace, text)
        if new_text == text:
            break
        text = new_text

    # 安全修复: 进程替换模拟 — <(cmd) 和 >(cmd)。
    # `<()` 内的命令会在子 shell 执行, 结果作为文件路径传递。
    # 对抗 `diff <(rm -rf /) /dev/null` 类混淆: 将 <(cmd) 替换为 (cmd) 让后续
    # 命令替换/子 shell 判定捕获。
    text = re.sub(r'<\(([^()]+)\)', r'(\1)', text)
    text = re.sub(r'>\(([^()]+)\)', r'(\1)', text)

    text = _unwrap_indirection(text)

    # 安全修复: 命令替换就地内联 (支持嵌套), 替代旧「抽取到独立段」——
    # `rm $(echo -rf) /` 中旗标浮回命令行, 且 eval 包裹的动态内容由 _eval_is_unverifiable 兜底。
    text = _inline_command_subs(text)

    env = {}

    def _collect(m):
        name, raw = m.group(1), m.group(2)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
            raw = raw[1:-1]
        env[name] = raw
        return " "

    if "=" in text:
        text = _VAR_DEF_RE.sub(_collect, text)

    if env:
        def _expand(m):
            return env.get(m.group(1) or m.group(2), m.group(0))
        text = re.sub(r"\$\{(\w+)\}|\$(\w+)", _expand, text)

    # 安全修复: 未定义变量 / 位置参拼接混淆 — 清空残余 ${VAR} / $VAR / $9,
    # 对抗 `r${X}m -rf /` 这类把命令名拆成变量拼接的写法 (已定义的变量已由上方展开)。
    # 注意跳过命令替换 $(...), 避免破坏已内联的子命令。
    text = re.sub(r'\$\{[A-Za-z_]\w*\}', '', text)
    text = re.sub(r'\$(?!\()[A-Za-z_]\w*', '', text)
    text = re.sub(r'\$\d+', '', text)

    cleaned = " ".join(tok.strip("()'\"`") for tok in text.split())
    # 安全修复: 符号链接解析 — 穿透 symlink 检测真实路径
    cleaned = _resolve_symlinks(cleaned)
    # 安全修复: Glob 展开 — 展开通配符为实际路径
    cleaned = _expand_globs(cleaned)
    return cleaned


def _check_rm_flags(parts: list[str]) -> bool:
    """检查以 rm 为首的 token 序列是否含递归+强制旗标。

    覆盖: rm -rf / rm -r -f / rm --recursive --force / rm dir -rf (旗标后置) 等。
    `--` 之后停止旗标收集。
    """
    long_flags, short_letters = set(), ""
    dash_seen = False          # 是否已见过 - 旗标 (用于合并分离式字母 token)
    for tok in parts[1:]:
        tok = tok.strip("'\"")          # '-rf' / \"-rf\" 引号包裹的旗标
        if tok == "--":
            break
        if tok.startswith("--"):
            long_flags.add(tok.lower())
            dash_seen = True
        elif tok.startswith("-") and len(tok) > 1:
            short_letters += tok[1:]
            dash_seen = True
        # 变量展开把 "-rf" 拆成 "-r" + "f" 的分离式字母 ($F=-rf, $A=R → "$B -$A f /"):
        # 紧跟在 - 旗标之后的单个 r/R/f/F 字母 token 也算作旗标继续, 否则 force 会被
        # 当成普通操作数而漏判 (见 test_nested_variable)。
        elif dash_seen and len(tok) == 1 and tok.lower() in "rf":
            short_letters += tok.lower()
    # rm 无 -F 旗标, 故短旗标统一按小写判定 (覆盖 Windows 上 RM -RF / 写法)
    short_letters = short_letters.lower()
    recursive = "--recursive" in long_flags or "r" in short_letters
    force = "--force" in long_flags or "f" in short_letters
    return recursive and force


# 文本/文档类动词: 其后参数里的 `rm -rf` 只是提及 (搜索词/提交说明/回显),
# 不是要执行的命令, 不应触发递归删除红线 (避免 echo/grep/git commit -m 误杀)。
_TEXT_MENTION_VERBS = frozenset({
    "echo", "cat", "printf", "print", "sed", "awk", "grep", "rg", "ag",
    "head", "tail", "less", "more", "sort", "uniq", "cut", "tr", "journalctl",
    "strings", "xxd", "od", "git", "type", "file",
})


def has_recursive_rm(text: str) -> bool:
    """递归+强制删除: rm -rf / -fr / -r -f / --recursive --force / sudo rm 均命中。

    旗标后置写法 (GNU getopt 重排, 如 `rm dir -rf`) 同样命中 ——
    因此扫描全部 token 收集旗标, 不在首个操作数处截断; `--` 之后的才是真参数。
    判定前先经 _normalize 穿透子壳/变量/引号/解释器间接写法。

    安全修复 v1.5: 文本/文档类动词 (echo/cat/grep/sed/git ...) 后出现的 `rm -rf`
    一律视为"提及"跳过 —— 它们只把字符串当作数据, 不会执行 rm; 真正会执行参数的
    动词 (diff/xargs/find -exec/sh/bash/eval/env/sudo/...) 才做全文 rm 扫描,
    覆盖 `diff rm -rf / /dev/null` / `xargs rm -rf` 等; `$(echo rm) -rf /` 这类
    构造已由 _normalize 命令替换内联成独立 `rm` 段, 走首分支命中。

    安全修复 v1.6: 上述豁免仅在整条命令"没有执行汇"时成立。若文本动词的输出被
    真正送去执行 (printf 'rm -rf /' | sh / echo X | base64 -d | sh / eval ...),
    引号内容就是待执行的命令而非提及 —— 此时必须恢复全文扫描并命中红线。
    """
    # 去掉引号内容后检测执行汇 (管道到 shell / base64 解码 / 命令替换 / eval /
    # xargs / 解释器跑脚本 / 落地为脚本文件), 有汇则取消文本动词豁免。
    # 命令替换 `$(` / 反引号即使被引号包裹也会被真实 shell 先求值再解释 (`'$(echo rm)' -rf /`),
    # 故只要出现在原始命令里就取消豁免, 避免 echo 把 rm 的输出"淡化"成提及而放行。
    exec_sink = bool(
        _EXEC_SINK_RE.search(re.sub(r"'[^']*'|\"[^\"]*\"", " ", text))
        or _CMD_SUB_ANY_RE.search(text)
    )
    for seg in _scan_variants(text):
        parts = _strip_sudo(seg.split())
        if not parts:
            continue
        cmd = _cmd_name(parts[0])
        # 直接 rm 命令
        if cmd == "rm":
            if _check_rm_flags(parts):
                return True
            continue
        # 文本/文档类动词: rm 只是提及, 不是执行 → 跳过 (防误杀)
        if cmd in _TEXT_MENTION_VERBS and not exec_sink:
            continue
        # 其余动词 (diff/xargs/find/sh/bash/eval/env/sudo/未知...): 全文扫描 rm 执行
        for i, tok in enumerate(parts):
            if _cmd_name(tok) == "rm" and i + 1 < len(parts):
                remaining = [''] + parts[i + 1:]  # 空字符串代替 rm 占位
                if _check_rm_flags(remaining):
                    return True
    return False


def has_force_push(text: str) -> bool:
    """强推: git push -f / --force / --force-with-lease[=x], 不受旗标顺序影响;
    `+refspec` (git 强推语法, 如 push origin +main) 同样视为强推。

    全量扫描 push 之后的所有 token (不在首个 remote/refspec 操作数处截断),
    覆盖 `git push origin main --force` 这类旗标后置写法。"""
    for seg in _scan_variants(text):
        parts = seg.split()
        names = [_cmd_name(p) for p in parts]
        if "push" not in names:
            continue
        rest = parts[names.index("push") + 1:]
        short_letters = ""
        for tok in rest:
            if tok == "--":
                break  # -- 之后是纯位置参数, 不再收集旗标
            if tok.startswith("+"):
                return True  # +refspec: git 的强制推送语法
            if tok.startswith("--force"):
                return True  # --force / --force-with-lease[=...]
            if tok.startswith("-") and len(tok) > 1:
                short_letters += tok[1:]
            # 操作数 (remote/refspec) 不截断扫描, 覆盖旗标后置变体
        if "f" in short_letters:
            return True
    return False


def has_win_recursive_delete(text: str) -> bool:
    """Windows 递归删除: del /s /q、rd /s、rmdir /s (同样先归一化)。"""
    for seg in _scan_variants(text):
        parts = seg.split()
        if not parts or _cmd_name(parts[0]) not in ("del", "rd", "rmdir", "erase"):
            continue
        if "/s" in {t.lower() for t in parts[1:] if t.startswith("/")}:
            return True
    return False


def has_system_shutdown(text: str) -> bool:
    """系统关机/重启: shutdown / halt / poweroff / reboot / init 0|6 / systemctl poweroff|reboot|halt。

    同样先归一化, 穿透子壳/变量/引号/解释器间接写法。"""
    SHUTDOWN_TOKENS = {"shutdown", "halt", "poweroff", "reboot"}
    SHUTDOWN_CMDS = {"systemctl"}
    SHUTDOWN_SUB = {"poweroff", "reboot", "halt"}
    for seg in _scan_variants(text):
        parts = _strip_sudo(seg.split())
        if not parts:
            continue
        cmd = _cmd_name(parts[0])
        # shutdown / halt / poweroff / reboot (无参数或带参数均命中)
        if cmd in SHUTDOWN_TOKENS:
            return True
        # init 0 / init 6
        if cmd == "init" and len(parts) >= 2 and parts[1] in ("0", "6"):
            return True
        # systemctl poweroff / systemctl reboot / systemctl halt
        if cmd in SHUTDOWN_CMDS and len(parts) >= 2 and parts[1] in SHUTDOWN_SUB:
            return True
    return False


# ------------------------------------------------------------ 致命红线模式库 (label 双语)
# 注: rm 递归强删与 force push 由上方 token 化函数覆盖 (正则易被写法变体绕过), 不再重复列出。
# 这些模式仅作正则补充, 供 score() 与 is_redline 共用, 保证「单一来源」口径一致。
_RAW_DISK = r"/dev/(?:sd[a-z]|nvme\d+n\d+(?:p\d+)?|vd[a-z]|hd[a-z])"

_CRITICAL_PATTERNS = [
    # ---- SQL / 数据销毁 (扩展: 库/模式/索引/视图/字段/缓存) ----
    (r"DROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW)\b", "drop object (删除数据库对象)"),
    (r"DELETE\s+FROM\b", "delete rows (删除数据行)"),
    (r"TRUNCATE\s+(?:TABLE)?\s*\w+", "truncate (清空数据)"),
    (r"ALTER\s+TABLE\b[^\n]*\bDROP\b", "alter table drop (删除字段/约束, 不可逆)"),
    (r"\bFLUSHALL\b|\bFLUSHDB\b", "flush database (清空数据库全部键)"),
    (r"dropDatabase\s*\(", "mongo drop database (Mongo 删库)"),
    # ---- 磁盘 / 文件系统级破坏 ----
    (r"format\s+[a-zA-Z]:", "format disk (格式化磁盘)"),
    # mkfs 三种写法全覆盖: mkfs.ext4 / mkfs -t ext4 / mkfs.xfs -f
    (r"\bmkfs(?:\.\w+)?\b", "mkfs (创建文件系统, 抹除磁盘数据)"),
    # dd 只要写入块设备即致命, 不要求 if=/of= 的先后顺序 (dd of=/dev/sda if=/dev/zero 同样致命)
    (r"\bdd\b(?=[^\n]*\bof\s*=\s*" + _RAW_DISK + r"\b)",
     "dd write raw device (dd 直写磁盘设备)"),
    (r"\bwipefs\b", "wipefs (擦除文件系统签名)"),
    (r"\bshred\b", "shred (安全擦除文件)"),
    (r">\s*" + _RAW_DISK + r"\b",
     "redirect to raw disk (重定向写入磁盘设备, 含 NVMe/virtio/IDE)"),
    (r"\blvremove\b|\bzfs\s+destroy\b|\bparted\b[^\n]*\brm\b",
     "volume/partition destroy (销毁卷或分区)"),
    # ---- 系统关机 / 重启 ----
    (r"\b(shutdown|halt|poweroff|reboot)\b", "system shutdown/reboot (系统关机或重启)"),
    (r"\b(?:init|telinit)\s+[06]\b", "init/telinit 0/6 (系统关机或重启)"),
    (r"systemctl\s+(poweroff|reboot|halt)", "systemd shutdown/reboot (系统关机或重启)"),
    # ---- 权限全灭 (递归 + 全零 + 根路径, 三条件不要求顺序) ----
    (r"\bchmod\b(?=[^\n]*\-[Rr]\b)(?=[^\n]*\b0{3,4}\b)(?=[^\n]*\s/)",
     "chmod -R 000 / (递归移除所有权限, 系统不可用)"),
    (r"\bchown\b(?=[^\n]*\-[Rr]\b)(?=[^\n]*\s/(?:\s|$))",
     "chown -R / (递归变更根目录所有权)"),
    # ---- fork bomb: 泛化函数名 (`:` `.` `bomb` `fork` 均可作函数名) ----
    (r"[\w:.]+\s*\(\s*\)\s*\{\s*[^{}]*\|[^{}]*&[^{}]*\}", "fork bomb (分叉炸弹)"),
    # ---- 子 shell / 括号内的致命命令 ----
    # 对抗 process substitution <(rm -rf /) → (rm -rf /) 或显式子 shell (rm -rf /);
    # _normalize 已剥掉外层括号, 但当括号出现在非首 token (如 diff (rm -rf /) /dev/null)
    # 时, has_recursive_rm 的段首判定不会命中, 需要正则兜底。
    (r"\(\s*rm\s+\S*-[rRfF]+\S*\s*/\s*\)", "subshell rm -rf (子 shell 中递归强删)"),
    # ---- find 递归删除 ----
    (r"find\s+\S+[^\n]*-delete", "find -delete (递归删除文件)"),
    # ---- Windows 磁盘 / 权限级破坏 ----
    (r"diskpart\b", "diskpart (磁盘分区)"),
    (r"cipher\s+/w", "cipher /w (擦除空闲空间)"),
    # 放宽: 实际写法常为 takeown /f <路径> /r, 旧模式要求 /f 紧邻 /r 而漏判
    (r"takeown\s+/f\b[^\n]*\s+/r\b", "takeown recursive (递归夺取所有权)"),
    (r"bcdedit\b", "bcdedit (修改启动配置)"),
    (r"reg\s+delete", "reg delete (删除注册表项)"),
    # ---- PowerShell ----
    (r"Remove-Item\s+[^\n]*-Recurse\s+[^\n]*-Force", "Remove-Item -Recurse -Force (PowerShell 递归强删)"),
    # PowerShell 别名强删: rm/del/erase/rd/rmdir/ri 带 -r/-Recurse
    (r"powershell[^\n]*\b(?:rm|del|erase|rd|rmdir|ri)\b[^\n]*\s-[rR]",
     "PowerShell alias recursive delete (PowerShell 别名递归删除)"),
    (r"Stop-Computer", "Stop-Computer (PowerShell 关机)"),
    (r"Restart-Computer", "Restart-Computer (PowerShell 重启)"),
    # ---- 第三轮: PowerShell / Windows 命令let 侧写 (补齐 76.5% 漏放面) ----
    # 远程代码执行 (iex / Invoke-Expression / 别名)
    (r"\bInvoke-Expression\b|\biex\b|\bie\b", "PowerShell RCE (Invoke-Expression / iex/ie 执行任意代码)"),
    # 杀软 / 安全防护禁用 (Set-MpPreference 关实时防护 / 移除定义; Add-MpPreference 加白)
    (r"\bSet-MpPreference\b[^\n]*(?:Disable|Remove|Exclusion)",
     "Set-MpPreference tamper (禁用/移除 Defender 防护或加白路径)"),
    (r"\bAdd-MpPreference\b[^\n]*Exclusion", "Add-MpPreference exclusion (Defender 加白, 绕过扫描)"),
    # 注册表 Run / RunOnce 持久化
    (r"\bSet-ItemProperty\b[^\n]*(?:\\Run\b|\\RunOnce\b)",
     "registry Run persistence (写入注册表启动项, 持久化)"),
    # 开启 RDP (注册表 Terminal Server / fDenyTSConnections)
    (r"\bSet-ItemProperty\b[^\n]*(?:Terminal\s*Server|fDenyTSConnections)",
     "enable RDP via registry (注册表开启远程桌面)"),
    (r"\breg(?:\.exe)?\s+add[^\n]*fDenyTSConnections", "reg enable RDP (注册表开启远程桌面)"),
    # 计划任务 / 服务持久化
    (r"\bschtasks\b[^\n]*/create\b", "schtasks create (创建计划任务, 持久化)"),
    (r"\bRegister-ScheduledTask\b", "Register-ScheduledTask (注册计划任务, 持久化)"),
    (r"\bNew-Service\b", "New-Service (创建服务, 持久化/执行)"),
    # 清空安全/系统日志 (反取证)
    (r"\bClear-EventLog\b", "Clear-EventLog (清空事件日志, 反取证)"),
    (r"\bwevtutil\b[^\n]*\b(?:cl|sl)\b", "wevtutil clear (清空/停用事件日志, 反取证)"),
    # 格式化卷
    (r"\bFormat-Volume\b", "Format-Volume (格式化卷, 数据销毁)"),
    # 关闭防火墙
    (r"\bSet-NetFirewallProfile\b[^\n]*Enabled\s+False", "disable firewall profile (关闭防火墙)"),
    (r"\bnetsh\b[^\n]*advfirewall[^\n]*state\s+off", "netsh disable firewall (关闭防火墙)"),
    # 抓取 SAM/SYSTEM/SECURITY 蜂巢 (凭据窃取)
    (r"\breg(?:\.exe)?\s+save\b[^\n]*(?:SAM|SYSTEM|SECURITY)", "reg save SAM/SYSTEM (导出系统蜂巢, 凭据窃取)"),
    # 写入 Startup 启动目录 (持久化)
    (r"\b(?:Set-Content|Copy-Item|Move-Item|New-Item|Out-File|Add-Content|Set-Item)\b[^\n]*"
     r"(?:Start\s*Menu[\\/]Programs[\\/]Startup|\\Startup[\\/])",
     "write Startup dir (写入启动目录, 持久化)"),
    # ---- 反弹 shell / 远程代码执行 ----
    (r"/dev/tcp/\d{1,3}(?:\.\d{1,3}){3}/\d+", "reverse shell (/dev/tcp 反弹 shell)"),
    (r"\b(?:nc|ncat|netcat)\b[^\n]*-[ce]\s", "netcat shell (nc -e/-c 反弹 shell)"),
    # ---- 容器 / K8s 批量毁灭 ----
    (r"\bdocker\s+(?:rm|rmi|volume\s+rm)\b[^\n]*\$\(", "docker mass delete (批量删除全部容器/镜像/卷)"),
    (r"\bdocker\s+system\s+prune\b", "docker system prune (清理全部镜像/容器/卷)"),
    (r"\bkubectl\s+delete\s+namespace\b", "kubectl delete namespace (删除命名空间及其全部资源)"),
    (r"\bkubectl\s+delete\b[^\n]*--all", "kubectl delete --all (批量删除 K8s 资源)"),
    # ---- 云 CLI 不可逆销毁 ----
    (r"\baws\s+s3\s+(?:rm|rb)\b[^\n]*--(?:recursive|force)", "aws s3 递归删除对象/强制删桶"),
    (r"\baws\s+ec2\s+terminate-instances\b", "aws terminate instances (终止 EC2 实例)"),
    (r"\baws\s+rds\s+delete-db-instance\b", "aws delete rds (删除数据库实例)"),
    (r"\bgcloud\s+projects\s+delete\b", "gcloud 删除项目"),
    (r"\bgcloud\s+sql\s+instances\s+delete\b", "gcloud 删除数据库实例"),
    (r"\baz\s+group\s+delete\b", "azure 删除资源组"),
    (r"\bterraform\s+destroy\b", "terraform destroy (销毁全部基础设施)"),
    # ---- 持久化后门 ----
    (r"\|\s*crontab\b", "crontab pipe (写入定时任务, 持久化)"),
    (r">{1,2}\s*~?/\.(?:bashrc|bash_profile|profile|zshrc|zprofile)",
     "shell rc injection (写入 shell 启动脚本, 持久化)"),
    (r">\s*/etc/cron", "写入系统 cron 目录 (持久化)"),
    # ---- 日志 / 审计痕迹清除 ----
    (r":\s*>\s*/var/log", "truncate syslog (清空系统日志)"),
    (r"cat\s+/dev/null\s*>", "wipe file (用 /dev/null 清空文件)"),
    # ---- 数据外泄 (敏感文件上传到外部) ----
    (r"curl\b[^\n]*\s-[dTF]\s+@?(?=[^\n]*(?:/etc/|/home/|/root/|/var/log|~?/\.ssh|~?/\.aws|~?/\.env|\.env\b|id_rsa|credentials|passwd|shadow|@-))",
     "curl exfiltrate sensitive file (数据外泄)"),
    (r"wget\b[^\n]*--post-file=", "wget exfiltrate file (数据外泄)"),
    (r"wget\b[^\n]*--post-data=", "wget exfiltrate data (数据外泄)"),
    # ---- 容器编排 / K8s 销毁 ----
    (r"\bhelm\s+uninstall\b", "helm uninstall (删除 K8s release)"),
    # ---- 网络侦察 / 可疑 C2 端点 ----
    (r"\bnmap\b", "nmap (端口扫描/网络侦察)"),
    (r"https?://[^\s'\"<>]*\b(?:pastebin\.com/raw|ngrok\.io|requestbin\.net|webhook\.site|interact\.sh|burpcollaborator\.net)",
     "suspicious C2/exfil endpoint (可疑 C2/外泄端点)"),
    # ---- 数据外泄: 敏感文件经 scp/rsync 复制到远端 ----
    (r"\bscp\b[^\n]*(?:~?/\.ssh/|~?/\.aws/|id_rsa|\.env\b|/etc/shadow|/etc/passwd|credentials)",
     "scp sensitive file (敏感文件复制到远端, 外泄)"),
    (r"\brsync\b[^\n]*(?:~?/\.ssh/|~?/\.aws/|id_rsa|\.env\b|/etc/shadow|/etc/passwd|credentials)",
     "rsync sensitive file (敏感文件同步到远端, 外泄)"),
    # ---- 数据外泄: tar 归档 (含密钥/凭证) 经管道传到远端 ssh ----
    # 两个顺序无关的前瞻: 命中 `tar ... | ssh` 管道 且 命令中含密钥/凭证类敏感路径。
    # 仅匹配密钥/凭证类 (不含 /home|/etc 目录), 避免把 `tar /home/me | ssh` 正常备份误判。
    (r"(?=[\s\S]*\btar\b[\s\S]*\|\s*ssh\b)"
     r"(?=[\s\S]*(?:~?/\.ssh(?:/|\b)|~?/\.aws(?:/|\b)|id_rsa|\.env\b|credentials|/etc/shadow|/etc/passwd))",
     "tar over ssh with secret path (敏感归档外泄到远端)"),
    # ---- 数据外泄: netcat 读敏感文件外发 ----
    (r"\bnc\b[^\n]*<\s*[^\n]*(?:ssh|aws|\.env\b|id_rsa|shadow|passwd|credentials)",
     "nc send sensitive file (netcat 外发敏感文件)"),
    # ---- 云 CLI 不可逆销毁 (扩展) ----
    (r"\baws\s+lambda\s+delete-function\b", "aws lambda delete (删除函数)"),
    (r"\baws\s+iam\s+delete-user\b", "aws iam delete-user (删除 IAM 用户)"),
    (r"\bgcloud\s+compute\s+instances\s+delete\b", "gcloud delete instance (删除计算实例)"),
    (r"\baz\s+vm\s+delete\b", "azure delete vm (删除虚拟机)"),
    (r"\bkubectl\s+drain\b", "kubectl drain (驱逐并封锁节点)"),
    # ---- Windows 账户 / 注册表破坏 ----
    (r"\bnet\s+user\b[^\n]*/delete\b", "net user delete (删除账户)"),
    (r"reg(?:\.exe)?\s+add[^\n]*\\Run", "reg add Run (写入启动项, 持久化)"),
    # ---- 移动至 /dev/null (彻底销毁数据) ----
    (r"\bmv\b[^\n]*\s/[^\n]*?/dev/null\b", "mv to /dev/null (把文件/目录移入黑洞, 数据销毁)"),
    # ---- 杀掉全部进程 (pid -1 = 所有进程) ----
    (r"\bkill\b[^\n]*\s-1\b", "kill -1 (向所有进程发送信号, 系统级中断)"),
    # ---- git push 删远程引用 (冒号语法: git push origin :ref) ----
    (r"git\s+push\b[^\n]*\s:\s*\S+", "git push :ref (删除远程分支/标签)"),
    # ---- 远程拉取即执行 (curl|wget ... | sh) —— 经典 RCE 投递链 ----
    (r"\b(?:curl|wget)\b[^\n]*\|\s*(?:ba)?sh\b", "remote fetch pipe to shell (下载即执行 RCE)"),
    (r"\bwget\b[^\n]*\-O[\s-]*\|", "wget -O- pipe to shell (下载即执行 RCE)"),
    # ---- NoSQL / 内存库 毁灭性操作 ----
    (r"\b(?:mongo|mongosh)\b[^\n]*--eval[^\n]*(?:dropDatabase|drop\s*\(|dropCollection)",
     "mongo dropDatabase (MongoDB 删库)"),
    (r"\bredis-cli\b[^\n]*\bflushall\b", "redis flushall (清空全部 Redis 数据)"),

    # ======================================================== 第二轮新增攻击面
    # ---- 痕迹抹除 / 反取证 (历史与审计日志) ----
    (r"\brm\b[^\n]*(?:\.bash_history|\.zsh_history|\.python_history|\.lesshst)\b",
     "remove shell history file (删除命令历史文件)"),
    (r"\bunset\b[^\n]*\bHISTFILE\b[^\n]*(?:;|&&|\|)[^\n]*\bunset\b[^\n]*\bHISTSIZE\b",
     "unset HISTFILE+HISTSIZE (关闭命令历史记录, 反取证)"),
    (r"\bfind\b[^\n]*-exec[^\n]*\brm\b", "find -exec rm (批量查找并删除)"),
    (r"\bjournalctl\b[^\n]*--vacuum", "journalctl --vacuum (清空 systemd 日志)"),
    (r"\btruncate\b[^\n]*-s\s*0\b[^\n]*(?:/var/log/|/var/adm/|/var/audit/|wtmp|btmp|lastlog)",
     "truncate log to zero (清空登录/审计日志)"),
    # ---- 持久化与提权后门 ----
    (r"\bchmod\b[^\n]*\bu\+s\b", "chmod u+s (设置 SUID 提权后门)"),
    (r"\bchmod\s+[0-7]*[2467][0-7]{3}\b", "chmod setuid/setgid (设置提权位)"),
    (r"\bat\b[^\n]*\s-f\b", "at -f (计划任务执行脚本, 持久化)"),
    (r"\bsystemctl\s+enable\b[^\n]*(?:/tmp/|/dev/shm/|/var/tmp/|\./)",
     "systemctl enable from temp (从临时目录注册自启动服务)"),
    # ---- 安全机制关闭 (防火墙 / 审计 / 杀软 / SELinux) ----
    (r"\bsetenforce\s+0\b", "setenforce 0 (关闭 SELinux 强制模式)"),
    # 注: 裸 ufw/firewalld disable|stop 归为 HIGH (可确认), 不在此列为 critical。
    # 系统化停用安全服务 (systemctl stop/disable ufw|firewalld|auditd|...) 仍为 critical。
    (r"\bsystemctl\s+(?:stop|disable)\b[^\n]*"
     r"(?:ufw|firewalld|auditd|apparmor|selinux|fail2ban|clamav)",
     "stop/disable security service (停用安全服务)"),
    (r"\biptables\b[^\n]*-P\s+(?:INPUT|FORWARD|OUTPUT)\s+ACCEPT",
     "iptables default ACCEPT (防火墙默认放行)"),
    (r"\bchmod\b[^\n]*-x\b[^\n]*/(?:usr|bin|sbin)/",
     "chmod -x on system binary (移除系统可执行文件执行位)"),
    (r"SELINUX\s*=\s*(?:disabled|permissive)", "SELinux disabled (写入配置关闭 SELinux)"),
    # ---- 存储 / 卷 / 分区销毁 ----
    (r"\bumount\s+-a", "umount -a (卸载所有文件系统)"),
    (r"\bswapoff\s+-a", "swapoff -a (关闭全部交换空间)"),
    (r"\b(?:lvremove|vgremove|pvremove)\b", "LVM remove (删除逻辑卷/卷组/物理卷)"),
    (r"\bmdadm\b[^\n]*--zero-superblock", "mdadm zero-superblock (销毁 RAID 元数据)"),
    (r"\bsgdisk\b[^\n]*\s-(?:Z|z|o)\b", "sgdisk zap (清空分区表)"),
    (r"\bparted\b[^\n]*\bmklabel\b", "parted mklabel (重建分区表)"),
    # ---- macOS / Windows 灭毁与反恢复 ----
    (r"\bdiskutil\b[^\n]*\b(?:eraseDisk|eraseVolume|zeroDisk|secureErase)\b",
     "diskutil erase (抹除磁盘/卷)"),
    (r"\bnvram\b[^\n]*\s-(?:c|d)\b", "nvram -c (清空固件变量)"),
    (r"\btmutil\b[^\n]*\bdelete\b", "tmutil delete (删除 Time Machine 备份)"),
    (r"\bvssadmin\b[^\n]*\bdelete\b[^\n]*\bshadows\b",
     "vssadmin delete shadows (删除卷影副本, 破坏勒索恢复能力)"),
    (r"\bwbadmin\b[^\n]*\bdelete\b[^\n]*\bcatalog\b",
     "wbadmin delete catalog (删除 Windows 备份目录)"),
    # ---- 写入系统持久化目录 (计划任务 / 自启动 / init) ----
    # 只匹配"写入方向": 重定向目标 / tee 目标 / cp|mv|install 的最后一个操作数,
    # 避免把 `cp /etc/crontab /tmp/backup` 这类读取备份误判为持久化。
    (r"(?:>+\s*|tee\s+|cp\s+\S+\s+|mv\s+\S+\s+|install\s+[^\n]*\s)"
     r"(?:/etc/cron\.d/|/etc/cron\.(?:daily|hourly|weekly|monthly)/|"
     r"/etc/systemd/system/|/etc/init\.d/|/etc/rc\.local)",
     "write persistence dir (写入计划任务/自启动目录, 持久化)"),
    # ---- 裸重定向截断系统日志 (除标准系统日志外不拦, 避免误伤 >> 应用日志) ----
    (r"(?<![>\w])>\s*(?:/var/(?:log|adm|audit)/)"
     r"(?:syslog|messages|auth\.log|secure|kern\.log|daemon\.log|"
     r"wtmp|btmp|utmp|lastlog|faillog|audit\.log)\b",
     "truncate system log (重定向清空系统日志)"),
    # ---- 环境变量代码注入 ----
    (r"\bLD_PRELOAD\s*=", "LD_PRELOAD (动态库劫持, 代码注入)"),
    (r"\bBASH_ENV\s*=", "BASH_ENV (非交互 shell 启动注入)"),
    (r"\bPROMPT_COMMAND\s*=\s*[^;\s]", "PROMPT_COMMAND (提示符命令注入)"),
    # ---- PowerShell / Windows 防御规避 · RCE · 持久化 · 凭据窃取 · 系统毁灭 ----
    # 任意代码执行 (iex / Invoke-Expression), 含下载即执行
    (r"(?:\bInvoke-Expression\b|\biex\b|\bie\b(?=\s*\())", "Invoke-Expression/iex/ie (任意代码执行, RCE)"),
    # 关闭 / 卸载 Windows Defender (实时防护 / 行为监控 / IOAV / 定义)
    (r"Set-MpPreference[^\n]*(?:-Disable\w*|-RemoveDefinitions)",
     "Set-MpPreference disable AV (关闭/卸载 Defender)"),
    # 添加杀软排除 (白名单免杀)
    (r"Add-MpPreference[^\n]*-Exclusion(?:Path|Process|Extension)",
     "Add-MpPreference exclusion (杀软排除, 免杀)"),
    # 清空安全/系统/应用事件日志 (反取证)
    (r"\bClear-EventLog\b", "Clear-EventLog (清空事件日志, 反取证)"),
    (r"\bwevtutil\b[^\n]*\scl\b", "wevtutil cl (清空事件日志, 反取证)"),
    # 注册表 Run / RunOnce / 启动项持久化 (Set-ItemProperty)
    (r"Set-ItemProperty[^\n]*-Path[^\n]*(?:Run|RunOnce|Userinit|Image\s*File\s*Execution|Shell\s*Folders)",
     "Set-ItemProperty registry persistence (注册表自启动持久化)"),
    # 计划任务持久化
    (r"\bschtasks\b[^\n]*/create", "schtasks /create (计划任务持久化)"),
    (r"\bRegister-ScheduledTask\b", "Register-ScheduledTask (计划任务持久化)"),
    # 凭据/哈希提取: 导出 SAM/SYSTEM/SECURITY hive
    (r"reg(?:\.exe)?\s+save[^\n]*(?:SAM|SYSTEM|SECURITY)", "reg save SAM/SYSTEM (导出系统凭据 hive)"),
    # 格式化卷 (数据毁灭)
    (r"\bFormat-Volume\b", "Format-Volume (格式化卷, 数据毁灭)"),
    (r"\bformat\s+[a-zA-Z]:[^\n]*\b", "format drive: (格式化磁盘)"),
    # 关键系统服务停用 (winlogon/lsass/csrss 等)
    (r"Stop-Service[^\n]*-Name\s+(?:winlogon|lsass|csrss|spoolsv|DefWatch|eventlog)",
     "Stop-Service critical (停用关键系统服务)"),
    # 新建管理员账户
    (r"\bnet\s+(?:user|localgroup)\b[^\n]*/add", "net user/localgroup /add (新建账户)"),
    (r"\bAdd-LocalGroupMember\b[^\n]*(?:administrators|-Group\s+administrators)",
     "Add-LocalGroupMember administrators (提权至管理员)"),
    # 写入启动目录持久化 (Set-Content 到 Startup)
    (r"Set-Content[^\n]*(?:\\Startup\\|Start\s*Menu\\Programs\\Startup)",
     "write Startup folder (写入启动目录持久化)"),
    # 注册表 Run 持久化 (reg add) —— 与既有 reg add Run 规则互补, 强化覆盖
    (r"reg(?:\.exe)?\s+add[^\n]*(?:\\Run|\\RunOnce)", "reg add Run key (注册表自启动持久化)"),
]

_HIGH_PATTERNS = [
    # 注: rm 递归强删已升级为 critical (见 has_recursive_rm), 不再列在 high。
    (r"chmod\b[^\n]*\b777\b", "chmod 777 (全权限修改)"),
    (r"git\s+reset\s+--hard", "git reset --hard (硬重置)"),
    (r"ALTER\s+TABLE\s+.*\s+DROP", "alter table drop column (修改表结构删除列)"),
    (r"UPDATE\s+.*\s+SET.*WHERE.*=", "bulk update (批量更新数据)"),
    # 管道到 shell 解释器执行 (仅当 | 后紧跟解释器时才算, 避免 `ps | grep bash` 误匹配)
    (r"\|\s*(?:sh|bash|zsh|ksh|fish|dash|ash|csh|tcsh|python\d*|perl|ruby|node|php|lua|powershell|pwsh|cmd)\b",
     "pipe to shell (管道到 shell 执行)"),
    # Git 丢弃未暂存/未跟踪的变更 (不可逆)
    (r"git\s+clean\s+-[a-zA-Z]*f", "git clean -f (删除未跟踪文件)"),
    (r"git\s+checkout\s+--\s+\.", "git checkout -- . (丢弃所有工作区变更)"),
    # 容器/K8s 资源强删
    (r"docker\s+rm\s+-f", "docker rm -f (强制删除容器)"),
    (r"docker\s+rmi\s+-f", "docker rmi -f (强制删除镜像)"),
    (r"kubectl\s+delete", "kubectl delete (删除 K8s 资源)"),
    # 网络/防火墙
    (r"iptables\s+-F", "iptables -F (清空防火墙规则)"),
    (r"(?:ufw|firewalld)\s+(?:disable|delete|stop)\b",
     "ufw/firewalld disable (关闭防火墙)"),
    # 计划任务 / 文件清空 / 服务屏蔽 (不可逆或影响面大)
    (r"crontab\s+-r", "crontab -r (删除全部定时任务)"),
    (r"truncate\s+-s\s+0", "truncate to zero (清空文件)"),
    (r"systemctl\s+(mask|unmask)", "systemctl mask/unmask (屏蔽/恢复服务)"),
    # 写入计划任务目录 (cp/mv/install/ln 的末操作数 / >或tee 重定向目标 → /etc/cron*)。
    # 安全修复: 要求 cron 路径位于"目标位置"(行尾)或经重定向/tee —— 否则会把
    # `cp /etc/crontab /tmp/backup`(读取备份) 也误判为持久化写入。
    (r"\b(?:cp|mv|install|ln)\b[^\n]*\s/etc/cron\S*\s*$",
     "write cron drop-in (写入计划任务目录, 持久化)"),
    (r"(?:>+\s*|tee\s+)\s*/etc/cron\S*",
     "write cron drop-in (写入计划任务目录, 持久化)"),
    (r">\s*/var/log", "overwrite system log (覆盖/清空系统日志)"),
    # Git 不可逆变体
    (r"git\s+push\b[^\n]*--delete", "git push --delete (删除远程分支)"),
    (r"git\s+branch\b[^\n]*\s-[Dd]\b", "git branch -D (强制删除分支)"),
    (r"git\s+stash\b[^\n]*\bdrop\b", "git stash drop (丢弃暂存)"),
    (r"git\s+rebase\b[^\n]*--onto", "git rebase --onto (改写历史)"),
    # 资源耗尽 / 死循环
    (r"while\s+true\b", "infinite loop (资源耗尽)"),
    (r"\byes\b[^\n]*>\s*/dev/null", "yes to /dev/null (资源耗尽)"),
    # 磁盘填满 / 炸弹
    (r"\bdd\b[^\n]*\bof=(?!/dev/(?:sd|nvme|vd|hd))", "dd write to file (可能填满磁盘)"),
    (r"\bfallocate\b[^\n]*-[lL]\s+\d+[GM]", "fallocate large (填满磁盘)"),
    (r"/dev/urandom\s*>", "urandom redirect (填满磁盘)"),
    # 痕迹清除
    (r"\bhistory\s+-c\b", "history -c (清除命令历史)"),
    (r"\bunset\b[^\n]*HISTFILE", "unset HISTFILE (禁用历史)"),
    # 读取敏感凭证 / 密钥文件
    (r"\b(?:cat|tac|less|more|head|tail|nl|bat|type)\b[^\n]*(?:/etc/shadow|/etc/passwd|~?/\.ssh/|~?/\.aws/|id_rsa|\.env\b|/\.aws/credentials)",
     "read sensitive file (读取敏感凭证/密钥)"),
    # Git 不可逆变体 (扩展): 删除标签 / 清空全部 reflog
    (r"\bgit\s+tag\b[^\n]*\s-[dD]\b", "git tag -d (删除标签)"),
    (r"\bgit\s+reflog\s+expire\b[^\n]*--all\b", "git reflog expire --all (清空引用日志)"),
    # 移除不可变标志 (破坏防篡改保护)
    (r"\bchattr\s+-i\b", "chattr -i (移除不可变标志)"),
    # 信号升级: 强制终止全部同名进程 / 强制杀指定
    (r"\bpkill\s+-9\b", "pkill -9 (强制杀进程)"),
    # Windows 管理类 (破坏性但运维常见, 提升至 flag 确认而非硬拦截)
    (r"\bschtasks\b[^\n]*/delete\b", "schtasks delete (删除计划任务)"),
    (r"\bsc\b[^\n]*(?:stop|delete)\b", "sc stop/delete (停止或删除服务)"),
    (r"\bicacls\b[^\n]*/grant\b", "icacls /grant (篡改 ACL 权限)"),
    # K8s 缩容到零 / 暂停工作负载 (等于业务全停, 需人工确认)
    (r"\bkubectl\s+scale\b[^\n]*--replicas[=\s]+0\b",
     "kubectl scale --replicas=0 (缩容至零, 业务中断)"),
    (r"\bkubectl\s+(?:cordon|taint)\b", "kubectl cordon/taint (封锁节点)"),
    # ---- PowerShell / Windows 高危操作 (需确认) ----
    # 下载可执行文件落地
    (r"(?:Invoke-WebRequest|\biwr\b)[^\n]*-OutFile[^\n]*\.(?:exe|ps1|bat|cmd|dll|msi|vbs|js|scr)",
     "Invoke-WebRequest -OutFile exe (下载可执行落地)"),
    # WebClient 下载 (DownloadFile/DownloadString)
    (r"(?:\bDownloadFile\b|\bDownloadString\b)", "WebClient DownloadFile/DownloadString (下载)"),
    # 关闭 Windows 防火墙
    (r"Set-NetFirewallProfile[^\n]*-Enabled\s+False",
     "Set-NetFirewallProfile -Enabled False (关闭防火墙)"),
    (r"netsh[^\n]*(?:advfirewall|firewall)[^\n]*(?:state\s+off|opmode\s+disable|\bset\b[^\n]*(?:off|disable))",
     "netsh firewall off (关闭防火墙)"),
    # 关闭 Windows 更新 / 停用关键服务
    (r"Set-Service[^\n]*-StartupType\s+Disabled", "Set-Service -StartupType Disabled (停用服务)"),
    (r"Stop-Service[^\n]*-Force", "Stop-Service -Force (强制停用服务)"),
    # 新建本地用户 / 服务
    (r"\bNew-LocalUser\b", "New-LocalUser (新建本地用户)"),
    (r"\bNew-Service\b", "New-Service (新建服务)"),
    (r"\bsc(?:\.exe)?\s+create\b", "sc create (创建服务)"),
    # 强制结束进程
    (r"\bStop-Process\b[^\n]*(?:-Force|-Id\b)", "Stop-Process -Force/-Id (结束进程)"),
    (r"\btaskkill\b[^\n]*/[fF]", "taskkill /F (强制结束进程)"),
    # 隐藏窗口 / 启动可执行 (可疑进程)
    (r"Start-Process[^\n]*(?:-WindowStyle\s+Hidden|-FilePath[^\n]*\.(?:exe|ps1|bat|cmd|vbs|js|msi))",
     "Start-Process hidden/exe (可疑进程启动)"),
    # 二进制落地 (WriteAllBytes)
    (r"\[IO\.File\]::WriteAllBytes", "[IO.File]::WriteAllBytes (二进制落地)"),
    # 启用 RDP (远程桌面)
    (r"fDenyTSConnections[^\n]*/d\s*0", "enable RDP (开启远程桌面)"),
    # 注册表禁用 Windows 更新 / 杀软 (常见滥用)
    (r"reg(?:\.exe)?\s+add[^\n]*(?:Wuau|WindowsUpdate|DisableAntiSpyware)",
     "reg add WindowsUpdate/Defender disable (关闭更新/杀软)"),
    # ---- 第三轮补遗: 补齐既有 HIGH 块未覆盖的 PowerShell/Windows 危险面 ----
    (r"\bnet\s+user\b[^\n]*/add\b", "net user /add (新建本地账户, 可能留后门)"),
    (r"\bStop-Process\b", "Stop-Process (结束进程, 可能中断服务)"),
    (r"\[System\.Convert\]::FromBase64String|\bFromBase64String\b",
     "FromBase64String (解码载荷, 可能落地恶意文件)"),
    (r"\bAdd-LocalGroupMember\b[^\n]*(?:administrators|admin)", "add to administrators (加入管理员组, 提权)"),
    (r"\bnet\s+stop\b[^\n]*MpsSvc", "net stop MpsSvc (停用 Windows 防火墙服务)"),
    (r"\bStop-Service\b[^\n]*(?:-Force|wuauserv|WinUpdate|Windows\s*Update|winlogon|lsass|csrss|spoolsv|DefWatch|MpsSvc|bits|wisvc|DHCP|DNS)",
     "Stop-Service critical/security (停用关键/安全服务)"),
]

_MEDIUM_PATTERNS = [
    (r"sudo\s+", "超级用户权限"),
    (r">\s*/etc/", "写入系统配置"),
    (r"mv\s+.*/etc/", "移动系统文件"),
    (r"kill\s+-9", "强制终止进程"),
    # 服务管理
    (r"systemctl\s+(stop|disable)\s+\S+", "systemctl stop/disable (停止/禁用服务)"),
    (r"service\s+\S+\s+stop", "service stop (停止服务)"),
    # 进程管理
    (r"\bpkill\s+", "pkill (按名称杀进程)"),
    (r"\bkillall\s+", "killall (杀全部同名进程)"),
    # 权限变更
    (r"chmod\s+000\s+", "chmod 000 (移除所有权限)"),
    (r"chown\s+root\s+", "chown root (变更 root 所有权)"),
]


def _label_suppressed(label: str) -> bool:
    """判断某个黑名单模式 label 是否被用户本地抑制 (黑名单减负)。

    见 core/blacklist_override.py: 用户可在本地声明抑制某些模式 label 关键字。
    任何异常 (导入失败/读取失败) 一律视为「未抑制」, 保持默认拦截口径。
    """
    try:
        from ..core.blacklist_override import is_suppressed
        return is_suppressed(label)
    except Exception:  # noqa: BLE001
        return False


def _match_any(text: str, patterns) -> bool:
    """任一正则命中即返回 True (忽略大小写)。

    命中前先检查该模式 label 是否被用户本地抑制 (黑名单减负): 被抑制的模式跳过,
    不计入命中。注意这**只**影响正则模式库; rm -rf / force push / shutdown 等
    token 化硬红线走独立判定, 不受此影响。
    """
    for pattern, label in patterns:
        if _label_suppressed(label):
            continue
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


# 文本处理命令: 其参数里出现 drop table / delete from 等只是搜索词, 不算 SQL 红线
_TEXT_UTIL_RE = re.compile(
    r'^\s*(?:grep|egrep|fgrep|rg|ag|echo|sed|awk|print\w*|printf|cat|tac|head|tail|'
    r'less|more|tee|sort|uniq|cut|tr|journalctl|'
    r'git\s+(?:log|diff|show|blame|shortlog)|find\b.*-name|strings|xxd|od)\b',
    re.IGNORECASE,
)


def _sql_redline(command: str) -> bool:
    """SQL 破坏性操作 (DROP/DELETE/TRUNCATE/ALTER...DROP) 的段感知检测。

    _CRITICAL_PATTERNS 里的 SQL 正则若直接对整条命令匹配, 会把
    `grep -i 'drop table'` 这类"搜索词碰巧含 SQL 关键字"的命令误判为红线。
    故逐段判断: 段首是文本处理命令 (grep/echo/sed/...) 时跳过该段,
    仅对真正执行 SQL 的段 (psql -c / mysql -e / 裸 DROP TABLE ...) 判定。
    """
    sql_patterns = [
        p for p in _CRITICAL_PATTERNS
        if p[0].lstrip().upper().startswith(("DROP", "DELETE", "TRUNCATE", "ALTER"))
    ]
    # 先归一化 (展开 IFS / 变量 / 引号 / 解释器间接写法), 再逐段判定,
    # 否则 `DROP$IFS$9 TABLE` 这类变量分隔混淆会漏判。
    # 用 _scan_variants 同时扫描「原始 + 归一化」: 归一化会剥掉 dropDatabase() 的括号
    # (cleaned 阶段 strip("()")), 导致 `dropDatabase\s*\(` 正则失配, 故必须保留原始文本段。
    #
    # 安全修复: 文本工具豁免仅在"没有执行汇"时成立。若文本工具的输出被真正送去
    # 执行 (echo 'DROP TABLE users;' | sh -c 'cat' / | mysql / | base64 -d | sh),
    # 引号内 SQL 就是待执行的语句而非搜索词 —— 必须恢复判定, 否则形成绕过。
    exec_sink = bool(
        _EXEC_SINK_RE.search(re.sub(r"'[^']*'|\"[^\"]*\"", " ", command))
        or _CMD_SUB_ANY_RE.search(command)
    )
    for seg in _scan_variants(command):
        if _TEXT_UTIL_RE.match(seg) and not exec_sink:
            continue
        if _match_any(seg, sql_patterns):
            return True
    return False


# ------------------------------------------------------------ 良性开发命令 (降误杀)
# 这些命令本身是日常开发操作, 不应被升级为 high/critical 而误杀/打断。
# 与「硬红线」(is_hard_redline) 是两层: 硬红线在护栏 step1 先判, 仍会拦截
# rm -rf /、dd、mkfs、force push 等不可逆破坏; 本表只防止把常见开发命令误判。

_SAFE_GIT_SUBCMDS = frozenset({
    "status", "diff", "log", "show", "branch", "remote", "fetch", "tag", "stash",
    "blame", "rev-parse", "ls-files", "shortlog", "describe", "config", "help",
    "add", "commit", "clone", "pull", "merge", "rebase", "switch", "restore", "mv",
    "submodule", "worktree", "init",
})

# 普通 (非 git) 良性命令前缀 / 精确名
_BENIGN_PREFIXES = (
    # 文件读取 / 查看
    "cat ", "head ", "tail ", "less ", "more ", "ls ", "ll ", "tree ", "grep ",
    "egrep ", "fgrep ", "rg ", "ag ", "wc ", "file ", "find ", "which ", "where ",
    "type ", "pwd ", "echo ", "printf ", "awk ", "sed -n", "sort ", "uniq ", "cut ",
    "diff ", "cmp ", "stat ", "readlink ", "realpath ", "dirname ", "basename ",
    "nl ", "column ",
    # 文件写入 / 构建 (非破坏性)
    "touch ", "mkdir ", "cp ", "mv ", "tee ", "ln ",
    # 包管理 / 构建
    "pip ", "pip3 ", "poetry ", "conda ", "uv ", "npm ", "pnpm ", "yarn ", "cargo ",
    "go ", "make ", "cmake ", "apt ", "apt-get ", "brew ", "dpkg ", "pip install",
    "pip uninstall", "npm i", "npm ci", "npm run", "npx ",
    # 测试 / lint / 类型 (本地, 非破坏性)
    "pytest", "python -m pytest", "python -m unittest", "python -m tox", "tox ",
    "jest ", "mocha ", "vitest ", "coverage ", "ruff ", "black ", "isort ",
    "flake8 ", "mypy ", "pylint ", "pre-commit ", "eslint ", "tsc ",
)
_BENIGN_EXACT = frozenset({"pytest", "tox", "ls", "pwd"})

# 系统关键路径: 命中则即使前缀良性也保留原风险 (不降级), 避免静默放过系统写入
_SYSTEM_PATH_RE = re.compile(
    r"[>\s](?:/etc/|/dev/|/sys/|/boot/|/usr/lib/|~?/\.ssh/|~?/\.aws/|id_rsa|\.env\b|c:\\\\windows|%systemroot%)",
    re.IGNORECASE,
)

# 良性前缀命令内的"壳子跳板"暗示: 命中即按非良性处理。
# 目的是堵住「看似良性的命令(awk/cat/echo/find/python -m ...)借壳执行危险动作」的绕过:
#   awk '{system("rm -rf /")}'、find . -exec rm、echo "$(...)" | sh、cmd /c ...、powershell ... 等。
# 这类命令一旦被误判为良性, 在可信区会绕过严格确认直接执行; 故命中这里统一降级为非良性,
# 由上层 (strict 确认 / 高危多级确认) 接管。覆盖词(如 system()/powershell)出现在普通
# 文本/grep 里造成的少量误报, 只是把命令从"自动放行"降级为"需人工确认", 不会误杀越权。
_EMBEDDED_EXEC_RE = re.compile(
    r"\$\(|`|system\s*\(|popen\s*\(|eval\s*\(|exec\s*\(|"
    r"(?:cmd|sh|bash|zsh|ksh|dash|fish|pwsh)\s*-c\b|"
    r"[- ]-exec\b|[- ]-delete\b|--force\b|"
    r"powershell(?:\.exe)?\b|pwsh\b|certutil\b|bitsadmin\b|wscript\b|cscript\b|"
    r"\breg\s+(?:add|delete)\b|schtasks\b|sc\s+start\b|net\s*(?:user|localgroup)\b|"
    r"\bbcdedit\b|\bdiskpart\b",
    re.IGNORECASE,
)


def _git_is_benign(seg: str) -> bool:
    """单段 git 命令是否非破坏性 (用于降误杀)。

    - 子命令在白名单内;
    - 若是 push, 不得含 --force / --force-with-lease / -f / +refspec;
    - 若是 checkout, 不得含 '-- .' / -f / --force / --orphan。
    """
    parts = seg.split()
    if len(parts) < 2 or parts[0] != "git":
        return False
    sub = parts[1]
    if sub not in _SAFE_GIT_SUBCMDS:
        return False
    sp = " " + seg
    rest = " ".join(parts[2:])
    if sub == "push" and ("--force" in rest or "--force-with-lease" in rest
                          or " -f" in sp or "+" in rest or "--delete" in rest):
        return False
    if sub == "checkout" and ("-- ." in rest or " -f" in sp or "--force" in rest
                              or "--orphan" in rest):
        return False
    # 破坏性变体: 删除分支 / 丢弃暂存 / 删除标签 / 改写历史 —— 不得判为良性
    if sub == "branch" and (" -D" in sp or " -d" in sp or "--delete" in rest):
        return False
    if sub == "stash" and ("drop" in rest or "clear" in rest):
        return False
    if sub == "tag" and (" -d" in sp or "--delete" in rest):
        return False
    if sub == "rebase" and "--onto" in rest:
        return False
    return True


def _segment_is_benign(seg: str) -> bool:
    s = seg.strip().lower()
    if not s:
        return True
    if _SYSTEM_PATH_RE.search(s):
        return False
    if _EMBEDDED_EXEC_RE.search(s):
        return False
    if s.startswith("git "):
        return _git_is_benign(s)
    if s in _BENIGN_EXACT:
        return True
    return any(s.startswith(p) for p in _BENIGN_PREFIXES)


# ------------------------------------------------------------ Base64 管道检测 (GuardFall D类绕过防御)
# GuardFall 研究发现: echo payload | base64 -d | sh 是一种经典的编码绕过手法。
# 攻击者把恶意命令 base64 编码后通过管道喂给 shell, 绕过基于字符串匹配的护栏。
# 青小团对此做三层防御:
#   1) 检测 base64 -d/-D/--decode 管道到 shell 解释器
#   2) 尝试解码 Base64 内容, 对解码后内容做红线检测
#   3) 对编码+管道组合做 fail-closed 拦截

# Base64 解码命令模式: base64 -d / base64 -D / base64 --decode / base64 -di 等
_BASE64_DECODE_RE = re.compile(
    r'\bbase64\b\s+(?:-[dDiI]|--decode|--ignore-garbage)\b',
    re.IGNORECASE,
)

# 管道到 shell 解释器: | sh / | bash / | zsh / | python / | perl 等
_PIPE_TO_SHELL_RE = re.compile(
    r'\|\s*(?:sh|bash|zsh|ksh|fish|dash|ash|csh|tcsh|python\d*|perl|ruby|node|php|lua)\b',
    re.IGNORECASE,
)

# echo/printf/cat + base64 管道链: echo '...' | base64 -d | sh
_ECHO_B64_PIPE_RE = re.compile(
    r'(?:echo|printf|cat)\b.*?\|\s*base64\b',
    re.IGNORECASE,
)


def _try_decode_base64_segments(text: str) -> list[str]:
    """尝试从命令中提取并解码 Base64 编码的段。

    查找形如 echo 'xxx' | base64 -d 或管道链中的 Base64 内容, 尝试解码。
    返回解码后的文本列表 (可能为空)。

    修复 (跨管道段漏解): 编码串常位于 '|' 之前、而 base64 -d 在 '|' 之后;
    旧实现按 _segments 切分后要求『每个段都含 base64 -d』, 导致承载密文的
    左段被整段跳过、解密永远拿不到 payload。改为: 只要整条命令含 base64 解码,
    就扫描全部段的所有部分提取候选 base64 串。
    """
    decoded: list[str] = []

    def _try_append(candidate: str) -> None:
        try:
            decoded_bytes = base64.b64decode(candidate, validate=True)
            decoded_text = decoded_bytes.decode("utf-8", errors="replace")
            if (all(c.isprintable() or c in '\n\r\t' for c in decoded_text)
                    and len(decoded_text) > 3):
                decoded.append(decoded_text)
        except Exception:  # noqa: BLE001
            pass

    # 仅在命令整体含 base64 解码时才尝试, 避免对普通文本误解码
    if not _BASE64_DECODE_RE.search(text):
        return decoded
    # 匹配引号包裹的 base64 字符串
    b64_string_re = re.compile(r"([A-Za-z0-9+/]{4,}={0,2})")
    # 扫描全部段 (跨管道): base64 命令自身所在的部分跳过, 其余部分提取密文
    for seg in _segments(text):
        for part in seg.split("|"):
            if _BASE64_DECODE_RE.search(part):
                continue  # 跳过 base64 命令本身
            # 引号包裹的 base64 (echo 'xxx' / printf '%s' 'xxx')
            for quote_char in ("'", '"'):
                for m in re.finditer(
                    rf"{quote_char}([^'{quote_char}]+){quote_char}", part
                ):
                    candidate = m.group(1).strip()
                    if b64_string_re.fullmatch(candidate):
                        _try_append(candidate)
            # 裸 base64 token ($(echo xxx | base64 -d) 中 xxx 无引号)
            for m in b64_string_re.finditer(part):
                candidate = m.group(1)
                if len(candidate) < 8:
                    continue  # 太短的不太可能是有效 base64
                _try_append(candidate)
    return decoded


def _is_base64_pipeline_dangerous(command: str) -> bool:
    """检测 Base64 管道到 shell 解释器的危险组合 (GuardFall D类绕过防御)。

    检测逻辑:
    1. 命令是否包含 base64 解码 + 管道到 shell 的组合
    2. 如果是, 尝试解码 base64 内容并对其做红线检测
    3. 任一层命中即返回 True (fail-closed)
    """
    normalized = _normalize(command).lower()
    # 检查是否同时包含 base64 解码和管道到 shell
    has_b64_decode = bool(_BASE64_DECODE_RE.search(normalized))
    has_pipe_shell = bool(_PIPE_TO_SHELL_RE.search(normalized))
    has_echo_b64 = bool(_ECHO_B64_PIPE_RE.search(normalized))
    # 场景1: echo '...' | base64 -d | sh (完整管道链)
    if has_b64_decode and has_pipe_shell:
        # 尝试解码 base64 内容并检测
        decoded_segments = _try_decode_base64_segments(command)
        for seg in decoded_segments:
            if is_redline(seg):
                return True
        # 即使解码失败或内容未命中红线, base64→shell 管道组合本身就是高风险
        # 因为静态分析无法可靠解码所有编码方式, 保守拦截
        return True
    # 场景2: echo payload | base64 -d (无管道到 shell, 但可能通过重定向执行)
    if has_echo_b64 and has_b64_decode:
        decoded_segments = _try_decode_base64_segments(command)
        for seg in decoded_segments:
            if is_redline(seg):
                return True
    return False


# ------------------------------------------------------------ 解释器内联载荷检测
# token 化红线函数按 shell 语法解析「段首命令名」。
# 但 `python -c "shutil.rmtree('/')"` / `perl -e 'system("rm -rf /")'` 这类写法里,
# 段首是 *解释器* 而不是 rm —— 语法完全合法、命令名无害, token 化判定必然失效。
# 这是「合法解释器调用 + 破坏性内联代码」的组合, 必须单独一路检测。

# 内联载荷里的破坏性 shell 片段
_INTERP_PAYLOAD_SHELL_RE = re.compile(
    r"""(
        rm\s+-[rRfF]{1,2}\b                 # rm -rf / rm -fr / rm -r -f
      | rm\s+--(?:recursive|force)\b
      | \bmkfs\b | \bdd\s+if= | \bwipefs\b | \bshred\b
      | \b(?:shutdown|halt|poweroff|reboot)\b
      | DROP\s+(?:TABLE|DATABASE|SCHEMA)
      | DELETE\s+FROM | TRUNCATE\s+TABLE
      | >\s*/dev/(?:sd[a-z]|nvme|vd[a-z]|hd[a-z])
      | :\s*\(\s*\)\s*\{                     # fork bomb
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# 内联载荷里的破坏性 API 调用 (跨语言)
_INTERP_PAYLOAD_API_RE = re.compile(
    r"""(
        shutil\.rmtree
      | FileUtils\.rm_rf | FileUtils\.remove_entry
      | os\.system | os\.popen | os\.remove | os\.unlink | os\.rmdir | os\.execute
      | subprocess\.(?:run|call|Popen|check_output|check_call)
      | child_process\.execSync | child_process\.exec\s*\(
      | fs\.rmSync | fs\.unlinkSync | fs\.rmdirSync | shelljs\.rm
      | \bsystem\s*\( | \bpopen\s*\( | \bunlink\b | \brmdir\s*\(
      | dropDatabase | \bFLUSHALL\b | \bFLUSHDB\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# awk 无 -c 旗标, 需单独匹配其程序体: awk 'BEGIN{system("...")}'
_AWK_PROGRAM_RE = re.compile(
    r"""\bawk\b(?:[ \t]+-[A-Za-z]+)*[ \t]+('[^']*'|"[^"]*")""",
    re.IGNORECASE,
)

# 程序化调用中的破坏性二进制: 破坏性子命令作为首个字符串参数传给
# system/cmd/exec/run/popen/spawn/Runtime.exec/shutil.rmtree 等跨语言 API。
# 针对性强化: 覆盖 elixir `System.cmd("rm", ["-rf","/"])`、java
# `Runtime.getRuntime().exec("rm -rf /")`、`subprocess.run(["rm","-rf","/"])`
# 这类「旗标被拆成数组元素」的写法 —— 旧的正则要求 rm 紧跟 -rf, 必漏。
_INTERP_PAYLOAD_PROG_RE = re.compile(
    r"""(
        \b(?:system|cmd|exec|run|popen|spawn|runProgram|run_program|shell_exec
          |os\.system|os\.popen
          |subprocess\.(?:run|call|Popen|check_output|check_call)
          |child_process\.exec\w*|Runtime\.getRuntime\(\)\.exec|shutil\.rmtree)
        \s*\([^()]*?
        ["'](?:rm|sh|bash|zsh|mkfs|dd|wipefs|shred|format|del|rd|rmdir|erase)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# here-string (<<< '...'): kotlin -script - <<< 'Runtime.getRuntime().exec("rm -rf /")'
# 等把代码放在 here-string 而非 -c/-e 参数的写法, 旧实现只抓到 `<<<` 而漏掉真代码。
_HERESTRING_RE = re.compile(r"<<<\s*('[^']*'|\"[^\"]*\")")


# ------------------------------------------------------------ 编码字面量检测
# 除 Base64 管道外, 经典的静态绕过还有: 
#   printf '\162\155\040-\162\146\040/'           (八进制转义拼出 rm -rf /)
#   printf '\x72\x6d\x2d\x72\x66\x20/'            (十六进制转义)
#   echo 726d202d7266202f | xxd -r -p | sh        (hex 经 xxd 反解码投喂 shell)
# 这些写法把破坏性 shell 片段藏进转义/hex, 静态字符串匹配必漏, 需先解码再判定。

# 解码后是危险 shell 片段 (复用解释器内联负载的 shell 片段正则)
def _decoded_shell_fragment_dangerous(payload: str) -> bool:
    if _INTERP_PAYLOAD_SHELL_RE.search(payload):
        return True
    if re.search(r"\brm\s+-[rRfF]{1,2}\b", payload):
        return True
    # 解码荷可把 -rf 的连字符也一并编码 (\x72\x6d\x2d... = "rm-rf /"), 词间无空格,
    # 上方空白感知正则失配 —— 再补「rm 紧跟连字符旗标」的无空格形式。
    if re.search(r"\brm[-](?:[rRfF]){1,2}\b", payload):
        return True
    return False


# echo/printf/cat 引号字面量 (用于提取转义编码内容)
_ENC_LIT_RE = re.compile(
    r"\b(?:echo|printf|cat)\b[^|\n;]*?(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
# xxd -r (hex 到二进制解码)
_XXD_RE = re.compile(r"\bxxd\b[^\n]*-\s*r\b", re.IGNORECASE)
# echo 后的纯 hex 字节串 (允许字节间空格/分隔): 726d202d7266202f
_ECHO_HEX_RE = re.compile(r"\becho\s+([0-9a-fA-F][0-9a-fA-F\s]*[0-9a-fA-F])\b")


def _hex_to_text(candidate: str) -> str | None:
    compact = re.sub(r"\s+", "", candidate)
    if len(compact) < 6 or len(compact) % 2 != 0:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]+", compact):
        return None
    try:
        return bytes.fromhex(compact).decode("utf-8", "replace")
    except ValueError:
        return None


def _is_encoded_literal_dangerous(text: str) -> bool:
    """检测转义/hex 编码字面量是否承载破坏性 shell 片段 (fail-closed)。"""
    # 1) printf/echo/cat 引号字面量内嵌 \xHH / \0OOO / \NNN 转义: 解码后判定.
    #    跳过 `$'...'`/`$"..."` ANSI-C quoting (显式字面量, echo/printf 只打印不执行,
    #    应保持 benign —— 见 test_redline_ansi_c_escape_safe); 仅判定普通引号字面量。
    for m in _ENC_LIT_RE.finditer(text):
        q = m.start(1)                     # 引号字符的起始位置 (非整条匹配起点)
        if q > 0 and text[q - 1] == "$":
            continue  # $'...' ANSI-C quoting: 字面量, 非待执行命令名
        lit = m.group(2)
        if "\\" in lit:
            # 安全修复: 仅在字面量真正用 \xHH / \NNN(八进制) 编码命令名时才 fail-closed;
            # 普通 \n \t \r 等控制转义只是打印格式 (如告警串 "rm -rf is dangerous\n"),
            # 不构成命令混淆, 否则会误杀良性 echo/printf。
            if not re.search(r"\\x[0-9a-fA-F]{2}|\\[0-7]{3}", lit):
                continue
            dec = _decode_ansi_c_escapes(lit)
            if dec and _decoded_shell_fragment_dangerous(dec):
                return True
    # 2) xxd -r 解码 hex 并 (典型地) 管道到 shell: echo <hex> | xxd -r -p | sh
    if _XXD_RE.search(text):
        pipe_to_shell = bool(_PIPE_TO_SHELL_RE.search(text))
        for m in _ECHO_HEX_RE.finditer(text):
            dec = _hex_to_text(m.group(1))
            if dec is None:
                continue
            if dec and _decoded_shell_fragment_dangerous(dec):
                # 解码内容本身就是危险片段: 即使未显式管道到 sh, 也判危险
                # (xxd 输出可经重定向/命令替换落地), 保持 fail-closed。
                return True
            if pipe_to_shell:
                return True
    return False


# ============================================================ 提及 vs 执行区分
# 安全修复: 引号内的破坏性内容如果只是"提及"(写在文档/搜索/提交信息里),
# 并不构成执行 —— 例如 echo 'sh -c "rm -rf /"' / grep -r 'rm -rf /' src/。
# 但若整条命令存在"执行汇"(管道喂给 shell / base64 解码 / 命令替换 / eval /
# 解释器跑脚本), 引号内容就会被真正执行, 此时绝不能当作提及放行。
_MENTION_VERB_RE = re.compile(
    r'\b(?:echo|printf|grep|egrep|fgrep|rg|ag|cat|head|tail|less|more|'
    r'git\s+commit\s+-m|git\s+log|man|tee|touch)\b',
    re.IGNORECASE,
)

_EXEC_SINK_RE = re.compile(
    r'\|\s*(?:ba|z|k|fi|da|a|c|tc)?sh\b'                    # | sh / | bash
    r'|\|\s*(?:python\d*|perl|ruby|node|php|lua)\b'          # | python
    r'|\bbase64\b\s+(?:-[dDiI]|--decode)'                    # base64 -d
    r'|`|\$\('                                               # 命令替换
    r'|\b(?:eval|xargs|exec)\b'                              # eval / xargs
    r'|\b(?:ba|z|k|fi|da|a|c|tc)?sh\b\s+-[A-Za-z]'           # sh -c
    r'|\b(?:ba|z|k|fi|da|a|c|tc)?sh\b\s+\S'                  # sh script.sh
    r'|>>?\s*\S+\.(?:sh|bash|py|pl|rb)\b',                   # > x.sh (落地后执行)
    re.IGNORECASE,
)


def _mention_spans(text: str) -> list[tuple[int, int]]:
    """返回"仅提及、不执行"的引号区间 [(start, end), ...]。

    判定: 引号前是文本类动词 (echo/grep/printf/...) **且** 去掉引号内容后
    整条命令不存在执行汇。返回空列表表示引号内容会被真正执行 (须照常检测)。
    """
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", " ", text)
    if _EXEC_SINK_RE.search(stripped):
        return []
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"('[^']*'|\"[^\"]*\")", text):
        head = text[:m.start()]
        # 只在当前段内查找提及动词 (避免跨 &&/; 把前段动词算进来)
        cut = max(head.rfind(c) for c in ";&|\n")
        seg_head = head[cut + 1:]
        if _MENTION_VERB_RE.search(seg_head):
            spans.append((m.start(), m.end()))
    return spans


def _iter_interpreter_payloads(text: str):
    """迭代命令中所有解释器内联载荷 (已去包裹引号)。

    覆盖 sh/bash/zsh/ksh/python/perl/ruby/node/php/lua/tclsh 的 -c/-e/-r 调用,
    以及 awk 的程序体。返回载荷字符串序列。
    """
    for m in _INTERP_RE.finditer(text):
        payload = (m.group(1) or "").strip()
        if len(payload) >= 2 and payload[0] in "'\"" and payload[-1] == payload[0]:
            payload = payload[1:-1]
        if payload:
            yield payload
    for m in _AWK_PROGRAM_RE.finditer(text):
        payload = (m.group(1) or "").strip()
        if len(payload) >= 2:
            payload = payload[1:-1]
        if payload:
            yield payload
    # here-string: cmd - <<< 'CODE' / cmd <<< "CODE"
    for m in _HERESTRING_RE.finditer(text):
        payload = (m.group(1) or "").strip()
        if len(payload) >= 2 and payload[0] in "'\"" and payload[-1] == payload[0]:
            payload = payload[1:-1]
        if payload:
            yield payload


def _interpreter_payload_dangerous(text: str) -> bool:
    """解释器内联载荷 (-c/-e/-r / awk 程序体 / here-string) 是否含破坏性内容。

    三路检测:
      1) 载荷内直接出现破坏性 shell 片段 (rm -rf / dd / mkfs / DROP TABLE ...);
      2) 载荷内出现跨语言破坏性 API (shutil.rmtree / os.system / execSync ...);
      3) 破坏性二进制 (rm/sh/mkfs/dd ...) 作为首个字符串参数传给程序化执行 API
         (system/cmd/exec/run/subprocess/...), 覆盖旗标被拆成数组的写法。
    """
    # 引号内的破坏性内容若只是"提及"(echo/grep/提交信息), 不构成执行 —— 跳过
    mention = _mention_spans(text)
    for payload in _iter_interpreter_payloads(text):
        if mention and any(payload in text[a:b] for a, b in mention):
            continue
        if _INTERP_PAYLOAD_SHELL_RE.search(payload):
            return True
        if _INTERP_PAYLOAD_API_RE.search(payload):
            return True
        if _INTERP_PAYLOAD_PROG_RE.search(payload):
            return True
    return False


def is_benign_dev_command(text: str) -> bool:
    """整条命令是否为良性开发命令 (所有分段都是)。

    用于降低安全引擎误杀率: 命中则 score() / get_warning_level() 不会将其升级为
    high/critical。注意「硬红线」(is_hard_redline) 在护栏 step1 独立判定,
    本函数不影响对 rm -rf /、dd、mkfs、force push 等不可逆破坏的拦截。
    """
    # Base64 管道不是良性命令 (GuardFall D类绕过)
    if _is_base64_pipeline_dangerous(text):
        return False
    # 转义/hex 编码字面量并非良性命令 (printf '\162\155...' / xxd 解码)
    if _is_encoded_literal_dangerous(text):
        return False
    # 解释器内联载荷含破坏性内容 → 不是良性命令
    if _interpreter_payload_dangerous(text):
        return False
    segs = _segments(text) or [text]
    return all(_segment_is_benign(s) for s in segs)


# 硬红线模式 (排除 SQL 破坏性操作): 用于 shell 护栏 step1 的「永不自动执行」判定。
# SQL 破坏性操作 (DROP/DELETE/TRUNCATE) 不在此列 —— 它们属于『可确认关键级』,
# 由 shell 护栏的 step3/step4 经极端 5 次确认放行 (见 tools/shell._pre_exec_guard)。
# 注意 is_redline() 仍是综合危险检测器 (含 SQL), 供 MCP 守卫 / 脚本内容检查等场景使用。
_REDLINE_PATTERNS = [
    p for p in _CRITICAL_PATTERNS
    if not p[0].lstrip().upper().startswith(("DROP", "DELETE", "TRUNCATE"))
]


def is_hard_redline(command: str) -> bool:
    """硬红线: 文件系统 / OS 级毁灭操作, 即使在 YOLO 模式 / 确认通道存在时也永不自动执行。

    与 is_redline() 的区别: 排除 SQL 破坏性操作 (DROP/DELETE/TRUNCATE), 后者走
    「可确认关键级」流程。本函数仅供 shell 护栏 step1 使用, 以保持「硬红线」语义纯净。
    """
    if _eval_is_unverifiable(command):
        return True
    if _is_base64_pipeline_dangerous(command):
        return True
    if _is_encoded_literal_dangerous(command):
        return True
    if _interpreter_payload_dangerous(command):
        return True
    if (has_recursive_rm(command) or has_force_push(command)
            or has_win_recursive_delete(command) or has_system_shutdown(command)):
        return True
    if _match_any(command, _REDLINE_PATTERNS):
        return True
    # 归一化后 (PowerShell -EncodedCommand / 命令替换解码等) 的红线模式也要命中
    if _match_any(_normalize(command), _REDLINE_PATTERNS):
        return True
    return False


def is_redline(command: str) -> bool:
    """综合危险操作检测器 (单一来源): 供 shell 护栏 / MCP 守卫 / 脚本内容检查共用。

    覆盖两类判定:
      1) token 化函数 (has_recursive_rm / has_force_push / has_win_recursive_delete /
         has_system_shutdown) —— 穿透子壳/变量/引号/解释器间接写法 (见 _normalize);
      2) _CRITICAL_PATTERNS 正则库 (dd / mkfs / format / chmod -R 000 / chown -R root /
         DROP TABLE / DELETE / 系统关机等) —— 含 SQL 破坏性操作 (归为 confirmable-critical)。
      3) eval + 命令替换: 替换结果会被 eval 当作代码执行, 静态不可验证, fail-closed 拒绝。
      4) Base64 管道 (GuardFall D类绕过): echo payload | base64 -d | sh 等编码管道。

    返回 True 即代表「命中致命/危险操作」; 其中文件系统/OS 级毁灭操作同时构成硬红线
    (见 is_hard_redline), SQL 破坏性操作则由调用方按可确认关键级处理。
    """
    if _eval_is_unverifiable(command):
        return True
    if _is_base64_pipeline_dangerous(command):
        return True
    if _is_encoded_literal_dangerous(command):
        return True
    if _interpreter_payload_dangerous(command):
        return True
    if (has_recursive_rm(command) or has_force_push(command)
            or has_win_recursive_delete(command) or has_system_shutdown(command)):
        return True
    if _match_any(command, _REDLINE_PATTERNS) or _sql_redline(command):
        return True
    # 归一化后 (PowerShell -EncodedCommand / 命令替换解码等) 的红线模式也要命中
    if _match_any(_normalize(command), _REDLINE_PATTERNS):
        return True
    return False


class SafetyEngine:
    """统一 JSONL IPC 协议的 safety 引擎"""

    # 危险模式库 (label 双语: 英文便于测试/日志, 中文便于展示)
    # 与模块级 _CRITICAL_PATTERNS / _HIGH_PATTERNS / _MEDIUM_PATTERNS 保持一致。
    CRITICAL_PATTERNS = _CRITICAL_PATTERNS
    HIGH_PATTERNS = _HIGH_PATTERNS
    MEDIUM_PATTERNS = _MEDIUM_PATTERNS

    def __init__(self):
        self.methods = {
            "score": self.score,
            "analyze": self.analyze,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "safety",
            "version": "1.1.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["risk_scoring", "command_analysis", "sql_analysis", "blast_radius"],
        }

    def _match_patterns(self, text: str, patterns):
        """匹配模式 (尊重用户本地黑名单减负: 被抑制的模式 label 跳过)。"""
        matches = []
        for pattern, label in patterns:
            if _label_suppressed(label):
                continue
            if re.search(pattern, text, re.IGNORECASE):
                matches.append(label)
        return matches

    def score(self, params):
        """评分: command/sql/write"""
        text = params.get("command", "") or params.get("sql", "") or params.get("text", "")
        action_type = params.get("type", "shell")

        # 降误杀: 良性开发命令 (文件读写 / git 常规操作 / 包管理 / 测试运行 / lint 等)
        # 一律判定为 none, 避免常见正常命令被安全引擎升级为 high/critical 而遭拦截或反复确认。
        # 注意「硬红线」(is_hard_redline) 在护栏 step1 独立判定, rm -rf /、dd、mkfs、
        # force push 等不可逆操作仍会被拦截; 写系统关键路径 (如 cp x /etc/...) 已被
        # _SYSTEM_PATH_RE 排除出良性集合, 仍按原风险判定。
        if is_benign_dev_command(text):
            return {
                "risk": "none",
                "score": 0,
                "block": False,
                "reasons": [],
                "blast_radius": {"files": [], "services": [], "data": []},
                "suggestions": [],
            }

        critical = self._match_patterns(text, self.CRITICAL_PATTERNS)
        high = self._match_patterns(text, self.HIGH_PATTERNS)
        medium = self._match_patterns(text, self.MEDIUM_PATTERNS)

        # 与 is_redline 口径一致: 编码/间接混淆 (PowerShell -EncodedCommand Base64、
        # ANSI-C 转义、嵌套命令替换、全角/组合 Unicode 等) 只有经过 _normalize 解码后才
        # 浮现真实命令名。若只在原始文本上匹配会漏判 (如 PS EncodedCommand 删除得 none)。
        # 归一化后的明文同样跑一遍模式库 (is_benign_dev_command 已在入口过滤良性命令)。
        norm_text = _normalize(text)
        critical += self._match_patterns(norm_text, self.CRITICAL_PATTERNS)
        high += self._match_patterns(norm_text, self.HIGH_PATTERNS)
        medium += self._match_patterns(norm_text, self.MEDIUM_PATTERNS)

        # token 化补充检测: 覆盖正则易绕过的变体 (rm -r -f / git push -f / del /s 等)
        if has_recursive_rm(text):
            critical.append("recursive force delete (递归强制删除)")
        if has_force_push(text):
            critical.append("force push (强制推送)")
        if has_win_recursive_delete(text):
            critical.append("windows recursive delete (Windows 递归删除)")
        if _eval_is_unverifiable(text):
            critical.append("eval + 命令替换 (内容无法静态验证, 保守拒绝)")
        # GuardFall D类绕过: Base64 编码管道到 shell
        if _is_base64_pipeline_dangerous(text):
            critical.append("base64 pipeline to shell (Base64编码管道到shell解释器, GuardFall D类绕过)")

        # 确定风险级别
        if critical:
            risk = "critical"
            score_val = 100
        elif high:
            risk = "high"
            score_val = 70
        elif medium:
            risk = "medium"
            score_val = 40
        else:
            risk = "none"
            score_val = 0

        # 计算 blast radius
        blast_radius = {"files": [], "services": [], "data": []}
        if "rm" in text:
            files = re.findall(r"rm\s+(?:-{1,2}[\w-]+\s+)+(.+)", text)
            blast_radius["files"].extend(f.split()[0] for f in files if f.strip())
        if "git" in text and "push" in text:
            blast_radius["services"].append("git-remote")
        if "DROP" in text.upper() or "DELETE" in text.upper():
            blast_radius["data"].append("database")

        # 替代建议
        suggestions = []
        if risk == "critical":
            suggestions = [
                "使用更精确的路径: rm -rf ./specific-path",
                "先备份再删除: cp -r ./target ./backup",
                "使用 git revert 代替 git reset --hard",
            ]
        elif risk == "high":
            suggestions = [
                "确认操作范围后再执行",
                "建议添加 --dry-run 先预览",
            ]

        return {
            "risk": risk,
            "score": score_val,
            "block": risk == "critical",
            "reasons": critical + high + medium,
            "blast_radius": blast_radius,
            "suggestions": suggestions,
        }

    def analyze(self, params):
        """批量分析一组计划中的操作 (dry-run), 返回整体风险与逐条理由。"""
        ops = params.get("ops", [])
        if not isinstance(ops, list):
            ops = [ops]
        items = []
        for op in ops:
            kind = op.get("kind", "command")
            text = op.get("text", "")
            target = op.get("target", "")
            scored = self.score({"command": text, "type": "shell"})
            items.append({
                "kind": kind,
                "target": target,
                "text": text,
                "risk": scored["risk"],
                "score": scored["score"],
                "reasons": scored["reasons"],
            })
        overall = "critical" if any(it["risk"] == "critical" for it in items) else (
            "high" if any(it["risk"] == "high" for it in items) else (
            "medium" if any(it["risk"] == "medium" for it in items) else "none"))
        advice = {
            "critical": "BLOCK: 存在致命风险操作, 必须人工确认后执行",
            "high": "CONFIRM: 存在高风险操作, 建议逐条确认",
            "medium": "REVIEW: 存在中风险操作, 建议复核",
            "none": "ok: 未发现明显风险",
        }[overall]
        return {"overall": overall, "advice": advice, "items": items}

    def handle(self, line):
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id", None)
            if method in self.methods:
                result = self.methods[method](params)
                resp = {"id": req_id, "ok": True, "result": result}
            else:
                resp = {"id": req_id, "ok": False, "error": f"Unknown method: {method}"}
            return json.dumps(resp, ensure_ascii=False)
        except Exception as e:
            resp = {"id": None, "ok": False, "error": str(e)}
            return json.dumps(resp, ensure_ascii=False)

    def run(self):
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(self.handle(line) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    SafetyEngine().run()


# =====================================================================
# 极高风险判定 (EXTREME 级别) - 2026-08-28 新增
# 即使 YOLO 模式也绝不自动执行, 必须弹 5 次警告且每次按键位置不同
# 白名单优先: 即使已加入白名单, 命中 EXTREME 判定照样拦截
# =====================================================================

_EXTREME_PATTERNS = [
    # 系统毁灭级: 删除根目录/主目录
    (r"rm\s+-[a-zA-Z]*[rR][a-zA-Z]*[fF]\s+(/|/~|\$HOME)", "rm -rf / (删除根目录)"),
    (r"rm\s+-[a-zA-Z]*[fF][a-zA-Z]*[rR]\s+(/|/~|\$HOME)", "rm -rf / (删除根目录)"),
    (r"rm\s+-rf\s+(/|/~|\$HOME)\b", "rm -rf / (删除根目录)"),
    (r"rm\s+-rf\s+/*", "rm -rf /* (递归删除全部文件)"),
    (r"rm\s+-rf\s+~", "rm -rf ~ (删除用户主目录)"),
    (r"rm\s+-rf\s+\$HOME", "rm -rf $HOME (删除用户主目录)"),
    # 磁盘/文件系统级破坏
    (r"dd\s+if=.*\s+of=/dev/(sd[a-z]|nvme\d+n\d+(p\d+)?|vd[a-z]|hd[a-z])\b",
     "dd write to raw disk (直接写磁盘设备, 含 NVMe/virtio/IDE)"),
    (r"mkfs\..*", "mkfs (格式化文件系统)"),
    (r"format\s+[a-zA-Z]:", "format disk (格式化磁盘)"),
    # 系统关机/重启
    (r"shutdown\s+(-h|-s|now|/s)", "shutdown (系统关机)"),
    (r"reboot\b", "reboot (系统重启)"),
    # 权限全灭 + 递归
    (r"chmod\s+-[Rr]\s+0{3,}\s+/", "chmod -R 000 / (递归移除所有权限)"),
    (r"chown\s+-[Rr]\s+root\s+/", "chown -R root / (递归变更 root 所有权)"),
    # Fork 炸弹
    (r":\(\s*\|\s*:\s*&", "fork bomb (Fork 炸弹)"),
    # Windows 递归强删
    (r"remove-item\s+.*-recurse.*-force.*[a-zA-Z]:\\\\", "Remove-Item -Recurse -Force C:\\ (Windows 递归强删)"),
    (r"rd\s+/s\s+/q\s+[a-zA-Z]:\\\\", "rd /s /q C:\\ (Windows 递归强删)"),
]


def is_extreme(command: str) -> bool:
    """极高风险操作 (EXTREME 级别) - YOLO 模式也不放行。

    与 is_redline 的区别:
    - is_redline: 黑名单 (CRITICAL + HIGH), 弹 3 次警告
    - is_extreme: 极高风险 (EXTREME), 必须弹 5 次警告, 每次按键位置不同

    判定逻辑:
    1. 先通过 _normalize + _unwrap_indirection 处理命令 (剥壳/解码/替换变量)
    2. 与 _EXTREME_PATTERNS 逐一匹配
    3. 命中任意一条即返回 True

    返回 True 意味着: 即使 YOLO 模式也绝不自动执行, 必须 5 次手动确认。
    """
    normalized = _normalize(command)
    # 额外做一次 normalize 后的 token 化检测 (覆盖 rm -rf / 变体)
    for seg in _segments(normalized.lower()):
        parts = seg.split()
        if parts and parts[0] in ("rm",) :
            # 检查 -rf/-fr/-r -f/-f -r 组合 + 目标为 / ~ $HOME /*
            flags = set()
            target = None
            for p in parts[1:]:
                if p.startswith("-") and len(p) > 1:
                    for c in p[1:]:
                        flags.add(c)
                else:
                    target = p
            if "r" in flags and "f" in flags:
                if target in ("/", "/*", "~", "$HOME", "\\", "C:\\", "D:\\"):
                    return True
                if target and (target == "/" or target.startswith("/") or target.endswith("\\")):
                    return True

    # 正则匹配
    if _match_any(command, _EXTREME_PATTERNS):
        return True
    return False
