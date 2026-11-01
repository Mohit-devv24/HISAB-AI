import { useState } from 'react';
import { VoiceService } from '../services/voice.service';

export function useVoice() {
  const [isListening, setIsListening] = useState(false);
  const [status, setStatus] = useState<string>('idle');

  return { isListening, status, setIsListening, setStatus };
}
