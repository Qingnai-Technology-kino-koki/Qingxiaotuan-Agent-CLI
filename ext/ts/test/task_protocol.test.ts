import { describe, expect, it } from "vitest";
import { validateTaskManifest } from "../src/task-protocol";

describe("shared task protocol", () => {
  const valid = {
    job_id: "bg-1234abcd",
    task: "检查工程",
    workspace: "C:/workspace",
    profile: "default",
    status: "running",
    pid: 123,
    started_at: 1,
    updated_at: 2,
    heartbeat: 2,
    turns: 1,
    result: "",
    error: null,
  };

  it("accepts a Python-compatible manifest", () => {
    expect(validateTaskManifest(valid).job_id).toBe("bg-1234abcd");
  });

  it("rejects unsafe job ids", () => {
    expect(() => validateTaskManifest({ ...valid, job_id: "../../x" })).toThrow("job_id");
  });

  it("rejects unknown states", () => {
    expect(() => validateTaskManifest({ ...valid, status: "paused" })).toThrow("status");
  });
});
