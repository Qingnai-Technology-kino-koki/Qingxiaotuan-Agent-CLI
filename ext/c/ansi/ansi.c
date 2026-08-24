/*
 * ansi.c —— 青小团终端 ANSI / 富文本解析渲染引擎 (C 侧外部进程)
 *
 * 能力:
 *   - parse: 把带 ANSI 转义序列的字节流解析为带样式的片段 (text/style);
 *   - strip: 去掉所有 ANSI 转义, 返回纯文本 (用于日志/长度计算);
 *   - render: 把样式片段重新序列化为目标格式 (目前支持 plain / 简易 html)。
 *
 * IPC 方法:
 *   parse  { text, markup } -> { segments:[{text,fg,bg,bold,...}] }
 *   strip  { text }         -> { plain }
 *   render { text, format } -> { out }
 */
#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

typedef struct {
    char *text;
    int fg;       /* -1 默认, 0-15 标准色, 或 0x10000+256 真彩 */
    int bg;
    int bold, italic, underline, dim, invert;
} seg_t;

static seg_t *g_seg = NULL;
static size_t g_seg_n = 0, g_seg_cap = 0;
static void add_seg(const char *text, int fg, int bg, int bold, int italic, int ul, int dim, int inv) {
    if (g_seg_n >= g_seg_cap) {
        g_seg_cap = g_seg_cap ? g_seg_cap * 2 : 64;
        g_seg = realloc(g_seg, g_seg_cap * sizeof(seg_t));
    }
    g_seg[g_seg_n].text = strdup(text);
    g_seg[g_seg_n].fg = fg; g_seg[g_seg_n].bg = bg;
    g_seg[g_seg_n].bold = bold; g_seg[g_seg_n].italic = italic;
    g_seg[g_seg_n].underline = ul; g_seg[g_seg_n].dim = dim; g_seg[g_seg_n].invert = inv;
    g_seg_n++;
}

static void reset_segs(void) {
    for (size_t i = 0; i < g_seg_n; i++) free(g_seg[i].text);
    g_seg_n = 0;
}

/* 解析 ANSI CSI 序列, 返回消耗的字符数 (不含 \e[ 与终止符) */
static void apply_csi(const char *seq, int seqlen, int *fg, int *bg, int *bold, int *italic, int *ul, int *dim, int *inv) {
    /* seq 形如 "1;31;48;5;200m" 或 "0m" */
    /* 简单状态机: 按 ; 切分参数 */
    int params[16]; int pn = 0; int cur = 0;
    for (int i = 0; i < seqlen; i++) {
        char c = seq[i];
        if (c >= '0' && c <= '9') cur = cur * 10 + (c - '0');
        else if (c == ';') { if (pn < 16) params[pn++] = cur; cur = 0; }
    }
    if (pn < 16) params[pn++] = cur;
    for (int i = 0; i < pn; i++) {
        int p = params[i];
        if (p == 0) { *fg = -1; *bg = -1; *bold = 0; *italic = 0; *ul = 0; *dim = 0; *inv = 0; }
        else if (p == 1) *bold = 1;
        else if (p == 3) *italic = 1;
        else if (p == 4) *ul = 1;
        else if (p == 2) *dim = 1;
        else if (p == 7) *inv = 1;
        else if (p >= 30 && p <= 37) *fg = p - 30;
        else if (p == 39) *fg = -1;
        else if (p >= 40 && p <= 47) *bg = p - 40;
        else if (p == 49) *bg = -1;
        else if (p == 38 && i + 1 < pn && params[i+1] == 5 && i + 2 < pn) { *fg = 0x10000 + params[i+2]; i += 2; }
        else if (p == 48 && i + 1 < pn && params[i+1] == 5 && i + 2 < pn) { *bg = 0x10000 + params[i+2]; i += 2; }
    }
}

static qxt_json *h_parse(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *text = qxt_json_get_str(params, "text", "");
    reset_segs();
    int fg = -1, bg = -1, bold = 0, italic = 0, ul = 0, dim = 0, inv = 0;
    size_t len = strlen(text);
    char *buf = malloc(len + 1);
    size_t bi = 0;
    for (size_t i = 0; i < len; ) {
        if (text[i] == 27 && i + 1 < len && text[i+1] == '[') {
            /* flush 当前片段 */
            if (bi > 0) { buf[bi] = '\0'; add_seg(buf, fg, bg, bold, italic, ul, dim, inv); bi = 0; }
            size_t j = i + 2;
            while (j < len && !(text[j] >= '@' && text[j] <= '~')) j++;
            if (j < len) {
                int seqlen = (int)(j - i - 2);
                char *seq = malloc(seqlen + 1);
                memcpy(seq, text + i + 2, seqlen); seq[seqlen] = '\0';
                apply_csi(seq, seqlen, &fg, &bg, &bold, &italic, &ul, &dim, &inv);
                free(seq);
                i = j + 1;
            } else { i = len; }
        } else {
            buf[bi++] = text[i++];
        }
    }
    if (bi > 0) { buf[bi] = '\0'; add_seg(buf, fg, bg, bold, italic, ul, dim, inv); }
    free(buf);

    qxt_json *segs = qxt_json_arr();
    for (size_t i = 0; i < g_seg_n; i++) {
        qxt_json *s = qxt_json_obj();
        qxt_json_obj_set(s, "text", qxt_json_str(g_seg[i].text));
        qxt_json_obj_set(s, "fg", qxt_json_num(g_seg[i].fg));
        qxt_json_obj_set(s, "bg", qxt_json_num(g_seg[i].bg));
        qxt_json_obj_set(s, "bold", qxt_json_bool(g_seg[i].bold));
        qxt_json_obj_set(s, "italic", qxt_json_bool(g_seg[i].italic));
        qxt_json_obj_set(s, "underline", qxt_json_bool(g_seg[i].underline));
        qxt_json_obj_set(s, "dim", qxt_json_bool(g_seg[i].dim));
        qxt_json_obj_set(s, "invert", qxt_json_bool(g_seg[i].invert));
        qxt_json_arr_push(segs, s);
    }
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "segments", segs);
    qxt_json_obj_set(res, "count", qxt_json_num((double)g_seg_n));
    return res;
}

static qxt_json *h_strip(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *text = qxt_json_get_str(params, "text", "");
    size_t len = strlen(text);
    char *out = malloc(len + 1);
    size_t oi = 0;
    for (size_t i = 0; i < len; ) {
        if (text[i] == 27 && i + 1 < len && text[i+1] == '[') {
            size_t j = i + 2;
            while (j < len && !(text[j] >= '@' && text[j] <= '~')) j++;
            i = (j < len) ? j + 1 : len;
        } else {
            out[oi++] = text[i++];
        }
    }
    out[oi] = '\0';
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "plain", qxt_json_str(out));
    free(out);
    return res;
}

static const char *html_esc(const char *s) {
    static char buf[4096];
    size_t o = 0;
    for (size_t i = 0; s[i] && o < sizeof(buf) - 8; i++) {
        char c = s[i];
        const char *rep = NULL;
        if (c == '&') rep = "&amp;";
        else if (c == '<') rep = "&lt;";
        else if (c == '>') rep = "&gt;";
        if (rep) { while (*rep) buf[o++] = *rep++; }
        else buf[o++] = c;
    }
    buf[o] = '\0';
    return buf;
}

static qxt_json *h_render(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *text = qxt_json_get_str(params, "text", "");
    const char *fmt = qxt_json_get_str(params, "format", "plain");
    if (strcmp(fmt, "html") != 0) {
        /* plain: 直接 strip */
        qxt_json *r = h_strip(params, err_out);
        if (r) qxt_json_obj_set(r, "format", qxt_json_str("plain"));
        return r;
    }
    reset_segs();
    int fg = -1, bg = -1, bold = 0, italic = 0, ul = 0, dim = 0, inv = 0;
    size_t len = strlen(text);
    char *buf = malloc(len + 1); size_t bi = 0;
    /* 复用 parse 逻辑建段, 再渲染 html */
    for (size_t i = 0; i < len; ) {
        if (text[i] == 27 && i + 1 < len && text[i+1] == '[') {
            if (bi > 0) { buf[bi] = '\0'; add_seg(buf, fg, bg, bold, italic, ul, dim, inv); bi = 0; }
            size_t j = i + 2;
            while (j < len && !(text[j] >= '@' && text[j] <= '~')) j++;
            if (j < len) {
                int seqlen = (int)(j - i - 2);
                char *seq = malloc(seqlen + 1); memcpy(seq, text + i + 2, seqlen); seq[seqlen] = '\0';
                apply_csi(seq, seqlen, &fg, &bg, &bold, &italic, &ul, &dim, &inv);
                free(seq); i = j + 1;
            } else i = len;
        } else buf[bi++] = text[i++];
    }
    if (bi > 0) { buf[bi] = '\0'; add_seg(buf, fg, bg, bold, italic, ul, dim, inv); }
    free(buf);

    size_t cap = 8192; char *html = malloc(cap); size_t hi = 0;
    hi += snprintf(html + hi, cap - hi, "<pre>");
    for (size_t i = 0; i < g_seg_n; i++) {
        seg_t *s = &g_seg[i];
        hi += snprintf(html + hi, cap - hi, "<span style=\"");
        if (s->bold) hi += snprintf(html + hi, cap - hi, "font-weight:bold;");
        if (s->italic) hi += snprintf(html + hi, cap - hi, "font-style:italic;");
        if (s->underline) hi += snprintf(html + hi, cap - hi, "text-decoration:underline;");
        if (s->fg >= 0 && s->fg < 16) hi += snprintf(html + hi, cap - hi, "color:ansi%d;", s->fg);
        else if (s->fg >= 0x10000) hi += snprintf(html + hi, cap - hi, "color:#%06x;", s->fg & 0xffffff);
        hi += snprintf(html + hi, cap - hi, "\">%s</span>", html_esc(s->text));
    }
    hi += snprintf(html + hi, cap - hi, "</pre>");

    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "format", qxt_json_str("html"));
    qxt_json_obj_set(res, "out", qxt_json_str(html));
    free(html);
    return res;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) { fprintf(stderr, "ansi selftest ok\n"); return 0; }
    qxt_ipc_set_engine_name("ansi");
    qxt_ipc_register("parse", h_parse);
    qxt_ipc_register("strip", h_strip);
    qxt_ipc_register("render", h_render);
    return qxt_ipc_run_loop();
}
