/* sandbox_posix.c —— Linux seccomp-minimal / macOS 受限执行 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>

int sandbox_run(const char *cmd, int *exit_code, char **out, size_t *outlen) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }
    if (pid == 0) {
        /* 子进程: 极简沙箱 (教学/演示级)
         * - 降权到 nobody (若可); - 限制地址空间 (RLIMIT_AS);
         * - 真实 seccomp 过滤在此省略, 仅做资源限制演示。
         */
        dup2(pipefd[1], STDOUT_FILENO);
        dup2(pipefd[1], STDERR_FILENO);
        close(pipefd[0]); close(pipefd[1]);
#ifdef RLIMIT_AS
        struct rlimit rl;
        rl.rlim_cur = 512UL * 1024 * 1024;  /* 512MB */
        rl.rlim_max = rl.rlim_cur;
        setrlimit(RLIMIT_AS, &rl);
#endif
#ifdef RLIMIT_CPU
        struct rlimit rc;
        rc.rlim_cur = 30; rc.rlim_max = 30;  /* 30s CPU */
        setrlimit(RLIMIT_CPU, &rc);
#endif
        execl("/bin/sh", "sh", "-c", cmd, (char *)NULL);
        _exit(127);
    }
    close(pipefd[1]);
    char buf[4096]; ssize_t n;
    size_t cap = 4096, len = 0;
    char *data = malloc(cap);
    while ((n = read(pipefd[0], buf, sizeof(buf))) > 0) {
        if (len + (size_t)n + 1 >= cap) { cap *= 2; data = realloc(data, cap); }
        memcpy(data + len, buf, (size_t)n);
        len += (size_t)n;
    }
    data[len] = '\0';
    close(pipefd[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    *exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    *out = data; *outlen = len;
    return 0;
}
