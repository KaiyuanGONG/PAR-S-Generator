import { useEffect, useRef, useState } from "react";
import {
  api,
  openTaskSocket,
  type CaseRecord,
  type Protocol,
  type RunEvent,
  type TaskState,
} from "../api";
import { Bar, Card, Empty, StageRail, Status } from "../ui";

export default function RunCenter({
  protocol,
  configPath,
  runRoot,
  setRunRoot,
  task,
  setTask,
}: {
  protocol: Protocol | null;
  configPath: string | null;
  runRoot: string | null;
  setRunRoot: (v: string) => void;
  task: TaskState | null;
  setTask: (t: TaskState | null) => void;
}) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [stages, setStages] = useState<Record<string, string>>({});
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [progress, setProgress] = useState<{ stage: string; done: number; total: number } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!task) return;
    const stop = openTaskSocket(task.task_id, (e) => {
      setEvents((prev) => [...prev, e]);
      if (e.stage && e.type.startsWith("stage_")) {
        setStages((s) => ({ ...s, [e.stage!]: e.status ?? e.type.replace("stage_", "") }));
      }
      if (e.type === "progress" && e.stage) {
        setProgress({ stage: e.stage, done: e.done ?? 0, total: e.total ?? 0 });
      }
      if (e.type === "finished") {
        api.task(task.task_id).then(setTask).catch(() => {});
        if (e.run_root) refresh(e.run_root);
      }
    });
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.task_id]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events.length]);

  async function refresh(root: string) {
    try {
      const [s, c] = await Promise.all([api.stages(root), api.cases(root, 0, 200)]);
      setStages(Object.fromEntries(s.stages.map((x) => [x.stage, x.status])));
      setCases(c.cases);
    } catch {
      /* run may not exist yet */
    }
  }

  useEffect(() => {
    if (runRoot) refresh(runRoot);
  }, [runRoot]);

  async function start(resume: boolean) {
    if (!configPath) {
      setErr("Create a dataset first — no run configuration selected.");
      return;
    }
    setErr(null);
    setEvents([]);
    try {
      const r = await api.startRun({
        config_path: configPath,
        resume,
        finalize: true,
        allow_simind_execution: confirmed,
      });
      setRunRoot(r.run_root);
      const t = await api.task(r.task_id);
      setTask(t);
    } catch (e: any) {
      setErr(String(e.message ?? e));
    }
  }

  async function pause() {
    if (task) {
      try {
        setTask(await api.pause(task.task_id));
      } catch (e: any) {
        setErr(String(e.message ?? e));
      }
    }
  }

  const order = protocol?.stage_order ?? [];
  const running = task?.status === "running";

  return (
    <>
      {err && <div className="banner err">{err}</div>}
      {task?.status === "failed" && <div className="banner err">{task.error}</div>}
      {task?.status === "paused" && <div className="banner warn">{task.error}</div>}
      {task?.status === "finished" && (
        <div className="banner ok">
          Run finished{task.result?.finalized ? " and finalized — the manifest is now immutable." : "."}
        </div>
      )}

      <Card
        title="Pipeline"
        note={runRoot ? <span className="mono">{runRoot}</span> : "no run started"}
      >
        <StageRail order={order} states={stages} />
        {progress && (
          <div style={{ marginTop: 12 }}>
            <div style={{ display: "flex", fontSize: 12.5, color: "var(--tx-2)", marginBottom: 5 }}>
              <span>
                {progress.stage.replace(/_/g, " ")} — {progress.done} of {progress.total} cases
              </span>
              <span className="sp" />
              <span className="mono">
                {progress.total ? Math.round((progress.done / progress.total) * 100) : 0}%
              </span>
            </div>
            <Bar value={progress.total ? progress.done / progress.total : 0} />
          </div>
        )}
        <div className="actions" style={{ marginTop: 14 }}>
          <button className="primary" onClick={() => start(false)} disabled={running || !configPath}>
            Start run
          </button>
          <button onClick={() => start(true)} disabled={running || !configPath}>
            Resume
          </button>
          <button onClick={pause} disabled={!running}>
            Pause after current case
          </button>
          <span className="sp" />
          <label style={{ fontSize: 12.5, color: "var(--tx-2)", display: "flex", gap: 6 }}>
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            I reviewed the plan — allow real SIMIND execution
          </label>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          {configPath ? (
            <>
              config <code>{configPath}</code>
            </>
          ) : (
            "Create a dataset in workspace 1 first."
          )}
          {" · "}the server refuses execute mode without the confirmation above.
        </div>
      </Card>

      <div className="split">
        <Card title="Cases" note={`${cases.length} recorded`} flush>
          {cases.length ? (
            <table>
              <thead>
                <tr>
                  <th>case</th>
                  <th>split</th>
                  <th>phantom QC</th>
                  <th>projection QC</th>
                  <th className="num">seed</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.case_id}>
                    <td className="mono">{c.case_id}</td>
                    <td>{c.split ?? "—"}</td>
                    <td>
                      <Status s={(c.qc as any)?.phantom?.status ?? (c.qc as any)?.phantom_status} />
                    </td>
                    <td>
                      <Status s={(c.qc as any)?.projection?.status ?? (c.qc as any)?.projection_status} />
                    </td>
                    <td className="num">{String(c.seed ?? "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No cases yet. Start a run to populate the ledger.</Empty>
          )}
        </Card>

        <Card title="Event stream" note={`${events.length} events`}>
          <div className="log" ref={logRef}>
            {events.length === 0 && <div className="muted">waiting for the first event…</div>}
            {events.map((e, i) => (
              <div key={i}>
                <time>{new Date((e.ts ?? 0) * 1000).toLocaleTimeString()}</time>
                <span
                  className={
                    "lv " +
                    (e.type === "finished" ? "ok" : e.type === "error" ? "err" : e.type === "paused" ? "warn" : "")
                  }
                >
                  {e.type.replace("stage_", "")}
                </span>
                <span>
                  {e.stage ?? ""}
                  {e.done != null ? ` ${e.done}/${e.total}` : ""}
                  {e.line ?? e.message ?? e.error ?? ""}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
