import { useEffect, useState } from "react";
import { api, type Protocol } from "../api";
import { Card, Field, KV } from "../ui";

export default function Simulation({
  defaults,
  protocol,
}: {
  defaults: Record<string, any> | null;
  protocol: Protocol | null;
}) {
  const [exe, setExe] = useState(defaults?.simind_exe ?? "simind/simind.exe");
  const [smc, setSmc] = useState(defaults?.smc_file ?? "simind/ge870_czt.smc");
  const [nn, setNn] = useState<number>(defaults?.nn_multiplier ?? 10);
  const [workers, setWorkers] = useState<number>(defaults?.max_simind_workers ?? 1);
  const [obs, setObs] = useState(true);
  const [check, setCheck] = useState<Record<string, { valid: boolean; detail: string }>>({});

  useEffect(() => {
    if (defaults) {
      setExe(defaults.simind_exe);
      setSmc(defaults.smc_file);
      setNn(defaults.nn_multiplier);
      setWorkers(defaults.max_simind_workers);
    }
  }, [defaults]);

  useEffect(() => {
    let live = true;
    Promise.all([api.fsValidate(exe, "simind_exe"), api.fsValidate(smc, "smc")])
      .then(([a, b]) => live && setCheck({ exe: a, smc: b }))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [exe, smc]);

  const mark = (k: string) =>
    check[k] ? (
      check[k].valid ? (
        <span style={{ color: "var(--ok)" }}>found</span>
      ) : (
        <span style={{ color: "var(--err)" }}>{check[k].detail || "not found"}</span>
      )
    ) : null;

  return (
    <>
      <Card title="Executable and inputs" note="checksummed at plan time">
        <Field label="SIMIND executable" hint={mark("exe")}>
          <div className="with-btn">
            <input type="text" className="mono" value={exe} onChange={(e) => setExe(e.target.value)} />
            <button>Browse…</button>
          </div>
        </Field>
        <Field label="SMC change file" hint={mark("smc")}>
          <div className="with-btn">
            <input type="text" className="mono" value={smc} onChange={(e) => setSmc(e.target.value)} />
            <button>Browse…</button>
          </div>
        </Field>
      </Card>

      <div className="grid cols-2">
        <Card title="Photon transport">
          <Field label="History multiplier" hint="/NN — a photon-history multiplier, not an acquisition-time control.">
            <input
              type="number"
              className="mono"
              style={{ width: 110 }}
              value={nn}
              min={1}
              onChange={(e) => setNn(Number(e.target.value))}
            />
          </Field>
          <Field label="Parallel workers" hint="Independent cases only; unique staging and output stems per worker.">
            <input
              type="number"
              className="mono"
              style={{ width: 110 }}
              value={workers}
              min={1}
              max={32}
              onChange={(e) => setWorkers(Number(e.target.value))}
            />
          </Field>
          <KV k="Photon energy · window" v="140 keV · 126–154 keV" />
          <KV k="Attenuation" v="type −7 · μ × voxel (validated)" />
          <KV k="Per-case seed" v="deterministic /RR (terminal switch)" />
        </Card>

        <Card title="Expectation and observation">
          <div className="actions" style={{ marginBottom: 10 }}>
            <button className={!obs ? "primary" : ""} onClick={() => setObs(false)}>
              Expectation only
            </button>
            <button className={obs ? "primary" : ""} onClick={() => setObs(true)}>
              Expectation + observation
            </button>
          </div>
          <p style={{ color: "var(--tx-2)", fontSize: 12.5, marginBottom: 10 }}>
            The <code>.a00</code> output is a variance-reduced weighted Monte Carlo <b>expectation</b>, not a
            Poisson observation. When an observation layer is requested it is written to a separate directory by
            seeded Poisson sampling, so noise can be re-derived without re-running Monte Carlo, and the
            expectation is never overwritten.
          </p>
          <KV k="Observation policy" v="empirical total counts" />
          <KV
            k="Reference totals"
            v={
              protocol
                ? `${(Math.min(...protocol.empirical_clinical_total_counts) / 1e6).toFixed(2)}–${(
                    Math.max(...protocol.empirical_clinical_total_counts) / 1e6
                  ).toFixed(2)} M`
                : "—"
            }
          />
          <KV
            k="Angular CV gate"
            v={
              protocol
                ? `${protocol.empirical_clinical_angular_cv_range[0].toFixed(3)}–${protocol.empirical_clinical_angular_cv_range[1].toFixed(3)}`
                : "—"
            }
          />
          <KV k="Split inheritance" v="observation inherits parent phantom split" />
        </Card>
      </div>

      <details className="expert">
        <summary>
          Expert settings <span className="muted">raw SIMIND index and flag values</span>
        </summary>
        <div className="inner">
          <table>
            <thead>
              <tr>
                <th>switch</th>
                <th>value</th>
                <th>meaning</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="mono">/25</td>
                <td className="mono">{protocol?.simind_activity_time_index25 ?? "—"}</td>
                <td>activity × time product per projection</td>
              </tr>
              <tr>
                <td className="mono">100 / 101</td>
                <td className="mono">
                  {protocol ? `${protocol.detector_matrix[0]} / ${protocol.detector_matrix[1]}` : "—"}
                </td>
                <td>native detector matrix</td>
              </tr>
              <tr>
                <td className="mono">14 / 15</td>
                <td className="mono">−7 / −7</td>
                <td>XcatBinMap float32 source and density</td>
              </tr>
              <tr>
                <td className="mono">/IN:x21</td>
                <td className="mono">{defaults?.type7_density_threshold_times_1000 ?? "—"}</td>
                <td>density threshold ×1000 (type-7 correction)</td>
              </tr>
              <tr>
                <td className="mono">cross-sections</td>
                <td className="mono">{(defaults?.phantom_cross_sections ?? []).join(" / ") || "—"}</td>
                <td>phantom media tables</td>
              </tr>
              <tr>
                <td className="mono">/NN · /RR</td>
                <td className="mono">
                  {nn} · {defaults?.simind_seed_base ?? "—"}+i
                </td>
                <td>history multiplier · per-case seed base</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>

      <div className="muted" style={{ marginTop: 12 }}>
        Canonical projection transform: <code>{protocol?.canonical_projection_transform ?? "—"}</code> ·
        contract v{protocol?.contract_version ?? "—"}
      </div>
    </>
  );
}
