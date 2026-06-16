import { api } from "./api";
import type { AssetItem } from "./portfolio";

export interface ScenarioComparison {
  base_total_usd?: string | null;
  new_total_usd?: string | null;
  delta_usd?: string | null;
  delta_pct?: string | null;
  base_asset_count?: number;
  new_asset_count?: number;
}

export interface ScenarioResponse {
  result_total_usd?: string | null;
  comparison: ScenarioComparison;
  assets: AssetItem[];
  advice: Array<{
    title: string;
    category?: string | null;
    body: string;
    relevance?: string | null;
    disclaimer?: string | null;
  }>;
  changes: Array<Record<string, unknown>>;
}

export async function simulateScenario(body: {
  scenario_text: string;
  base_snapshot_id?: string;
}): Promise<ScenarioResponse> {
  const { data } = await api.post<ScenarioResponse>(
    "/scenarios/simulate",
    body,
  );
  return data;
}
