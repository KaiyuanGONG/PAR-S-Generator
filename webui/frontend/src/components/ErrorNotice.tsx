import { ApiError } from "../api";
import { useI18n, type Translate } from "../i18n";

const GUIDED_STATUS = new Set([403, 404, 409, 422]);

export function rawError(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export function guidedError(error: unknown, t: Translate) {
  if (error instanceof ApiError && GUIDED_STATUS.has(error.status)) {
    return t(`error.http${error.status}` as "error.http403" | "error.http404" | "error.http409" | "error.http422");
  }
  return rawError(error);
}

export default function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n();
  const raw = rawError(error);
  const guide = guidedError(error, t);
  const guided = guide !== raw;

  return (
    <div className="banner err" role="alert">
      <span>{guide}</span>
      {guided && <details className="diagnostic-details"><summary>{t("error.rawDetails")}</summary><code>{raw}</code></details>}
      {onRetry && <button type="button" onClick={onRetry}>{t("action.retry")}</button>}
    </div>
  );
}
