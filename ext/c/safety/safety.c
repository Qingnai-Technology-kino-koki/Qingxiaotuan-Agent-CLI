/* safety.c —— 青小团「最小影响半径」安全护栏外部引擎
 *
 * 理念 (对标世界级 Agent 的核心差异化):
 *   真正的世界级 Agent 不是"能改更多", 而是"绝不乱改、能把破坏锁在最小半径内"。
 *   本引擎在危险操作**执行前**做静态可达性/模式分析, 给出:
 *     - risk 等级 (none/low/medium/high/critical)
 *     - blast_radius: 本次操作预计波及的文件/符号/数据库表数量
 *     - reasons: 命中了哪些危险模式
 *     - safe_preview: 若可能, 给出等价的更安全的替代命令 (仅建议, 不自动执行)
 *
 * 所有方法均为 dry-run 只读分析, 绝不修改任何文件系统或执行任何命令。
 */
#include "ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ----------------------------------------------------------------- 危险模式表
 * 每条规则: 正则-like 子串/关键词 + 风险等级 + 人类可读原因 + 可选安全建议模板。
 */
typedef enum { R_NONE=0, R_LOW=1, R_MEDIUM=2, R_HIGH=3, R_CRITICAL=4 } risk_t;

typedef struct {
    const char *pattern;
    risk_t      risk;
    const char *reason;
    const char *suggest;   /* 可选安全替代建议 */
} danger_rule_t;

static const danger_rule_t RULES[] = {
    /* 版本控制破坏 */
    {"push --force",        R_CRITICAL, "force push 会重写远端历史, 可能导致协作者丢失提交", "改用 git push --force-with-lease (只在远端未被他人更新时强制)"},
    {"push -f",             R_CRITICAL, "force push 会重写远端历史", "改用 git push --force-with-lease"},
    {"reset --hard",        R_HIGH,     "reset --hard 会丢弃工作区与暂存区全部未提交改动", "先 git stash 或 git commit, 确认后再考虑 --hard"},
    {"checkout -- .",       R_MEDIUM,   "checkout -- . 会丢弃所有未暂存文件改动", "用 git restore --staged 精确指定文件"},
    {"clean -f",            R_HIGH,     "git clean -f 会永久删除未跟踪文件", "先 git clean -ndx 预览将被删除的文件"},
    /* 文件系统破坏 */
    {"rm -rf",              R_CRITICAL, "rm -rf 递归强制删除, 几乎不可恢复", "先 ls 目标, 用 trash / 回收站机制, 禁止对 ~ / / 操作"},
    {"rm -fr",              R_CRITICAL, "rm -rf 递归强制删除", "先 ls 目标, 用 trash 机制"},
    {"del /s /q",           R_CRITICAL, "Windows 递归静默删除", "先 echo 目标列表, 确认后再删"},
    {"rmdir /s",            R_CRITICAL, "Windows 递归删除目录", "先确认目录内容"},
    {"format ",             R_CRITICAL, "format 会清空整个卷", "禁止在 Agent 中执行"},
    {"sudo rm",             R_CRITICAL, "提权后删除, 影响系统文件风险极高", "禁止在 Agent 中执行"},
    /* 数据库破坏 */
    {"drop table",          R_CRITICAL, "DROP TABLE 永久删除整张表及数据", "先 SELECT 备份, 或改用 rename 暂存"},
    {"drop database",       R_CRITICAL, "DROP DATABASE 永久删除整个库", "禁止在 Agent 中执行, 需人工确认"},
    {"truncate",            R_HIGH,     "TRUNCATE 清空全表数据且难回滚", "用带 WHERE 的 DELETE 或先备份"},
    {"delete from",         R_MEDIUM,   "DELETE 语句需确认作用范围", "务必带 WHERE; 生产库先 SELECT 同条件核对行数"},
    {"delete",              R_MEDIUM,   "DELETE 语句需确认作用范围", "务必带 WHERE 子句"},
    {"alter table",         R_MEDIUM,   "ALTER TABLE 可能锁表/丢列", "评估锁影响; 大表用在线 DDL 工具"},
    /* 配置 / 凭证 */
    {"chmod 777",           R_HIGH,     "chmod 777 对所有用户开放, 安全隐患", "按需最小权限, 如 750 / 600"},
    {"chmod -r 777",        R_HIGH,     "递归 777 放大权限风险", "避免递归开放"},
    {"curl ",               R_MEDIUM,   "网络下载, 需确认来源可信", "校验 checksum / 签名后再用"},
    {"wget ",               R_MEDIUM,   "网络下载, 需确认来源可信", "校验 checksum / 签名"},
    {"| sh",                R_HIGH,     "管道直传 shell 执行, 可能运行未审查的远程脚本", "先下载到文件人工审阅, 再执行"},
    {"| bash",              R_HIGH,     "管道直传 bash 执行未审查脚本", "先下载到文件人工审阅"},
    {"sudo ",               R_MEDIUM,   "提权操作需谨慎", "确认必要性与作用范围"},
    /* 资源耗尽 */
    {":(){",                R_CRITICAL, "Fork 炸弹会耗尽系统进程资源", "禁止在 Agent 中执行"},
    {"dd if=",              R_HIGH,     "dd 直接写块设备, 误操作即数据灾难", "双重确认 of= 目标, 禁止对系统盘"},
    /* 迁移 / 生产 */
    {"migrations/",         R_MEDIUM,   "删除/改动迁移文件会破坏数据库版本一致性", "如需回滚, 写新迁移而非删旧迁移"},
    {"production",          R_MEDIUM,   "疑似触碰生产环境配置", "确认目标环境; 生产变更走审批"},
    {"prod.",               R_MEDIUM,   "疑似触碰生产环境", "确认目标环境"},
    /* 凭证泄露风险 (写操作包含密钥) */
    {"sk-",                 R_HIGH,     "字符串中含疑似 API Key (sk- 前缀), 写入可能泄露", "改用环境变量/密钥管理服务"},
    {"password=",           R_MEDIUM,   "明文 password 赋值, 注意别提交到仓库", "改用密钥引用"},
    {"secret=",             R_MEDIUM,   "明文 secret 赋值", "改用密钥引用"},
};
static const size_t N_RULES = sizeof(RULES) / sizeof(RULES[0]);

/* 启发式: 根据目标路径/文本估算影响半径 */
static long estimate_blast(const char *kind, const char *target, const char *text) {
    if (!target && !text) return 1;
    const char *s = target ? target : text;
    long r = 1;
    /* 通配符 / 递归意味着更大半径 */
    if (strchr(s, '*') || strchr(s, '%')) r += 50;
    if (strstr(s, "/") || strstr(s, "\\")) r += 5;          /* 跨目录 */
    if (strstr(s, "src/") || strstr(s, "app/")) r += 10;    /* 核心代码区 */
    if (kind && (strcmp(kind, "sql") == 0)) r += 20;         /* 库级影响 */
    if (kind && (strcmp(kind, "delete") == 0)) r += 30;
    return r;
}

static int contains_ci(const char *hay, const char *needle) {
    if (!hay || !needle) return 0;
    size_t hlen = strlen(hay), nlen = strlen(needle);
    if (nlen == 0 || hlen < nlen) return 0;
    for (size_t i = 0; i + nlen <= hlen; i++) {
        size_t j = 0;
        while (j < nlen && tolower((unsigned char)hay[i+j]) == tolower((unsigned char)needle[j])) j++;
        if (j == nlen) return 1;
    }
    return 0;
}

/* 一个操作的完整分析 */
static qxt_json *analyze_one(const qxt_json *op) {
    const char *kind   = qxt_json_get_str(op, "kind", "command");
    const char *target = qxt_json_get_str(op, "target", NULL);
    const char *text   = qxt_json_get_str(op, "text", NULL);
    const char *s      = text ? text : (target ? target : "");

    qxt_json *reasons = qxt_json_arr();
    qxt_json *suggests = qxt_json_arr();
    risk_t worst = R_NONE;
    for (size_t i = 0; i < N_RULES; i++) {
        if (contains_ci(s, RULES[i].pattern)) {
            qxt_json_arr_push(reasons, qxt_json_str(RULES[i].reason));
            if (RULES[i].suggest)
                qxt_json_arr_push(suggests, qxt_json_str(RULES[i].suggest));
            if (RULES[i].risk > worst) worst = RULES[i].risk;
        }
    }
    const char *risk_str = "none";
    switch (worst) {
        case R_CRITICAL: risk_str = "critical"; break;
        case R_HIGH:     risk_str = "high"; break;
        case R_MEDIUM:   risk_str = "medium"; break;
        case R_LOW:      risk_str = "low"; break;
        default:         risk_str = "none"; break;
    }
    long blast = estimate_blast(kind, target, text);
    /* 高危叠加放大半径 */
    if (worst >= R_HIGH) blast *= 3;

    qxt_json *item = qxt_json_obj();
    qxt_json_obj_set(item, "kind", qxt_json_str(kind));
    if (target) qxt_json_obj_set(item, "target", qxt_json_str(target));
    if (text)   qxt_json_obj_set(item, "text", qxt_json_str(text));
    qxt_json_obj_set(item, "risk", qxt_json_str(risk_str));
    qxt_json_obj_set(item, "blast_radius", qxt_json_num((double)blast));
    qxt_json_obj_set(item, "reasons", reasons);
    qxt_json_obj_set(item, "safe_preview", suggests);
    return item;
}

/* analyze: 批量分析一组计划中的操作 (dry-run, 不执行) */
static qxt_json *h_analyze(const qxt_json *params, char **err_out) {
    qxt_json *ops = qxt_json_obj_get(params, "ops");
    if (!ops || ops->type != QXT_ARR) { *err_out = strdup("missing ops[]"); return NULL; }
    qxt_json *items = qxt_json_arr();
    risk_t overall = R_NONE;
    for (size_t i = 0; i < ops->u.arr.len; i++) {
        qxt_json *it = analyze_one(ops->u.arr.items[i]);
        const char *rk = qxt_json_get_str(it, "risk", "none");
        risk_t rv = R_NONE;
        if (strcmp(rk,"critical")==0) rv=R_CRITICAL;
        else if (strcmp(rk,"high")==0) rv=R_HIGH;
        else if (strcmp(rk,"medium")==0) rv=R_MEDIUM;
        else if (strcmp(rk,"low")==0) rv=R_LOW;
        if (rv > overall) overall = rv;
        qxt_json_arr_push(items, it);
    }
    const char *overall_str = "none";
    switch (overall) {
        case R_CRITICAL: overall_str = "critical"; break;
        case R_HIGH:     overall_str = "high"; break;
        case R_MEDIUM:   overall_str = "medium"; break;
        case R_LOW:      overall_str = "low"; break;
        default:         overall_str = "none"; break;
    }
    qxt_json *res = qxt_json_obj();
    qxt_json_obj_set(res, "overall", qxt_json_str(overall_str));
    qxt_json_obj_set(res, "count", qxt_json_num((double)ops->u.arr.len));
    qxt_json_obj_set(res, "items", items);
    /* 给 Agent 的明确行动建议 */
    const char *advice = "ok";
    if (overall >= R_CRITICAL) advice = "BLOCK: 存在致命风险操作, 必须人工确认, 不要自动执行";
    else if (overall == R_HIGH) advice = "CONFIRM: 高风险, 执行前需用户明确批准";
    else if (overall == R_MEDIUM) advice = "REVIEW: 中等风险, 建议预览安全替代";
    qxt_json_obj_set(res, "advice", qxt_json_str(advice));
    return res;
}

/* score: 对单条命令字符串做快速风险评分 (供 shell 工具执行前调用) */
static qxt_json *h_score(const qxt_json *params, char **err_out) {
    const char *cmd = qxt_json_get_str(params, "command", NULL);
    if (!cmd) { *err_out = strdup("missing command"); return NULL; }
    qxt_json *op = qxt_json_obj();
    qxt_json_obj_set(op, "kind", qxt_json_str("command"));
    qxt_json_obj_set(op, "text", qxt_json_str(cmd));
    qxt_json *one = analyze_one(op);
    qxt_json_free(op);
    /* one 已包含 risk/blast_radius/reasons/safe_preview, 直接追加 block 字段返回,
       避免浅拷贝子值导致双重释放。 */
    const char *rk = qxt_json_get_str(one, "risk", "none");
    int block = (strcmp(rk, "critical") == 0) ? 1 : 0;
    qxt_json_obj_set(one, "block", qxt_json_bool(block));
    return one;
}

static int g_fail = 0;

static int selftest(void) {
    /* 用例1: force push 应判 critical + 建议 */
    {
        qxt_json *p = qxt_json_obj();
        qxt_json_obj_set(p, "command", qxt_json_str("git push --force origin main"));
        qxt_json *r = h_score(p, &(char*){0});
        const char *risk = qxt_json_get_str(r, "risk", "?");
        if (strcmp(risk, "critical") != 0) { fprintf(stderr, "[selftest] force push not critical: %s\n", risk); g_fail = 1; }
        if (!qxt_json_get_bool(r, "block", 0)) { fprintf(stderr, "[selftest] force push not blocked\n"); g_fail = 1; }
        qxt_json_free(r); qxt_json_free(p);
    }
    /* 用例2: 普通 ls 应 none */
    {
        qxt_json *p = qxt_json_obj();
        qxt_json_obj_set(p, "command", qxt_json_str("ls -la src/"));
        qxt_json *r = h_score(p, &(char*){0});
        const char *risk = qxt_json_get_str(r, "risk", "?");
        if (strcmp(risk, "none") != 0) { fprintf(stderr, "[selftest] ls should be none: %s\n", risk); g_fail = 1; }
        qxt_json_free(r); qxt_json_free(p);
    }
    /* 用例3: 批量 analyze 整体应为 critical */
    {
        qxt_json *p = qxt_json_obj();
        qxt_json *ops = qxt_json_arr();
        qxt_json *op1 = qxt_json_obj();
        qxt_json_obj_set(op1, "kind", qxt_json_str("command"));
        qxt_json_obj_set(op1, "text", qxt_json_str("rm -rf build/"));
        qxt_json_arr_push(ops, op1);
        qxt_json_obj_set(p, "ops", ops);
        qxt_json *r = h_analyze(p, &(char*){0});
        const char *ov = qxt_json_get_str(r, "overall", "?");
        if (strcmp(ov, "critical") != 0) { fprintf(stderr, "[selftest] analyze overall not critical: %s\n", ov); g_fail = 1; }
        qxt_json_free(r); qxt_json_free(p);
    }
    if (g_fail == 0) fprintf(stderr, "safety selftest ok (3 cases passed)\n");
    return g_fail;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--selftest") == 0) return selftest();
    qxt_ipc_set_engine_name("safety");
    qxt_ipc_register("analyze", h_analyze);
    qxt_ipc_register("score",  h_score);
    return qxt_ipc_run_loop();
}
