#!/usr/bin/env bash
# 编译所有 C 外部引擎到 ext/dist/bin/qxt_<name>[.exe]
# 用法: bash scripts/build-c.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 转成 Windows 原生路径, 避免 MSYS 对含空格路径的转换歧义
if command -v cygpath >/dev/null 2>&1; then
  ROOT="$(cygpath -w "$ROOT" | sed 's/\\/\//g')"
fi
SRC="$ROOT/ext/c"
OUT="$ROOT/ext/dist/bin"
COMMON="$SRC/common"
IPC="$COMMON/ipc.c"
mkdir -p "$OUT"

# 优先用仓库约定 / 常见 MinGW 路径, 再回退到 PATH
GCC=""
for cand in /d/ucrt64/bin/gcc /mingw64/bin/gcc /usr/bin/gcc gcc; do
  if command -v "$cand" >/dev/null 2>&1; then GCC="$cand"; break; fi
done
if [ -z "$GCC" ]; then
  echo "错误: 未找到 gcc, 请先安装 MinGW-w64 或将其加入 PATH" >&2
  exit 1
fi
echo "使用编译器: $GCC ($($GCC --version | head -1))"

# 目标平台后缀 (Windows 为 .exe, 其他为空)
EXE=""
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*|*_NT*) EXE=".exe" ;;
esac

# 需要 bcrypt 的引擎 (Windows 加密相关)
needs_bcrypt() { case "$1" in crypto|sandbox) return 0 ;; *) return 1 ;; esac; }

built=0
for dir in "$SRC"/*/; do
  name="$(basename "$dir")"
  [ "$name" = "common" ] && continue
  src="$dir${name}.c"
  [ -f "$src" ] || { echo "跳过 $name (无 ${name}.c)"; continue; }
  out="$OUT/qxt_${name}${EXE}"
  flags=(-O2 -std=c11 -I"$COMMON")
  # Windows 静态链接, 消除运行时 DLL 依赖
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*|*_NT*) flags+=(-static) ;;
  esac
  echo "编译 $name -> $out"
  "$GCC" "${flags[@]}" "$src" "$IPC" -o "$out" -lpthread $(needs_bcrypt "$name" && echo -lbcrypt)
  built=$((built+1))
done

echo "完成: 编译 $built 个 C 引擎到 $OUT"
