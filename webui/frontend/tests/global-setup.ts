import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";


const frontendRoot = resolve(import.meta.dirname, "..");
const repoRoot = resolve(frontendRoot, "../..");

function resolvePython(): string {
  if (process.env.PARS_TEST_PYTHON) return process.env.PARS_TEST_PYTHON;
  const managed = join(repoRoot, ".venv-windows-v1", "Scripts", "python.exe");
  if (existsSync(managed)) return managed;
  try {
    const condaBase = execFileSync(
      process.env.ComSpec ?? "cmd.exe",
      ["/d", "/s", "/c", "conda info --base"],
      { encoding: "utf8" },
    ).trim();
    const spect = join(condaBase, "envs", "SPECT", "python.exe");
    if (existsSync(spect)) return spect;
  } catch {
    // Fall through to PATH resolution and its actionable import error.
  }
  return "python";
}

async function isReady(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(500) });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitUntilReady(url: string, child: ChildProcess, timeoutMs = 120_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isReady(url)) return;
    if (child.exitCode !== null) throw new Error(`Test server exited before ${url} became ready: ${child.exitCode}`);
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function stopOwnedProcess(child: ChildProcess | null): Promise<void> {
  if (!child || child.exitCode !== null || child.pid === undefined) return;
  const pid = child.pid;
  child.kill();
  const exited = await Promise.race([
    new Promise<boolean>((resolveExit) => child.once("exit", () => resolveExit(true))),
    new Promise<boolean>((resolveTimeout) => setTimeout(() => resolveTimeout(false), 5_000)),
  ]);
  if (!exited && process.platform === "win32") {
    try {
      execFileSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
    } catch {
      // The process can exit between the timeout and taskkill invocation.
    }
  }
}

export default async function globalSetup() {
  let apiProcess: ChildProcess | null = null;
  let viteProcess: ChildProcess | null = null;
  try {
    if (!(await isReady("http://127.0.0.1:8765/api/health"))) {
      apiProcess = spawn(
        resolvePython(),
        ["-m", "uvicorn", "webui.server.app:app", "--host", "127.0.0.1", "--port", "8765", "--log-level", "warning"],
        { cwd: repoRoot, stdio: "inherit", windowsHide: true },
      );
      await waitUntilReady("http://127.0.0.1:8765/api/health", apiProcess);
    }
    if (!(await isReady("http://127.0.0.1:5173"))) {
      const builtIndex = join(frontendRoot, "dist", "index.html");
      if (!existsSync(builtIndex)) {
        throw new Error("Built Web assets are missing. Run npm run build before browser acceptance tests.");
      }
      viteProcess = spawn(
        process.execPath,
        [
          join(frontendRoot, "node_modules", "vite", "bin", "vite.js"),
          "preview",
          "--host",
          "127.0.0.1",
          "--port",
          "5173",
          "--strictPort",
        ],
        { cwd: frontendRoot, stdio: "inherit", windowsHide: true },
      );
      await waitUntilReady("http://127.0.0.1:5173", viteProcess);
    }
  } catch (error) {
    await stopOwnedProcess(viteProcess);
    await stopOwnedProcess(apiProcess);
    throw error;
  }

  return async () => {
    await stopOwnedProcess(viteProcess);
    await stopOwnedProcess(apiProcess);
  };
}
