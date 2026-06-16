import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import {
  getAllocation,
  setAllocation,
  useAssetLabels,
  type AssetType,
} from "@/services/portfolio";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";

const TYPES: AssetType[] = [
  "cash",
  "bank_deposit",
  "crypto",
  "stock",
  "real_estate",
  "debt",
  "other",
];

function driftClass(v: string | null): string {
  const n = Number(v ?? 0);
  if (n > 5) return "text-amber-500";
  if (n < -5) return "text-sky-500";
  return "text-muted-foreground";
}

export function AllocationSection() {
  const t = useT();
  const labels = useAssetLabels();
  const qc = useQueryClient();
  const alloc = useQuery({ queryKey: ["allocation"], queryFn: getAllocation });
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    if (alloc.data && edit) {
      const d: Record<string, string> = {};
      for (const r of alloc.data.rows) {
        if (r.target_pct != null) d[r.asset_type] = r.target_pct;
      }
      setDraft(d);
    }
  }, [edit, alloc.data]);

  const save = useMutation({
    mutationFn: () => {
      const targets: Record<string, number> = {};
      for (const [k, v] of Object.entries(draft)) {
        const n = Number(v);
        if (n > 0) targets[k] = n;
      }
      return setAllocation(targets);
    },
    onSuccess: () => {
      setEdit(false);
      qc.invalidateQueries({ queryKey: ["allocation"] });
    },
  });

  const rows = alloc.data?.rows ?? [];
  const draftTotal = Object.values(draft).reduce((s, v) => s + (Number(v) || 0), 0);

  return (
    <section className="rounded-lg border border-border bg-card p-6">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <Scale className="h-4 w-4" />
          {t("Целевая структура", "Target allocation")}
        </h2>
        <Button variant="outline" onClick={() => setEdit((v) => !v)}>
          {edit
            ? t("Отмена", "Cancel")
            : alloc.data?.has_target
              ? t("Изменить", "Edit")
              : t("Задать цели", "Set targets")}
        </Button>
      </div>

      {edit ? (
        <div className="mt-4 space-y-2">
          {TYPES.map((ty) => (
            <div key={ty} className="flex items-center justify-between gap-3">
              <span className="text-sm">{labels[ty]}</span>
              <div className="flex items-center gap-1">
                <Input
                  className="w-20 text-right"
                  inputMode="numeric"
                  placeholder="0"
                  value={draft[ty] ?? ""}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, [ty]: e.target.value }))
                  }
                />
                <span className="text-sm text-muted-foreground">%</span>
              </div>
            </div>
          ))}
          <div className="flex items-center justify-between pt-2 text-sm">
            <span className={draftTotal === 100 ? "text-emerald-500" : "text-muted-foreground"}>
              {t("Сумма:", "Total:")} {draftTotal}%
              {draftTotal !== 100 ? t(" (рекомендуется 100%)", " (100% recommended)") : ""}
            </span>
            <Button disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? "…" : t("Сохранить", "Save")}
            </Button>
          </div>
        </div>
      ) : rows.length > 0 ? (
        <div className="mt-4 space-y-2">
          {rows.map((r) => (
            <div key={r.asset_type} className="text-sm">
              <div className="flex items-center justify-between">
                <span>{labels[r.asset_type] ?? r.asset_type}</span>
                <span className="tabular-nums text-muted-foreground">
                  {r.current_pct}%
                  {r.target_pct != null && (
                    <>
                      {" "}
                      / {t("цель", "target")} {r.target_pct}%{" "}
                      <span className={driftClass(r.drift_pct)}>
                        ({Number(r.drift_pct) > 0 ? "+" : ""}
                        {r.drift_pct})
                      </span>
                    </>
                  )}
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${Math.min(Number(r.current_pct), 100)}%` }}
                />
              </div>
            </div>
          ))}
          {alloc.data?.has_target && (
            <p className="pt-1 text-xs text-muted-foreground">
              {t(
                "Дрифт — отклонение текущей доли от целевой. Это наблюдение, не рекомендация к сделкам.",
                "Drift is how far the current share is from target. It's an observation, not a recommendation to trade.",
              )}
            </p>
          )}
        </div>
      ) : (
        <p className="mt-4 text-sm text-muted-foreground">
          {t(
            "Задайте целевые доли по типам активов — и увидите отклонение (дрифт).",
            "Set target shares by asset type to see the drift.",
          )}
        </p>
      )}
    </section>
  );
}
