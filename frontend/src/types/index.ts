export type TransactionType = 'UDHAAR' | 'JAMA';

export interface Transaction {
  id: number | string;
  customer_name: string;
  customer_email?: string;
  amount: number;
  amount_paise?: number;
  type: TransactionType;
  notes?: string;
  created_at: string;
}

export interface PendingVoiceOperation {
  operation_id: string;
  customer_name: string;
  customer_email?: string;
  amount: number;
  transaction_type: TransactionType;
  notes?: string;
  status: 'pending' | 'waiting_for_email' | 'committed' | 'cancelled';
}

export interface VoiceAssistantResponse {
  reply_message: string;
  action_taken: string;
  requires_confirmation: boolean;
  transcribed_text?: string;
  pending_operation?: PendingVoiceOperation | null;
  data?: any;
}

export interface LLMParsedResponse {
  intent: 'NEW_TRANSACTION' | 'CONFIRMATION' | 'CANCELLATION' | 'QUERY_BALANCE' | 'UNKNOWN';
  customer_name?: string | null;
  amount?: number | null;
  transaction_type?: TransactionType | null;
  customer_email?: string | null;
  description?: string;
}

export interface ShopkeeperProfile {
  id: number;
  email: string;
  full_name: string;
  shop_name: string;
  role: 'owner' | 'manager' | 'staff';
}

export interface DailySummary {
  total_udhaar: number;
  total_jama: number;
  net_balance: number;
  active_customers_count: number;
  transactions_count: number;
}
