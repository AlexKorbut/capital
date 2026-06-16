import { api } from "./api";

export interface AdviceItem {
  id: string;
  title: string;
  category: string | null;
  body: string;
  relevance: string | null;
  disclaimer: string;
  is_read: boolean;
  created_at: string | null;
}

export interface AdviceSession {
  session_id: string;
  snapshot_id: string | null;
  generated_at: string | null;
  advice_count: number;
  items: AdviceItem[];
}

export async function generateAdvice(): Promise<AdviceSession> {
  const { data } = await api.post<AdviceSession>("/advice/generate");
  return data;
}

export async function getLatestAdvice(): Promise<AdviceSession | null> {
  const { data } = await api.get<AdviceSession | null>("/advice/latest");
  return data;
}

export async function markAdviceRead(itemId: string): Promise<void> {
  await api.post(`/advice/${itemId}/read`);
}
