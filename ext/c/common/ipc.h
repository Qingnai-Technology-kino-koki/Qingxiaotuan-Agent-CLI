/*
 * ipc.h —— 青小团外部进程 IPC 公共协议 (C 侧)
 *
 * 协议 (行分隔 JSONL, UTF-8, 每行一条消息, 以 \n 结尾):
 *
 *   请求:  {"id": <int>, "method": "<string>", "params": { ... }}
 *   响应:  {"id": <int>, "ok": <bool>, "result": { ... } | "error": "<string>"}
 *   流块:  {"id": <int>, "stream": true, "chunk": <any>}   (可选, 多行)
 *
 * 设计约束:
 *   - 零外部依赖 (仅 libc), 便于跨平台 (Linux/macOS/Windows) 编译。
 *   - 解析器为"够用"子集: 对象/数组/字符串/数字/true/false/null,
 *     字符串支持 \n \t \r \b \f \" \\ \/ \uXXXX 转义。
 *   - 每个程序都是长驻进程, 从 stdin 逐行读请求, 向 stdout 写响应。
 *
 * 用法: 程序注册一组 method 处理器, 调 ipc_run_loop() 进入循环。
 */
#ifndef QXT_IPC_H
#define QXT_IPC_H

#include <stddef.h>
#include <stdbool.h>

#ifdef _WIN32
#  define QXT_API
#else
#  define QXT_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------- JSON 值 */
typedef enum {
    QXT_NULL,
    QXT_BOOL,
    QXT_NUM,
    QXT_STR,
    QXT_ARR,
    QXT_OBJ
} qxt_json_type;

typedef struct qxt_json qxt_json;

struct qxt_json {
    qxt_json_type type;
    union {
        bool        b;
        double      num;
        char       *str;          /* 拥有所有权, 用 qxt_json_free 释放 */
        struct {
            qxt_json **items;
            size_t     len;
            size_t     cap;
        } arr;
        struct {
            char     **keys;
            qxt_json **vals;
            size_t     len;
            size_t     cap;
        } obj;
    } u;
};

/* 解析一行 JSONL (以 \0 结尾的字符串), 返回新分配的值或 NULL(解析失败)。 */
QXT_API qxt_json *qxt_json_parse(const char *text);

/* 序列化值到新建字符串 (调用方 free)。pretty=0 紧凑。 */
QXT_API char *qxt_json_stringify(const qxt_json *v, int pretty);

/* 释放值树。 */
QXT_API void qxt_json_free(qxt_json *v);

/* 便捷构造器 */
QXT_API qxt_json *qxt_json_null(void);
QXT_API qxt_json *qxt_json_bool(bool b);
QXT_API qxt_json *qxt_json_num(double d);
QXT_API qxt_json *qxt_json_str(const char *s);          /* 拷贝 s */
QXT_API qxt_json *qxt_json_arr(void);
QXT_API qxt_json *qxt_json_obj(void);

/* 数组/对象追加 (转移所有权, 调用后不要再 free 传入的子值) */
QXT_API void qxt_json_arr_push(qxt_json *arr, qxt_json *item);
QXT_API void qxt_json_obj_set(qxt_json *obj, const char *key, qxt_json *val);

/* 对象查值 (返回 NULL 表示缺失) */
QXT_API qxt_json *qxt_json_obj_get(const qxt_json *obj, const char *key);

/* 便捷读取: 字符串返回内部指针(不要 free), 数字/布尔转换; 缺失返回默认值 */
QXT_API const char *qxt_json_get_str(const qxt_json *obj, const char *key, const char *def);
QXT_API double      qxt_json_get_num(const qxt_json *obj, const char *key, double def);
QXT_API bool        qxt_json_get_bool(const qxt_json *obj, const char *key, bool def);

/* ---------------------------------------------------------- IPC 循环 */

/* method 处理器: 返回新分配的响应 result (成功) 或设置 *err 为错误串(调用方接管)。
 * params 为请求中的 params 对象 (可能为 NULL)。 */
typedef qxt_json *(*qxt_ipc_handler)(const qxt_json *params, char **err_out);

/* 注册一个 method 处理器。 */
QXT_API void qxt_ipc_register(const char *method, qxt_ipc_handler handler);

/* 设置引擎名 (出现在 ready 帧与内置 _meta 响应里)。 */
QXT_API void qxt_ipc_set_engine_name(const char *name);

/* 进入请求-响应循环, 直到 stdin EOF 或收到 method=="_quit"。
 * 程序启动时会向 stdout 写一行 {"ready":true} 表示已就绪。 */
QXT_API int qxt_ipc_run_loop(void);

/* 主动写一条 JSONL 到 stdout 并 flush (供 handler 内部发 stream chunk)。 */
QXT_API void qxt_ipc_emit(const qxt_json *v);

#ifdef __cplusplus
}
#endif

#endif /* QXT_IPC_H */
