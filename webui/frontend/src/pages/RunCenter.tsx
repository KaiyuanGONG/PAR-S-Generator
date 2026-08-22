import { useEffect, useRef, useState } from "react";
import {
  api,
  openTaskSocket,
  type CaseRecord,
  type Protocol,
  type RunEvent,
  type TaskState,
} from "../api";
import ErrorNotice from "../components/ErrorNotice";
import { stageTranslationKey, statusTranslationKey, translateStatus, useI18n, type PipelineStage } from "../i18n";
import { Empty, StageRail, Status } from "../ui";
import { useWorkspace } from "../workspace";

const COMPLETE_STATES = new Set(["passed", "prepared", "skipped", "complete", "done", "finished"]);
const ATTENTION_STATES = new Set(["failed", "error", "warning", "paused", "running", "simulating"]);

function readable(value: string) {
  return value.replace(/[_-]/g, " ");
}

function qcStatus(record: CaseRecord, area: "phantom" | "projection") {
  const qc = record.qc;
  const nested = qc?.[area];
  if (nested && typeof nested === "object" && "status" in nested) {
    const value = (nested as { status?: unknown }).status;
    return typeof value === "string" ? value : undefined;
  }
  const flat = qc?.[`${area}_status`];
  return typeof flat === "string" ? flat : undefined;
}

function QuietStatus({ value, emptyLabel }: { value?: string; emptyLabel: string }) {
  const { t } = useI18n();
  const normalized = value?.toLowerCase();
  if (normalized && ATTENTION_STATES.has(normalized)) return <Status s={value} label={translateStatus(t, value)} />;

  return (
    <span
      className={"run-qc-text" + (normalized && COMPLETE_STATES.has(normalized) ? " is-complete" : "")}
      data-status={normalized ?? "unrecorded"}
    >
      {value ? translateStatus(t, value) : emptyLabel}
    </span>
  );
}

function eventTone(event: RunEvent) {
  const type = event.type.toLowerCase();
  const status = event.status?.toLowerCase() ?? "";
  if (type.includes("error") || type.includes("failed") || status === "failed") return "err";
  if (type.includes("pause") || status === "paused" || status === "warning") return "warn";
  if (type.includes("finish") || COMPLETE_STATES.has(status)) return "ok";
  if (type.includes("progress") || status === "running") return "run";
  return "";
}

function eventDetail(event: RunEvent, fallback: string) {
  const parts: string[] = [];
  if (event.stage) parts.push(readable(event.stage));
  if (event.done != null) parts.push(`${event.done}/${event.total ?? "?"}`);
  const message = event.line ?? event.message ?? event.error;
  if (message) parts.push(message);
  return parts.join(" · ") || fallback;
}

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
  const { t, locale } = useI18n();
  const { state: workspace, dispatch } = useWorkspace();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [stages, setStages] = useState<Record<string, string>>({});
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [progress, setProgress] = useState<{ stage: string; done: number; total: number } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pauseRequested, setPauseRequested] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [configCopied, setConfigCopied] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const requiresExecutionConsent = workspace.draft.simulation.mode === "execute";

  useEffect(() => {
    if (!task) return;
    const stop = openTaskSocket(task.task_id, (event) => {
      setEvents((previous) => [...previous, event]);
      if (event.stage && event.type.startsWith("stage_")) {
        setStages((previous) => ({
          ...previous,
          [event.stage!]: event.status ?? event.type.replace("stage_", ""),
        }));
      }
      if (event.type === "progress" && event.stage) {
        setProgress({ stage: event.stage, done: event.done ?? 0, total: event.total ?? 0 });
      }
      if (event.type === "finished" || event.type === "paused" || event.type === "error") {
        api.task(task.task_id).then(setTask).catch(() => {});
      }
      if (event.type === "finished" && event.run_root) refresh(event.run_root);
    });
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.task_id]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events.length]);

  useEffect(() => {
    if (task?.status !== "running") setPauseRequested(false);
  }, [task?.status]);

  async function refresh(root: string) {
    try {
      const [stageResponse, caseResponse] = await Promise.all([
        api.stages(root),
        api.cases(root, 0, 200),
      ]);
      setStages(Object.fromEntries(stageResponse.stages.map((item) => [item.stage, item.status])));
      dispatch({
        type: "run/stages",
        stages: Object.fromEntries(stageResponse.stages.map((item) => [item.stage, item.status])),
      });
      setCases(caseResponse.cases);
    } catch {
      /* A newly requested run may not have created its ledger yet. */
    }
  }

  useEffect(() => {
    if (runRoot) refresh(runRoot);
  }, [runRoot]);

  async function start(resume: boolean) {
    if (!configPath) {
      setErr(t("run.noRun"));
      return;
    }
    setErr(null);
    setEvents([]);
    setPauseRequested(false);
    try {
      const response = await api.startRun({
        config_path: configPath,
        resume,
        finalize: false,
        allow_simind_execution: confirmed,
      });
      setRunRoot(response.run_root);
      dispatch({ type: "run/started", taskId: response.task_id, runRoot: response.run_root });
      const nextTask = await api.task(response.task_id);
      setTask(nextTask);
    } catch (error: unknown) {
      setErr(error);
    }
  }

  async function pause() {
    if (!task || task.status !== "running" || pauseRequested) return;
    const stableLifecycle = workspace.lifecycle === "pause-requested" ? "running" : workspace.lifecycle;
    setErr(null);
    setPauseRequested(true);
    dispatch({ type: "run/pause-requested" });
    try {
      setTask(await api.pause(task.task_id));
    } catch (error: unknown) {
      setPauseRequested(false);
      dispatch({ type: "lifecycle/set", lifecycle: stableLifecycle });
      setErr(error);
    }
  }

  async function copyCanonicalConfig() {
    if (!workspace.activeRun.canonicalConfig) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(workspace.activeRun.canonicalConfig, null, 2));
      setConfigCopied(true);
      window.setTimeout(() => setConfigCopied(false), 1600);
    } catch (error: unknown) {
      setErr(error);
    }
  }

  const order = protocol?.stage_order ?? [];
  const running = task?.status === "running";
  const completedStages = order.filter((name) => COMPLETE_STATES.has(stages[name]?.toLowerCase())).length;
  const withinStage = progress?.total ? Math.max(0, Math.min(1, progress.done / progress.total)) : 0;
  const progressStageIsComplete = progress
    ? COMPLETE_STATES.has(stages[progress.stage]?.toLowerCase())
    : false;
  const progressContribution = progressStageIsComplete ? 0 : withinStage;
  const overallFraction = order.length
    ? Math.min(1, (completedStages + progressContribution) / order.length)
    : withinStage;
  const overallPercent = Math.round(overallFraction * 100);
  const activeStage = progress?.stage ?? order.find((name) => stages[name]?.toLowerCase() === "running");
  const sealed = COMPLETE_STATES.has(stages.finalize?.toLowerCase());
  const taskDisplayStatus = sealed ? "sealed" : pauseRequested ? "pause-requested" : (task?.status ?? "not-started");
  const stageLabel = (name: string) =>
    name in stageTranslationKey
      ? t(stageTranslationKey[name as PipelineStage])
      : readable(name);

  return (
    <div className="run-center">
      <div className="run-notices" aria-live="assertive" aria-atomic="true">
        {err != null && <ErrorNotice error={err} />}
        {task?.status === "failed" && <div className="banner err">{task.error ?? t("status.failed")}</div>}
        {task?.status === "paused" && (
          <div className="banner warn">
            {t("status.paused")}{task.error ? ` — ${task.error}` : "."}
          </div>
        )}
        {task?.status === "finished" && (
          <div className="banner ok">
            {task.result?.finalized
              ? t("status.sealed")
              : t("run.finished")}
          </div>
        )}
      </div>

      <section className="run-overview" aria-labelledby="run-overview-title">
        <header className="run-section-heading">
          <div>
            <span className="run-eyebrow">{t("run.executionControl")}</span>
            <h2 id="run-overview-title">{t("run.stageOverview")}</h2>
          </div>
          <div className="run-identity">
            <span className="run-state" data-status={taskDisplayStatus} role="status">
              {t(statusTranslationKey[workspace.lifecycle])}
            </span>
            <code title={runRoot ?? undefined}>{runRoot ?? t("run.noRun")}</code>
          </div>
        </header>

        <div className="run-stage-strip" aria-label={t("run.orderedStages")}>
          {order.length ? (
            <StageRail order={order} states={stages} label={stageLabel} statusLabel={(status) => translateStatus(t, status)} ariaLabel={t("run.stageOverview")} />
          ) : (
            <Empty>{t("common.notAvailable")}</Empty>
          )}
        </div>

        <div className="run-progress" aria-label={t("run.overallProgress")}>
          <div className="run-progress-label">
            <span>
              <strong>{activeStage ? stageLabel(activeStage) : t(statusTranslationKey[workspace.lifecycle])}</strong>
              {progress && (
                <span className="run-progress-cases">
                  {t("run.progress", { stage: stageLabel(progress.stage), done: progress.done, total: progress.total })}
                </span>
              )}
            </span>
            <span className="mono">{overallPercent}%</span>
          </div>
          <progress
            className="run-progress-meter"
            data-complete={overallPercent === 100}
            aria-label={t("run.overallCompletion")}
            max={100}
            value={overallPercent}
          >
            {overallPercent}%
          </progress>
        </div>

        <details className="run-contract-inspector">
          <summary>
            <span>{t("run.effectiveContract")}</span>
            <small>{workspace.activeRun.canonicalConfig ? t("run.serverNormalized") : t("common.notAvailable")}</small>
          </summary>
          {workspace.activeRun.canonicalConfig ? (
            <div className="run-contract-body">
              <pre className="code mono">{JSON.stringify(workspace.activeRun.canonicalConfig, null, 2)}</pre>
              <button type="button" onClick={() => void copyCanonicalConfig()}>{configCopied ? t("run.configCopied") : t("run.copyConfig")}</button>
            </div>
          ) : <Empty>{t("run.noCanonicalConfig")}</Empty>}
        </details>
      </section>

      <div className="run-workbench">
        <section className="run-case-ledger" aria-labelledby="run-case-ledger-title">
          <header className="run-section-heading">
            <div>
              <span className="run-eyebrow">{t("common.measured")}</span>
              <h2 id="run-case-ledger-title">{t("run.caseLedger")}</h2>
            </div>
            <span className="run-section-count mono">{t("run.cases.count", { count: cases.length })}</span>
          </header>

          {cases.length ? (
            <div className="run-table-region" role="region" aria-label={t("run.generatedLedger")} tabIndex={0}>
              <table>
                <caption>{t("run.ledgerCaption")}</caption>
                <thead>
                  <tr>
                  <th scope="col">{t("common.case")}</th>
                  <th scope="col">{t("run.split")}</th>
                  <th scope="col">{t("run.phantomQc")}</th>
                  <th scope="col">{t("run.projectionQc")}</th>
                  <th scope="col" className="num">{t("common.seed")}</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((record) => (
                    <tr key={record.case_id}>
                      <th scope="row" className="mono">{record.case_id}</th>
                      <td>{record.split ?? "—"}</td>
                      <td><QuietStatus value={qcStatus(record, "phantom")} emptyLabel={t("common.notAvailable")} /></td>
                      <td><QuietStatus value={qcStatus(record, "projection")} emptyLabel={t("common.notAvailable")} /></td>
                      <td className="num">{String(record.seed ?? "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>{t("run.noCases")}</Empty>
          )}
        </section>

        <aside className="run-events" aria-labelledby="run-events-title">
          <header className="run-section-heading">
            <div>
              <span className="run-eyebrow">{t("run.liveTrace")}</span>
              <h2 id="run-events-title">{t("run.eventStream")}</h2>
            </div>
            <span className="run-section-count mono">{t("run.events.count", { count: events.length })}</span>
          </header>
          <div
            className="run-event-stream log"
            ref={logRef}
            role="log"
            aria-label={t("run.liveEvents")}
            aria-live="polite"
            aria-relevant="additions"
          >
            {events.length === 0 && (
              <div className="run-event-empty muted">{t("run.waitingEvents")}</div>
            )}
            {events.map((event, index) => (
              <div className="run-event-row" key={`${event.ts}-${event.type}-${index}`}>
                <time dateTime={new Date((event.ts ?? 0) * 1000).toISOString()}>
                  {new Date((event.ts ?? 0) * 1000).toLocaleTimeString(locale)}
                </time>
                <span className={`lv ${eventTone(event)}`}>{readable(event.type.replace("stage_", ""))}</span>
                <span className="run-event-detail">{eventDetail(event, t("run.eventReceived"))}</span>
              </div>
            ))}
          </div>
        </aside>
      </div>

      <section className="run-command-inline" aria-labelledby="run-controls-title">
        <div className="run-command-context">
          <strong id="run-controls-title">{t("run.safeBoundary")}</strong>
          <p>
            {configPath ? (
              <>{t("run.configPath", { path: configPath })}</>
            ) : runRoot ? (
              t("run.readOnlySelected")
            ) : (
              t("run.noRun")
            )}
            {" "}{t("run.neverAutoSeals")}
          </p>
        </div>

        {sealed ? (
          <span className="run-sealed-readonly mono">{t("status.sealed")}</span>
        ) : (
          <>
            {requiresExecutionConsent && <label className="run-execution-consent">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>
                <strong>{t("run.realExecutionConsent")}</strong>
                <small>{t("run.computeImpact")}</small>
              </span>
            </label>}

            <div className="run-command-actions actions">
              <button type="button" className="primary" onClick={() => start(false)} disabled={running || !configPath || (requiresExecutionConsent && !confirmed)}>
                {t("action.start")}
              </button>
              <button type="button" onClick={() => start(true)} disabled={running || !configPath || (requiresExecutionConsent && !confirmed)}>
                {t("action.resume")}
              </button>
              <button type="button" onClick={pause} disabled={!running || pauseRequested}>
                {pauseRequested ? t("status.pause-requested") : t("action.pause")}
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
