import { useEffect, useState } from "react";
import "./theme.css";
import { api, type Protocol, type TaskState } from "./api";
import NewDataset from "./pages/NewDataset";
import PhantomDesign from "./pages/PhantomDesign";
import Simulation from "./pages/Simulation";
import RunCenter from "./pages/RunCenter";
import QCDataset from "./pages/QCDataset";

const WORKSPACES = [
  { n: "1", key: "new", label: "New dataset", desc: "Name one immutable run and lock its protocol contract." },
  { n: "2", key: "phantom", label: "Phantom design", desc: "Set the cohort sampling parameters and inspect one draw." },
  { n: "3", key: "sim", label: "Simulation", desc: "Review the exact SIMIND inputs and the expectation/observation policy." },
  { n: "4", key: "run", label: "Run center", desc: "The only place where anything executes." },
  { n: "5", key: "qc", label: "QC & dataset", desc: "Stage gates, case ledger, projections and the immutable manifest." },
];

export default function App() {
  const [page, setPage] = useState("new");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [protocol, setProtocol] = useState<Protocol | null>(null);
  const [defaults, setDefaults] = useState<Record<string, any> | null>(null);
  const [health, setHealth] = useState<{ version: string; repo_root: string } | null>(null);
  const [runsRoot, setRunsRoot] = useState("runs");
  const [configPath, setConfigPath] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [runRoot, setRunRoot] = useState<string | null>(null);
  const [task, setTask] = useState<TaskState | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    Promise.all([api.protocol(), api.defaults(), api.health()])
      .then(([p, d, h]) => {
        setProtocol(p);
        setDefaults(d);
        setHealth(h);
        if (d?.runs_root) setRunsRoot(d.runs_root);
      })
      .catch(() => setOffline(true));
  }, []);

  const ws = WORKSPACES.find((w) => w.key === page)!;

  return (
    <div className="shell">
      <aside className="rail">
        <div className="rail-head">
          <b>PAR-S Generator</b>
          <span>Synthetic liver SPECT datasets</span>
        </div>
        <div className="rail-label">Workspaces</div>
        <nav>
          {WORKSPACES.map((w) => (
            <button
              key={w.key}
              className="rail-item"
              aria-current={page === w.key}
              onClick={() => setPage(w.key)}
            >
              <span className="n">{w.n}</span>
              {w.label}
              {w.key === "run" && task?.status === "running" && <span className="badge">running</span>}
              {w.key === "new" && configPath && <span className="badge">ready</span>}
            </button>
          ))}
        </nav>
        <div className="rail-foot">
          {runId ? (
            <>
              <div style={{ color: "var(--tx-2)" }}>Active run</div>
              <div className="mono">{runId}</div>
            </>
          ) : (
            <div>no active run</div>
          )}
          <div className="mono" style={{ marginTop: 6 }}>
            service {health?.version ?? "—"} · contract v{protocol?.contract_version ?? "—"}
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <span className="step">0{ws.n}</span>
          <h1>{ws.label}</h1>
          <span className="desc">{ws.desc}</span>
          <div className="right">
            <button className="ghost" onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
              {theme === "light" ? "Dark" : "Light"}
            </button>
          </div>
        </header>

        <div className="body">
          {offline && (
            <div className="banner err">
              Cannot reach the local service. Start it with <code>python webui/server/app.py</code> in the
              repository root.
            </div>
          )}

          {page === "new" && (
            <NewDataset
              protocol={protocol}
              runsRoot={runsRoot}
              setRunsRoot={setRunsRoot}
              onCreated={(cfg, id) => {
                setConfigPath(cfg);
                setRunId(id);
              }}
            />
          )}
          {page === "phantom" && <PhantomDesign defaults={defaults} />}
          {page === "sim" && <Simulation defaults={defaults} protocol={protocol} />}
          {page === "run" && (
            <RunCenter
              protocol={protocol}
              configPath={configPath}
              runRoot={runRoot}
              setRunRoot={setRunRoot}
              task={task}
              setTask={setTask}
            />
          )}
          {page === "qc" && (
            <QCDataset protocol={protocol} runRoot={runRoot} setRunRoot={setRunRoot} />
          )}
        </div>

        <footer className="footbar">
          <span className="mono">{health?.repo_root ?? ""}</span>
          <span className="right">
            {task && (
              <span className={"pill " + (task.status === "running" ? "run" : task.status === "failed" ? "err" : "ok")}>
                <i className="dot" />
                {task.status}
              </span>
            )}
            <span className="muted">
              {protocol
                ? `${protocol.source_activity_mbq} MBq · ${protocol.exposure_s_per_projection} s/proj · 60 views`
                : ""}
            </span>
          </span>
        </footer>
      </div>
    </div>
  );
}
