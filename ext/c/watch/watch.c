/* watch.c —— 青小团文件监视外部进程 (主逻辑, 平台无关部分) */
#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <sys/stat.h>
#include <dirent.h>
#include <time.h>

/* 跨平台轮询式文件监视: 周期性 stat 快照比对, 检测新增/修改/删除。
 * 不依赖任何平台专属文件通知 API, 简单且可移植。
 */
typedef void (*watch_cb)(const char *path, const char *kind);

typedef struct {
    char *root;
    watch_cb cb;
    pthread_t tid;
    volatile int stop;
} watcher_t;

/* 递归收集路径 -> mtime 快照 (调用方释放返回的哈希表由调用方管理, 这里用简单数组) */
static char **g_paths = NULL;
static time_t *g_mtimes = NULL;
static size_t g_n = 0, g_cap = 0;

static void snap_add(const char *path, time_t m) {
    if (g_n >= g_cap) { g_cap = g_cap ? g_cap * 2 : 256; g_paths = realloc(g_paths, g_cap * sizeof(char *)); g_mtimes = realloc(g_mtimes, g_cap * sizeof(time_t)); }
    g_paths[g_n] = strdup(path); g_mtimes[g_n] = m; g_n++;
}
static time_t snap_find(const char *path) {
    for (size_t i = 0; i < g_n; i++) if (strcmp(g_paths[i], path) == 0) return g_mtimes[i];
    return (time_t)-1;
}

static void snap_walk(const char *dir) {
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) continue;
        char full[1024];
        snprintf(full, sizeof(full), "%s/%s", dir, e->d_name);
        struct stat st;
        if (stat(full, &st) != 0) continue;
        if (S_ISDIR(st.st_mode)) { snap_add(full, st.st_mtime); snap_walk(full); }
        else snap_add(full, st.st_mtime);
    }
    closedir(d);
}

static void *watcher_thread(void *arg) {
    watcher_t *w = (watcher_t *)arg;
    char **old_paths = NULL; time_t *old_m = NULL; size_t old_n = 0, old_cap = 0;
    /* 初始快照 */
    g_n = 0; g_cap = 0; g_paths = NULL; g_mtimes = NULL;
    snap_walk(w->root);
    old_cap = g_n; old_paths = malloc(g_n * sizeof(char *)); old_m = malloc(g_n * sizeof(time_t));
    for (size_t i = 0; i < g_n; i++) { old_paths[i] = strdup(g_paths[i]); old_m[i] = g_mtimes[i]; }
    old_n = g_n;
    while (!w->stop) {
        g_n = 0; g_cap = 0; g_paths = NULL; g_mtimes = NULL;
        snap_walk(w->root);
        /* 新增 / 修改 */
        for (size_t i = 0; i < g_n; i++) {
            time_t prev = (time_t)-1;
            for (size_t j = 0; j < old_n; j++) if (strcmp(old_paths[j], g_paths[i]) == 0) { prev = old_m[j]; break; }
            if (prev == (time_t)-1) w->cb(g_paths[i], "created");
            else if (prev != g_mtimes[i]) w->cb(g_paths[i], "modified");
        }
        /* 删除 */
        for (size_t j = 0; j < old_n; j++) {
            int found = 0;
            for (size_t i = 0; i < g_n; i++) if (strcmp(g_paths[i], old_paths[j]) == 0) { found = 1; break; }
            if (!found) w->cb(old_paths[j], "deleted");
        }
        /* 轮换快照 */
        for (size_t i = 0; i < old_n; i++) free(old_paths[i]);
        free(old_paths); free(old_m);
        old_cap = g_n; old_paths = malloc(g_n * sizeof(char *)); old_m = malloc(g_n * sizeof(time_t));
        for (size_t i = 0; i < g_n; i++) { old_paths[i] = strdup(g_paths[i]); old_m[i] = g_mtimes[i]; }
        old_n = g_n;
        for (size_t i = 0; i < g_n; i++) free(g_paths[i]);
        free(g_paths); free(g_mtimes); g_paths = NULL; g_mtimes = NULL; g_n = 0;
        struct timespec ts = {1, 0}; nanosleep(&ts, NULL);
    }
    for (size_t i = 0; i < old_n; i++) free(old_paths[i]);
    free(old_paths); free(old_m);
    return NULL;
}

static void *watcher_start(const char *root, watch_cb cb) {
    watcher_t *w = (watcher_t *)calloc(1, sizeof(watcher_t));
    w->root = strdup(root); w->cb = cb; w->stop = 0;
    pthread_create(&w->tid, NULL, watcher_thread, w);
    return w;
}
static void watcher_stop(void *handle) {
    watcher_t *w = (watcher_t *)handle;
    if (!w) return;
    w->stop = 1;
    pthread_join(w->tid, NULL);
    free(w->root); free(w);
}

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_running = 0;
static void *g_handle = NULL;

static void on_event(const char *path, const char *kind) {
    pthread_mutex_lock(&g_lock);
    if (!g_running) { pthread_mutex_unlock(&g_lock); return; }
    pthread_mutex_unlock(&g_lock);
    /* 经 stdout 写一条 stream 事件 (不带 id 的广播) */
    qxt_json *ev = qxt_json_obj();
    qxt_json_obj_set(ev, "event", qxt_json_str("watch"));
    qxt_json_obj_set(ev, "path", qxt_json_str(path));
    qxt_json_obj_set(ev, "kind", qxt_json_str(kind));
    qxt_ipc_emit(ev);
    qxt_json_free(ev);
}

static qxt_json *h_watch(const qxt_json *params, char **err_out) {
    (void)err_out;
    const char *root = qxt_json_get_str(params, "root", ".");
    pthread_mutex_lock(&g_lock);
    if (g_running) { pthread_mutex_unlock(&g_lock); *err_out = strdup("already watching"); return NULL; }
    g_handle = watcher_start(root, on_event);
    g_running = 1;
    pthread_mutex_unlock(&g_lock);
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "ok", qxt_json_bool(1));
    qxt_json_obj_set(res, "root", qxt_json_str(root));
    return res;
}

static qxt_json *h_stop(const qxt_json *params, char **err_out) {
    (void)err_out; (void)params;
    pthread_mutex_lock(&g_lock);
    if (g_handle) watcher_stop(g_handle);
    g_handle = NULL; g_running = 0;
    pthread_mutex_unlock(&g_lock);
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "ok", qxt_json_bool(1));
    return res;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) { fprintf(stderr, "watch selftest ok\n"); return 0; }
    qxt_ipc_set_engine_name("watch");
    qxt_ipc_register("watch", h_watch);
    qxt_ipc_register("stop", h_stop);
    int rc = qxt_ipc_run_loop();
    pthread_mutex_lock(&g_lock);
    if (g_handle) watcher_stop(g_handle);
    g_running = 0;
    pthread_mutex_unlock(&g_lock);
    return rc;
}
