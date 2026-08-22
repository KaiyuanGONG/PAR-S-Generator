import { useEffect, useState } from "react";
import { api, type Protocol } from "../api";
import ErrorNotice from "../components/ErrorNotice";
import FileBrowser from "../components/FileBrowser";
import { useI18n } from "../i18n";
import { useWorkspace } from "../workspace";

export default function NewDataset({ protocol, defaults }: { protocol: Protocol | null; defaults: Record<string, unknown> | null }) {
  const { state, dispatch } = useWorkspace();
  const { t } = useI18n();
  const { runId, runsRoot, cohortMode, positiveCases, negativeCases } = state.draft.identity;
  const cases = positiveCases + negativeCases;
  const draft = state.draft.protocol;
  const locked = state.activeRun.locked;
  const advanced = false;
  const activity = Number(draft.source_activity_mbq ?? protocol?.source_activity_mbq ?? 60);
  const exposure = Number(draft.exposure_time_s_per_projection ?? protocol?.exposure_s_per_projection ?? 28.4);
  const index25 = Number(draft.smc_index25_activity_time ?? protocol?.simind_activity_time_index25 ?? 1704);
  const contractMatches = Number.isFinite(activity) && Number.isFinite(exposure) && Number.isFinite(index25)
    && Math.abs(activity * exposure - index25) <= Math.max(0.001, Math.abs(index25) * 1e-6);
  const identityValid = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(runId);
  const [rootState, setRootState] = useState<"checking" | "valid" | "invalid">("checking");
  const [rootDetail, setRootDetail] = useState("");
  const [rootError, setRootError] = useState<unknown>(null);
  const [browserOpen, setBrowserOpen] = useState(false);

  useEffect(() => {
    let live = true;
    setRootState("checking");
    api.fsValidate(runsRoot, "runs_root")
      .then((result) => {
        if (!live) return;
        setRootState(result.valid ? "valid" : "invalid");
        setRootDetail(result.detail);
        setRootError(null);
      })
      .catch((caught) => {
        if (!live) return;
        setRootState("invalid");
        setRootDetail("");
        setRootError(caught);
      });
    return () => { live = false; };
  }, [runsRoot]);

  const cohortValid = (
    cohortMode === "positive_only" && positiveCases >= 1 && negativeCases === 0
  ) || (
    cohortMode === "true_negative_only" && positiveCases === 0 && negativeCases >= 1
  ) || (
    cohortMode === "mixed" && positiveCases >= 1 && negativeCases >= 1
  );
  const ready = identityValid && rootState === "valid" && contractMatches && cohortValid;
  const estimatedGiB = (cases * 0.075).toFixed(2);

  function setCohortMode(mode: typeof cohortMode) {
    const positive = mode === "true_negative_only" ? 0 : Math.max(1, positiveCases);
    const negative = mode === "positive_only" ? 0 : Math.max(1, negativeCases);
    dispatch({ type: "draft/identity", patch: { cohortMode: mode, positiveCases: positive, negativeCases: negative, cases: positive + negative } });
  }
  useEffect(() => {
    if (!locked) dispatch({ type: "plan/section", section: "protocol", status: ready ? "ready" : "incomplete" });
  }, [dispatch, locked, ready]);

  const patchProtocol = (patch: Record<string, string | number | boolean | undefined>) => {
    dispatch({ type: "draft/protocol", patch });
  };

  function resetPlan() {
    if (window.confirm(t("protocol.resetConfirm"))) {
      dispatch({ type: "workspace/reset", defaults: defaults ?? undefined });
    }
  }

  return (
    <div className="protocol-workspace">
      {locked && <div className="banner ok" role="status">{t("protocol.locked")}</div>}
      {rootError != null && <ErrorNotice error={rootError} />}

      <section className="protocol-identity" aria-labelledby="protocol-identity-title">
        <header className="workspace-section-head">
          <div><span className="run-eyebrow">01</span><h2 id="protocol-identity-title">{t("protocol.identity")}</h2></div>
          <span className="section-state" data-state={identityValid && rootState === "valid" ? "passed" : "failed"}>{identityValid && rootState === "valid" ? t("status.ready") : t("status.incomplete")}</span>
        </header>
        <div className="form-grid">
          <label className="stacked-field" htmlFor="protocol-run-id">{t("protocol.runId")}
            <input id="protocol-run-id" type="text" className="mono" value={runId} disabled={locked} aria-invalid={!identityValid} onChange={(event) => dispatch({ type: "draft/identity", patch: { runId: event.target.value } })} />
            <small>{identityValid ? t("protocol.runIdHelp") : t("protocol.runIdInvalid")}</small>
          </label>
          <label className="stacked-field" htmlFor="protocol-runs-root">{t("protocol.runsRoot")}
            <span className="field-with-action"><input id="protocol-runs-root" type="text" className="mono" value={runsRoot} disabled={locked} aria-invalid={rootState === "invalid"} onChange={(event) => dispatch({ type: "draft/identity", patch: { runsRoot: event.target.value } })} /><button type="button" disabled={locked} onClick={() => setBrowserOpen(true)}>{t("action.browse")}</button></span>
            <small data-tone={rootState === "invalid" ? "danger" : undefined}>{rootState === "checking" ? t("common.loading") : rootState === "valid" ? t("protocol.rootReady") : rootDetail}</small>
          </label>
          <label className="stacked-field" htmlFor="protocol-cohort-mode">{t("protocol.cohortMode")}
            <select id="protocol-cohort-mode" value={cohortMode} disabled={locked} onChange={(event) => setCohortMode(event.target.value as typeof cohortMode)}>
              <option value="positive_only">{t("protocol.positiveOnly")}</option>
              <option value="true_negative_only">{t("protocol.negativeOnly")}</option>
              <option value="mixed">{t("protocol.mixed")}</option>
            </select>
          </label>
          {cohortMode !== "true_negative_only" && <label className="stacked-field" htmlFor="protocol-positive-cases">{t("protocol.positiveCases")}
            <input id="protocol-positive-cases" type="number" min={1} className="mono" value={positiveCases} disabled={locked} onChange={(event) => { const value = Math.max(1, Math.floor(Number(event.target.value))); dispatch({ type: "draft/identity", patch: { positiveCases: value, cases: value + negativeCases } }); }} />
          </label>}
          {cohortMode !== "positive_only" && <label className="stacked-field" htmlFor="protocol-negative-cases">{t("protocol.negativeCases")}
            <input id="protocol-negative-cases" type="number" min={1} className="mono" value={negativeCases} disabled={locked} onChange={(event) => { const value = Math.max(1, Math.floor(Number(event.target.value))); dispatch({ type: "draft/identity", patch: { negativeCases: value, cases: positiveCases + value } }); }} />
          </label>
          }
        </div>
        <p className="parameter-note">{t("protocol.workEstimate", { count: cases, space: estimatedGiB })}</p>
      </section>

      <section className="protocol-contract" aria-labelledby="protocol-contract-title">
        <header className="workspace-section-head">
          <div><span className="run-eyebrow">02</span><h2 id="protocol-contract-title">{t("protocol.contract")}</h2></div>
          <span className="section-state" data-state={contractMatches ? "passed" : "failed"}>{contractMatches ? t("protocol.validatedPreset") : t("status.failed")}</span>
        </header>
        <div className="contract-intro">
          <div><strong>{String(draft.protocol_label ?? "GE 870 CZT current liver SPECT research protocol")}</strong><span className="mono">{String(draft.protocol_status ?? "stage3_protocol_promoted_pilot_pending")}</span></div>
          <span className="section-state" data-state="passed">windows_v1 · hybrid_v2_limited_activity_v1</span>
        </div>
        <div className="contract-grid">
          <label className="contract-value">{t("protocol.activity")}<span><input type="number" value={activity} disabled={!advanced || locked} onChange={(event) => patchProtocol({ source_activity_mbq: Number(event.target.value) })} /><i>MBq</i></span></label>
          <span className="contract-operator" aria-hidden="true">×</span>
          <label className="contract-value">{t("protocol.exposure")}<span><input type="number" value={exposure} disabled={!advanced || locked} onChange={(event) => patchProtocol({ exposure_time_s_per_projection: Number(event.target.value) })} /><i>s/view</i></span></label>
          <span className="contract-operator" aria-hidden="true">=</span>
          <label className="contract-value">SMC Index-25<span><input type="number" value={index25} disabled={!advanced || locked} onChange={(event) => patchProtocol({ smc_index25_activity_time: Number(event.target.value) })} /><i>MBq·s</i></span></label>
        </div>
        <div className="contract-check" data-state={contractMatches ? "passed" : "failed"}>
          <strong>{contractMatches ? t("protocol.productMatches") : t("protocol.productMismatch")}</strong>
          <span className="mono">{activity} × {exposure} = {(activity * exposure).toFixed(3)} · {t("protocol.expected")} {index25.toFixed(3)}</span>
        </div>
        <dl className="contract-facts">
          <div><dt>{t("protocol.isotopeEnergy")}</dt><dd>Tc-99m · 140.5 keV</dd></div>
          <div><dt>{t("protocol.views")}</dt><dd>60 · 360°</dd></div>
          <div><dt>{t("protocol.projectionMatrix")}</dt><dd>128 × 128 · 4.42 mm</dd></div>
          <div><dt>{t("protocol.detectorMatrix")}</dt><dd>{protocol ? `${protocol.detector_matrix[0]} × ${protocol.detector_matrix[1]}` : "—"}</dd></div>
          <div><dt>{t("protocol.split")}</dt><dd>80 / 10 / 10 · seed 42</dd></div>
          <div><dt>{t("common.status")}</dt><dd className="mono">{String(draft.activity_time_contract_status ?? protocol?.activity_time_contract_status ?? "—")}</dd></div>
        </dl>
      </section>

      <section className="protocol-output" aria-labelledby="protocol-output-title">
        <header className="workspace-section-head"><div><span className="run-eyebrow">03</span><h2 id="protocol-output-title">{t("protocol.outputLayout")}</h2></div><span>{t("protocol.runIsolated")}</span></header>
        <pre className="tree mono">{`${runsRoot.replace(/[\\/]+$/, "")}/${runId}/
├── run.json              effective config + stage evidence
├── cases.jsonl           per-case provenance and QC
├── splits.json           fixed phantom-level partition
├── dataset_manifest.json sha-256 inventory
├── phantom/  simind_input/  expectation/
└── observation/  qc/  logs/  figures/`}</pre>
        <ul className="plain-evidence-list"><li>{t("protocol.seedEvidence")}</li><li>{t("protocol.inputHashes")}</li><li>{t("protocol.resumeEvidence")}</li><li>{t("protocol.immutableAfterSeal")}</li></ul>
      </section>

      <footer className="page-command-shelf protocol-command-shelf">
        <span className="command-signal" data-tone={ready ? "success" : "danger"} />
        <div className="command-copy"><strong>{ready ? t("protocol.planReady") : t("protocol.planIncomplete")}</strong><span>{locked ? t("shell.configLocked") : t("protocol.continueHelp")}</span></div>
        <span className="command-meta mono">{t("common.casesShort", { count: cases })} · {runsRoot}</span>
        <div className="command-actions">{locked ? <button type="button" onClick={() => dispatch({ type: "run/fork", runId: `${runId}-fork` })}>{t("action.fork")}</button> : <><button type="button" onClick={resetPlan}>{t("action.resetPlan")}</button><button type="button" className="primary" disabled={!ready} onClick={() => { dispatch({ type: "plan/section", section: "protocol", status: "ready" }); dispatch({ type: "view/set", view: "phantom" }); }}>{t("action.continuePhantom")}</button></>}</div>
      </footer>

      <FileBrowser open={browserOpen} title={t("protocol.chooseRunsRoot")} initialPath={runsRoot} selection="directory" nativeKind="runs_root" onSelect={(path) => dispatch({ type: "draft/identity", patch: { runsRoot: path } })} onClose={() => setBrowserOpen(false)} />
    </div>
  );
}
