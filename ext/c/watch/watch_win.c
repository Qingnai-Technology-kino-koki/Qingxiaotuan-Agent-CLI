/* watch_win.c —— Windows ReadDirectoryChangesW 平台实现 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

typedef void (*watch_cb)(const char *path, const char *kind);

struct watcher {
    watch_cb cb;
    char *root;
    volatile int stop;
    HANDLE dir;
    HANDLE evt;
    pthread_t tid;
};

static void *watch_thread(void *arg) {
    struct watcher *w = arg;
    char buf[4096];
    DWORD bytes;
    while (!w->stop) {
        if (!ReadDirectoryChangesW(w->dir, buf, sizeof(buf), TRUE,
                FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME |
                FILE_NOTIFY_CHANGE_LAST_WRITE | FILE_NOTIFY_CHANGE_SIZE,
                &bytes, NULL, NULL)) {
            if (w->stop) break;
            Sleep(200);
            continue;
        }
        if (bytes == 0) continue;
        char *p = buf;
        while (p < buf + bytes) {
            FILE_NOTIFY_INFORMATION *info = (FILE_NOTIFY_INFORMATION *)p;
            const char *kind = (info->Action == FILE_ACTION_ADDED) ? "create"
                             : (info->Action == FILE_ACTION_REMOVED) ? "delete"
                             : (info->Action == FILE_ACTION_MODIFIED) ? "modify" : "change";
            int wlen = info->FileNameLength / sizeof(WCHAR);
            char name[512];
            int n = WideCharToMultiByte(CP_UTF8, 0, info->FileName, wlen, name, sizeof(name) - 1, NULL, NULL);
            name[n] = '\0';
            char full[1024];
            snprintf(full, sizeof(full), "%s\\%s", w->root, name);
            w->cb(full, kind);
            if (info->NextEntryOffset == 0) break;
            p += info->NextEntryOffset;
        }
    }
    return NULL;
}

void *watcher_start(const char *root, watch_cb cb) {
    struct watcher *w = calloc(1, sizeof(struct watcher));
    w->cb = cb;
    w->root = strdup(root);
    w->stop = 0;
    w->dir = CreateFileA(root, FILE_LIST_DIRECTORY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED, NULL);
    if (w->dir == INVALID_HANDLE_VALUE) { free(w->root); free(w); return NULL; }
    pthread_create(&w->tid, NULL, watch_thread, w);
    return w;
}

void watcher_stop(void *handle) {
    struct watcher *w = handle;
    if (!w) return;
    w->stop = 1;
    CancelIo(w->dir);
    CloseHandle(w->dir);
    pthread_join(w->tid, NULL);
    free(w->root);
    free(w);
}
