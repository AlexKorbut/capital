import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AxiosError } from "axios";
import {
  generateAdvice,
  getLatestAdvice,
  markAdviceRead,
  type AdviceItem,
} from "@/services/advice";
import { usePortfolioSocket } from "@/hooks/usePortfolioSocket";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useT, useLang } from "@/lib/i18n";

const CATEGORY_LABELS: Record<string, string> = {
  diversification: "Диверсификация",
  concentration: "Концентрация",
  currency: "Валюта",
  liquidity: "Ликвидность",
  risk: "Риск",
  tax: "Налоги",
  general: "Общее",
};
const CATEGORY_LABELS_EN: Record<string, string> = {
  diversification: "Diversification",
  concentration: "Concentration",
  currency: "Currency",
  liquidity: "Liquidity",
  risk: "Risk",
  tax: "Tax",
  general: "General",
};

function errMessage(e: unknown): string {
  if (e instanceof AxiosError) {
    return (e.response?.data as { detail?: string })?.detail ?? e.message;
  }
  return e instanceof Error ? e.message : "Не удалось получить советы";
}

function InsightCard({ item }: { item: AdviceItem }) {
  const t = useT();
  const catLabels = useLang((s) => s.lang) === "en" ? CATEGORY_LABELS_EN : CATEGORY_LABELS;
  const qc = useQueryClient();
  const read = useMutation({
    mutationFn: () => markAdviceRead(item.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["advice", "latest"] }),
  });

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          {item.category && (
            <span className="inline-block rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {catLabels[item.category] ?? item.category}
            </span>
          )}
          <h3 className="mt-2 font-semibold">{item.title}</h3>
        </div>
        {!item.is_read && (
          <button
            className="shrink-0 text-xs text-muted-foreground hover:underline"
            onClick={() => read.mutate()}
          >
            {t("Отметить прочитанным", "Mark as read")}
          </button>
        )}
      </div>

      <p className="mt-2 text-sm leading-relaxed">{item.body}</p>
      {item.relevance && (
        <p className="mt-2 text-xs text-muted-foreground">→ {item.relevance}</p>
      )}
      <p className="mt-3 border-t border-border pt-2 text-xs italic text-muted-foreground">
        {item.disclaimer}
      </p>
    </div>
  );
}

export function AdvisorPage() {
  const t = useT();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const latest = useQuery({
    queryKey: ["advice", "latest"],
    queryFn: getLatestAdvice,
  });

  const generate = useMutation({
    mutationFn: generateAdvice,
    onSuccess: (data) => qc.setQueryData(["advice", "latest"], data),
  });

  // Live refresh when the backend finishes a generation elsewhere.
  usePortfolioSocket((e) => {
    if (e.type === "advice_ready") {
      qc.invalidateQueries({ queryKey: ["advice", "latest"] });
    }
  });

  const session = latest.data;

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t("AI-советник", "AI advisor")}</h1>
        <div className="flex gap-3">
          <Button onClick={() => generate.mutate()} disabled={generate.isPending}>
            {generate.isPending
              ? t("Анализирую…", "Analyzing…")
              : t("Сгенерировать инсайты", "Generate insights")}
          </Button>
          <Button variant="ghost" onClick={() => navigate("/")}>
            {t("← Назад", "← Back")}
          </Button>
        </div>
      </header>

      {generate.isError && (
        <p className="mt-4 text-sm text-red-500">{errMessage(generate.error)}</p>
      )}

      {latest.isLoading && <LoadingState />}

      {latest.isError && (
        <ErrorState error={latest.error} onRetry={() => latest.refetch()} />
      )}

      {!latest.isLoading && !latest.isError && !session && (
        <EmptyState
          title={t("Пока нет инсайтов", "No insights yet")}
          hint={t(
            "Нажмите «Сгенерировать инсайты», чтобы проанализировать текущий портфель.",
            'Tap "Generate insights" to analyze your current portfolio.',
          )}
        />
      )}

      {session && (
        <section className="mt-8 space-y-4">
          {session.generated_at && (
            <p className="text-xs text-muted-foreground">
              {t("Сгенерировано", "Generated")}{" "}
              {new Date(session.generated_at).toLocaleString()}
            </p>
          )}
          {session.items.map((item) => (
            <InsightCard key={item.id} item={item} />
          ))}
        </section>
      )}
    </div>
  );
}
