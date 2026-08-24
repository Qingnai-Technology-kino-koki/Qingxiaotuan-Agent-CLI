/** Python/TypeScript 共享任务协议。
 *
 * JSON 字段与 Python `BackgroundStore` 对齐，供 detached worker、TUI 和
 * 外部 TS 引擎共同使用。该文件只包含纯类型和无副作用校验函数。
 */

export type TaskStatus = "queued" | "running" | "cancel_requested" | "done" | "failed" | "cancelled";

export interface TaskManifest {
  job_id: string;
  task: string;
  workspace: string;
  profile: string;
  status: TaskStatus;
  pid: number | null;
  started_at: number;
  updated_at: number;
  heartbeat: number;
  turns: number;
  result: string;
  error: string | null;
  session_file?: string;
  yolo?: boolean;
}

export interface TaskEvent {
  ts: number;
  type: "job.start" | "heartbeat" | "agent.turn" | "job.done" | "job.error" | "job.cancel";
  job_id: string;
  payload?: Record<string, unknown>;
}

const JOB_ID = /^bg-[a-f0-9]{8,64}$/;

export function validateTaskManifest(value: unknown): TaskManifest {
  if (!value || typeof value !== "object") throw new Error("task manifest must be an object");
  const item = value as Partial<TaskManifest>;
  if (typeof item.job_id !== "string" || !JOB_ID.test(item.job_id)) throw new Error("invalid task job_id");
  if (typeof item.task !== "string" || item.task.length > 100_000) throw new Error("invalid task text");
  if (typeof item.workspace !== "string" || item.workspace.length > 4_000) throw new Error("invalid workspace");
  if (typeof item.profile !== "string" || item.profile.length > 200) throw new Error("invalid profile");
  const statuses: TaskStatus[] = ["queued", "running", "cancel_requested", "done", "failed", "cancelled"];
  if (!statuses.includes(item.status as TaskStatus)) throw new Error("invalid task status");
  for (const key of ["started_at", "updated_at", "heartbeat", "turns"]) {
    if (typeof item[key as keyof TaskManifest] !== "number" || !Number.isFinite(item[key as keyof TaskManifest] as number)) {
      throw new Error(`invalid task ${key}`);
    }
  }
  return item as TaskManifest;
}
