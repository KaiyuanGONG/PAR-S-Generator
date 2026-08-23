import { useEffect, useMemo, useState } from "react";
import { api, type Protocol, type RunSummary, type TaskState } from "./api";
import ErrorNotice from "./components/ErrorNotice";
import { useI18n, statusTranslationKey, type Locale, type TranslationKey } from "./i18n";
import NewDataset from "./pages/NewDataset";
import PhantomDesign from "./pages/PhantomDesign";
import QCDataset from "./pages/QCDataset";
import RunCenter from "./pages/RunCenter";
import SealDataset from "./pages/SealDataset";
import Simulation from "./pages/Simulation";
import {
  WorkspaceProvider,
  useWorkspace,
  type RunLifecycleStatus,
  type WorkspaceView,
} from "./workspace";

interface Health {
  version: string;
  repo_root: string;
}

interface NavigationItem {
  view: WorkspaceView;
  label: TranslationKey;
}

const PLAN_ITEMS: NavigationItem[] = [
  { view: "protocol", label: "nav.protocol" },
  { view: "phantom", label: "nav.phantom" },
  { view: "simulation", label: "nav.simulation" },
];

const LIFECYCLE_ITEMS: NavigationItem[] = [
  { view: "run", label: "nav.run" },
  { view: "review", label: "nav.review" },
  { view: "seal", label: "nav.seal" },
];

const PAGE_COPY: Record<WorkspaceView, { title: TranslationKey; description: TranslationKey }> = {
  protocol: { title: "page.protocol.title", description: "page.protocol.description" },
  phantom: { title: "page.phantom.title", description: "page.phantom.description" },
  simulation: { title: "page.simulation.title", description: "page.simulation.description" },
  run: { title: "page.run.title", description: "page.run.description" },
  review: { title: "page.review.title", description: "page.review.description" },
  seal: { title: "page.seal.title", description: "page.seal.description" },
};

function ShellNavigation({ items }: { items: NavigationItem[] }) {
  const { state, dispatch } = useWorkspace();
  const { t } = useI18n();

  function navigationState(view: WorkspaceView): RunLifecycleStatus | "pending" | "incomplete" | "warning" {
    if (view === "protocol" || view === "phantom" || view === "simulation") {
      const section = state.plan.sections[view];
      return state.activeRun.locked || section === "locked" ? "ready" : section;
    }
    if (view === "run") return state.lifecycle;
    if (view === "review") {
      return state.lifecycle === "review" || state.lifecycle === "sealed" ? state.lifecycle : "pending";
    }
    return state.lifecycle === "sealed" ? "sealed" : state.lifecycle === "review" ? "ready" : "pending";
  }

  return (
    <>
      {items.map((item) => {
        const status = navigationState(item.view);
        const statusLabel =
          status === "pending" ? t("status.pending")
            : status === "incomplete" ? t("status.incomplete")
              : status === "warning" ? t("status.warning")
                : t(statusTranslationKey[status]);
        return (
          <button
            type="button"
            className="lifecycle-link"
            key={item.view}
            aria-current={state.view === item.view ? "page" : undefined}
            onClick={() => dispatch({ type: "view/set", view: item.view })}
          >
            <span className="lifecycle-state" data-state={status} aria-hidden="true" />
            <span>{t(item.label)}</span>
            <small>{statusLabel}</small>
          </button>
        );
      })}
    </>
  );
}

function AppShell({
  protocol,
  defaults,
  health,
  offline,
  bootstrapError,
  reconcileKey,
  onRetry,
}: {
  protocol: Protocol | null;
  defaults: Record<string, unknown> | null;
  health: Health | null;
  offline: boolean;
  bootstrapError: string | null;
  reconcileKey: number;
  onRetry: () => void;
}) {
  const { state, dispatch } = useWorkspace();
  const { locale, setLocale, t, themePreference, setThemePreference } = useI18n();
  const [task, setTask] = useState<TaskState | null>(null);
  const pageCopy = PAGE_COPY[state.view];

  useEffect(() => {
    if (offline) {
      dispatch({ type: "connection/set", offline: true });
      return;
    }
    dispatch({ type: "connection/set", offline: false });
    let live = true;
    Promise.allSettled([api.runs(state.draft.identity.runsRoot), api.tasks()])
      .then(async ([runsResult, tasksResult]) => {
        if (!live) return;
        if (runsResult.status === "rejected" && tasksResult.status === "rejected") {
          dispatch({ type: "connection/set", offline: true });
          return;
        }
        dispatch({ type: "connection/set", offline: false });
        const runsResponse = runsResult.status === "fulfilled" ? runsResult.value : { runs: [] };
        const tasksResponse = tasksResult.status === "fulfilled" ? tasksResult.value : { tasks: [] };
        const persisted = state.activeRun;
        const activeTask =
          tasksResponse.tasks.find(
            (candidate) =>
              candidate.task_id === persisted.taskId &&
              candidate.status !== "finished" &&
              candidate.status !== "failed",
          ) ??
          tasksResponse.tasks.find(
            (candidate) =>
              candidate.status !== "finished" &&
              candidate.status !== "failed" &&
              (candidate.run_id === persisted.runId || candidate.run_root === persisted.runRoot),
          );
        const restoredRun: RunSummary | undefined =
          runsResponse.runs.find((candidate) => candidate.root === (activeTask?.run_root ?? persisted.runRoot)) ??
          runsResponse.runs.find((candidate) => candidate.run_id === (activeTask?.run_id ?? persisted.runId)) ??
          (activeTask ? {
            run_id: activeTask.run_id,
            root: activeTask.run_root,
            config_path: persisted.configPath,
            finalized: activeTask.result?.finalized ?? false,
            stages: {},
          } : undefined);
        if (restoredRun) {
          const detail = await api.run(restoredRun.root).catch(() => null);
          if (!live) return;
          const canonical = detail?.effective_config;
          restoreRun(restoredRun, canonical && typeof canonical === "object" ? canonical : undefined);
        } else if (persisted.locked && runsResult.status === "fulfilled") {
          dispatch({ type: "run/clear" });
          dispatch({ type: "error/set-raw", error: t("error.staleRun") });
        }
        if (activeTask) updateTask(activeTask);
      })
      .catch(() => dispatch({ type: "connection/set", offline: true }));
    return () => {
      live = false;
    };
    // Reconcile once for the persisted lookup pointer. Draft edits must not restart recovery.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offline, reconcileKey]);

  function restoreRun(run: RunSummary, canonicalConfig?: Record<string, unknown>) {
    dispatch({
      type: "run/restored",
      runId: run.run_id,
      runRoot: run.root,
      configPath: run.config_path,
      finalized: run.finalized,
      canonicalConfig,
    });
    dispatch({ type: "run/stages", stages: run.stages });
  }

  function updateTask(next: TaskState | null) {
    setTask(next);
    if (!next) return;
    dispatch({
      type: "task/restored",
      taskId: next.task_id,
      status: next.status,
      runRoot: next.run_root,
      finalized: next.result?.finalized,
      error: next.error,
    });
  }

  function updateRunRoot(root: string) {
    dispatch({
      type: "run/restored",
      runId: state.activeRun.runId ?? state.draft.identity.runId,
      runRoot: root,
      configPath: state.activeRun.configPath,
      finalized: state.activeRun.finalized,
      canonicalConfig: state.activeRun.canonicalConfig,
    });
  }

  function selectExistingRun(root: string) {
    updateRunRoot(root);
    api
      .runs(state.draft.identity.runsRoot)
      .then((response) => {
        const match = response.runs.find((run) => run.root === root);
        if (!match) return;
        api.run(match.root).then((detail) => {
          const canonical = detail.effective_config;
          restoreRun(match, canonical && typeof canonical === "object" ? canonical : undefined);
        }).catch(() => restoreRun(match));
      })
      .catch(() => dispatch({ type: "connection/set", offline: true }));
  }

  const contextStatus = useMemo(() => t(statusTranslationKey[state.lifecycle]), [state.lifecycle, t]);
  const runLabel = state.activeRun.runId ?? state.draft.identity.runId;
  return (
    <div className="workbench-shell">
      <a className="skip-link" href="#workspace-main">{t("a11y.skipToWorkspace")}</a>
      <aside className="lifecycle-rail" aria-label={t("nav.lifecycle")}>
        <div className="brand-block">
          <div className="brand-mark">
            <span className="brand-glyph" aria-hidden="true" />
            <span className="brand-name">{t("app.name")}</span>
          </div>
          <p className="brand-subtitle">{t("app.subtitle")}</p>
        </div>

        <nav className="lifecycle-nav">
          <section className="lifecycle-group" aria-labelledby="plan-navigation-title">
            <h2 className="lifecycle-group-title" id="plan-navigation-title">{t("nav.plan")}</h2>
            <ShellNavigation items={PLAN_ITEMS} />
          </section>
          <section className="lifecycle-group" aria-label={t("nav.lifecycle")}>
            <ShellNavigation items={LIFECYCLE_ITEMS} />
          </section>
        </nav>

        <div className="rail-context">
          <div className="rail-context-label">{t("shell.currentRun", { runId: "" }).replace(" · ", "")}</div>
          <div className="rail-context-run mono" title={runLabel}>{runLabel || t("shell.noActiveRun")}</div>
          <div className="rail-service">
            <span className="service-light" data-online={!state.offline} aria-hidden="true" />
            <span>{state.offline ? t("shell.offline") : t("shell.online")}</span>
          </div>
          {health && <small className="rail-diagnostic mono" title={health.repo_root}>{t("shell.service", { version: health.version })}</small>}
        </div>
      </aside>

      <main className="workbench-main">
        <header className="context-bar">
          <div className="context-title">
            <h1>{t(pageCopy.title)}</h1>
            <p>{t(pageCopy.description)}</p>
          </div>
          <div className="context-run">
            <span className="context-run-id mono" title={runLabel}>{runLabel}</span>
            <span className="context-mode">{t(`simulation.mode.${state.draft.simulation.mode}`)}</span>
            <span>{contextStatus}</span>
            <span>{t("shell.contract", { version: protocol?.contract_version ?? "—" })}</span>
          </div>
          <div className="context-tools">
            <label className="sr-only" htmlFor="locale-select">{t("shell.language")}</label>
            <select
              id="locale-select"
              className="compact-select"
              value={locale}
              onChange={(event) => setLocale(event.target.value as Locale)}
            >
              <option value="en">EN</option>
              <option value="zh">中文</option>
              <option value="fr">FR</option>
            </select>
            <label className="sr-only" htmlFor="theme-select">{t("shell.theme")}</label>
            <select
              id="theme-select"
              className="compact-select"
              value={themePreference}
              onChange={(event) => setThemePreference(event.target.value as "system" | "light" | "dark")}
            >
              <option value="system">{t("theme.system")}</option>
              <option value="light">{t("theme.light")}</option>
              <option value="dark">{t("theme.dark")}</option>
            </select>
          </div>
        </header>

        <div className="workspace-scroll" id="workspace-main" tabIndex={-1} data-workspace={state.view}>
          {state.offline && (
            <div className="banner err" role="alert">
              {t("error.serviceUnavailable", { command: "python webui/server/app.py" })}
              <button type="button" onClick={onRetry}>{t("action.retry")}</button>
            </div>
          )}
          {!state.offline && bootstrapError && (
            <div className="banner warn" role="status">
              <span>{t("error.partialResources")}</span>
              <details><summary>{t("common.details")}</summary><pre className="code mono">{bootstrapError}</pre></details>
              <button type="button" onClick={onRetry}>{t("action.retry")}</button>
            </div>
          )}
          {state.rawError && (
            <ErrorNotice
              error={new Error(state.rawError)}
              onRetry={() => {
                dispatch({ type: "error/set-raw", error: null });
                onRetry();
              }}
            />
          )}
          {state.view === "protocol" && <NewDataset protocol={protocol} defaults={defaults} />}
          {state.view === "phantom" && <PhantomDesign />}
          {state.view === "simulation" && <Simulation />}
          {state.view === "run" && (
            <RunCenter
              protocol={protocol}
              configPath={state.activeRun.configPath}
              runRoot={state.activeRun.runRoot}
              setRunRoot={updateRunRoot}
              task={task}
              setTask={updateTask}
            />
          )}
          {state.view === "review" && (
            <QCDataset protocol={protocol} runRoot={state.activeRun.runRoot} setRunRoot={selectExistingRun} />
          )}
          {state.view === "seal" && <SealDataset />}
        </div>

        <footer className="command-shelf command-shelf-placeholder" aria-hidden="true" />
      </main>
    </div>
  );
}

export default function App() {
  const [protocol, setProtocol] = useState<Protocol | null>(null);
  const [defaults, setDefaults] = useState<Record<string, unknown> | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [reconcileKey, setReconcileKey] = useState(0);

  async function loadBootstrap() {
    const results = await Promise.allSettled([api.protocol(), api.defaults(), api.health()]);
    const [protocolResult, defaultsResult, healthResult] = results;
    if (protocolResult.status === "fulfilled") setProtocol(protocolResult.value);
    if (defaultsResult.status === "fulfilled") setDefaults(defaultsResult.value);
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    const failures = results.flatMap((result) => result.status === "rejected" ? [String(result.reason)] : []);
    setBootstrapError(failures.length ? failures.join("\n") : null);
    setOffline(results.every((result) => result.status === "rejected"));
    setReconcileKey((current) => current + 1);
  }

  useEffect(() => {
    void loadBootstrap();
  }, []);

  return (
    <WorkspaceProvider defaults={defaults}>
      <AppShell
        protocol={protocol}
        defaults={defaults}
        health={health}
        offline={offline}
        bootstrapError={bootstrapError}
        reconcileKey={reconcileKey}
        onRetry={() => void loadBootstrap()}
      />
    </WorkspaceProvider>
  );
}
