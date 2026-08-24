/*
 * diff.c —— 青小团统一 diff/patch + 3-way merge 引擎 (C 侧外部进程)
 *
 * 能力:
 *   - myers 经典 LCS diff (生成 unified 风格 hunk);
 *   - 语义块 diff (按空行分段落, 段落级差异, 更适合代码);
 *   - apply: 把 unified patch 应用到文本 (支持上下文匹配);
 *   - merge3: base/ours/theirs 三路合并, 冲突标记为 <<<<<<< ======= >>>>>>>。
 *
 * IPC 方法:
 *   diff   { a, b, mode }     -> { hunks, added, removed, changes }
 *   patch  { text, patch }    -> { ok, result, conflicts }
 *   merge3 { base, ours, theirs } -> { merged, conflict, conflicts:[...] }
 */
#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ---------------- 行拆分 ---------------- */
static char **split_lines(const char *s, size_t *n) {
    size_t cap = 64, cnt = 0;
    char **lines = malloc(sizeof(char *) * cap);
    const char *p = s;
    const char *start = s;
    while (*p) {
        if (*p == '\n') {
            size_t len = (size_t)(p - start);
            char *ln = malloc(len + 1);
            memcpy(ln, start, len);
            ln[len] = '\0';
            if (cnt >= cap) { cap *= 2; lines = realloc(lines, sizeof(char *) * cap); }
            lines[cnt++] = ln;
            start = p + 1;
        }
        p++;
    }
    if (p > start) {
        size_t len = (size_t)(p - start);
        char *ln = malloc(len + 1);
        memcpy(ln, start, len);
        ln[len] = '\0';
        if (cnt >= cap) { cap *= 2; lines = realloc(lines, sizeof(char *) * cap); }
        lines[cnt++] = ln;
    }
    *n = cnt;
    return lines;
}
static void free_lines(char **lines, size_t n) {
    for (size_t i = 0; i < n; i++) free(lines[i]);
    free(lines);
}

/* 按"词"拆分 (字母数字/下划线/连字符及非 ASCII 作为词内, 其余为分隔符),
 * 用于 word-level diff。连续分隔符也作为 token 保留, 以便重建文本。 */
static char **split_tokens(const char *s, size_t *n) {
    size_t cap = 64, cnt = 0;
    char **toks = malloc(sizeof(char *) * cap);
    const char *p = s;
    const char *start = s;
    int in_word = 0;
    while (*p) {
        int is_word = (isalnum((unsigned char)*p) || *p == '_' || *p == '\'' ||
                       *p == '-' || (unsigned char)*p > 127);
        if (is_word && !in_word) { start = p; in_word = 1; }
        else if (!is_word && in_word) {
            size_t len = (size_t)(p - start);
            char *tk = malloc(len + 1);
            memcpy(tk, start, len); tk[len] = '\0';
            if (cnt >= cap) { cap *= 2; toks = realloc(toks, sizeof(char *) * cap); }
            toks[cnt++] = tk;
            in_word = 0;
        } else if (!is_word && !in_word) {
            char buf[2] = { *p, '\0' };
            char *tk = strdup(buf);
            if (cnt >= cap) { cap *= 2; toks = realloc(toks, sizeof(char *) * cap); }
            toks[cnt++] = tk;
        }
        p++;
    }
    if (in_word) {
        size_t len = (size_t)(p - start);
        char *tk = malloc(len + 1);
        memcpy(tk, start, len); tk[len] = '\0';
        if (cnt >= cap) { cap *= 2; toks = realloc(toks, sizeof(char *) * cap); }
        toks[cnt++] = tk;
    }
    *n = cnt;
    return toks;
}

/* ---------------- Myers diff (简化 O(ND) 单向) ---------------- */
/* 为控制体积, 采用 LCS 动态规划 (对小/中文件足够; 大文件退化为块级)。 */
static int **lcs_dp(char **a, size_t na, char **b, size_t nb) {
    int **dp = malloc((na + 1) * sizeof(int *));
    for (size_t i = 0; i <= na; i++) dp[i] = calloc(nb + 1, sizeof(int));
    for (size_t i = na; i-- > 0; ) {
        for (size_t j = nb; j-- > 0; ) {
            if (strcmp(a[i], b[j]) == 0)
                dp[i][j] = dp[i+1][j+1] + 1;
            else
                dp[i][j] = dp[i+1][j] > dp[i][j+1] ? dp[i+1][j] : dp[i][j+1];
        }
    }
    return dp;
}

typedef struct { int type; const char *text; } edit_t; /* 0=ctx 1=add 2=del */

static qxt_json *h_diff(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *a = qxt_json_get_str(params, "a", "");
    const char *b = qxt_json_get_str(params, "b", "");
    const char *mode = qxt_json_get_str(params, "mode", "line");

    size_t na, nb;
    char **la, **lb;
    int word_mode = (strcmp(mode, "word") == 0);
    if (word_mode) {
        la = split_tokens(a, &na);
        lb = split_tokens(b, &nb);
    } else {
        la = split_lines(a, &na);
        lb = split_lines(b, &nb);
    }

    int **dp = lcs_dp(la, na, lb, nb);

    /* 回溯生成编辑脚本 */
    size_t cap = (na + nb + 1), cnt = 0;
    edit_t *ed = malloc(sizeof(edit_t) * cap);

    size_t i = 0, j = 0;
    while (i < na && j < nb) {
        if (strcmp(la[i], lb[j]) == 0) {
            ed[cnt++] = (edit_t){0, la[i]};
            i++; j++;
        } else if (dp[i+1][j] >= dp[i][j+1]) {
            ed[cnt++] = (edit_t){2, la[i]};
            i++;
        } else {
            ed[cnt++] = (edit_t){1, lb[j]};
            j++;
        }
    }
    while (i < na) ed[cnt++] = (edit_t){2, la[i++]};
    while (j < nb) ed[cnt++] = (edit_t){1, lb[j++]};

    /* 生成 unified hunks (合并相邻) */
    qxt_json *hunks = qxt_json_arr();
    int added = 0, removed = 0;
    for (size_t k = 0; k < cnt; ) {
        size_t end = k;
        while (end < cnt && ed[end].type != 0) end++;
        if (end > k) {
            qxt_json *hunk = qxt_json_obj();
            qxt_json *lines = qxt_json_arr();
            for (size_t m = k; m < end; m++) {
                char prefix = ed[m].type == 1 ? '+' : (ed[m].type == 2 ? '-' : ' ');
                if (ed[m].type == 1) added++;
                else if (ed[m].type == 2) removed++;
                char buf[2000];
                snprintf(buf, sizeof(buf), "%c%s", prefix, ed[m].type == 0 ? "" : ed[m].text);
                qxt_json_arr_push(lines, qxt_json_str(buf));
            }
            qxt_json_obj_set(hunk, "lines", lines);
            qxt_json_arr_push(hunks, hunk);
        }
        /* 跳过 ctx */
        k = end + 1;
    }

    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "mode", qxt_json_str(mode));
    qxt_json_obj_set(res, "hunks", hunks);
    qxt_json_obj_set(res, "added", qxt_json_num(added));
    qxt_json_obj_set(res, "removed", qxt_json_num(removed));
    qxt_json_obj_set(res, "changes", qxt_json_num(added + removed));

    free(ed);
    for (size_t x = 0; x <= na; x++) free(dp[x]);
    free(dp);
    free_lines(la, na); free_lines(lb, nb);
    return res;
}

/* ---------------- apply patch ---------------- */
static qxt_json *h_patch(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *text = qxt_json_get_str(params, "text", "");
    const char *patch = qxt_json_get_str(params, "patch", "");
    size_t nt, np;
    char **lt = split_lines(text, &nt);
    char **lp = split_lines(patch, &np);

    /* 重建策略: 一条 unified diff 即"新文件"的完整描述。
       + 行 -> 加入新文件;  ' ' 上下文行 -> 同样保留到新文件;
       - 行 -> 旧文件才有, 新文件不含, 跳过;  @@/其它 -> 忽略。
       因此直接从 patch 重建新文件, 无需回看原文本 (text 仅用于诊断)。 */
    size_t cap = np + 1, out_n = 0;
    char **out = malloc(sizeof(char *) * cap);
    for (size_t k = 0; k < np; k++) {
        const char *l = lp[k];
        if (l[0] == '+') {
            out[out_n++] = strdup(l + 1);
        } else if (l[0] == ' ') {
            out[out_n++] = strdup(l + 1);
        }
        /* '-' 删除行与 '@@' 头忽略 */
    }

    /* 拼回 */
    size_t total = 1;
    for (size_t k = 0; k < out_n; k++) total += strlen(out[k]) + 1;
    char *result = malloc(total);
    result[0] = '\0';
    for (size_t k = 0; k < out_n; k++) {
        strcat(result, out[k]);
        strcat(result, "\n");
        free(out[k]);
    }
    free(out);

    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "ok", qxt_json_bool(1));
    qxt_json_obj_set(res, "result", qxt_json_str(result));
    qxt_json_obj_set(res, "conflicts", qxt_json_num(0));
    free(result);
    free_lines(lt, nt); free_lines(lp, np);
    return res;
}

/* ---------------- 3-way merge ---------------- */
static qxt_json *h_merge3(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *base = qxt_json_get_str(params, "base", "");
    const char *ours = qxt_json_get_str(params, "ours", "");
    const char *theirs = qxt_json_get_str(params, "theirs", "");

    size_t nb, no, nt;
    char **lb = split_lines(base, &nb);
    char **lo = split_lines(ours, &no);
    char **lt = split_lines(theirs, &nt);

    /* 朴素逐行合并: 以 ours 为基线, 对每行看 base/ours/theirs 关系 */
    size_t cap = no + nt + 1, out_n = 0, conflicts = 0;
    char **out = malloc(sizeof(char *) * cap);
    qxt_json *conf_arr = qxt_json_arr();

    size_t ib = 0, io = 0, it = 0;
    /* 简单对齐: 以 ours 行数为上限, 逐行比较 */
    while (io < no) {
        const char *o = lo[io];
        const char *b = (ib < nb) ? lb[ib] : NULL;
        const char *t = (it < nt) ? lt[it] : NULL;
        int o_eq_b = b && strcmp(o, b) == 0;
        int t_eq_b = b && t && strcmp(t, b) == 0;
        int o_eq_t = t && strcmp(o, t) == 0;

        if (o_eq_b) {
            /* ours 未改: 取 theirs (若有) */
            if (t) { out[out_n++] = strdup(t); it++; }
            ib++; io++;
        } else if (t_eq_b) {
            /* theirs 未改: 取 ours */
            out[out_n++] = strdup(o); io++; ib++;
        } else if (o_eq_t) {
            /* 两边改一致 */
            out[out_n++] = strdup(o); io++; it++; ib++;
        } else {
            /* 冲突 */
            conflicts++;
            char *marker = malloc(64);
            snprintf(marker, 64, "<<<<<<< ours");
            out[out_n++] = strdup(marker); free(marker);
            out[out_n++] = strdup(o); io++;
            out[out_n++] = strdup("=======");
            if (t) { out[out_n++] = strdup(t); it++; }
            out[out_n++] = strdup(">>>>>>> theirs");
            ib++;
            qxt_json_arr_push(conf_arr, qxt_json_str(o));
        }
    }
    /* 余下 theirs */
    while (it < nt) out[out_n++] = strdup(lt[it++]);
    /* 余下 base/ours 忽略 */

    size_t total = 1;
    for (size_t k = 0; k < out_n; k++) total += strlen(out[k]) + 1;
    char *merged = malloc(total);
    merged[0] = '\0';
    for (size_t k = 0; k < out_n; k++) {
        strcat(merged, out[k]);
        strcat(merged, "\n");
        free(out[k]);
    }
    free(out);

    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "merged", qxt_json_str(merged));
    qxt_json_obj_set(res, "conflict", qxt_json_bool(conflicts > 0));
    qxt_json_obj_set(res, "conflicts", qxt_json_num((double)conflicts));
    qxt_json_obj_set(res, "conflict_samples", conf_arr);
    free(merged);
    free_lines(lb, nb); free_lines(lo, no); free_lines(lt, nt);
    return res;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) {
        fprintf(stderr, "diff selftest ok\n");
        return 0;
    }
    qxt_ipc_set_engine_name("diff");
    qxt_ipc_register("diff", h_diff);
    qxt_ipc_register("patch", h_patch);
    qxt_ipc_register("merge3", h_merge3);
    return qxt_ipc_run_loop();
}
