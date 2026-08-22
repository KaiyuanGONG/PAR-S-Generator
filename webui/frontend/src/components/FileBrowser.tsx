import { useEffect, useRef, useState } from "react";
import { ApiError, api, type FsEntry } from "../api";
import ErrorNotice from "./ErrorNotice";
import { useI18n } from "../i18n";

interface FileBrowserProps {
  open: boolean;
  title: string;
  initialPath?: string;
  selection: "file" | "directory";
  extensions?: string[];
  nativeKind?: "simind_exe" | "smc" | "runs_root" | "export_root";
  onSelect: (path: string) => void;
  onClose: () => void;
}

interface Listing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  roots: string[];
}

function joinPath(parent: string, name: string) {
  const separator = parent.includes("\\") ? "\\" : "/";
  return `${parent.replace(/[\\/]$/, "")}${separator}${name}`;
}

function parentPath(path: string) {
  const normalized = path.replace(/[\\/]$/, "");
  const separatorAt = Math.max(normalized.lastIndexOf("\\"), normalized.lastIndexOf("/"));
  if (separatorAt < 0) return "";
  if (separatorAt === 0) return "/";
  if (/^[A-Za-z]:/.test(normalized) && separatorAt === 2) return `${normalized.slice(0, 2)}\\`;
  return normalized.slice(0, separatorAt);
}

export default function FileBrowser({
  open,
  title,
  initialPath = "",
  selection,
  extensions,
  nativeKind,
  onSelect,
  onClose,
}: FileBrowserProps) {
  const { t } = useI18n();
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function browse(path: string, fallBackToParent = false) {
    setBusy(true);
    setError(null);
    setSelected(null);
    try {
      const payload = await api.fsList(path) as Listing;
      setListing(payload);
    } catch (caught) {
      if (
        fallBackToParent &&
        caught instanceof ApiError &&
        (caught.status === 404 || caught.status === 422) &&
        parentPath(path) &&
        parentPath(path) !== path
      ) {
        await browse(parentPath(path), false);
        return;
      }
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    void browse(initialPath, true);
    requestAnimationFrame(() => headingRef.current?.focus());
    return () => openerRef.current?.focus();
    // Re-open at the caller's current path; path changes while open do not reset navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const accepts = (entry: FsEntry) => {
    if (entry.type === "dir" || !extensions?.length) return true;
    return extensions.some((extension) => entry.name.toLowerCase().endsWith(extension.toLowerCase()));
  };

  function activate(entry: FsEntry) {
    if (!listing) return;
    const path = joinPath(listing.path, entry.name);
    if (entry.type === "dir") void browse(path);
    else if (accepts(entry)) setSelected(path);
  }

  function confirm() {
    const path = selection === "directory" ? listing?.path : selected;
    if (!path) return;
    onSelect(path);
    onClose();
  }

  async function pickNative() {
    if (!nativeKind) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.fsPick(nativeKind, listing?.path ?? initialPath);
      if (!result.cancelled && result.path) {
        onSelect(result.path);
        onClose();
      }
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section
        ref={dialogRef}
        className="file-browser"
        role="dialog"
        aria-modal="true"
        aria-labelledby="file-browser-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
          if (event.key === "Tab") {
            const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])") ?? [])]
              .filter((element) => element.offsetParent !== null);
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first.focus();
            }
          }
        }}
      >
        <header className="file-browser-head">
          <div>
            <span className="run-eyebrow">{t("browser.localFiles")}</span>
            <h2 id="file-browser-title" ref={headingRef} tabIndex={-1}>{title}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label={t("action.close")}>×</button>
        </header>

        <div className="file-browser-roots" aria-label={t("browser.allowedRoots")}>
          {listing?.roots.map((root) => <button type="button" key={root} onClick={() => void browse(root)} title={root}>{root}</button>)}
        </div>
        <div className="file-browser-path mono">
          <button type="button" disabled={!listing?.parent || busy} onClick={() => listing?.parent && void browse(listing.parent)} aria-label={t("browser.parent")}>↑</button>
          <span title={listing?.path}>{listing?.path ?? initialPath}</span>
        </div>

        {error != null && <ErrorNotice error={error} onRetry={() => void browse(listing?.path ?? initialPath, true)} />}
        <div className="file-browser-list" role="listbox" aria-busy={busy} tabIndex={0}>
          {busy ? <p>{t("common.loading")}</p> : listing?.entries.filter(accepts).map((entry) => {
            const path = joinPath(listing.path, entry.name);
            return (
              <button
                type="button"
                role="option"
                aria-selected={selected === path}
                className="file-entry"
                key={`${entry.type}:${entry.name}`}
                data-type={entry.type}
                onClick={() => activate(entry)}
                onDoubleClick={() => {
                  if (entry.type === "file" && accepts(entry)) {
                    onSelect(path);
                    onClose();
                  }
                }}
              >
                <span aria-hidden="true">{entry.type === "dir" ? "▸" : "·"}</span>
                <strong>{entry.name}</strong>
                <small className="mono">{entry.size == null ? "" : `${Math.max(1, Math.round(entry.size / 1024))} KiB`}</small>
              </button>
            );
          })}
        </div>

        <footer className="file-browser-actions">
          <span className="mono" title={selected ?? undefined}>{selection === "file" ? selected ?? t("browser.noSelection") : listing?.path}</span>
          {nativeKind && <button type="button" onClick={() => void pickNative()} disabled={busy}>Windows · {t("action.browse")}</button>}
          <button type="button" onClick={onClose}>{t("action.cancel")}</button>
          <button type="button" className="primary" onClick={confirm} disabled={selection === "file" ? !selected : !listing}>{selection === "file" ? t("browser.chooseFile") : t("browser.chooseFolder")}</button>
        </footer>
      </section>
    </div>
  );
}
