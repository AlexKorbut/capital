import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { simulateScenario, type ScenarioResponse } from "@/services/scenario";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { formatUsd as usd } from "@/lib/utils";

const EXAMPLE = "что если продам биткоин и переложу всё в евро?";
const EXAMPLE_EN = "what if I sell Bitcoin and move everything into euros?";

function errMessage(e: unknown): string {
  if (e instanceof AxiosError) {
    return (e.response?.data as { detail?: string })?.detail ?? e.message;
  }
  return e instanceof Error ? e.message : "Не удалось смоделировать сценарий";
}


function Delta({ result }: { result: ScenarioResponse }) {
  const t = useT();
  const c = result.comparison;
  const delta = c.delta_usd != null ? Number(c.delta_usd) : null;
  const up = delta != null && delta > 0;
  const down = delta != null && delta < 0;
  return (
    <div className="rounded-lg border border-border bg-card p-6">
      <p className="text-sm text-muted-foreground">
        {t("Гипотетический капитал", "Hypothetical net worth")}
      </p>
      <p className="mt-1 text-3xl font-bold tracking-tight">
        {usd(c.new_total_usd)}
      </p>
      <p className="mt-2 text-sm">
        <span className="text-muted-foreground">{t("было ", "was ")}</span>
        <span className="tabular-nums">{usd(c.base_total_usd)}</span>
        {delta != null && (
          <span
            className={
              up
                ? "ml-3 font-medium text-emerald-600"
                : down
                  ? "ml-3 font-medium text-red-500"
                  : "ml-3 font-medium text-muted-foreground"
            }
          >
            {up ? "▲" : down ? "▼" : ""} {usd(c.delta_usd)}
            {c.delta_pct != null ? ` (${c.delta_pct}%)` : ""}
          </span>
        )}
      </p>
    </div>
  );
}

export function ScenariosPage() {
  const t = useT();
  const example = t(EXAMPLE, EXAMPLE_EN);
  const navigate = useNavigate();
  const [text, setText] = useState("");

  const sim = useMutation({
    mutationFn: () => simulateScenario({ scenario_text: text.trim() }),
  });

  const result = sim.data;

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">
          {t("Сценарии «что если»", "What-if scenarios")}
        </h1>
        <Button variant="ghost" onClick={() => navigate("/")}>
          {t("← Назад", "← Back")}
        </Button>
      </header>

      <section className="mt-8">
        <label className="text-sm text-muted-foreground">
          {t(
            "Опишите гипотезу — мы смоделируем её на вашем текущем портфеле, ничего не меняя.",
            "Describe a hypothesis — we'll model it on your current portfolio without changing anything.",
          )}
        </label>
        <textarea
          className="mt-2 min-h-[100px] w-full rounded-md border border-input bg-background p-3 text-sm outline-none ring-ring focus-visible:ring-2"
          placeholder={example}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="mt-2 flex items-center gap-3">
          <Button
            onClick={() => sim.mutate()}
            disabled={!text.trim() || sim.isPending}
          >
            {sim.isPending ? t("Моделирую…", "Simulating…") : t("Смоделировать", "Simulate")}
          </Button>
          <button
            type="button"
            className="text-sm text-muted-foreground underline-offset-2 hover:underline"
            onClick={() => setText(example)}
          >
            {t("Вставить пример", "Insert example")}
          </button>
        </div>
        {sim.isError && (
          <p className="mt-3 text-sm text-red-500">{errMessage(sim.error)}</p>
        )}
      </section>

      {result && (
        <section className="mt-8 space-y-4">
          <Delta result={result} />

          {result.assets.length > 0 && (
            <div className="rounded-lg border border-border bg-card p-6">
              <h2 className="text-sm font-medium text-muted-foreground">
                {t("Портфель после изменений", "Portfolio after changes")} (
                {result.assets.length})
              </h2>
              <div className="mt-3 divide-y divide-border">
                {result.assets.map((a, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between py-2 text-sm"
                  >
                    <span>
                      {String(a.amount)} {a.symbol ?? a.currency}
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      {usd(a.usd_value == null ? null : String(a.usd_value))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {result.advice.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-medium text-muted-foreground">
                {t(
                  "Аналитика по гипотетическому портфелю",
                  "Analysis of the hypothetical portfolio",
                )}
              </h2>
              {result.advice.map((item, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-border bg-card p-5"
                >
                  <h3 className="font-semibold">{item.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed">{item.body}</p>
                  {item.disclaimer && (
                    <p className="mt-3 border-t border-border pt-2 text-xs italic text-muted-foreground">
                      {item.disclaimer}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
