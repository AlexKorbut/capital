import { useEffect, useRef } from "react";
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

function focusable(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      "button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
    ),
  );
}

export function SnapshotOverwriteDialog({
  snapshotDate,
  pending,
  error,
  variant = "portfolio",
  onCancel,
  onConfirm,
}: {
  snapshotDate: string;
  pending: boolean;
  /** Non-409 failure of the overwrite request. Shown inside the dialog. */
  error?: string | null;
  /** Wallet sync replaces balances only; a portfolio confirm replaces every asset. */
  variant?: "portfolio" | "wallets";
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  const when = formatSnapshotDate(snapshotDate);
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;

  useEffect(() => {
    const root = dialogRef.current;
    if (!root) return;
    const items = focusable(root);
    (items[0] ?? root).focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (!pending) cancelRef.current();
        event.preventDefault();
        return;
      }
      if (event.key !== "Tab" || !root) return;
      const nodes = focusable(root);
      if (nodes.length === 0) {
        event.preventDefault();
        root.focus();
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !root.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !root.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    function onFocusIn(event: FocusEvent) {
      if (!root || root.contains(event.target as Node)) return;
      (focusable(root)[0] ?? root).focus();
    }

    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [pending]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={pending ? undefined : onCancel}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="snapshot-overwrite-title"
        tabIndex={-1}
        data-testid="snapshot-overwrite-dialog"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="snapshot-overwrite-title" className="text-lg font-semibold leading-snug">
          {t(
            `Снимок за ${when} уже есть. Перезаписать?`,
            `A snapshot for ${when} already exists. Overwrite?`,
          )}
        </h2>
        <p className="mt-3 text-sm text-muted-foreground">
          {variant === "wallets"
            ? t(
                "Заменятся только балансы кошельков. Наличные, вклады, недвижимость и остальные активы этого снимка останутся. Итог пересчитается.",
                "Only wallet balances will be replaced. Cash, deposits, property and the other assets in this snapshot stay. The total will be recalculated.",
              )
            : t(
                "Активы этого снимка будут заменены, итог и динамика пересчитаются. Советы и сценарии, которые ссылаются на снимок, сохранятся.",
                "This snapshot's assets will be replaced, and the total and history will be recalculated. Advice and scenarios that point at it stay linked.",
              )}
        </p>
        {error && (
          <p className="mt-3 text-sm text-red-500" data-testid="snapshot-overwrite-error" role="alert">
            {error}
          </p>
        )}
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
