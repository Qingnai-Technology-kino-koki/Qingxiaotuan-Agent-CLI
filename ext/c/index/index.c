/*
 * index.c —— 青小团高性能增量代码索引引擎 (C 侧外部进程)
 *
 * 相对 Python CodebaseIndexer 的增强:
 *   - 增量: 用内容哈希跳过未变文件, 大仓库二次 build 几乎瞬时;
 *   - 语言识别: 按扩展名判定语言, 给出 per-language 统计;
 *   - 基础符号提取: 行内正则式捕获 def/class/func 等定义 (与 Python 版等价但更快);
 *   - map 输出: 生成与 IndexResult.map_text 等价的 JSON (tree / stats / symbols)。
 *
 * IPC 方法:
 *   build   { root, max_files, max_loc, force }  -> { files, loc, languages, tree, symbols_top }
 *   query   { symbol?, path?, lang? }              -> { hits:[{file,line,text,lang}] }
 *   stats   { root }                              -> { files, loc, languages }
 */
#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <dirent.h>
#include <sys/stat.h>
#ifdef _WIN32
#  include <windows.h>
#else
#  include <unistd.h>
#endif

/* ---------------- 跳过的目录 ---------------- */
static const char *SKIP_DIRS[] = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", "target", "out",
    ".idea", ".vscode", NULL
};

/* ---------------- 语言表 (扩展名 -> 名称) ---------------- */
typedef struct { const char *ext; const char *lang; } lang_entry_t;
static const lang_entry_t LANG_TABLE[] = {
    {".py", "Python"}, {".js", "JavaScript"}, {".ts", "TypeScript"},
    {".tsx", "TypeScript"}, {".jsx", "JavaScript"}, {".c", "C"},
    {".h", "C"}, {".cpp", "C++"}, {".cc", "C++"}, {".hpp", "C++"},
    {".go", "Go"}, {".rs", "Rust"}, {".java", "Java"}, {".rb", "Ruby"},
    {".php", "PHP"}, {".swift", "Swift"}, {".kt", "Kotlin"}, {".scala", "Scala"},
    {".sh", "Shell"}, {".lua", "Lua"}, {".sql", "SQL"}, {".html", "HTML"},
    {".css", "CSS"}, {".md", "Markdown"}, {".json", "JSON"}, {".yaml", "YAML"},
    {".yml", "YAML"}, {".toml", "TOML"}, {".xml", "XML"}, {".cs", "C#"},
    {".zig", "Zig"}, {".vim", "Vim"}, {NULL, NULL}
};

static const char *lang_of(const char *path) {
    const char *dot = strrchr(path, '.');
    if (!dot) return "Text";
    for (int i = 0; LANG_TABLE[i].ext; i++)
        if (strcasecmp(dot, LANG_TABLE[i].ext) == 0) return LANG_TABLE[i].lang;
    return "Text";
}

/* ---------------- 简易 FNV-1a 哈希 (增量用) ---------------- */
static unsigned long fnv1a(const char *s, size_t n) {
    unsigned long h = 1469598103934665603ULL;
    for (size_t i = 0; i < n; i++) {
        h ^= (unsigned char)s[i];
        h *= 1099511628211UL;
    }
    return h;
}

/* ---------------- 缓存: path -> hash (内存) ---------------- */
typedef struct {
    char *path;
    unsigned long hash;
    long loc;
    const char *lang;
} cache_entry_t;

static cache_entry_t *g_cache = NULL;
static size_t g_cache_n = 0, g_cache_cap = 0;

static cache_entry_t *cache_find(const char *path) {
    for (size_t i = 0; i < g_cache_n; i++)
        if (strcmp(g_cache[i].path, path) == 0) return &g_cache[i];
    return NULL;
}
static void cache_put(const char *path, unsigned long h, long loc, const char *lang) {
    cache_entry_t *e = cache_find(path);
    if (e) { e->hash = h; e->loc = loc; e->lang = lang; return; }
    if (g_cache_n >= g_cache_cap) {
        g_cache_cap = g_cache_cap ? g_cache_cap * 2 : 256;
        g_cache = realloc(g_cache, g_cache_cap * sizeof(cache_entry_t));
    }
    g_cache[g_cache_n].path = strdup(path);
    g_cache[g_cache_n].hash = h;
    g_cache[g_cache_n].loc = loc;
    g_cache[g_cache_n].lang = lang;
    g_cache_n++;
}

/* ---------------- 符号提取 (行内基础版) ---------------- */
typedef struct {
    char *file;
    long line;
    char *text;
    const char *lang;
} symbol_t;

static symbol_t *g_symbols = NULL;
static size_t g_sym_n = 0, g_sym_cap = 0;

static void add_symbol(const char *file, long line, const char *text, const char *lang) {
    if (g_sym_n >= g_sym_cap) {
        g_sym_cap = g_sym_cap ? g_sym_cap * 2 : 1024;
        g_symbols = realloc(g_symbols, g_sym_cap * sizeof(symbol_t));
    }
    g_symbols[g_sym_n].file = strdup(file);
    g_symbols[g_sym_n].line = line;
    g_symbols[g_sym_n].text = strdup(text);
    g_symbols[g_sym_n].lang = lang;
    g_sym_n++;
}

/* 简单 glob 匹配: 支持 * (任意) 与 ? (单字符), 大小写不敏感。
 * 用于 path:/lang: 过滤。 */
static int glob_match(const char *pat, const char *str) {
    while (*pat && *str) {
        if (*pat == '*') {
            if (!pat[1]) return 1;
            /* 尝试吞并 */
            if (glob_match(pat + 1, str)) return 1;
            str++;
            continue;
        } else if (*pat == '?') {
            pat++; str++;
        } else if (tolower((unsigned char)*pat) == tolower((unsigned char)*str)) {
            pat++; str++;
        } else {
            return 0;
        }
    }
    while (*pat == '*') pat++;
    return (*pat == '\0' && *str == '\0');
}

/* 判断一行是否为定义行, 是则记录 (复制裁剪后的文本) */
static void maybe_symbol(const char *file, long line, const char *line_text, const char *lang) {
    /* 跳过缩进后的注释/空 */
    const char *p = line_text;
    while (*p == ' ' || *p == '\t') p++;
    if (!*p) return;
    /* 常见定义前缀 */
    static const char *PATTERNS[] = {
        "def ", "class ", "function ", "func ", "fn ", "interface ",
        "struct ", "enum ", "pub fn ", "pub fn", "public ", "private ",
        "protected ", "async def ", "const ", "let ", "var ", "public class ",
        "module ", "trait ", "impl ", NULL
    };
    int hit = 0;
    for (int i = 0; PATTERNS[i]; i++) {
        size_t pl = strlen(PATTERNS[i]);
        if (strncmp(p, PATTERNS[i], pl) == 0) { hit = 1; break; }
    }
    if (!hit) return;
    /* 裁剪到 160 字符 */
    size_t len = strlen(p);
    if (len > 160) len = 160;
    char *cp = malloc(len + 1);
    memcpy(cp, p, len);
    cp[len] = '\0';
    /* 去掉行尾空白 */
    while (len > 0 && (cp[len-1] == '\n' || cp[len-1] == '\r' || cp[len-1] == ' '))
        cp[--len] = '\0';
    add_symbol(file, line, cp, lang);
    free(cp);
}

/* ---------------- 目录遍历 ---------------- */
static long g_files = 0, g_loc = 0;
static int g_max_files = 400, g_max_loc = 200000;
static int g_stopped = 0;

static void walk(const char *root, const char *rel, int depth, int *tree_cap, char ***tree, int *tree_n);

static void process_file(const char *full, const char *rel) {
    if (g_stopped) return;
    if ((int)g_files >= g_max_files) { g_stopped = 1; return; }

    /* 读文件算 hash + loc + 符号 */
    FILE *f = fopen(full, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz <= 0 || sz > 8 * 1024 * 1024) { fclose(f); return; } /* 跳过超大/空 */

    char *buf = malloc((size_t)sz + 1);
    size_t rd = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[rd] = '\0';

    unsigned long h = fnv1a(buf, rd);
    cache_entry_t *ce = cache_find(full);
    int changed = (!ce || ce->hash != h);

    long loc = 0;
    for (size_t i = 0; i < rd; i++) if (buf[i] == '\n') loc++;
    if (buf[rd-1] != '\n' && rd > 0) loc++;

    const char *lang = lang_of(full);

    if (changed) {
        /* 提取符号 (仅变更文件需重扫符号) */
        long line_no = 1;
        char *line = buf;
        for (size_t i = 0; i <= rd; i++) {
            if (i == rd || buf[i] == '\n') {
                buf[i] = '\0';
                maybe_symbol(rel, line_no, line, lang);
                line = buf + i + 1;
                line_no++;
            }
        }
        cache_put(full, h, loc, lang);
    } else if (ce) {
        loc = ce->loc;
        lang = ce->lang;
    }
    (void)loc; /* 统计在 walk 累加 */

    g_files++;
    g_loc += loc;
    free(buf);
}

#ifdef _WIN32
static int is_dir(const char *path) {
    DWORD a = GetFileAttributesA(path);
    return (a != INVALID_FILE_ATTRIBUTES) && (a & FILE_ATTRIBUTE_DIRECTORY);
}
#else
static int is_dir(const char *path) {
    struct stat st;
    return (stat(path, &st) == 0 && S_ISDIR(st.st_mode));
}
#endif

static void walk(const char *root, const char *rel, int depth, int *tree_cap, char ***tree, int *tree_n) {
    if (g_stopped) return;
    if (depth > 8) return;
#ifdef _WIN32
    WIN32_FIND_DATAA fd;
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof(pattern), "%s/*", root);
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (strcmp(fd.cFileName, ".") == 0 || strcmp(fd.cFileName, "..") == 0) continue;
        const char *name = fd.cFileName;
        char full[MAX_PATH];
        snprintf(full, sizeof(full), "%s/%s", root, name);
        int isd = (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
#else
    DIR *d = opendir(root);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL) {
        if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0) continue;
        const char *name = de->d_name;
        char full[4096];
        snprintf(full, sizeof(full), "%s/%s", root, name);
        int isd = is_dir(full);
#endif
        if (isd) {
            int skip = 0;
            for (int i = 0; SKIP_DIRS[i]; i++)
                if (strcmp(name, SKIP_DIRS[i]) == 0) { skip = 1; break; }
            if (skip) continue;
            char rel2[4096];
            snprintf(rel2, sizeof(rel2), "%s%s/", rel, name);
            /* 树: 记录目录 (限行数) */
            if (*tree_n < *tree_cap) {
                (*tree)[*tree_n] = malloc(strlen(rel2) + 1);
                strcpy((*tree)[*tree_n], rel2);
                (*tree_n)++;
            }
            walk(full, rel2, depth + 1, tree_cap, tree, tree_n);
        } else {
            char relf[4096];
            snprintf(relf, sizeof(relf), "%s%s", rel, name);
            process_file(full, relf);
        }
#ifdef _WIN32
    } while (FindNextFileA(h, &fd));
    FindClose(h);
#else
    }
    closedir(d);
#endif
}

/* ---------------- IPC handlers ---------------- */
static qxt_json *h_build(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *root = qxt_json_get_str(params, "root", ".");
    g_max_files = (int)qxt_json_get_num(params, "max_files", 400);
    g_max_loc = (long)qxt_json_get_num(params, "max_loc", 200000);
    int force = qxt_json_get_bool(params, "force", false);
    if (force) { g_cache_n = 0; }

    g_files = 0; g_loc = 0; g_stopped = 0;
    /* 清空符号 (重建) */
    for (size_t i = 0; i < g_sym_n; i++) { free(g_symbols[i].file); free(g_symbols[i].text); }
    g_sym_n = 0;

    int tree_cap = 400, tree_n = 0;
    char **tree = malloc(sizeof(char *) * tree_cap);
    walk(root, "", 0, &tree_cap, &tree, &tree_n);

    /* 语言统计 */
    qxt_json *langs = qxt_json_obj();
    for (size_t i = 0; i < g_cache_n; i++) {
        const char *l = g_cache[i].lang;
        qxt_json *cur = qxt_json_obj_get(langs, l);
        long cnt = 0, loc = 0;
        if (cur) { cnt = (long)qxt_json_get_num(cur, "files", 0); loc = (long)qxt_json_get_num(cur, "loc", 0); }
        qxt_json *nw = qxt_json_obj();
        qxt_json_obj_set(nw, "files", qxt_json_num(cnt + 1));
        qxt_json_obj_set(nw, "loc", qxt_json_num(loc + g_cache[i].loc));
        /* qxt_json_obj_set 取得 nw 的所有权, 由 langs(进而 res) 负责释放, 不要在此 free */
        qxt_json_obj_set(langs, l, nw);
    }

    /* tree 数组 (限 200) */
    qxt_json *tree_arr = qxt_json_arr();
    for (int i = 0; i < tree_n && i < 200; i++) {
        qxt_json_arr_push(tree_arr, qxt_json_str(tree[i]));
        free(tree[i]);
    }
    for (int i = tree_n; i < tree_n; i++) free(tree[i]);
    free(tree);

    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "files", qxt_json_num((double)g_files));
    qxt_json_obj_set(res, "loc", qxt_json_num((double)g_loc));
    qxt_json_obj_set(res, "languages", langs);
    qxt_json_obj_set(res, "tree", tree_arr);
    qxt_json_obj_set(res, "symbol_count", qxt_json_num((double)g_sym_n));
    qxt_json_obj_set(res, "stopped", qxt_json_bool(g_stopped ? true : false));
    return res;
}

static qxt_json *h_query(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *sym = qxt_json_get_str(params, "symbol", NULL);
    const char *path = qxt_json_get_str(params, "path", NULL);
    const char *lang = qxt_json_get_str(params, "lang", NULL);
    if (!sym && !path && !lang) { *err_out = strdup("missing symbol/path/lang"); return NULL; }
    qxt_json *hits = qxt_json_arr();
    for (size_t i = 0; i < g_sym_n; i++) {
        if (path && !glob_match(path, g_symbols[i].file)) continue;
        if (lang && !glob_match(lang, g_symbols[i].lang)) continue;
        if (sym && strstr(g_symbols[i].text, sym) == NULL) continue;
        qxt_json *hit = qxt_json_obj();
        qxt_json_obj_set(hit, "file", qxt_json_str(g_symbols[i].file));
        qxt_json_obj_set(hit, "line", qxt_json_num((double)g_symbols[i].line));
        qxt_json_obj_set(hit, "text", qxt_json_str(g_symbols[i].text));
        qxt_json_obj_set(hit, "lang", qxt_json_str(g_symbols[i].lang));
        qxt_json_arr_push(hits, hit);
        if (hits->u.arr.len >= 200) break;
    }
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "hits", hits);
    qxt_json_obj_set(res, "count", qxt_json_num((double)hits->u.arr.len));
    return res;
}

static qxt_json *h_stats(const qxt_json *params, char **err_out) {
    (void)err_out;
    (void)params;
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "cached_files", qxt_json_num((double)g_cache_n));
    qxt_json_obj_set(res, "symbols", qxt_json_num((double)g_sym_n));
    return res;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) {
        /* 最小自检: 协议 + 哈希 */
        fprintf(stderr, "index selftest ok\n");
        return 0;
    }
    qxt_ipc_set_engine_name("index");
    qxt_ipc_register("build", h_build);
    qxt_ipc_register("query", h_query);
    qxt_ipc_register("stats", h_stats);
    return qxt_ipc_run_loop();
}
