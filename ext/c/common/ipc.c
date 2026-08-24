/*
 * ipc.c —— JSON 解析/序列化 + JSONL IPC 请求-响应循环 (零依赖)
 * 见 ipc.h 的协议说明。
 */
#include "ipc.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>

/* ============================================================ 内存小工具 */
static void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) { fprintf(stderr, "qxt_ipc: OOM\n"); exit(3); }
    return p;
}
static void *xcalloc(size_t n, size_t s) {
    void *p = calloc(n ? n : 1, s ? s : 1);
    if (!p) { fprintf(stderr, "qxt_ipc: OOM\n"); exit(3); }
    return p;
}
static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n ? n : 1);
    if (!q) { fprintf(stderr, "qxt_ipc: OOM\n"); exit(3); }
    return q;
}
static char *xstrdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *p = xmalloc(n);
    memcpy(p, s, n);
    return p;
}

/* ============================================================ JSON 构造 */
qxt_json *qxt_json_null(void) {
    qxt_json *v = xcalloc(1, sizeof(qxt_json));
    v->type = QXT_NULL;
    return v;
}
qxt_json *qxt_json_bool(bool b) {
    qxt_json *v = xcalloc(1, sizeof(qxt_json));
    v->type = QXT_BOOL; v->u.b = b;
    return v;
}
qxt_json *qxt_json_num(double d) {
    qxt_json *v = xcalloc(1, sizeof(qxt_json));
    v->type = QXT_NUM; v->u.num = d;
    return v;
}
qxt_json *qxt_json_str(const char *s) {
    qxt_json *v = xcalloc(1, sizeof(qxt_json));
    v->type = QXT_STR; v->u.str = xstrdup(s ? s : "");
    return v;
}
qxt_json *qxt_json_arr(void) {
    qxt_json *v = xcalloc(1, sizeof(qxt_json));
    v->type = QXT_ARR;
    return v;
}
qxt_json *qxt_json_obj(void) {
    qxt_json *v = xcalloc(1, sizeof(qxt_json));
    v->type = QXT_OBJ;
    return v;
}

void qxt_json_arr_push(qxt_json *arr, qxt_json *item) {
    if (arr->u.arr.len >= arr->u.arr.cap) {
        size_t nc = arr->u.arr.cap ? arr->u.arr.cap * 2 : 8;
        arr->u.arr.items = xrealloc(arr->u.arr.items, nc * sizeof(qxt_json *));
        arr->u.arr.cap = nc;
    }
    arr->u.arr.items[arr->u.arr.len++] = item;
}
void qxt_json_obj_set(qxt_json *obj, const char *key, qxt_json *val) {
    for (size_t i = 0; i < obj->u.obj.len; i++) {
        if (strcmp(obj->u.obj.keys[i], key) == 0) {
            qxt_json_free(obj->u.obj.vals[i]);
            obj->u.obj.vals[i] = val;
            return;
        }
    }
    if (obj->u.obj.len >= obj->u.obj.cap) {
        size_t nc = obj->u.obj.cap ? obj->u.obj.cap * 2 : 8;
        obj->u.obj.keys = xrealloc(obj->u.obj.keys, nc * sizeof(char *));
        obj->u.obj.vals = xrealloc(obj->u.obj.vals, nc * sizeof(qxt_json *));
        obj->u.obj.cap = nc;
    }
    obj->u.obj.keys[obj->u.obj.len] = xstrdup(key);
    obj->u.obj.vals[obj->u.obj.len] = val;
    obj->u.obj.len++;
}
qxt_json *qxt_json_obj_get(const qxt_json *obj, const char *key) {
    if (!obj || obj->type != QXT_OBJ) return NULL;
    for (size_t i = 0; i < obj->u.obj.len; i++)
        if (strcmp(obj->u.obj.keys[i], key) == 0) return obj->u.obj.vals[i];
    return NULL;
}
const char *qxt_json_get_str(const qxt_json *obj, const char *key, const char *def) {
    qxt_json *v = qxt_json_obj_get(obj, key);
    if (v && v->type == QXT_STR) return v->u.str;
    return def;
}
double qxt_json_get_num(const qxt_json *obj, const char *key, double def) {
    qxt_json *v = qxt_json_obj_get(obj, key);
    if (v && v->type == QXT_NUM) return v->u.num;
    return def;
}
bool qxt_json_get_bool(const qxt_json *obj, const char *key, bool def) {
    qxt_json *v = qxt_json_obj_get(obj, key);
    if (v && v->type == QXT_BOOL) return v->u.b;
    return def;
}

void qxt_json_free(qxt_json *v) {
    if (!v) return;
    switch (v->type) {
        case QXT_STR:
            free(v->u.str);
            break;
        case QXT_ARR:
            for (size_t i = 0; i < v->u.arr.len; i++) qxt_json_free(v->u.arr.items[i]);
            free(v->u.arr.items);
            break;
        case QXT_OBJ:
            for (size_t i = 0; i < v->u.obj.len; i++) {
                free(v->u.obj.keys[i]);
                qxt_json_free(v->u.obj.vals[i]);
            }
            free(v->u.obj.keys);
            free(v->u.obj.vals);
            break;
        default: break;
    }
    free(v);
}

/* ============================================================ 解析器 */
typedef struct {
    const char *p;
    const char *end;
    char err[128];
} parser_t;

static qxt_json *parse_value(parser_t *ps);

static void ps_err(parser_t *ps, const char *msg) {
    strncpy(ps->err, msg, sizeof(ps->err) - 1);
    ps->err[sizeof(ps->err) - 1] = '\0';
}

static void skip_ws(parser_t *ps) {
    while (ps->p < ps->end) {
        char c = *ps->p;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ps->p++;
        else break;
    }
}

static qxt_json *parse_string(parser_t *ps) {
    /* 假定当前 *ps->p == '"' */
    ps->p++; /* skip " */
    size_t cap = 32, len = 0;
    char *buf = xmalloc(cap);
    while (ps->p < ps->end) {
        char c = *ps->p++;
        if (c == '"') {
            buf[len] = '\0';
            qxt_json *v = qxt_json_str(buf);
            free(buf);
            return v;
        }
        if (c == '\\') {
            if (ps->p >= ps->end) { free(buf); ps_err(ps, "bad escape"); return NULL; }
            char e = *ps->p++;
            char out;
            switch (e) {
                case 'n': out = '\n'; break;
                case 't': out = '\t'; break;
                case 'r': out = '\r'; break;
                case 'b': out = '\b'; break;
                case 'f': out = '\f'; break;
                case '"': out = '"'; break;
                case '\\': out = '\\'; break;
                case '/': out = '/'; break;
                case 'u': {
                    if (ps->end - ps->p < 4) { free(buf); ps_err(ps, "bad \\u"); return NULL; }
                    int code = 0;
                    for (int i = 0; i < 4; i++) {
                        char h = *ps->p++;
                        code <<= 4;
                        if (h >= '0' && h <= '9') code |= h - '0';
                        else if (h >= 'a' && h <= 'f') code |= h - 'a' + 10;
                        else if (h >= 'A' && h <= 'F') code |= h - 'A' + 10;
                        else { free(buf); ps_err(ps, "bad \\u hex"); return NULL; }
                    }
                    /* 仅处理 BMP (够用); 代理对被忽略为 '?' */
                    if (len + 1 >= cap) { cap *= 2; buf = xrealloc(buf, cap); }
                    if (code < 0x80) {
                        buf[len++] = (char)code;
                    } else if (code < 0x800) {
                        buf[len++] = (char)(0xC0 | (code >> 6));
                        buf[len++] = (char)(0x80 | (code & 0x3F));
                    } else {
                        buf[len++] = (char)(0xE0 | (code >> 12));
                        buf[len++] = (char)(0x80 | ((code >> 6) & 0x3F));
                        buf[len++] = (char)(0x80 | (code & 0x3F));
                    }
                    continue;
                }
                default: free(buf); ps_err(ps, "bad escape char"); return NULL;
            }
            if (len + 1 >= cap) { cap *= 2; buf = xrealloc(buf, cap); }
            buf[len++] = out;
        } else {
            if (len + 1 >= cap) { cap *= 2; buf = xrealloc(buf, cap); }
            buf[len++] = c;
        }
    }
    free(buf);
    ps_err(ps, "unterminated string");
    return NULL;
}

static qxt_json *parse_number(parser_t *ps) {
    const char *start = ps->p;
    if (ps->p < ps->end && (*ps->p == '-' || *ps->p == '+')) ps->p++;
    while (ps->p < ps->end && (isdigit((unsigned char)*ps->p) || *ps->p == '.' ||
                               *ps->p == 'e' || *ps->p == 'E' || *ps->p == '+' || *ps->p == '-'))
        ps->p++;
    char tmp[64];
    size_t n = (size_t)(ps->p - start);
    if (n >= sizeof(tmp)) n = sizeof(tmp) - 1;
    memcpy(tmp, start, n);
    tmp[n] = '\0';
    double d = atof(tmp);
    return qxt_json_num(d);
}

static qxt_json *parse_literal(parser_t *ps, const char *lit, size_t llen, qxt_json *val) {
    if ((size_t)(ps->end - ps->p) < llen || strncmp(ps->p, lit, llen) != 0) {
        ps_err(ps, "bad literal");
        return NULL;
    }
    ps->p += llen;
    return val;
}

static qxt_json *parse_array(parser_t *ps) {
    ps->p++; /* skip [ */
    qxt_json *arr = qxt_json_arr();
    skip_ws(ps);
    if (ps->p < ps->end && *ps->p == ']') { ps->p++; return arr; }
    for (;;) {
        skip_ws(ps);
        qxt_json *item = parse_value(ps);
        if (!item) { qxt_json_free(arr); return NULL; }
        qxt_json_arr_push(arr, item);
        skip_ws(ps);
        if (ps->p >= ps->end) { qxt_json_free(arr); ps_err(ps, "unterminated array"); return NULL; }
        if (*ps->p == ',') { ps->p++; continue; }
        if (*ps->p == ']') { ps->p++; break; }
        qxt_json_free(arr); ps_err(ps, "expected , or ]"); return NULL;
    }
    return arr;
}

static qxt_json *parse_object(parser_t *ps) {
    ps->p++; /* skip { */
    qxt_json *obj = qxt_json_obj();
    skip_ws(ps);
    if (ps->p < ps->end && *ps->p == '}') { ps->p++; return obj; }
    for (;;) {
        skip_ws(ps);
        if (ps->p >= ps->end || *ps->p != '"') { qxt_json_free(obj); ps_err(ps, "expected key"); return NULL; }
        qxt_json *k = parse_string(ps);
        if (!k) { qxt_json_free(obj); return NULL; }
        skip_ws(ps);
        if (ps->p >= ps->end || *ps->p != ':') { qxt_json_free(obj); qxt_json_free(k); ps_err(ps, "expected :"); return NULL; }
        ps->p++;
        skip_ws(ps);
        qxt_json *v = parse_value(ps);
        if (!v) { qxt_json_free(obj); qxt_json_free(k); return NULL; }
        qxt_json_obj_set(obj, k->u.str, v);
        qxt_json_free(k);
        skip_ws(ps);
        if (ps->p >= ps->end) { qxt_json_free(obj); ps_err(ps, "unterminated object"); return NULL; }
        if (*ps->p == ',') { ps->p++; continue; }
        if (*ps->p == '}') { ps->p++; break; }
        qxt_json_free(obj); ps_err(ps, "expected , or }"); return NULL;
    }
    return obj;
}

static qxt_json *parse_value(parser_t *ps) {
    skip_ws(ps);
    if (ps->p >= ps->end) { ps_err(ps, "unexpected eof"); return NULL; }
    char c = *ps->p;
    switch (c) {
        case '"': return parse_string(ps);
        case '{': return parse_object(ps);
        case '[': return parse_array(ps);
        case 't': return parse_literal(ps, "true", 4, qxt_json_bool(true));
        case 'f': return parse_literal(ps, "false", 5, qxt_json_bool(false));
        case 'n': return parse_literal(ps, "null", 4, qxt_json_null());
        default:
            if (c == '-' || c == '+' || isdigit((unsigned char)c)) return parse_number(ps);
            ps_err(ps, "unexpected char");
            return NULL;
    }
}

qxt_json *qxt_json_parse(const char *text) {
    parser_t ps;
    ps.p = text;
    ps.end = text + strlen(text);
    ps.err[0] = '\0';
    qxt_json *v = parse_value(&ps);
    if (!v) return NULL;
    skip_ws(&ps);
    if (ps.p != ps.end) {
        /* 尾部有多余内容: 对单行协议只需首个值, 允许忽略 */
    }
    return v;
}

/* ============================================================ 序列化 */
typedef struct {
    char  *buf;
    size_t len;
    size_t cap;
    int    indent;   /* 当前缩进层级, <0 表示紧凑 */
} ser_t;

static void ser_init(ser_t *s, int pretty) {
    s->cap = 256; s->len = 0;
    s->buf = xmalloc(s->cap);
    s->buf[0] = '\0';
    s->indent = pretty ? 0 : -1;
}
static void ser_putc(ser_t *s, char c) {
    if (s->len + 1 >= s->cap) { s->cap *= 2; s->buf = xrealloc(s->buf, s->cap); }
    s->buf[s->len++] = c;
}
static void ser_puts(ser_t *s, const char *t) {
    while (*t) ser_putc(s, *t++);
}
static void ser_indent(ser_t *s) {
    if (s->indent < 0) return;
    for (int i = 0; i < s->indent; i++) ser_puts(s, "  ");
}

static void ser_escape(ser_t *s, const char *str) {
    ser_putc(s, '"');
    for (const char *p = str; *p; p++) {
        char c = *p;
        switch (c) {
            case '"': ser_puts(s, "\\\""); break;
            case '\\': ser_puts(s, "\\\\"); break;
            case '\n': ser_puts(s, "\\n"); break;
            case '\t': ser_puts(s, "\\t"); break;
            case '\r': ser_puts(s, "\\r"); break;
            case '\b': ser_puts(s, "\\b"); break;
            case '\f': ser_puts(s, "\\f"); break;
            default:
                if ((unsigned char)c < 0x20) {
                    char hex[8];
                    snprintf(hex, sizeof(hex), "\\u%04x", c);
                    ser_puts(s, hex);
                } else ser_putc(s, c);
        }
    }
    ser_putc(s, '"');
}

static void ser_value(ser_t *s, const qxt_json *v);

static void ser_value(ser_t *s, const qxt_json *v) {
    if (!v) { ser_puts(s, "null"); return; }
    switch (v->type) {
        case QXT_NULL: ser_puts(s, "null"); break;
        case QXT_BOOL: ser_puts(s, v->u.b ? "true" : "false"); break;
        case QXT_NUM: {
            double d = v->u.num;
            if (d == (double)(long long)d && fabs(d) < 1e15) {
                char t[32]; snprintf(t, sizeof(t), "%.0f", d); ser_puts(s, t);
            } else {
                char t[32]; snprintf(t, sizeof(t), "%.17g", d); ser_puts(s, t);
            }
            break;
        }
        case QXT_STR: ser_escape(s, v->u.str); break;
        case QXT_ARR: {
            ser_putc(s, '[');
            if (s->indent >= 0) { ser_putc(s, '\n'); s->indent++; }
            for (size_t i = 0; i < v->u.arr.len; i++) {
                if (s->indent >= 0) ser_indent(s);
                ser_value(s, v->u.arr.items[i]);
                if (i + 1 < v->u.arr.len) ser_putc(s, ',');
                if (s->indent >= 0) ser_putc(s, '\n');
            }
            if (s->indent >= 0) { s->indent--; ser_indent(s); }
            ser_putc(s, ']');
            break;
        }
        case QXT_OBJ: {
            ser_putc(s, '{');
            if (s->indent >= 0) { ser_putc(s, '\n'); s->indent++; }
            for (size_t i = 0; i < v->u.obj.len; i++) {
                if (s->indent >= 0) ser_indent(s);
                ser_escape(s, v->u.obj.keys[i]);
                ser_puts(s, s->indent >= 0 ? ": " : ":");
                ser_value(s, v->u.obj.vals[i]);
                if (i + 1 < v->u.obj.len) ser_putc(s, ',');
                if (s->indent >= 0) ser_putc(s, '\n');
            }
            if (s->indent >= 0) { s->indent--; ser_indent(s); }
            ser_putc(s, '}');
            break;
        }
    }
}

char *qxt_json_stringify(const qxt_json *v, int pretty) {
    ser_t s;
    ser_init(&s, pretty);
    ser_value(&s, v);
    ser_putc(&s, '\0');
    return s.buf;
}

/* ============================================================ IPC 循环 */
typedef struct {
    char            method[64];
    qxt_ipc_handler  handler;
} reg_t;

static reg_t  g_regs[64];
static size_t g_nreg = 0;
static char   g_engine_name[64] = "";   /* 由 qxt_ipc_set_engine_name 设置 */

void qxt_ipc_register(const char *method, qxt_ipc_handler handler) {
    if (g_nreg >= 64) { fprintf(stderr, "qxt_ipc: too many methods\n"); return; }
    strncpy(g_regs[g_nreg].method, method, sizeof(g_regs[0].method) - 1);
    g_regs[g_nreg].method[sizeof(g_regs[0].method) - 1] = '\0';
    g_regs[g_nreg].handler = handler;
    g_nreg++;
}

/* 设置引擎名, 出现在 ready 帧与 _meta 响应里 (便于 Python 侧统一自省)。 */
void qxt_ipc_set_engine_name(const char *name) {
    strncpy(g_engine_name, name ? name : "", sizeof(g_engine_name) - 1);
    g_engine_name[sizeof(g_engine_name) - 1] = '\0';
}

static qxt_ipc_handler find_handler(const char *method) {
    for (size_t i = 0; i < g_nreg; i++)
        if (strcmp(g_regs[i].method, method) == 0) return g_regs[i].handler;
    return NULL;
}

void qxt_ipc_emit(const qxt_json *v) {
    char *s = qxt_json_stringify(v, 0);
    fputs(s, stdout);
    fputc('\n', stdout);
    fflush(stdout);
    free(s);
}

/* 读一行 (动态扩容), 返回 NULL 表示 EOF */
static char *read_line(void) {
    size_t cap = 256, len = 0;
    char *buf = xmalloc(cap);
    int c;
    while ((c = getchar()) != EOF) {
        if (c == '\n') {
            if (len > 0 && buf[len - 1] == '\r') len--;
            buf[len] = '\0';
            return buf;
        }
        if (len + 1 >= cap) { cap *= 2; buf = xrealloc(buf, cap); }
        buf[len++] = (char)c;
    }
    if (len == 0) { free(buf); return NULL; }
    buf[len] = '\0';
    return buf;
}

static void send_response(long id, int ok, qxt_json *result, const char *err) {
    qxt_json *resp = qxt_json_obj();
    qxt_json_obj_set(resp, "id", qxt_json_num((double)id));
    qxt_json_obj_set(resp, "ok", qxt_json_bool(ok ? true : false));
    if (ok) {
        qxt_json_obj_set(resp, "result", result ? result : qxt_json_obj());
    } else {
        qxt_json_obj_set(resp, "error", qxt_json_str(err ? err : "unknown"));
        if (result) qxt_json_free(result);
    }
    qxt_ipc_emit(resp);
    qxt_json_free(resp);
}

int qxt_ipc_run_loop(void) {
    /* 启动就绪信号 */
    qxt_json *ready = qxt_json_obj();
    qxt_json_obj_set(ready, "ready", qxt_json_bool(true));
    if (g_engine_name[0])
        qxt_json_obj_set(ready, "engine", qxt_json_str(g_engine_name));
    qxt_ipc_emit(ready);
    qxt_json_free(ready);

    char *line;
    while ((line = read_line()) != NULL) {
        qxt_json *req = qxt_json_parse(line);
        free(line);
        if (!req || req->type != QXT_OBJ) {
            if (req) qxt_json_free(req);
            continue; /* 跳过非法行 */
        }
        long id = (long)qxt_json_get_num(req, "id", 0);
        const char *method = qxt_json_get_str(req, "method", NULL);
        qxt_json *params = qxt_json_obj_get(req, "params");

        if (method && strcmp(method, "_quit") == 0) {
            qxt_json_free(req);
            break;
        }
        if (!method) {
            send_response(id, 0, NULL, "missing method");
            qxt_json_free(req);
            continue;
        }
        /* 内置自省方法: 与 TS 引擎的 _meta / list / _ping 对齐 */
        if (strcmp(method, "_meta") == 0 || strcmp(method, "list") == 0) {
            qxt_json *res = qxt_json_obj();
            qxt_json_obj_set(res, "engine", qxt_json_str(g_engine_name[0] ? g_engine_name : "unknown"));
            qxt_json_obj_set(res, "version", qxt_json_str("c"));
            qxt_json *methods = qxt_json_arr();
            for (size_t i = 0; i < g_nreg; i++)
                qxt_json_arr_push(methods, qxt_json_str(g_regs[i].method));
            qxt_json_obj_set(res, "methods", methods);
            send_response(id, 1, res, NULL);
            qxt_json_free(req);
            continue;
        }
        if (strcmp(method, "_ping") == 0) {
            qxt_json *res = qxt_json_obj();
            qxt_json_obj_set(res, "pong", qxt_json_bool(true));
            send_response(id, 1, res, NULL);
            qxt_json_free(req);
            continue;
        }
        qxt_ipc_handler h = find_handler(method);
        if (!h) {
            send_response(id, 0, NULL, "unknown method");
            qxt_json_free(req);
            continue;
        }
        char *err = NULL;
        qxt_json *result = h(params, &err);
        if (err) {
            send_response(id, 0, result, err);
            free(err);
        } else {
            send_response(id, 1, result, NULL);
        }
        qxt_json_free(req);
    }
    return 0;
}
