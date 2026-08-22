import { useEffect, useState } from "react";
import { ApiError, api, type PreflightResult } from "../api";
import ErrorNotice from "../components/ErrorNotice";
import FileBrowser from "../components/FileBrowser";
import { useI18n, type TranslationKey } from "../i18n";
import { toCreateRunRequest, useWorkspace, type ObservationDraft, type RunMode } from "../workspace";

export const EXPECTATION_ONLY_OBSERVATION = {
  create_poisson_observation: false,
  observation_policy: "fixed_scale",
  observation_protocol_status: "toy",
} as const satisfies Partial<ObservationDraft>;

export const EMPIRICAL_OBSERVATION = {
  create_poisson_observation: true,
  observation_policy: "empirical_total_counts",
  observation_protocol_status: "empirical_protocol_matching",
} as const satisfies Partial<ObservationDraft>;

export function observationEnabledPatch(enabled: boolean, currentStatus: string): Partial<ObservationDraft> {
  return enabled
    ? { create_poisson_observation: true }
    : {
        create_poisson_observation: false,
        observation_policy: "fixed_scale",
        observation_protocol_status: currentStatus === "empirical_protocol_matching" ? "toy" : currentStatus,
      };
}

export function observationPolicyPatch(policy: string, currentStatus: string): Partial<ObservationDraft> {
  return policy === "empirical_total_counts"
    ? {
        create_poisson_observation: true,
        observation_policy: policy,
        observation_protocol_status: "empirical_protocol_matching",
      }
    : {
        observation_policy: "fixed_scale",
        observation_protocol_status: currentStatus === "empirical_protocol_matching" ? "toy" : currentStatus,
      };
}

type BrowserTarget = "exe" | "smc" | "experiments" | null;

const PREFLIGHT_LABELS: Record<string, TranslationKey> = {
  simind_executable: "simulation.check.simind_executable",
  smc_file: "simulation.check.smc_file",
  type7_source_density: "simulation.check.type7_source_density",
  phantom_interactions: "simulation.check.phantom_interactions",
  density_sampling: "simulation.check.density_sampling",
  density_shape: "simulation.check.density_shape",
  cross_sections: "simulation.check.cross_sections",
  activity_time: "simulation.check.activity_time",
  detector_request: "simulation.check.detector_request",
  smc_parse: "simulation.check.smc_parse",
};

export default function Simulation() {
  const { state, dispatch } = useWorkspace();
  const { t } = useI18n();
  const locked = state.activeRun.locked;
  const simulation = state.draft.simulation;
  const exe = String(simulation.simind_exe ?? "simind/simind.exe");
  const smc = String(simulation.smc_file ?? "simind/ge870_czt.smc");
  const nn = Number(simulation.nn_multiplier ?? 10);
  const workers = Number(simulation.max_simind_workers ?? 1);
  const [browser, setBrowser] = useState<BrowserTarget>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "err" | "warn"; text: string; error?: unknown } | null>(null);
  const [experimentDestination, setExperimentDestination] = useState("");

  useEffect(() => {
    if (state.plan.preflightConfigDigest === null) setPreflight(null);
  }, [state.plan.preflightConfigDigest]);

  async function runPreflight() {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.preflightRun(toCreateRunRequest(state.draft));
      setPreflight(result);
      dispatch({
        type: "plan/preflight",
        configDigest: result.config_digest,
        errors: result.errors,
        warnings: result.warnings,
      });
      setMessage({ tone: result.ready ? (result.warnings.length ? "warn" : "ok") : "err", text: result.ready ? t("simulation.preflightReady") : t("simulation.preflightBlocked") });
    } catch (caught) {
      const raw = caught instanceof ApiError ? caught.detail : caught instanceof Error ? caught.message : String(caught);
      setMessage({ tone: "err", text: raw, error: caught });
      dispatch({ type: "plan/preflight", configDigest: null, errors: [raw] });
    } finally {
      setBusy(false);
    }
  }

  async function lockPlan() {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.createRun(toCreateRunRequest(state.draft));
      dispatch({ type: "run/created", configPath: result.config_path, canonicalConfig: result.config });
      dispatch({ type: "view/set", view: "run" });
    } catch (caught) {
      setMessage({ tone: "err", text: "", error: caught });
    } finally {
      setBusy(false);
    }
  }

  async function prepareExperiments(destination: string) {
    setExperimentDestination(destination);
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.prepareExperiments({ destination, simind_exe: exe, smc_file: smc });
      setMessage({ tone: "ok", text: t("simulation.experimentsPrepared", { count: result.prepared }) });
    } catch (caught) {
      setMessage({ tone: "err", text: "", error: caught });
    } finally {
      setBusy(false);
    }
  }

  const sectionState = state.plan.sections;
  const preflightCurrent = Boolean(preflight?.ready && state.plan.preflightConfigDigest);
  const planReady = sectionState.protocol === "ready" && sectionState.phantom === "ready" && preflightCurrent;
  const prerequisiteText = [
    sectionState.protocol === "ready" ? null : t("simulation.needProtocol"),
    sectionState.phantom === "ready" ? null : t("simulation.needPreview"),
    preflightCurrent ? null : t("simulation.needPreflight"),
  ].filter(Boolean).join(" · ");

  return (
    <div className="simulation-workspace">
      {message?.error ? <ErrorNotice error={message.error} /> : message && <div className={`banner ${message.tone}`} role="status">{message.text}</div>}

      <section className="simulation-inputs" aria-labelledby="simulation-inputs-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">01</span><h2 id="simulation-inputs-title">{t("simulation.inputs")}</h2></div><span>{t("simulation.hashAtLock")}</span></header>
        <div className="simulation-paths">
          <label className="stacked-field" htmlFor="simind-executable">{t("simulation.simindExecutable")}<span className="field-with-action"><input id="simind-executable" className="mono" value={exe} disabled={locked} onChange={(event) => dispatch({ type: "draft/simulation", patch: { simind_exe: event.target.value } })} /><button type="button" disabled={locked} onClick={() => setBrowser("exe")}>{t("action.browse")}</button></span></label>
          <label className="stacked-field" htmlFor="smc-file">{t("simulation.smcFile")}<span className="field-with-action"><input id="smc-file" className="mono" value={smc} disabled={locked} onChange={(event) => dispatch({ type: "draft/simulation", patch: { smc_file: event.target.value } })} /><button type="button" disabled={locked} onClick={() => setBrowser("smc")}>{t("action.browse")}</button></span></label>
        </div>
        <div className="mode-selector" role="radiogroup" aria-label={t("protocol.mode")}>
          {(["prepare", "mock", "execute"] as RunMode[]).map((mode) => <label key={mode} data-selected={simulation.mode === mode}><input type="radio" name="simulation-mode" value={mode} checked={simulation.mode === mode} disabled={locked} onChange={() => dispatch({ type: "draft/simulation", patch: { mode } })} /><strong>{t(`simulation.mode.${mode}`)}</strong><span>{t(`simulation.mode.${mode}.help`)}</span></label>)}
        </div>
      </section>

      <section className="simulation-transport" aria-labelledby="simulation-transport-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">02</span><h2 id="simulation-transport-title">{t("simulation.transport")}</h2></div><span className="mono">/NN · /RR</span></header>
        <div className="transport-controls">
          <label className="stacked-field">{t("simulation.historyMultiplier")}<input type="number" min={1} max={1000000} step={1} className="mono" value={nn} disabled={locked} onChange={(event) => dispatch({ type: "draft/simulation", patch: { nn_multiplier: Math.max(1, Math.min(1_000_000, Math.floor(Number(event.target.value)))) } })} /><small>{nn === 1 ? "Fast test" : nn === 10 ? "Recommended quality" : nn > 10 ? "Higher runtime cost" : t("simulation.historyHelp")}</small></label>
          <label className="stacked-field">{t("simulation.parallelWorkers")}<input type="number" min={1} max={32} className="mono" value={workers} disabled={locked} onChange={(event) => dispatch({ type: "draft/simulation", patch: { max_simind_workers: Math.max(1, Math.min(32, Number(event.target.value))) } })} /><small>{t("simulation.workerHelp")}</small></label>
          <div className="stacked-field"><strong>/RR seed</strong><span className="mono">domain-isolated per case</span><small>Derived from the Windows v1 global seed and recorded in the manifest.</small></div>
        </div>
        <dl className="transport-facts"><div><dt>{t("simulation.energyWindow")}</dt><dd>{preflight?.smc ? `${preflight.smc.energy_kev} keV · ${preflight.smc.window_kev.join("–")} keV` : "—"}</dd></div><div><dt>{t("simulation.acquisition")}</dt><dd>{preflight?.smc ? `${preflight.smc.views} ${t("protocol.views")} · ${preflight.smc.rotation_radius_cm} cm` : "—"}</dd></div><div><dt>{t("simulation.attenuation")}</dt><dd>type −7 · μ × voxel</dd></div></dl>
      </section>

      <section className="simulation-observation" aria-labelledby="simulation-observation-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">03</span><h2 id="simulation-observation-title">{t("simulation.observation")}</h2></div><span>{t("simulation.separateLayers")}</span></header>
        <p className="observation-explainer">Windows v1 fixes the expectation/observation separation and empirical-count observation policy. It is recorded in the effective configuration and cannot be overridden by a production draft.</p>
      </section>

      <section className="simulation-preflight" aria-labelledby="simulation-preflight-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">04</span><h2 id="simulation-preflight-title">{t("simulation.preflight")}</h2></div><span className="section-state" data-state={preflight?.ready ? "passed" : "failed"}>{preflight?.ready ? t("status.ready") : t("status.pending")}</span></header>
        {preflight ? <div className="preflight-checks">{preflight.checks.map((check) => <div key={check.id} data-state={check.status}><span aria-hidden="true">{check.status === "passed" ? "✓" : check.status === "warning" ? "!" : "×"}</span><strong>{PREFLIGHT_LABELS[check.id] ? t(PREFLIGHT_LABELS[check.id]) : check.id.replaceAll("_", " ")}</strong><p>{check.detail}</p></div>)}</div> : <div className="preflight-empty"><strong>{t("simulation.preflightNotRun")}</strong><p>{t("simulation.preflightHelp")}</p></div>}
        {preflight && <dl className="transport-facts"><div><dt>Runtime status</dt><dd className="mono">{preflight.provenance.windows_runtime.status}</dd></div><div><dt>SIMIND SHA-256</dt><dd className="mono" title={preflight.provenance.windows_runtime.simind_sha256 ?? undefined}>{preflight.provenance.windows_runtime.simind_sha256?.slice(0, 16) ?? "—"}…</dd></div><div><dt>SMC SHA-256</dt><dd className="mono" title={preflight.provenance.windows_runtime.smc_sha256 ?? undefined}>{preflight.provenance.windows_runtime.smc_sha256?.slice(0, 16) ?? "—"}…</dd></div></dl>}
        {preflight?.smc && <details className="expert-disclosure simulation-expert"><summary>{t("simulation.expert")}</summary><div className="expert-body"><table><thead><tr><th>{t("simulation.switch")}</th><th>{t("simulation.value")}</th><th>{t("common.details")}</th></tr></thead><tbody>{Object.entries(preflight.smc.raw_indices).map(([index, value]) => <tr key={index}><td className="mono">/{index}</td><td className="mono">{value}</td><td>{index === "25" ? t("simulation.activityTime") : index === "100" || index === "101" ? t("simulation.detectorRequest") : t("simulation.rawValue")}</td></tr>)}</tbody></table><p className="parameter-note mono">{t("simulation.flags")}: {preflight.smc.enabled_flags.join(", ")}</p><button type="button" onClick={() => setBrowser("experiments")} disabled={locked || busy}>{t("simulation.prepareExperiments")}</button>{experimentDestination && <p className="parameter-note mono">{experimentDestination}</p>}</div></details>}
      </section>

      <footer className="page-command-shelf simulation-command-shelf">
        <span className="command-signal" data-tone={locked || planReady ? "success" : preflight?.errors.length ? "danger" : "warning"} />
        <div className="command-copy"><strong>{locked ? t("shell.configLocked") : planReady ? t("simulation.planReady") : t("simulation.planIncomplete")}</strong><span>{locked ? state.activeRun.configPath : prerequisiteText}</span></div>
        <span className="command-meta mono">{t(`simulation.mode.${simulation.mode}`)} · {state.draft.identity.cases} {t("common.case")} · /NN {nn}</span>
        <div className="command-actions">{locked ? <button type="button" onClick={() => dispatch({ type: "run/fork", runId: `${state.draft.identity.runId}-fork` })}>{t("action.fork")}</button> : <><button type="button" onClick={() => void runPreflight()} disabled={busy}>{busy ? t("common.loading") : t("simulation.runPreflight")}</button><button type="button" className="primary" onClick={() => void lockPlan()} disabled={busy || !planReady}>{t("simulation.lockPlan")}</button></>}</div>
      </footer>

      <FileBrowser open={browser === "exe"} title={t("simulation.chooseExecutable")} initialPath={exe} selection="file" extensions={[".exe"]} nativeKind="simind_exe" onSelect={(path) => dispatch({ type: "draft/simulation", patch: { simind_exe: path } })} onClose={() => setBrowser(null)} />
      <FileBrowser open={browser === "smc"} title={t("simulation.chooseSmc")} initialPath={smc} selection="file" extensions={[".smc"]} nativeKind="smc" onSelect={(path) => dispatch({ type: "draft/simulation", patch: { smc_file: path } })} onClose={() => setBrowser(null)} />
      <FileBrowser open={browser === "experiments"} title={t("simulation.chooseExperimentDestination")} initialPath={state.draft.identity.runsRoot} selection="directory" nativeKind="export_root" onSelect={(path) => void prepareExperiments(path)} onClose={() => setBrowser(null)} />
    </div>
  );
}
