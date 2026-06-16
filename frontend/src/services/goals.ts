import { api } from "./api";

export interface Goal {
  id: string;
  title: string;
  target_usd: string;
  target_date: string | null;
  current_usd: string;
  remaining_usd: string;
  progress_pct: string | null;
  achieved: boolean;
  monthly_growth_usd: string | null;
  projected_date: string | null;
  created_at: string | null;
}

export async function listGoals(): Promise<Goal[]> {
  const { data } = await api.get<Goal[]>("/goals");
  return data;
}

export async function createGoal(body: {
  title: string;
  target_usd: number;
  target_date?: string | null;
}): Promise<Goal> {
  const { data } = await api.post<Goal>("/goals", body);
  return data;
}

export async function deleteGoal(id: string): Promise<void> {
  await api.delete(`/goals/${id}`);
}
