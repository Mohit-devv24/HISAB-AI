'use client';

import React from 'react';
import { Mic, MicOff, Check, X, Sparkles, Volume2 } from 'lucide-react';
import { PendingVoiceOperation } from '../types';
import { formatINR } from '../utils/formatters';

interface VoiceAssistantProps {
  isListening: boolean;
  isProcessing: boolean;
  transcript: string;
  statusMessage: string;
  pendingOperation: PendingVoiceOperation | null;
  onToggleMic: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export const VoiceAssistant: React.FC<VoiceAssistantProps> = ({
  isListening,
  isProcessing,
  transcript,
  statusMessage,
  pendingOperation,
  onToggleMic,
  onConfirm,
  onCancel,
}) => {
  return (
    <>
      {/* Floating Pending Confirmation Card (Two-step write validation) */}
      {pendingOperation && (
        <div className="fixed bottom-24 right-6 md:right-10 z-50 max-w-sm w-full bg-[#1e1e24] border-2 border-amber-500/80 rounded-2xl p-5 shadow-2xl backdrop-blur-xl animate-in slide-in-from-bottom duration-300">
          <div className="flex items-center gap-2 mb-2 text-amber-400 font-bold text-xs uppercase tracking-wider">
            <Sparkles className="w-4 h-4 animate-spin" />
            <span>Confirm Voice Transaction</span>
          </div>

          <div className="bg-black/40 rounded-xl p-3 mb-4 border border-white/5">
            <div className="text-sm text-gray-300">
              Customer:{' '}
              <span className="font-bold text-white">
                {pendingOperation.customer_name}
              </span>
            </div>
            <div className="text-xl font-black text-white mt-1">
              {formatINR(pendingOperation.amount)}
            </div>
            <div className="text-xs font-semibold mt-1">
              Type:{' '}
              <span
                className={
                  pendingOperation.transaction_type === 'UDHAAR'
                    ? 'text-rose-400'
                    : 'text-emerald-400'
                }
              >
                {pendingOperation.transaction_type}
              </span>
            </div>
          </div>

          <p className="text-xs text-gray-400 mb-4">
            Speak &quot;Haan save karo&quot; or click confirm:
          </p>

          <div className="flex gap-2">
            <button
              onClick={onConfirm}
              className="flex-1 py-2.5 px-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold flex items-center justify-center gap-1.5 shadow-lg shadow-emerald-600/30 transition active:scale-95"
            >
              <Check className="w-4 h-4" />
              <span>Haan, Save Karo</span>
            </button>
            <button
              onClick={onCancel}
              className="py-2.5 px-3 rounded-xl bg-white/10 hover:bg-white/20 text-gray-300 text-xs font-bold flex items-center justify-center gap-1.5 transition active:scale-95"
            >
              <X className="w-4 h-4" />
              <span>Cancel</span>
            </button>
          </div>
        </div>
      )}

      {/* Floating Microphone & Voice Hub */}
      <div className="fixed bottom-6 right-6 md:right-10 z-40 flex items-center gap-3">
        {/* Real-time Voice Status Pill */}
        <div className="hidden sm:flex flex-col items-end">
          <div className="bg-[#1C1C1E] border border-white/10 px-4 py-2 rounded-2xl shadow-xl text-xs max-w-xs text-right">
            <div className="font-semibold text-white flex items-center justify-end gap-1.5">
              <span>{isListening ? '🎙️ Listening (hi-IN)' : isProcessing ? '⚡ Groq Processing...' : 'Suno Hisab Voice AI'}</span>
            </div>
            <div className="text-[11px] text-gray-400 truncate mt-0.5">
              {statusMessage}
            </div>
          </div>
        </div>

        {/* Circular Action Mic Button */}
        <button
          type="button"
          onClick={onToggleMic}
          aria-label={isListening ? 'Stop listening' : 'Start voice recognition'}
          className={`w-14 h-14 md:w-16 md:h-16 rounded-full flex items-center justify-center text-white shadow-2xl transition-all duration-200 active:scale-95 ${
            isListening
              ? 'bg-rose-600 shadow-rose-600/50 mic-recording-pulse'
              : isProcessing
              ? 'bg-amber-600 shadow-amber-600/50 animate-pulse'
              : 'bg-gradient-to-r from-amber-600 to-amber-500 hover:from-amber-500 hover:to-amber-400 shadow-amber-500/30'
          }`}
        >
          {isListening ? (
            <Mic className="w-6 h-6 md:w-7 md:h-7 animate-bounce" />
          ) : (
            <Mic className="w-6 h-6 md:w-7 md:h-7" />
          )}
        </button>
      </div>
    </>
  );
};
