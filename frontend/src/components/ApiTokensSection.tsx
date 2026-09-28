import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Trash2 } from "lucide-react";
import {
  createToken,
  listTokens,
  revokeToken,
  type ApiTokenCreated,
} from "@/services/account";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errMessage } from "@/components/states";
import { useT } from "@/lib/i18n";

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function ApiTokensSection() {
  const t = useT();
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: ["api-tokens"], queryFn: listTokens });

  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<ApiTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () => createToken(name.trim()),
    onSuccess: (created) => {
      setFresh(created);
      setCopied(false);
      setName("");
      qc.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => revokeToken(id),
    onSuccess: (_void, id) => {
      if (fresh?.id === id) setFresh(null);
      qc.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });

  const copy = async () => {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(fresh.token);
      setCopied(true);
    } catch {
      /* clipboard may be unavailable (http, permissions) — the token is still visible */
    }
  };

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6">
      <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <KeyRound className="h-4 w-4" />
        {t("API-токены", "API tokens")}
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {t(
          "Персональные токены для программ, которые работают с вашими данными от вашего имени — например, для Джарвиса. Токен показывается один раз при создании.",
          "Personal tokens for programs that work with your data on your behalf — for example, Jarvis. A token is shown once, at creation.",
        )}
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <label className="flex-1 text-xs text-muted-foreground">
          {t("Название", "Name")}
          <Input
            className="mt-1 min-w-[200px]"
            placeholder={t("Джарвис на ноутбуке", "Jarvis on the laptop")}
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <Button disabled={create.isPending || !name.trim()} onClick={() => create.mutate()}>
          {create.isPending ? "…" : t("Создать токен", "Create token")}
        </Button>
      </div>
      {create.isError && (
        <p className="mt-2 text-sm text-destructive">{errMessage(create.error)}</p>
      )}

      {fresh && (
        <div className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-4">
          <p className="text-sm font-medium">
            {t(
              `Токен «${fresh.name}» создан. Скопируйте его сейчас — больше он не будет показан.`,
              `Token “${fresh.name}” created. Copy it now — it will not be shown again.`,
            )}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <code className="flex-1 break-all rounded bg-background px-2 py-1 font-mono text-xs">
              {fresh.token}
            </code>
            <Button variant="outline" className="h-8 px-3 text-xs" onClick={copy}>
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              <span className="ml-1">
                {copied ? t("Скопировано", "Copied") : t("Копировать", "Copy")}
              </span>
            </Button>
          </div>
        </div>
      )}

      <div className="mt-4">
        {tokens.isLoading && <p className="text-sm text-muted-foreground">…</p>}
        {tokens.isError && (
          <p className="text-sm text-destructive">{errMessage(tokens.error)}</p>
        )}
        {tokens.data && tokens.data.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {t("Активных токенов нет.", "No active tokens.")}
          </p>
        )}
        {tokens.data && tokens.data.length > 0 && (
          <ul className="divide-y divide-border rounded-md border border-border">
            {tokens.data.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                <span className="font-medium">{row.name}</span>
                <code className="font-mono text-xs text-muted-foreground">{row.prefix}…</code>
                <span className="text-xs text-muted-foreground">
                  {t("создан", "created")} {fmtDate(row.created_at)} ·{" "}
                  {t("использован", "last used")} {fmtDate(row.last_used_at)}
                </span>
                <Button
                  variant="outline"
                  className="ml-auto h-8 px-3 text-xs"
                  disabled={revoke.isPending}
                  onClick={() => {
                    if (
                      window.confirm(
                        t(
                          `Отозвать токен «${row.name}»? Программы, которые им пользуются, потеряют доступ.`,
                          `Revoke token “${row.name}”? Programs using it will lose access.`,
                        ),
                      )
                    ) {
                      revoke.mutate(row.id);
                    }
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                  <span className="ml-1">{t("Отозвать", "Revoke")}</span>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
