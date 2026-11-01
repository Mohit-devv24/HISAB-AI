import { apiRequest } from './api';
import { Transaction } from '../types';

export const TransactionService = {
  list: (params?: { limit?: number; offset?: number }) =>
    apiRequest<{ transactions: Transaction[] }>('/api/transactions'),
  create: (data: Partial<Transaction>) =>
    apiRequest<Transaction>('/api/transactions', { method: 'POST', body: JSON.stringify(data) })
};
