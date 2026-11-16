export type ExecutionIntentStatus =
  | 'draft'
  | 'pending_confirm'
  | 'auto_submit'
  | 'submitted'
  | 'rejected'
  | 'cancelled'
  | 'failed';

export type ExecutionConfirmMode = 'manual' | 'auto';

export type ExecutionIntentItem = {
  id: number;
  signalId: number;
  stockCode: string;
  stockName?: string | null;
  market: string;
  action: string;
  side: string;
  quantity: number;
  limitPrice: number;
  notionalCny?: number | null;
  status: ExecutionIntentStatus;
  confirmMode: ExecutionConfirmMode;
  broker: string;
  idempotencyKey: string;
  rejectReason?: string | null;
  brokerOrderId?: string | null;
  portfolioAccountId?: number | null;
  liveEnabled: boolean;
  metadata?: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
  submittedAt?: string | null;
};

export type ExecutionIntentListResponse = {
  items: ExecutionIntentItem[];
  total: number;
  page: number;
  pageSize: number;
};

export type ExecutionIntentListParams = {
  status?: ExecutionIntentStatus | '';
  stockCode?: string;
  signalId?: number;
  page?: number;
  pageSize?: number;
};
