import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import {
  ASSET_TYPE_LABELS,
  addAsset,
  deleteAsset,
  updateAsset,
  useAssetLabels,
  type AssetRow,
  type AssetType,
  type AssetUpsert,
} from "@/services/portfolio";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";

const TYPES = Object.keys(ASSET_TYPE_LABELS) as AssetType[];

interface Draft {
  asset_type: AssetType;
  amount: string;
  currency: string;
  location: string;
  ticker: string;
  appreciation_rate: string;
}

function emptyDraft(): Draft {
  return {
    asset_type: "cash",
    amount: "",
    currency: "USD",
    location: "",
    ticker: "",
    appreciation_rate: "",
  };
}

function rowToDraft(a: AssetRow): Draft {
  return {
    asset_type: a.asset_type,
    amount: a.amount ?? "",
    currency: a.currency ?? "USD",
    location: a.location ?? "",
    ticker: a.ticker ?? a.symbol ?? "",
    appreciation_rate: a.appreciation_rate ?? "",
  };
}

function draftToUpsert(d: Draft): AssetUpsert {
  const body: AssetUpsert = {
    asset_type: d.asset_type,
    amount: Number(d.amount),
    currency: d.currency.trim().toUpperCase() || "USD",
    location: d.location.trim() || null,
  };
  if (d.asset_type === "stock" || d.asset_type === "crypto") {
    body.ticker = d.ticker.trim().toUpperCase() || null;
    body.symbol = d.ticker.trim().toUpperCase() || null;
  }
  if (d.asset_type === "real_estate" || d.asset_type === "vehicle") {
    body.appreciation_rate = d.appreciation_rate
      ? Number(d.appreciation_rate)
      : null;
  }
  return body;
}

function DraftForm({
  draft,
  setDraft,
  onSubmit,
  onCancel,
  pending,
  submitLabel,
}: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  onSubmit: () => void;
  onCancel: () => void;
  pending: boolean;
  submitLabel: string;
}) {
  const t = useT();
  const labels = useAssetLabels();
  const showTicker = draft.asset_type === "stock" || draft.asset_type === "crypto";
  const showAppr =
    draft.asset_type === "real_estate" || draft.asset_type === "vehicle";
  return (
    <form
      className="mt-2 grid grid-cols-2 gap-2 rounded-md border border-border p-3 sm:grid-cols-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (Number(draft.amount) > 0) onSubmit();
      }}
    >
      <label className="text-xs text-muted-foreground">
        {t("Тип", "Type")}
        <select
          className="mt-1 block w-full rounded-md border border-input bg-background px-2 py-2 text-sm"
          value={draft.asset_type}
          onChange={(e) =>
            setDraft({ ...draft, asset_type: e.target.value as AssetType })
          }
        >
          {TYPES.map((ty) => (
            <option key={ty} value={ty}>
              {labels[ty]}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs text-muted-foreground">
        {draft.asset_type === "crypto" || draft.asset_type === "stock"
          ? t("Кол-во", "Quantity")
          : t("Сумма", "Amount")}
        <Input
          className="mt-1"
          inputMode="decimal"
          value={draft.amount}
          onChange={(e) => setDraft({ ...draft, amount: e.target.value })}
        />
      </label>
      <label className="text-xs text-muted-foreground">
        {t("Валюта", "Currency")}
        <Input
          className="mt-1"
          value={draft.currency}
          onChange={(e) => setDraft({ ...draft, currency: e.target.value })}
        />
      </label>
      {showTicker && (
        <label className="text-xs text-muted-foreground">
          {t("Тикер/символ", "Ticker/symbol")}
          <Input
            className="mt-1"
            placeholder="AAPL / BTC"
            value={draft.ticker}
            onChange={(e) => setDraft({ ...draft, ticker: e.target.value })}
          />
        </label>
      )}
      {showAppr && (
        <label className="text-xs text-muted-foreground">
          {t("Удорожание, %/год", "Appreciation, %/yr")}
          <Input
            className="mt-1"
            inputMode="numeric"
            placeholder={draft.asset_type === "vehicle" ? "-15" : "5"}
            value={draft.appreciation_rate}
            onChange={(e) =>
              setDraft({ ...draft, appreciation_rate: e.target.value })
            }
          />
        </label>
      )}
      <label className="text-xs text-muted-foreground">
        {t("Место", "Place")}
        <Input
          className="mt-1"
          value={draft.location}
          onChange={(e) => setDraft({ ...draft, location: e.target.value })}
        />
      </label>
      <div className="col-span-2 flex gap-2 sm:col-span-3">
        <Button type="submit" disabled={pending || Number(draft.amount) <= 0}>
          {pending ? "…" : submitLabel}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t("Отмена", "Cancel")}
        </Button>
      </div>
    </form>
  );
}

export function AssetsEditor({
  assets,
  money,
}: {
  assets: AssetRow[];
  money: (v: string | number | null | undefined) => string;
}) {
  const t = useT();
  const labels = useAssetLabels();
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["portfolio"] });
  const [adding, setAdding] = useState(false);
  const [addDraft, setAddDraft] = useState<Draft>(emptyDraft());
  const [editId, setEditId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(emptyDraft());

  const create = useMutation({
    mutationFn: () => addAsset(draftToUpsert(addDraft)),
    onSuccess: () => {
      setAdding(false);
      setAddDraft(emptyDraft());
      invalidate();
    },
  });
  const save = useMutation({
    mutationFn: () => updateAsset(editId!, draftToUpsert(editDraft)),
    onSuccess: () => {
      setEditId(null);
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteAsset(id),
    onSuccess: invalidate,
  });

  return (
    <section className="rounded-lg border border-border bg-card p-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">
          {t("Активы", "Assets")} ({assets.length})
        </h2>
        <Button
          variant="outline"
          onClick={() => {
            setAdding((v) => !v);
            setEditId(null);
          }}
        >
          <Plus className="mr-1 h-4 w-4" />
          {t("Актив", "Asset")}
        </Button>
      </div>

      {adding && (
        <DraftForm
          draft={addDraft}
          setDraft={setAddDraft}
          onSubmit={() => create.mutate()}
          onCancel={() => setAdding(false)}
          pending={create.isPending}
          submitLabel={t("Добавить", "Add")}
        />
      )}

      <div className="mt-3 divide-y divide-border">
        {assets.map((a) =>
          editId === a.id ? (
            <DraftForm
              key={a.id}
              draft={editDraft}
              setDraft={setEditDraft}
              onSubmit={() => save.mutate()}
              onCancel={() => setEditId(null)}
              pending={save.isPending}
              submitLabel={t("Сохранить", "Save")}
            />
          ) : (
            <div
              key={a.id}
              className="flex items-center justify-between py-2 text-sm"
            >
              <div className="min-w-0">
                <span className="font-medium">
                  {labels[a.asset_type] ?? a.asset_type}
                </span>
                <span className="ml-2 text-muted-foreground">
                  {a.amount} {a.symbol ?? a.currency}
                  {a.location ? ` · ${a.location}` : ""}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="tabular-nums">
                  {money(a.estimated_usd ?? a.usd_value)}
                </span>
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground"
                  aria-label={t("Изменить", "Edit")}
                  onClick={() => {
                    setEditId(a.id);
                    setEditDraft(rowToDraft(a));
                    setAdding(false);
                  }}
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  className="text-muted-foreground hover:text-red-500"
                  aria-label={t("Удалить", "Delete")}
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(a.id)}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ),
        )}
      </div>
    </section>
  );
}
