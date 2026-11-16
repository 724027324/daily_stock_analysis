import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  ExecutionIntentItem,
  ExecutionIntentListParams,
  ExecutionIntentListResponse,
} from '../types/execution';

function omitUndefined(input: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined && value !== ''));
}

export async function listExecutionIntents(
  params: ExecutionIntentListParams = {},
): Promise<ExecutionIntentListResponse> {
  const response = await apiClient.get('/execution/intents', {
    params: omitUndefined({
      status: params.status,
      stock_code: params.stockCode,
      signal_id: params.signalId,
      page: params.page,
      page_size: params.pageSize,
    }),
  });
  return toCamelCase<ExecutionIntentListResponse>(response.data);
}

export async function getExecutionIntent(intentId: number): Promise<ExecutionIntentItem> {
  const response = await apiClient.get(`/execution/intents/${intentId}`);
  return toCamelCase<ExecutionIntentItem>(response.data);
}

export async function confirmExecutionIntent(intentId: number): Promise<ExecutionIntentItem> {
  const response = await apiClient.post(`/execution/intents/${intentId}/confirm`);
  return toCamelCase<ExecutionIntentItem>(response.data);
}

export async function rejectExecutionIntent(
  intentId: number,
  reason?: string,
): Promise<ExecutionIntentItem> {
  const response = await apiClient.post(`/execution/intents/${intentId}/reject`, {
    reason: reason || undefined,
  });
  return toCamelCase<ExecutionIntentItem>(response.data);
}

export async function cancelExecutionIntent(
  intentId: number,
  reason?: string,
): Promise<ExecutionIntentItem> {
  const response = await apiClient.post(`/execution/intents/${intentId}/cancel`, {
    reason: reason || undefined,
  });
  return toCamelCase<ExecutionIntentItem>(response.data);
}
