import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

export interface SnapshotConflict {
  code: "snapshot_exists";
  message: string;
  snapshot_date: string;
  snapshot_id: string;
}

export function readSnapshotConflict(error: unknown): SnapshotConflict | null {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response;
  if (response?.status !== 409) return null;
  const detail = response.data?.detail;
  if (!detail || typeof detail !== "object") return null;
  const body = detail as Partial<SnapshotConflict>;
  if (body.code !== "snapshot_exists" || !body.snapshot_date) return null;
  return {
    code: "snapshot_exists",
    message: body.message ?? "",
    snapshot_date: body.snapshot_date,
    snapshot_id: body.snapshot_id ?? "",
  };
}

/** ``2026-10-21`` → ``21.10.2026``. Leaves anything else untouched. */
export function formatSnapshotDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return iso;
  return `${match[3]}.${match[2]}.${match[1]}`;
}

export function SnapshotOverwriteDialog({
  snapshotDate,
  pending,
  onCancel,
  onConfirm,
}: {
  snapshotDate: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  const when = formatSnapshotDate(snapshotDate);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={pending ? undefined : onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="snapshot-overwrite-title"
        data-testid="snapshot-overwrite-dialog"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="snapshot-overwrite-title" className="text-lg font-semibold leading-snug">
          {t(
            `Снимок за ${when} уже есть. Перезаписать?`,
            `A snapshot for ${when} already exists. Overwrite?`,
          )}
        </h2>
        <p className="mt-3 text-sm text-muted-foreground">
          {t(
            "Активы этого снимка будут заменены, итог и динамика пересчитаются. Советы и сценарии, которые ссылаются на снимок, сохранятся.",
            "This snapshot's assets will be replaced, and the total and history will be recalculated. Advice and scenarios that point at it stay linked.",
          )}
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={pending}
            data-testid="snapshot-overwrite-cancel"
          >
            {t("Отмена", "Cancel")}
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            data-testid="snapshot-overwrite-confirm"
          >
            {pending ? t("Сохраняю…", "Saving…") : t("Перезаписать", "Overwrite")}
          </Button>
        </div>
      </div>
    </div>
  );
}
