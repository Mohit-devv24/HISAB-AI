import React, { useState } from 'react';

export const VoiceAssistant: React.FC = () => {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState('Tap to speak in Hindi/Hinglish');

  const toggleMic = () => {
    setIsRecording(!isRecording);
    setStatus(!isRecording ? 'Listening... Speak now' : 'Stopped.');
  };

  return (
    <div className="fixed bottom-6 right-6 flex flex-col items-end gap-2 z-40">
      <div className="bg-slate-900 border border-slate-700 px-4 py-2 rounded-xl text-xs text-slate-300 shadow-xl">
        {status}
      </div>
      <button
        onClick={toggleMic}
        className={`w-14 h-14 rounded-full flex items-center justify-center text-2xl shadow-2xl transition active:scale-95 ${
          isRecording ? 'bg-rose-600 animate-pulse' : 'bg-blue-600 hover:bg-blue-500'
        }`}
      >
        🎙️
      </button>
    </div>
  );
};
