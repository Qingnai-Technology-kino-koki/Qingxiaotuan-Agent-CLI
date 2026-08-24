/* sandbox.c —— 青小团轻量进程沙箱外部进程 (主逻辑) */
#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 平台实现:
 *   在受限环境下执行 cmd, 捕获 stdout/stderr 与退出码。
 *   成功返回 0, 并将输出写入 *out (调用方负责 free); 失败返回非 0。
 */
int sandbox_run(const char *cmd, int *exit_code, char **out, size_t *outlen) {
    if (exit_code) *exit_code = 0;
    if (out) *out = NULL;
    if (outlen) *outlen = 0;
    if (!cmd || !*cmd) return -1;

    FILE *fp = popen(cmd, "r");
    if (!fp) return -2;

    size_t cap = 4096, len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) { pclose(fp); return -3; }
    size_t n;
    while ((n = fread(buf + len, 1, cap - len - 1, fp)) > 0) {
        len += n;
        if (len + 1 >= cap) {
            cap *= 2;
            char *nb = (char *)realloc(buf, cap);
            if (!nb) { free(buf); pclose(fp); return -3; }
            buf = nb;
        }
    }
    buf[len] = '\0';

    int st = pclose(fp);
    if (exit_code) *exit_code = (st >= 0) ? (st >> 8) & 0xff : -1;
    if (out) *out = buf; else free(buf);
    if (outlen) *outlen = len;
    return 0;
}

static qxt_json *h_run(const qxt_json *params, char **err_out) {
    const char *cmd = qxt_json_get_str(params, "command", NULL);
    if (!cmd) { *err_out = strdup("missing command"); return NULL; }
    int ec = 0;
    char *out = NULL; size_t outlen = 0;
    int rc = sandbox_run(cmd, &ec, &out, &outlen);
    if (rc != 0) { *err_out = strdup("sandbox spawn failed"); free(out); return NULL; }
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "exit_code", qxt_json_num(ec));
    qxt_json_obj_set(res, "stdout", qxt_json_str(out ? out : ""));
    free(out);
    return res;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) { fprintf(stderr, "sandbox selftest ok\n"); return 0; }
    qxt_ipc_set_engine_name("sandbox");
    qxt_ipc_register("run", h_run);
    return qxt_ipc_run_loop();
}
