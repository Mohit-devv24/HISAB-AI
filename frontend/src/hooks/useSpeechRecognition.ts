'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../lib/api';
import { VoiceAssistantResponse, PendingVoiceOperation } from '../types';

export interface UseSpeechRecognitionOptions {
  onSuccess?: (res: VoiceAssistantResponse) => void;
  onError?: (error: string) => void;
  lang?: string;
}

export function useSpeechRecognition({ onSuccess, onError, lang = 'hi-IN' }: UseSpeechRecognitionOptions = {}) {
  const [isListening, setIsListening] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [statusMessage, setStatusMessage] = useState('Tap to speak in Hindi/Hinglish');
  const [activePendingOperation, setActivePendingOperation] = useState<PendingVoiceOperation | null>(null);

  const recognitionRef = useRef<any>(null);
  const silenceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const isSpeakingRef = useRef(false);

  // Initialize SpeechRecognition safely
  useEffect(() => {
    if (typeof window === 'undefined') return;

    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setStatusMessage('Voice recognition is not supported in this browser.');
      return;
    }

    const recognizer = new SpeechRecognition();
    recognizer.continuous = true;
    recognizer.interimResults = true;
    recognizer.lang = lang;

    recognizer.onstart = () => {
      setIsListening(true);
      setStatusMessage('Listening... Speak now in Hindi or Hinglish.');
    };

    recognizer.onresult = (event: any) => {
      if (isSpeakingRef.current) return; // Prevent acoustic feedback loop

      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);

      let interim = '';
      let finalChunk = '';

      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          finalChunk += event.results[i][0].transcript;
        } else {
          interim += event.results[i][0].transcript;
        }
      }

      const heard = (finalChunk || interim).trim();
      if (heard) {
        setTranscript(heard);
        setStatusMessage(`Heard: "${heard}"`);

        // 1300ms Silence debouncer: process sentence once speaker pauses
        silenceTimerRef.current = setTimeout(() => {
          handleProcessVoiceInput(heard);
        }, 1300);
      }
    };

    recognizer.onerror = (event: any) => {
      console.warn('[Speech] Recognition error:', event.error);
      if (event.error === 'not-allowed') {
        setStatusMessage('Microphone access denied. Please allow microphone permissions.');
        onError?.('Microphone access denied');
      } else if (event.error !== 'no-speech') {
        setStatusMessage(`Error: ${event.error}`);
      }
    };

    recognizer.onend = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognizer;

    return () => {
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch {}
      }
    };
  }, [lang]);

  // TTS Response playback with feedback gating
  const speakResponse = useCallback((text: string, onDone?: () => void) => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
      onDone?.();
      return;
    }

    isSpeakingRef.current = true;
    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'hi-IN';
    utterance.rate = 1.0;

    utterance.onend = () => {
      setTimeout(() => {
        isSpeakingRef.current = false;
        onDone?.();
      }, 400); // 400ms acoustic grace window
    };

    utterance.onerror = () => {
      isSpeakingRef.current = false;
      onDone?.();
    };

    window.speechSynthesis.speak(utterance);
  }, []);

  // Process voice input through Groq/FastAPI
  const handleProcessVoiceInput = async (spokenText: string) => {
    if (!spokenText.trim()) return;

    try {
      setIsProcessing(true);
      setStatusMessage('Processing with Groq AI...');

      const opId = activePendingOperation ? activePendingOperation.operation_id : null;
      const result = await api.sendVoiceCommand(spokenText, opId);

      setStatusMessage(result.reply_message);

      if (result.pending_operation) {
        setActivePendingOperation(result.pending_operation);
      } else if (result.action_taken === 'committed_transaction') {
        setActivePendingOperation(null);
      }

      speakResponse(result.reply_message, () => {
        if (result.requires_confirmation) {
          setStatusMessage('Waiting for confirmation ("Haan save kar do" / "Cancel")');
        } else {
          setStatusMessage('Tap to speak in Hindi/Hinglish');
        }
      });

      onSuccess?.(result);
    } catch (err: any) {
      console.error('[Speech] Error sending voice command:', err);
      setStatusMessage(err.message || 'Error processing speech');
      onError?.(err.message);
    } finally {
      setIsProcessing(false);
    }
  };

  const startListening = () => {
    if (!recognitionRef.current) return;
    try {
      setTranscript('');
      recognitionRef.current.start();
    } catch (e) {
      console.warn('[Speech] start failed:', e);
    }
  };

  const stopListening = () => {
    if (!recognitionRef.current) return;
    try {
      recognitionRef.current.stop();
      setIsListening(false);
    } catch (e) {
      console.warn('[Speech] stop failed:', e);
    }
  };

  const confirmPendingOperation = async () => {
    await handleProcessVoiceInput('yes');
  };

  const cancelPendingOperation = async () => {
    await handleProcessVoiceInput('no');
  };

  return {
    isListening,
    isProcessing,
    transcript,
    statusMessage,
    activePendingOperation,
    startListening,
    stopListening,
    confirmPendingOperation,
    cancelPendingOperation,
  };
}
