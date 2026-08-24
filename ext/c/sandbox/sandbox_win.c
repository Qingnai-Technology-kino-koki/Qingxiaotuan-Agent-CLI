/* sandbox_win.c —— Windows Job Object 受限执行 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int sandbox_run(const char *cmd, int *exit_code, char **out, size_t *outlen) {
    SECURITY_ATTRIBUTES sa; memset(&sa, 0, sizeof(sa));
    sa.nLength = sizeof(sa); sa.bInheritHandle = TRUE;
    HANDLE hRead = NULL, hWrite = NULL;
    if (!CreatePipe(&hRead, &hWrite, &sa, 0)) return -1;

    /* Job Object: 限制内存与 CPU */
    HANDLE job = CreateJobObject(NULL, NULL);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION lim;
    memset(&lim, 0, sizeof(lim));
    lim.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_TIME;
    lim.ProcessMemoryLimit = 512UL * 1024 * 1024;
    lim.BasicLimitInformation.PerJobUserTimeLimit.QuadPart = 30 * 10000000LL; /* 30s */
    SetInformationJobObject(job, JobObjectExtendedLimitInformation, &lim, sizeof(lim));

    STARTUPINFOA si; memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    si.hStdOutput = hWrite; si.hStdError = hWrite;
    si.dwFlags = STARTF_USESTDHANDLES;
    PROCESS_INFORMATION pi;
    memset(&pi, 0, sizeof(pi));
    char cmdline[8192];
    snprintf(cmdline, sizeof(cmdline), "cmd.exe /C %s", cmd);
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, CREATE_SUSPENDED, NULL, NULL, &si, &pi)) {
        CloseHandle(hRead); CloseHandle(hWrite); return -1;
    }
    AssignProcessToJobObject(job, pi.hProcess);
    ResumeThread(pi.hThread);

    CloseHandle(hWrite);
    char buf[4096]; DWORD n;
    size_t cap = 4096, len = 0;
    char *data = malloc(cap);
    while (ReadFile(hRead, buf, sizeof(buf), &n, NULL) && n > 0) {
        if (len + n + 1 >= cap) { cap *= 2; data = realloc(data, cap); }
        memcpy(data + len, buf, n);
        len += n;
    }
    data[len] = '\0';
    CloseHandle(hRead);

    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD ec = 0;
    GetExitCodeProcess(pi.hProcess, &ec);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread); CloseHandle(job);

    *exit_code = (int)ec;
    *out = data; *outlen = len;
    return 0;
}
