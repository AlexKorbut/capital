import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Target, Trophy, X } from "lucide-react";
import { createGoal, deleteGoal, listGoals, type Goal } from "@/services/goals";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";

function usd(value: string | null | undefined): string {
  if (value == null) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function GoalRow({ g, onDelete }: { g: Goal; onDelete: (id: string) => void }) {
  const t = useT();
  const pct = Math.min(Number(g.progress_pct ?? 0), 100);
  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-medium">
          {g.achieved && <Trophy className="h-4 w-4 text-amber-500" />}
          {g.title}
        </span>
        <button
          type="button"
          className="text-muted-foreground hover:text-red-500"
          onClick={() => onDelete(g.id)}
          aria-label={t("Удалить цель", "Delete goal")}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full ${g.achieved ? "bg-emerald-500" : "bg-primary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-1.5 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {usd(g.current_usd)} / {usd(g.target_usd)}
          {g.progress_pct != null ? ` · ${g.progress_pct}%` : ""}
        </span>
        {g.achieved ? (
          <span className="text-emerald-500">
            {t("Цель достигнута 🎉", "Goal reached 🎉")}
          </span>
        ) : g.projected_date ? (
          <span>
            {t("≈ к", "≈ by")} {new Date(g.projected_date).toLocaleDateString()}
          </span>
        ) : (
          <span>
            {t("осталось", "left")} {usd(g.remaining_usd)}
          </span>
        )}
      </div>
    </div>
  );
}

export function GoalsSection() {
  const t = useT();
  const qc = useQueryClient();
  const goals = useQuery({ queryKey: ["goals"], queryFn: listGoals });
  const [title, setTitle] = useState("");
  const [target, setTarget] = useState("");
  const [open, setOpen] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["goals"] });

  const create = useMutation({
    mutationFn: () =>
      createGoal({ title: title.trim(), target_usd: Number(target) }),
    onSuccess: () => {
      setTitle("");
      setTarget("");
      setOpen(false);
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteGoal(id),
    onSuccess: invalidate,
  });

  return (
    <section className="rounded-lg border border-border bg-card p-6">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <Target className="h-4 w-4" />
          {t("Цели", "Goals")}
        </h2>
        <Button variant="outline" onClick={() => setOpen((v) => !v)}>
          {open ? t("Отмена", "Cancel") : t("+ Цель", "+ Goal")}
        </Button>
      </div>

      {open && (
        <form
          className="mt-4 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (title.trim() && Number(target) > 0) create.mutate();
          }}
        >
          <label className="text-xs text-muted-foreground">
            {t("Название", "Name")}
            <Input
              className="mt-1 min-w-[160px]"
              placeholder={t("Подушка безопасности", "Emergency fund")}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="text-xs text-muted-foreground">
            {t("Цель, $", "Target, $")}
            <Input
              className="mt-1 max-w-[140px]"
              inputMode="numeric"
              placeholder="100000"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
          </label>
          <Button
            type="submit"
            disabled={create.isPending || !title.trim() || Number(target) <= 0}
          >
            {create.isPending ? "…" : t("Добавить", "Add")}
          </Button>
        </form>
      )}

      <div className="mt-4 space-y-3">
        {goals.data && goals.data.length > 0 ? (
          goals.data.map((g) => (
            <GoalRow key={g.id} g={g} onDelete={(id) => remove.mutate(id)} />
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            {t(
              "Поставьте цель — например, $100 000 — и следите за прогрессом.",
              "Set a goal — say $100,000 — and track your progress.",
            )}
          </p>
        )}
      </div>
    </section>
  );
}
