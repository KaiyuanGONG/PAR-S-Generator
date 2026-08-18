import { useEffect, useState } from "react";
import { api, type Protocol } from "../api";
import { Card, Field, KV } from "../ui";

export default function NewDataset({
  protocol,
  runsRoot,
  setRunsRoot,
  onCreated,
}: {
  protocol: Protocol | null;
  runsRoot: string;
  setRunsRoot: (v: string) => void;
  onCreated: (configPath: string, runId: string) => void;
}) {
  const [runId, setRunId] = useState("liver-spect-run");
  const [cases, setCases] = useState(10);
  const [mode, setMode] = useState("prepare");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [rootOk, setRootOk] = useState<boolean | null>(null);

  useEffect(() => {
    let live = true;
    api
      .fsValidate(runsRoot, "runs_root")
      .then((r) => live && setRootOk(r.valid))
      .catch(() => live && setRootOk(null));
    return () => {
      live = false;
    };
  }, [runsRoot]);

  async function create() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.createRun({ run_id: runId, runs_root: runsRoot, cases, mode });
      setMsg({ tone: "ok", text: `Configuration written to ${r.config_path}. Nothing has executed yet.` });
      onCreated(r.config_path, runId);
    } catch (e: any) {
      setMsg({ tone: "err", text: String(e.message ?? e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {msg && <div className={"banner " + msg.tone}>{msg.text}</div>}

      <div className="split">
        <div>
          <Card title="Run identity" note={`${cases} case${cases === 1 ? "" : "s"}`}>
            <Field label="Run ID">
              <input
                type="text"
                className="mono"
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
              />
            </Field>
            <Field
              label="Runs root"
              hint={
                rootOk === false ? (
                  <span style={{ color: "var(--err)" }}>not writable or not creatable</span>
                ) : rootOk ? (
                  "writable"
                ) : null
              }
            >
              <input
                type="text"
                className="mono"
                value={runsRoot}
                onChange={(e) => setRunsRoot(e.target.value)}
              />
            </Field>
            <Field label="Cases">
              <input
                type="number"
                min={1}
                className="mono"
                style={{ width: 110 }}
                value={cases}
                onChange={(e) => setCases(Math.max(1, Number(e.target.value)))}
              />
            </Field>
            <Field
              label="Mode"
              hint={
                mode === "execute"
                  ? "Real SIMIND execution — still requires an explicit confirmation in Run center."
                  : mode === "mock"
                  ? "Software smoke test. Projection physics are explicitly fake."
                  : "Writes exact SIMIND jobs without executing them."
              }
            >
              <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ width: 300 }}>
                <option value="prepare">prepare — write SIMIND jobs, do not execute</option>
                <option value="mock">mock — software smoke test, fake physics</option>
                <option value="execute">execute — run SIMIND</option>
              </select>
            </Field>
          </Card>

          <Card title="Directory to be created" note="run-isolated; never globbed across runs">
            <pre className="tree mono">{`${runsRoot.replace(/[\\/]+$/, "")}/${runId}/
├── run.json              effective config + stage evidence
├── cases.jsonl           per-case provenance and QC
├── splits.json           fixed phantom-level partition
├── dataset_manifest.json sha-256 inventory (immutable once final)
├── phantom/  simind_input/  expectation/
├── observation/  qc/  logs/  figures/`}</pre>
          </Card>
        </div>

        <div>
          <Card title="Protocol contract" note="locked into run.json on create">
            <div className="bignums" style={{ margin: "-13px -14px 12px", borderTop: 0 }}>
              <div>
                <b>{protocol?.source_activity_mbq ?? "—"}</b>
                <span>MBq source activity</span>
              </div>
              <div>
                <b>{protocol?.exposure_s_per_projection ?? "—"}</b>
                <span>s per projection</span>
              </div>
              <div>
                <b>{cases}</b>
                <span>cases</span>
              </div>
            </div>
            <KV k="Isotope · energy" v="Tc-99m · 140.5 keV" />
            <KV k="Views" v="60 over 360°" />
            <KV k="Projection matrix" v="128 × 128 · 4.42 mm" />
            <KV
              k="Detector matrix"
              v={protocol ? `${protocol.detector_matrix[0]} × ${protocol.detector_matrix[1]}` : "—"}
            />
            <KV k="SMC Index-25" v={protocol?.simind_activity_time_index25 ?? "—"} />
            <KV k="Split" v="80 / 10 / 10 · seed 42" />
          </Card>

          <Card title="Reproducibility">
            <ul style={{ paddingLeft: 16, color: "var(--tx-2)", fontSize: 12.5, lineHeight: 1.7 }}>
              <li>Per-case seeds derive from the run seed and are recorded in <code>cases.jsonl</code>.</li>
              <li>SIMIND binary and SMC file are checksummed at plan time.</li>
              <li>Resume accepts an artifact only when its hash and stage checks pass.</li>
              <li>A finalized manifest is immutable.</li>
            </ul>
            <div className="muted" style={{ marginTop: 10 }}>
              Contract status: {protocol?.activity_time_contract_status ?? "—"}
            </div>
          </Card>
        </div>
      </div>

      <div className="actions">
        <button className="primary" onClick={create} disabled={busy || !runId}>
          {busy ? "Creating…" : "Create dataset"}
        </button>
        <span className="muted">Writes an editable run configuration — no simulation starts.</span>
      </div>
    </>
  );
}
