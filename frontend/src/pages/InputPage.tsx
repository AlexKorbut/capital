import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { AxiosError } from "axios";
import {
  ASSET_TYPE_LABELS,
  confirmInput,
  downloadImportTemplate,
  importTable,
  submitInput,
  uploadInput,
  useAssetLabels,
  type AssetItem,
  type AssetType,
  type PreviewResponse,
  type UploadKind,
} from "@/services/portfolio";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";

const EXAMPLE =
  "10к евро налик в минске, 2 битка на кошельке, депозит 1000 лари в боге под 9%";
const EXAMPLE_EN =
  "€10k cash in Minsk, 2 BTC in a wallet, a 1000 GEL deposit at 9%";

const ASSET_TYPES = Object.keys(ASSET_TYPE_LABELS) as AssetType[];

function errMessage(e: unknown): string {
  if (e instanceof AxiosError) {
    return (e.response?.data as { detail?: string })?.detail ?? e.message;
  }
  return e instanceof Error ? e.message : "Что-то пошло не так";
}

export function InputPage() {
  const t = useT();
  const labels = useAssetLabels();
  const example = t(EXAMPLE, EXAMPLE_EN);
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [assets, setAssets] = useState<AssetItem[]>([]);
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const onPreview = (data: PreviewResponse) => {
    setPreview(data);
    setAssets(data.assets);
  };

  const submit = useMutation({
    mutationFn: () => submitInput({ text: text.trim() }),
    onSuccess: onPreview,
  });

  const upload = useMutation({
    mutationFn: (args: { file: Blob; kind: UploadKind; name?: string }) =>
      uploadInput(args.file, args.kind, args.name),
    onSuccess: onPreview,
  });

  const importer = useMutation({
    mutationFn: (args: { file: Blob; name?: string }) =>
      importTable(args.file, args.name),
    onSuccess: onPreview,
  });

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        upload.mutate({ file: blob, kind: "voice", name: "voice.webm" });
      };
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      /* mic permission denied — silently ignore */
    }
  }

  function stopRecording() {
    recorderRef.current?.stop();
    setRecording(false);
  }

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const name = file.name.toLowerCase();
    const isTable =
      name.endsWith(".csv") ||
      name.endsWith(".xlsx") ||
      name.endsWith(".xls") ||
      file.type.includes("csv") ||
      file.type.includes("spreadsheet") ||
      file.type.includes("excel");
    if (isTable) {
      // Deterministic table import (no LLM) — reliable for structured data.
      importer.mutate({ file, name: file.name });
    } else {
      const kind: UploadKind = file.type.startsWith("image/") ? "image" : "file";
      upload.mutate({ file, kind, name: file.name });
    }
    e.target.value = "";
  }

  const confirm = useMutation({
    mutationFn: () =>
      confirmInput({ thread_id: preview!.thread_id, assets }),
    onSuccess: () => navigate("/"),
  });

  function updateAsset(idx: number, patch: Partial<AssetItem>) {
    setAssets((prev) =>
      prev.map((a, i) => (i === idx ? { ...a, ...patch } : a)),
    );
  }

  function removeAsset(idx: number) {
    setAssets((prev) => prev.filter((_, i) => i !== idx));
  }

  const reset = () => {
    setPreview(null);
    setAssets([]);
  };

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t("Добавить активы", "Add assets")}</h1>
        <Button variant="ghost" onClick={() => navigate("/")}>
          {t("← Назад", "← Back")}
        </Button>
      </header>

      {!preview && (
        <section className="mt-8">
          <label className="text-sm text-muted-foreground">
            {t(
              "Опишите свои активы свободным текстом — на любом языке.",
              "Describe your assets in free text — in any language.",
            )}
          </label>
          <textarea
            className="mt-2 min-h-[140px] w-full rounded-md border border-input bg-background p-3 text-sm outline-none ring-ring focus-visible:ring-2"
            placeholder={example}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="mt-2 flex items-center gap-3">
            <Button
              onClick={() => submit.mutate()}
              disabled={!text.trim() || submit.isPending}
            >
              {submit.isPending ? t("Разбираю…", "Parsing…") : t("Разобрать", "Parse")}
            </Button>
            <button
              type="button"
              className="text-sm text-muted-foreground underline-offset-2 hover:underline"
              onClick={() => setText(example)}
            >
              {t("Вставить пример", "Insert example")}
            </button>
          </div>
          {submit.isError && (
            <p className="mt-3 text-sm text-red-500">{errMessage(submit.error)}</p>
          )}

          <div className="mt-6 border-t border-border pt-4">
            <p className="text-sm text-muted-foreground">
              {t(
                "…или загрузите голос, таблицу (Excel/CSV) или скриншот.",
                "…or upload voice, a spreadsheet (Excel/CSV) or a screenshot.",
              )}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              {recording ? (
                <Button
                  className="bg-red-500 text-white hover:bg-red-600"
                  onClick={stopRecording}
                >
                  {t("● Остановить запись", "● Stop recording")}
                </Button>
              ) : (
                <Button
                  variant="outline"
                  onClick={startRecording}
                  disabled={upload.isPending}
                >
                  {t("🎙 Записать голос", "🎙 Record voice")}
                </Button>
              )}

              <label className="inline-flex cursor-pointer items-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium hover:bg-accent">
                {t("📎 Загрузить файл", "📎 Upload file")}
                <input
                  type="file"
                  className="hidden"
                  accept=".xlsx,.xls,.csv,image/*"
                  onChange={onFile}
                  disabled={upload.isPending}
                />
              </label>

              {(upload.isPending || importer.isPending) && (
                <span className="text-sm text-muted-foreground">
                  {t("Обрабатываю…", "Processing…")}
                </span>
              )}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {t(
                "Таблица импортируется точно по колонкам (тип, сумма, валюта…).",
                "Spreadsheets import precisely by columns (type, amount, currency…).",
              )}{" "}
              <button
                type="button"
                className="underline-offset-2 hover:underline"
                onClick={() => downloadImportTemplate()}
              >
                {t("Скачать шаблон CSV", "Download CSV template")}
              </button>
            </p>
            {upload.isError && (
              <p className="mt-3 text-sm text-red-500">{errMessage(upload.error)}</p>
            )}
            {importer.isError && (
              <p className="mt-3 text-sm text-red-500">{errMessage(importer.error)}</p>
            )}
          </div>
        </section>
      )}

      {preview && (
        <section className="mt-8 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-medium">
              {t("Предпросмотр", "Preview")} ({assets.length})
            </h2>
            <Button variant="ghost" onClick={reset}>
              {t("Заново", "Start over")}
            </Button>
          </div>

          {preview.validation && preview.validation.warnings.length > 0 && (
            <ul className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-700">
              {preview.validation.warnings.map((w, i) => (
                <li key={i}>⚠ {w}</li>
              ))}
            </ul>
          )}

          {assets.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {t(
                "Активы не распознаны. Вернитесь и уточните описание.",
                "No assets recognized. Go back and refine the description.",
              )}
            </p>
          )}

          <div className="space-y-3">
            {assets.map((a, idx) => (
              <div
                key={idx}
                className="rounded-lg border border-border bg-card p-4"
              >
                <div className="flex items-center justify-between">
                  <select
                    className="rounded-md border border-input bg-background px-2 py-1 text-sm"
                    value={a.asset_type}
                    onChange={(e) =>
                      updateAsset(idx, {
                        asset_type: e.target.value as AssetType,
                      })
                    }
                  >
                    {ASSET_TYPES.map((ty) => (
                      <option key={ty} value={ty}>
                        {labels[ty]}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="text-sm text-red-500 hover:underline"
                    onClick={() => removeAsset(idx)}
                  >
                    {t("Удалить", "Remove")}
                  </button>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
                  <label className="text-xs text-muted-foreground">
                    {t("Сумма", "Amount")}
                    <Input
                      className="mt-1"
                      value={String(a.amount ?? "")}
                      onChange={(e) =>
                        updateAsset(idx, { amount: e.target.value })
                      }
                    />
                  </label>
                  <label className="text-xs text-muted-foreground">
                    {t("Валюта", "Currency")}
                    <Input
                      className="mt-1"
                      value={a.currency ?? ""}
                      onChange={(e) =>
                        updateAsset(idx, { currency: e.target.value })
                      }
                    />
                  </label>
                  <label className="text-xs text-muted-foreground">
                    {t("Место", "Place")}
                    <Input
                      className="mt-1"
                      value={a.location ?? ""}
                      onChange={(e) =>
                        updateAsset(idx, { location: e.target.value })
                      }
                    />
                  </label>
                  {(a.asset_type === "real_estate" ||
                    a.asset_type === "vehicle") && (
                    <label className="text-xs text-muted-foreground">
                      {t("Удорожание, %/год", "Appreciation, %/yr")}
                      <Input
                        className="mt-1"
                        inputMode="numeric"
                        placeholder={a.asset_type === "vehicle" ? "-15" : "5"}
                        value={
                          a.appreciation_rate == null
                            ? ""
                            : String(a.appreciation_rate)
                        }
                        onChange={(e) =>
                          updateAsset(idx, {
                            appreciation_rate: e.target.value || null,
                          })
                        }
                      />
                    </label>
                  )}
                </div>

                {a.note && (
                  <p className="mt-2 text-xs text-muted-foreground">{a.note}</p>
                )}
              </div>
            ))}
          </div>

          {confirm.isError && (
            <p className="text-sm text-red-500">{errMessage(confirm.error)}</p>
          )}

          <div className="flex gap-3">
            <Button
              onClick={() => confirm.mutate()}
              disabled={assets.length === 0 || confirm.isPending}
            >
              {confirm.isPending
                ? t("Сохраняю…", "Saving…")
                : t("Подтвердить и сохранить", "Confirm and save")}
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
