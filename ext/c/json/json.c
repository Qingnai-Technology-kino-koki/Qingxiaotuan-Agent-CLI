/* json.c —— 青小团结构化 JSON 引擎 (C 侧)

理念 (对标世界级 Agent 的工程能力):
    后端/前端开发者大量与 JSON 打交道 (API 响应 / 配置 / 数据迁移)。
    一个世界级 Agent 必须能精确、可审计地操作 JSON, 而非靠字符串正则糊弄。

本引擎提供 (均 dry-run, 不修改输入):
    - pointer: 按 RFC 6901 JSON Pointer 从文档取值 (/a/b/0/c)
    - diff:    结构化比较两个 JSON 文档, 返回逐路径的增/删/改 (用于迁移评审/PR 审查)
    - merge:   深合并两个 JSON 文档 (overlay 覆盖 base), 用于配置合并

依赖公共 IPC 协议 (common/ipc.h), 与 diff/crypto/safety 等兄弟引擎完全同构。
*/
#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ----------------------------------------------------- JSON Pointer 求值
 * 按 "/" 分割, 每个 token:
 *   - "~1" -> "/",  "~0" -> "~"  (RFC 6901 转义)
 *   - 数组下标为十进制整数
 */
static char *unescape_token(const char *tok, size_t len) {
    char *out = malloc(len + 1);
    if (!out) return NULL;
    size_t j = 0;
    for (size_t i = 0; i < len; i++) {
        if (tok[i] == '~' && i + 1 < len) {
            if (tok[i + 1] == '1') { out[j++] = '/'; i++; continue; }
            if (tok[i + 1] == '0') { out[j++] = '~'; i++; continue; }
        }
        out[j++] = tok[i];
    }
    out[j] = '\0';
    return out;
}

static qxt_json *pointer_get(qxt_json *doc, const char *pointer) {
    if (!pointer || pointer[0] == '\0') return doc;        /* "" 指向根 */
    if (pointer[0] != '/') return NULL;
    qxt_json *cur = doc;
    const char *p = pointer + 1;
    while (*p) {
        const char *slash = strchr(p, '/');
        size_t tlen = slash ? (size_t)(slash - p) : strlen(p);
        char *tok = unescape_token(p, tlen);
        if (!tok) return NULL;
        if (cur->type == QXT_OBJ) {
            qxt_json *v = qxt_json_obj_get(cur, tok);
            free(tok);
            if (!v) return NULL;
            cur = v;
        } else if (cur->type == QXT_ARR) {
            char *end = NULL;
            long idx = strtol(tok, &end, 10);
            free(tok);
            if (end == NULL || *end != '\0' || idx < 0 || (size_t)idx >= cur->u.arr.len) return NULL;
            cur = cur->u.arr.items[idx];
        } else {
            free(tok);
            return NULL;
        }
        if (!slash) break;
        p = slash + 1;
    }
    return cur;
}

/* ----------------------------------------------------- 结构化 diff
 * 递归比较两个 JSON, 产出变更项 [{op, path, from?, value?}]
 */
static void diff_walk(qxt_json *a, qxt_json *b, const char *path, qxt_json *out) {
    if (a == NULL && b == NULL) return;
    if (a == NULL) {
        qxt_json *c = qxt_json_obj();
        qxt_json_obj_set(c, "op", qxt_json_str("add"));
        qxt_json_obj_set(c, "path", qxt_json_str(path));
        qxt_json_obj_set(c, "value", b);
        qxt_json_arr_push(out, c);
        return;
    }
    if (b == NULL) {
        qxt_json *c = qxt_json_obj();
        qxt_json_obj_set(c, "op", qxt_json_str("remove"));
        qxt_json_obj_set(c, "path", qxt_json_str(path));
        qxt_json_arr_push(out, c);
        return;
    }
    if (a->type != b->type) {
        qxt_json *c = qxt_json_obj();
        qxt_json_obj_set(c, "op", qxt_json_str("replace"));
        qxt_json_obj_set(c, "path", qxt_json_str(path));
        qxt_json_obj_set(c, "from", a);
        qxt_json_obj_set(c, "value", b);
        qxt_json_arr_push(out, c);
        return;
    }
    if (a->type == QXT_OBJ) {
        /* 并集的 key */
        for (size_t i = 0; i < b->u.obj.len; i++) {
            const char *k = b->u.obj.keys[i];
            qxt_json *av = qxt_json_obj_get(a, k);
            char *np = malloc(strlen(path) + strlen(k) + 2);
            sprintf(np, "%s/%s", path, k);
            diff_walk(av, b->u.obj.vals[i], np, out);
            free(np);
        }
        for (size_t i = 0; i < a->u.obj.len; i++) {
            const char *k = a->u.obj.keys[i];
            if (qxt_json_obj_get(b, k)) continue;   /* 已在上面处理 */
            char *np = malloc(strlen(path) + strlen(k) + 2);
            sprintf(np, "%s/%s", path, k);
            diff_walk(a->u.obj.vals[i], NULL, np, out);
            free(np);
        }
        return;
    }
    if (a->type == QXT_ARR) {
        size_t n = a->u.arr.len > b->u.arr.len ? a->u.arr.len : b->u.arr.len;
        for (size_t i = 0; i < n; i++) {
            qxt_json *av = i < a->u.arr.len ? a->u.arr.items[i] : NULL;
            qxt_json *bv = i < b->u.arr.len ? b->u.arr.items[i] : NULL;
            char *np = malloc(strlen(path) + 24);
            sprintf(np, "%s/%zu", path, i);
            diff_walk(av, bv, np, out);
            free(np);
        }
        return;
    }
    /* 标量: 值不同则 replace */
    bool eq = false;
    if (a->type == QXT_STR) eq = strcmp(a->u.str, b->u.str) == 0;
    else if (a->type == QXT_NUM) eq = a->u.num == b->u.num;
    else if (a->type == QXT_BOOL) eq = a->u.b == b->u.b;
    else if (a->type == QXT_NULL) eq = true;
    if (!eq) {
        qxt_json *c = qxt_json_obj();
        qxt_json_obj_set(c, "op", qxt_json_str("replace"));
        qxt_json_obj_set(c, "path", qxt_json_str(path));
        qxt_json_obj_set(c, "from", a);
        qxt_json_obj_set(c, "value", b);
        qxt_json_arr_push(out, c);
    }
}

/* ----------------------------------------------------- 深克隆 (本地实现, 不依赖 ipc 未导出的 clone)
 */
static qxt_json *clone_json(qxt_json *v) {
    if (!v) return NULL;
    switch (v->type) {
        case QXT_NULL: return qxt_json_null();
        case QXT_BOOL: return qxt_json_bool(v->u.b);
        case QXT_NUM:  return qxt_json_num(v->u.num);
        case QXT_STR:  return qxt_json_str(v->u.str);
        case QXT_ARR: {
            qxt_json *a = qxt_json_arr();
            for (size_t i = 0; i < v->u.arr.len; i++)
                qxt_json_arr_push(a, clone_json(v->u.arr.items[i]));
            return a;
        }
        case QXT_OBJ: {
            qxt_json *o = qxt_json_obj();
            for (size_t i = 0; i < v->u.obj.len; i++)
                qxt_json_obj_set(o, v->u.obj.keys[i], clone_json(v->u.obj.vals[i]));
            return o;
        }
    }
    return qxt_json_null();
}

/* ----------------------------------------------------- 深合并
 * overlay 覆盖 base (对象递归, 数组整体替换)
 */
static qxt_json *deep_merge(qxt_json *base, qxt_json *overlay) {
    if (base == NULL) return overlay ? clone_json(overlay) : NULL;
    if (overlay == NULL) return clone_json(base);
    if (base->type != QXT_OBJ || overlay->type != QXT_OBJ)
        return clone_json(overlay);
    qxt_json *res = qxt_json_obj();
    for (size_t i = 0; i < base->u.obj.len; i++) {
        const char *k = base->u.obj.keys[i];
        qxt_json *ov = qxt_json_obj_get(overlay, k);
        if (ov) qxt_json_obj_set(res, k, deep_merge(base->u.obj.vals[i], ov));
        else    qxt_json_obj_set(res, k, clone_json(base->u.obj.vals[i]));
    }
    for (size_t i = 0; i < overlay->u.obj.len; i++) {
        const char *k = overlay->u.obj.keys[i];
        if (qxt_json_obj_get(base, k)) continue;
        qxt_json_obj_set(res, k, clone_json(overlay->u.obj.vals[i]));
    }
    return res;
}

/* ----------------------------------------------------- IPC 方法实现 */
static qxt_json *h_pointer(const qxt_json *params, char **err_out) {
    qxt_json *doc = qxt_json_obj_get(params, "doc");
    const char *ptr = qxt_json_get_str(params, "pointer", NULL);
    if (!doc) { *err_out = strdup("missing doc"); return NULL; }
    if (!ptr) { *err_out = strdup("missing pointer"); return NULL; }
    qxt_json *v = pointer_get(doc, ptr);
    if (!v) { *err_out = strdup("pointer not found"); return NULL; }
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "path", qxt_json_str(ptr));
    qxt_json_obj_set(res, "value", v);     /* 共享子树 (只读, 不释放) */
    return res;
}

static qxt_json *h_diff(const qxt_json *params, char **err_out) {
    qxt_json *a = qxt_json_obj_get(params, "a");
    qxt_json *b = qxt_json_obj_get(params, "b");
    if (!a || !b) { *err_out = strdup("missing a/b"); return NULL; }
    qxt_json *changes = qxt_json_arr();
    diff_walk(a, b, "", changes);
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "changes", changes);
    qxt_json_obj_set(res, "count", qxt_json_num((double)changes->u.arr.len));
    return res;
}

static qxt_json *h_merge(const qxt_json *params, char **err_out) {
    qxt_json *base = qxt_json_obj_get(params, "base");
    qxt_json *overlay = qxt_json_obj_get(params, "overlay");
    if (!base || !overlay) { *err_out = strdup("missing base/overlay"); return NULL; }
    qxt_json *merged = deep_merge(base, overlay);
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "merged", merged);
    return res;
}

/* ----------------------------------------------------- selftest */
static int g_fail = 0;

static int selftest(void) {
    /* 用例1: pointer 取值 */
    {
        qxt_json *doc = qxt_json_parse("{\"a\":{\"b\":[10,20,{\"c\":\"hi\"}]}}");
        qxt_json *p = qxt_json_obj();
        qxt_json_obj_set(p, "doc", doc);
        qxt_json_obj_set(p, "pointer", qxt_json_str("/a/b/2/c"));
        qxt_json *r = h_pointer(p, &(char*){0});
        const char *v = r ? qxt_json_get_str(r, "value", "?") : "?";
        if (strcmp(v, "hi") != 0) { fprintf(stderr, "[selftest] pointer: %s\n", v); g_fail = 1; }
        qxt_json_free(r); qxt_json_free(p); qxt_json_free(doc);
    }
    /* 用例2: diff 应产出 1 处 replace */
    {
        qxt_json *a = qxt_json_parse("{\"x\":1,\"y\":2}");
        qxt_json *b = qxt_json_parse("{\"x\":1,\"y\":99}");
        qxt_json *p = qxt_json_obj();
        qxt_json_obj_set(p, "a", a); qxt_json_obj_set(p, "b", b);
        qxt_json *r = h_diff(p, &(char*){0});
        long n = r ? (long)qxt_json_get_num(r, "count", -1) : -1;
        if (n != 1) { fprintf(stderr, "[selftest] diff count: %ld\n", n); g_fail = 1; }
        qxt_json_free(r); qxt_json_free(p); qxt_json_free(a); qxt_json_free(b);
    }
    /* 用例3: merge 覆盖 */
    {
        qxt_json *base = qxt_json_parse("{\"k1\":1,\"k2\":{\"n\":2}}");
        qxt_json *ov = qxt_json_parse("{\"k2\":{\"n\":20},\"k3\":3}");
        qxt_json *p = qxt_json_obj();
        qxt_json_obj_set(p, "base", base); qxt_json_obj_set(p, "overlay", ov);
        qxt_json *r = h_merge(p, &(char*){0});
        qxt_json *m = r ? qxt_json_obj_get(r, "merged") : NULL;
        qxt_json *n = m ? qxt_json_obj_get(m, "k2") : NULL;
        double nv = (n && n->type == QXT_OBJ) ? qxt_json_get_num(n, "n", -1) : -1;
        if (nv != 20) { fprintf(stderr, "[selftest] merge k2.n: %f\n", nv); g_fail = 1; }
        qxt_json_free(r); qxt_json_free(p); qxt_json_free(base); qxt_json_free(ov);
    }
    if (g_fail == 0) fprintf(stderr, "json selftest ok (3 cases passed)\n");
    return g_fail;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) return selftest();
    qxt_ipc_set_engine_name("json");
    qxt_ipc_register("pointer", h_pointer);
    qxt_ipc_register("diff",    h_diff);
    qxt_ipc_register("merge",   h_merge);
    return qxt_ipc_run_loop();
}
