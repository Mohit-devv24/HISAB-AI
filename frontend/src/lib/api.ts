import axios from 'axios';
import { Transaction, VoiceAssistantResponse, DailySummary, ShopkeeperProfile } from '../types';

const isBrowser = typeof window !== 'undefined';
// In the browser, use relative URL so Next.js rewrites forward to FastAPI without CORS issues
const API_BASE = isBrowser ? '' : (process.env.NEXT_PUBLIC_BACKEND_URL || 'http://127.0.0.1:8000');

const client = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
});

export const api = {
  // Voice Assistant API (powered by Groq LPU + Whisper on FastAPI)
  sendVoiceCommand: async (
    transcript: string,
    pendingOperationId: string | null = null,
    action: string = 'process'
  ): Promise<VoiceAssistantResponse> => {
    try {
      const resp = await client.post<VoiceAssistantResponse>('/api/voice-assistant', {
        text: transcript,
        pending_operation_id: pendingOperationId,
        action,
      });
      return resp.data;
    } catch (err: any) {
      console.error('[API] sendVoiceCommand error:', err);
      throw new Error(err.response?.data?.detail || 'Voice processing failed');
    }
  },

  // Raw Audio Voice API (Groq Whisper Speech-to-Text)
  sendVoiceAudioBlob: async (
    audioBlob: Blob,
    pendingOpId: string | null = null
  ): Promise<VoiceAssistantResponse> => {
    try {
      const formData = new FormData();
      formData.append('file', audioBlob, 'command.webm');
      if (pendingOpId) formData.append('pending_operation_id', pendingOpId);

      const resp = await client.post<VoiceAssistantResponse>('/api/voice-audio', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return resp.data;
    } catch (err: any) {
      console.error('[API] sendVoiceAudioBlob error:', err);
      throw new Error(err.response?.data?.detail || 'Audio transcription failed');
    }
  },

  // Transactions
  getTransactions: async (limit = 50, offset = 0): Promise<Transaction[]> => {
    try {
      const resp = await client.get('/api/transactions', { params: { limit, offset } });
      const raw = resp.data?.transactions || resp.data || [];
      return raw.map((t: any) => ({
        ...t,
        amount: t.amount !== undefined ? t.amount : (t.amount_paise || 0) / 100,
      }));
    } catch (err) {
      console.warn('[API] getTransactions error, returning mock cache:', err);
      return [];
    }
  },

  createTransaction: async (tx: Partial<Transaction>): Promise<Transaction> => {
    const resp = await client.post<Transaction>('/api/transactions', tx);
    return resp.data;
  },

  // Analytics & Summaries
  getDailySummary: async (date?: string): Promise<DailySummary> => {
    try {
      const resp = await client.get('/api/analytics/daily-summary', { params: { date } });
      return resp.data;
    } catch (err) {
      return {
        total_udhaar: 0,
        total_jama: 0,
        net_balance: 0,
        active_customers_count: 0,
        transactions_count: 0,
      };
    }
  },

  // Health
  getHealth: async (): Promise<{ status: string; ai_model: string; ai_configured: boolean }> => {
    const resp = await client.get('/api/health');
    return resp.data;
  },
};
