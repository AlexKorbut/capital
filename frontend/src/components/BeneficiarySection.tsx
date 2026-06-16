import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HeartHandshake } from "lucide-react";
import { getBeneficiary, setBeneficiary } from "@/services/account";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";

export function BeneficiarySection() {
  const t = useT();
  const qc = useQueryClient();
  const data = useQuery({ queryKey: ["beneficiary"], queryFn: getBeneficiary });
  const [email, setEmail] = useState("");
  const [days, setDays] = useState("90");

  useEffect(() => {
    if (data.data) {
      setEmail(data.data.email ?? "");
      if (data.data.days > 0) setDays(String(data.data.days));
    }
  }, [data.data]);

  const save = useMutation({
    mutationFn: () =>
      setBeneficiary({ email: email.trim() || null, days: Number(days) || 0 }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["beneficiary"] }),
  });
  const clear = useMutation({
    mutationFn: () => setBeneficiary({ email: null, days: 0 }),
    onSuccess: () => {
      setEmail("");
      qc.invalidateQueries({ queryKey: ["beneficiary"] });
    },
  });

  const active = (data.data?.email ?? "").length > 0;

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6">
      <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <HeartHandshake className="h-4 w-4" />
        {t("Доверенное лицо", "Trusted contact")}
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wide">
          Business
        </span>
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {t(
          "Если вы не входите в аккаунт дольше указанного срока, мы отправим доверенному лицу краткую сводку капитала (без доступа к аккаунту).",
          "If you don't sign in for longer than the set period, we'll email your trusted contact a brief net-worth summary (no account access).",
        )}
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <label className="flex-1 text-xs text-muted-foreground">
          {t("Email доверенного лица", "Trusted contact's email")}
          <Input
            type="email"
            className="mt-1 min-w-[200px]"
            placeholder="trusted@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="text-xs text-muted-foreground">
          {t("Неактивность, дней", "Inactivity, days")}
          <Input
            className="mt-1 w-24"
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <Button
          disabled={save.isPending || !email.trim() || Number(days) <= 0}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "…" : t("Сохранить", "Save")}
        </Button>
        {active && (
          <Button
            variant="outline"
            disabled={clear.isPending}
            onClick={() => clear.mutate()}
          >
            {t("Отключить", "Disable")}
          </Button>
        )}
      </div>

      {active && (
        <p className="mt-3 text-sm text-emerald-500">
          {t(
            `Активно: уведомление уйдёт на ${data.data?.email} после ${data.data?.days} дн. неактивности.`,
            `Active: a notice goes to ${data.data?.email} after ${data.data?.days} days of inactivity.`,
          )}
        </p>
      )}
    </section>
  );
}
