/* watch_posix.c —— inotify (Linux) / kqueue (macOS) 平台实现 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#include <dirent.h>
#include <sys/stat.h>

#if defined(__linux__)
#  include <sys/inotify.h>
#  include <limits.h>
#elif defined(__APPLE__) || defined(__FreeBSD__)
#  include <sys/event.h>
#  include <sys/time.h>
#endif

typedef void (*watch_cb)(const char *path, const char *kind);

struct watcher {
    watch_cb cb;
    char *root;
    volatile int stop;
#if defined(__linux__)
    int fd;
#elif defined(__APPLE__)
    int kq;
#endif
    pthread_t tid;
};

static void *watch_thread(void *arg) {
    struct watcher *w = arg;
#if defined(__linux__)
    char buf[4096] __attribute__((aligned(__alignof__(struct inotify_event))));
    while (!w->stop) {
        int n = read(w->fd, buf, sizeof(buf));
        if (n <= 0) { if (w->stop) break; continue; }
        int i = 0;
        while (i < n) {
            struct inotify_event *ev = (struct inotify_event *)(buf + i);
            if (ev->len > 0) {
                const char *kind = (ev->mask & IN_CREATE) ? "create"
                                 : (ev->mask & IN_DELETE) ? "delete"
                                 : (ev->mask & IN_MODIFY) ? "modify" : "change";
                char full[4096];
                snprintf(full, sizeof(full), "%s/%s", w->root, ev->name);
                w->cb(full, kind);
            }
            i += sizeof(struct inotify_event) + ev->len;
        }
    }
#elif defined(__APPLE__)
    struct kevent ke;
    while (!w->stop) {
        struct timespec to = {0, 200 * 1000 * 1000}; /* 200ms */
        int n = kevent(w->kq, NULL, 0, &ke, 1, &to);
        if (n > 0) {
            if (ke.filter == EVFILT_VNODE) {
                const char *kind = (ke.fflags & NOTE_DELETE) ? "delete"
                                     : (ke.fflags & NOTE_WRITE) ? "modify"
                                     : (ke.fflags & NOTE_RENAME) ? "rename" : "change";
                w->cb((char *)ke.udata, kind);
            }
        }
    }
#else
    /* 回退: 轮询 (每 500ms 扫一次 mtime) */
    (void)w;
#endif
    return NULL;
}

void *watcher_start(const char *root, watch_cb cb) {
    struct watcher *w = calloc(1, sizeof(struct watcher));
    w->cb = cb;
    w->root = strdup(root);
    w->stop = 0;
#if defined(__linux__)
    w->fd = inotify_init();
    if (w->fd < 0) { free(w->root); free(w); return NULL; }
    inotify_add_watch(w->fd, root, IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_FROM | IN_MOVED_TO);
#elif defined(__APPLE__)
    w->kq = kqueue();
    int fd = open(root, O_EVTONLY);
    if (fd < 0) { free(w->root); free(w); return NULL; }
    struct kevent ke;
    EV_SET(&ke, fd, EVFILT_VNODE, EV_ADD | EV_CLEAR, NOTE_WRITE | NOTE_DELETE | NOTE_RENAME, 0, (void *)root);
    kevent(w->kq, &ke, 1, NULL, 0, NULL);
#endif
    pthread_create(&w->tid, NULL, watch_thread, w);
    return w;
}

void watcher_stop(void *handle) {
    struct watcher *w = handle;
    if (!w) return;
    w->stop = 1;
    pthread_join(w->tid, NULL);
#if defined(__linux__)
    close(w->fd);
#elif defined(__APPLE__)
    close(w->kq);
#endif
    free(w->root);
    free(w);
}
