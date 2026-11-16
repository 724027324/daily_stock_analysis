import React, { useCallback, useEffect, useState } from 'react';
import {
  cancelExecutionIntent,
  confirmExecutionIntent,
  listExecutionIntents,
  rejectExecutionIntent,
} from '../api/execution';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { ExecutionIntentItem, ExecutionIntentStatus } from '../types/execution';

const STATUS_FILTERS: Array<ExecutionIntentStatus | ''> = [
  '',
  'pending_confirm',
  'submitted',
  'rejected',
  'failed',
  'cancelled',
  'auto_submit',
];

const ExecutionIntentsPage: React.FC = () => {
  const { t } = useUiLanguage();
  const [status, setStatus] = useState<ExecutionIntentStatus | ''>('pending_confirm');
  const [items, setItems] = useState<ExecutionIntentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ExecutionIntentItem | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listExecutionIntents({
        status: status || undefined,
        page: 1,
        pageSize: 50,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const runAction = async (intent: ExecutionIntentItem, action: 'confirm' | 'reject' | 'cancel') => {
    setBusyId(intent.id);
    setError(null);
    try {
      if (action === 'confirm') {
        await confirmExecutionIntent(intent.id);
      } else if (action === 'reject') {
        await rejectExecutionIntent(intent.id);
      } else {
        await cancelExecutionIntent(intent.id);
      }
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
      setConfirmTarget(null);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-4 md:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">{t('execution.title')}</h1>
          <p className="mt-1 text-sm text-secondary-text">{t('execution.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-secondary-text" htmlFor="execution-status-filter">
            {t('execution.filterStatus')}
          </label>
          <select
            id="execution-status-filter"
            className="rounded-xl border border-[var(--shell-sidebar-border)] bg-card px-3 py-2 text-sm"
            value={status}
            onChange={(event) => setStatus(event.target.value as ExecutionIntentStatus | '')}
          >
            {STATUS_FILTERS.map((value) => (
              <option key={value || 'all'} value={value}>
                {value ? t(`execution.status.${value}` as 'execution.status.pending_confirm') : t('execution.statusAll')}
              </option>
            ))}
          </select>
          <button type="button" className="btn-secondary" onClick={() => void reload()} disabled={loading}>
            {t('common.refresh')}
          </button>
        </div>
      </div>

      {error ? <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600">{error}</div> : null}

      <div className="overflow-x-auto rounded-2xl border border-[var(--shell-sidebar-border)] bg-card">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-[var(--shell-sidebar-border)] text-secondary-text">
            <tr>
              <th className="px-3 py-2">{t('execution.col.id')}</th>
              <th className="px-3 py-2">{t('execution.col.stock')}</th>
              <th className="px-3 py-2">{t('execution.col.side')}</th>
              <th className="px-3 py-2">{t('execution.col.qty')}</th>
              <th className="px-3 py-2">{t('execution.col.price')}</th>
              <th className="px-3 py-2">{t('execution.col.status')}</th>
              <th className="px-3 py-2">{t('execution.col.broker')}</th>
              <th className="px-3 py-2">{t('execution.col.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td className="px-3 py-6 text-secondary-text" colSpan={8}>
                  {loading ? t('common.loading') : t('execution.empty')}
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.id} className="border-t border-[var(--shell-sidebar-border)]">
                  <td className="px-3 py-2">{item.id}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium">{item.stockCode}</div>
                    <div className="text-xs text-secondary-text">{item.stockName || item.action}</div>
                  </td>
                  <td className="px-3 py-2">{item.side}</td>
                  <td className="px-3 py-2">{item.quantity}</td>
                  <td className="px-3 py-2">{item.limitPrice}</td>
                  <td className="px-3 py-2">
                    <div>{t(`execution.status.${item.status}` as 'execution.status.pending_confirm')}</div>
                    {item.rejectReason ? (
                      <div className="text-xs text-secondary-text">{item.rejectReason}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2">
                    {item.broker}
                    {item.liveEnabled ? '' : ` / ${t('execution.paper')}`}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-2">
                      {item.status === 'pending_confirm' ? (
                        <>
                          <button
                            type="button"
                            className="btn-primary"
                            disabled={busyId === item.id}
                            onClick={() => setConfirmTarget(item)}
                          >
                            {t('execution.confirm')}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busyId === item.id}
                            onClick={() => void runAction(item, 'reject')}
                          >
                            {t('execution.reject')}
                          </button>
                        </>
                      ) : null}
                      {item.status === 'pending_confirm' || item.status === 'auto_submit' || item.status === 'draft' ? (
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyId === item.id}
                          onClick={() => void runAction(item, 'cancel')}
                        >
                          {t('execution.cancel')}
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-secondary-text">
        {t('execution.total')}: {total}
      </p>

      <ConfirmDialog
        isOpen={confirmTarget != null}
        title={t('execution.confirmTitle')}
        message={
          confirmTarget
            ? t('execution.confirmBody')
                .replace('{code}', confirmTarget.stockCode)
                .replace('{side}', confirmTarget.side)
                .replace('{qty}', String(confirmTarget.quantity))
                .replace('{price}', String(confirmTarget.limitPrice))
            : ''
        }
        confirmText={t('execution.confirm')}
        cancelText={t('common.cancel')}
        onCancel={() => setConfirmTarget(null)}
        onConfirm={() => {
          if (confirmTarget) void runAction(confirmTarget, 'confirm');
        }}
      />
    </div>
  );
};

export default ExecutionIntentsPage;
