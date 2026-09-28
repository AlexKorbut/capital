import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import {
  getAiSettings,
  setAiSettings,
  type AiMode,
  type AiProvider,
} from "@/services/account";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errMessage } from "@/components/states";
import { useT } from "@/lib/i18n";

function ProviderRow({
  p,
  disabled,
  onSave,
  onRemove,
  pending,
}: {
  p: AiProvider;
  disabled: boolean;
  onSave: (value: string) => void;
  onRemove: () => void;
  pending: boolean;
}) {
  const t = useT();
  const [value, setValue] = useState("");
  return (
    <li className="flex flex-wrap items-center gap-2 py-2">
      <span className="w-28 text-sm font-medium">{p.label}</span>
      {p.configured ? (
        <>
          <code className="font-mono text-xs text-muted-foreground">{p.hint}</code>
          <span className="text-xs text-emerald-500">{t("подключён", "connected")}</span>
          <Button
            variant="outline"
            className="ml-auto h-8 px-3 text-xs"
            disabled={disabled || pending}
            onClick={onRemove}
          >
            {t("Удалить", "Remove")}
          </Button>
        </>
      ) : (
        <>
          <Input
            type="password"
            autoComplete="off"
            className="min-w-[220px] flex-1"
            placeholder={t("вставьте ключ", "paste the key")}
            value={value}
            disabled={disabled}
            onChange={(e) => setValue(e.target.value)}
          />
          <Button
            className="h-8 px-3 text-xs"
            disabled={disabled || pending || !value.trim()}
            onClick={() => {
              onSave(value.trim());
              setValue("");
            }}
          >
            {pending ? "…" : t("Сохранить", "Save")}
          </Button>
        </>
      )}
    </li>
  );
}

export function AiSection() {
  const t = useT();
  const qc = useQueryClient();
  const ai = useQuery({ queryKey: ["ai-settings"], queryFn: getAiSettings });

  const update = useMutation({
    mutationFn: setAiSettings,
    onSuccess: (data) => qc.setQueryData(["ai-settings"], data),
  });

  const mode: AiMode = ai.data?.mode ?? "kapital";
  const ownSelected = mode === "own";
  const serverAi = ai.data?.server_ai_available ?? false;

  const choose = (next: AiMode) => {
    if (next !== mode) update.mutate({ mode: next });
  };

  const option = (
    value: AiMode,
    title: string,
    body: string,
    note?: string,
  ) => (
    <button
      type="button"
      onClick={() => choose(value)}
      disabled={update.isPending}
      className={`flex-1 rounded-md border p-4 text-left transition ${
        mode === value
          ? "border-primary bg-primary/10"
          : "border-border hover:border-muted-foreground/50"
      }`}
    >
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-xs text-muted-foreground">{body}</p>
      {note && <p className="mt-1 text-xs text-amber-500">{note}</p>}
    </button>
  );

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6">
      <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <Sparkles className="h-4 w-4" />
        {t("Искусственный интеллект", "AI")}
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {t(
          "Разбор ввода, советник и сценарии работают на языковой модели. Выберите, чьими ключами она оплачивается.",
          "Input parsing, the advisor and scenarios run on a language model. Choose whose keys pay for it.",
        )}
      </p>

      {ai.isError && <p className="mt-2 text-sm text-destructive">{errMessage(ai.error)}</p>}

      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        {option(
          "kapital",
          t("ИИ КАПИТАЛЯ", "KAPITAL AI"),
          t(
            "Ничего настраивать не нужно: модели оплачиваются подпиской.",
            "Nothing to configure: the models are covered by your subscription.",
          ),
          serverAi
            ? undefined
            : t(
                "На этом сервере ИИ КАПИТАЛЯ не настроен — функции работают в демо-режиме.",
                "KAPITAL AI is not configured on this server — features run in demo mode.",
              ),
        )}
        {option(
          "own",
          t("Свои ключи", "Your own keys"),
          t(
            "Подключите ключи своих провайдеров — запросы идут напрямую и оплачиваются вашим аккаунтом у провайдера. Ключи хранятся зашифрованными.",
            "Bring your own provider keys — requests go straight to the provider and are billed to your account there. Keys are stored encrypted.",
          ),
        )}
      </div>

      {mode === "kapital" && (
        <p className="mt-3 text-xs text-muted-foreground">
          {t("Тарифы и лимиты ИИ — на странице ", "AI plans and limits — see ")}
          <Link to="/pricing" className="underline">
            {t("подписки", "pricing")}
          </Link>
          .
        </p>
      )}

      {ownSelected && ai.data && (
        <ul className="mt-4 divide-y divide-border">
          {ai.data.providers.map((p) => (
            <ProviderRow
              key={p.provider}
              p={p}
              disabled={update.isPending}
              pending={update.isPending}
              onSave={(value) => update.mutate({ keys: { [p.provider]: value } })}
              onRemove={() => update.mutate({ keys: { [p.provider]: null } })}
            />
          ))}
        </ul>
      )}

      {update.isError && (
        <p className="mt-2 text-sm text-destructive">{errMessage(update.error)}</p>
      )}

      {ai.data && (
        <p className={`mt-3 text-xs ${ai.data.demo ? "text-amber-500" : "text-emerald-500"}`}>
          {ai.data.demo
            ? t(
                "Сейчас: демо-режим — ИИ-функции отвечают заготовками без обращения к модели.",
                "Now: demo mode — AI features answer with canned results, no model is called.",
              )
            : t("Сейчас: модель подключена.", "Now: a model is connected.")}
        </p>
      )}
    </section>
  );
}
