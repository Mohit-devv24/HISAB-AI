import { apiRequest } from './api';

export const VoiceService = {
  sendTranscript: (transcript: string, confirmed = false, pendingOpId: string | null = null) =>
    apiRequest('/api/voice-assistant', {
      method: 'POST',
      body: JSON.stringify({ transcript, confirmed, pending_operation_id: pendingOpId })
    }),
  sendAudioBlob: async (audioBlob: Blob) => {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'voice_command.wav');
    const res = await fetch('/api/voice-audio', { method: 'POST', body: formData });
    return res.json();
  }
};
