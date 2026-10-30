/**
 * HisabAI - Production Frontend Voice Engine & Client Logic
 * Pure Vanilla JavaScript (No bundlers) adhering to production blueprint.
 * Features 1500ms silence debouncer to ensure speech recognition does not cut off prematurely.
 */

function renderConfirmationCard(data) {
    const card = document.getElementById('voiceConfirmationCard') || document.getElementById('voiceOperationPreviewCard');
    if (card) {
        card.style.display = 'block';
        const details = document.getElementById('voiceConfirmDetails') || document.getElementById('voicePreviewDetails');
        if (details && data) {
            details.innerText = `${data.customer_name || data.customer || 'Customer'} - ₹${data.amount || 0} (${data.transaction_type || data.type || 'UDHAAR'})`;
        }
    }
}

function clearConfirmationCard() {
    const card = document.getElementById('voiceConfirmationCard') || document.getElementById('voiceOperationPreviewCard');
    if (card) {
        card.style.display = 'none';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // If running inside index.html where full voice assistant is already integrated, avoid attaching duplicate recognizer
    if (typeof toggleContinuousVoice === 'function') {
        return;
    }

    const voiceBtn = document.getElementById('voiceAssistantBtn') || document.getElementById('voiceMicBtn');
    const statusText = document.getElementById('voiceStatus') || document.getElementById('voiceStageCaption');

    if (!voiceBtn || !statusText) {
        return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        statusText.innerText = "Speech Recognition not supported in this browser.";
        return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'hi-IN'; // Essential for Hinglish recognition

    let isListening = false;
    let speechDebounceTimer = null;
    let accumulatedTranscript = "";

    voiceBtn.addEventListener('click', () => {
        if (!isListening) {
            try {
                recognition.start();
                isListening = true;
                voiceBtn.classList.add('recording-active');
                statusText.innerText = "Listening... Speak now in Hindi or Hinglish.";
            } catch (e) {
                console.warn("[Voice Engine] recognition.start error:", e);
            }
        } else {
            try {
                recognition.stop();
                isListening = false;
                voiceBtn.classList.remove('recording-active');
                statusText.innerText = "Voice Assistant stopped.";
            } catch (e) {
                console.warn("[Voice Engine] recognition.stop error:", e);
            }
        }
    });

    recognition.onresult = (event) => {
        // Clear previous timeout since user is still speaking
        if (speechDebounceTimer) clearTimeout(speechDebounceTimer);

        let interim = '';
        let finalChunk = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
            if (event.results[i].isFinal) {
                finalChunk += event.results[i][0].transcript;
            } else {
                interim += event.results[i][0].transcript;
            }
        }

        accumulatedTranscript += finalChunk;
        statusText.innerText = `Heard: ${accumulatedTranscript} ${interim}`;

        // 1500ms Silence Debouncer: allows Indian Kirana shopkeepers to speak complete natural sentences
        speechDebounceTimer = setTimeout(() => {
            const finalSentence = (accumulatedTranscript + interim).trim();
            if (finalSentence) {
                statusText.innerText = "Processing with Gemma AI...";
                sendToFastAPI(finalSentence);
                accumulatedTranscript = ""; // Reset for next utterance
            }
        }, 1500);
    };

    recognition.onerror = (event) => {
        console.warn("[Voice Engine] Speech recognition error:", event.error);
        if (event.error !== 'no-speech') {
            statusText.innerText = `Voice Error: ${event.error}`;
        }
    };

    recognition.onend = () => {
        if (isListening) {
            try {
                recognition.start();
            } catch (e) {}
        }
    };

    async function sendToFastAPI(transcript) {
        try {
            const response = await fetch('/api/voice-command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ transcript: transcript, session_id: "user_1" })
            });
            const data = await response.json();

            // Render confirmation card based on response state
            statusText.innerText = data.message || data.reply_message || "Command processed.";
            if (data.state === "AWAITING_CONFIRMATION") {
                renderConfirmationCard(data.data || data.pending_transaction);
            } else if (data.state === "IDLE") {
                clearConfirmationCard();
            }
        } catch (error) {
            console.error("[Voice Engine] Error sending to FastAPI:", error);
            statusText.innerText = "Network Error.";
        }
    }
});
