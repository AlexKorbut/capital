import { Loader2 } from "lucide-react";
import { AxiosError } from "axios";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function errMessage(e: unknown): string {
  if (e instanceof AxiosError) {
    const detail = (e.response?.data as { detail?: unknown })?.detail;
    if (typeof detail === "string") return detail;
    // Tier/quota errors carry a structured detail: { code, message, upgrade_url }.
    if (detail && typeof detail === "object" && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
    return e.message;
  }
  return e instanceof Error ? e.message : "Что-то пошло не так";
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-5 w-5 animate-spin", className)} />;
}

export function LoadingState({ label }: { label?: string }) {
  const t = useT();
  return (
    <div className="mt-10 flex items-center justify-center gap-2 text-sm text-muted-foreground">
      <Spinner />
      <span>{label ?? t("Загрузка…", "Loading…")}</span>
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const t = useT();
  return (
    <div className="mt-10 rounded-lg border border-red-500/40 bg-red-500/10 p-6 text-center">
      <p className="text-sm font-medium text-red-500">{t("Ошибка", "Error")}</p>
      <p className="mt-1 text-sm text-muted-foreground">{errMessage(error)}</p>
      {onRetry && (
        <Button variant="outline" className="mt-4" onClick={onRetry}>
          {t("Повторить", "Retry")}
        </Button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <section className="mt-10 rounded-lg border border-dashed border-border bg-card p-10 text-center">
      <p className="text-lg font-medium">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted-foreground">{hint}</p>}
      {action && (
        <Button className="mt-4" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </section>
  );
}
