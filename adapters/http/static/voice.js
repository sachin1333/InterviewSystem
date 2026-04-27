/**
 * Vanilla JS voice interface for interview system.
 * Handles microphone capture, audio streaming, TTS playback, and VAD barge-in.
 */

const VOICE_CONFIG = {
  SAMPLE_RATE: 16000,
  BUFFER_SIZE: 1600, // ~100ms at 16kHz
  VAD_THRESHOLD_RMS: 0.02, // VAD energy threshold
  VAD_HOLDOFF_MS: 80, // Hold-off time before barge-in
  SILENCE_DURATION_MS: 700, // Silence timeout for EOS
};

class MicrophoneCapture {
  constructor() {
    this.stream = null;
    this.audioContext = null;
    this.worklet = null;
    this.processor = null;
    this.isListening = false;
    this.onAudioChunk = null; // Callback for PCM chunks
    this.onEnergy = null; // Callback for energy level
  }

  async start() {
    if (this.isListening) return;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)();

      const source = this.audioContext.createMediaStreamSource(this.stream);

      // Try AudioWorklet first, fallback to ScriptProcessor
      try {
        const workletCode = this._getWorkletCode();
        const blob = new Blob([workletCode], { type: 'application/javascript' });
        const workletUrl = URL.createObjectURL(blob);
        await this.audioContext.audioWorklet.addModule(workletUrl);

        this.worklet = new AudioWorkletNode(this.audioContext, 'ResamplerProcessor', {
          processorOptions: { targetSampleRate: VOICE_CONFIG.SAMPLE_RATE },
        });

        this.worklet.port.onmessage = (event) => {
          const { pcm_int16, rms } = event.data;
          if (this.onAudioChunk && pcm_int16.length > 0) {
            this.onAudioChunk(pcm_int16);
          }
          if (this.onEnergy) {
            this.onEnergy(rms);
          }
        };

        source.connect(this.worklet);
        this.worklet.connect(this.audioContext.destination);
      } catch (e) {
        console.log('AudioWorklet not available, using ScriptProcessor fallback');
        this._setupScriptProcessor(source);
      }

      this.isListening = true;
    } catch (err) {
      console.error('Microphone access denied:', err);
      throw err;
    }
  }

  _setupScriptProcessor(source) {
    const bufferSize = 4096;
    this.processor = this.audioContext.createScriptProcessor(bufferSize, 1, 1);

    const resampleBuffer = [];
    const targetRate = VOICE_CONFIG.SAMPLE_RATE;
    const inputRate = this.audioContext.sampleRate;

    this.processor.onaudioprocess = (event) => {
      const input = event.inputData[0];
      let rms = 0;
      let sum = 0;

      for (let i = 0; i < input.length; i++) {
        sum += input[i] * input[i];
      }
      rms = Math.sqrt(sum / input.length);

      // Simple resampling by averaging
      const ratio = targetRate / inputRate;
      for (let i = 0; i < input.length; i++) {
        resampleBuffer.push(input[i]);
        if (resampleBuffer.length >= bufferSize * ratio) {
          const chunk = resampleBuffer.splice(0, VOICE_CONFIG.BUFFER_SIZE);
          const int16 = this._float32ToInt16(chunk);
          if (this.onAudioChunk) {
            this.onAudioChunk(int16);
          }
        }
      }

      if (this.onEnergy) {
        this.onEnergy(rms);
      }
    };

    source.connect(this.processor);
    this.processor.connect(this.audioContext.destination);
  }

  _float32ToInt16(float32Array) {
    const int16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return int16;
  }

  _getWorkletCode() {
    return `
      class ResamplerProcessor extends AudioWorkletProcessor {
        constructor(options) {
          super();
          this.targetRate = options.processorOptions.targetSampleRate;
          this.inputRate = sampleRate;
          this.buffer = [];
          this.ratio = this.targetRate / this.inputRate;
        }

        process(inputs, outputs) {
          const input = inputs[0];
          if (!input || input.length === 0) return true;

          const inputSamples = input[0];
          let rms = 0;
          let sum = 0;

          for (let i = 0; i < inputSamples.length; i++) {
            sum += inputSamples[i] * inputSamples[i];
            this.buffer.push(inputSamples[i]);
          }

          rms = Math.sqrt(sum / inputSamples.length);

          if (this.buffer.length >= 1600) {
            const chunk = this.buffer.splice(0, 1600);
            const int16 = this.float32ToInt16(chunk);
            this.port.postMessage({ pcm_int16: Array.from(int16), rms });
          }

          return true;
        }

        float32ToInt16(float32Array) {
          const int16 = new Int16Array(float32Array.length);
          for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
          return int16;
        }
      }
      registerProcessor('ResamplerProcessor', ResamplerProcessor);
    `;
  }

  stop() {
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
    }
    if (this.worklet) {
      this.worklet.disconnect();
    }
    if (this.processor) {
      this.processor.disconnect();
    }
    this.isListening = false;
  }

  getAudioContext() {
    return this.audioContext;
  }
}

class TtsPlayer {
  constructor(audioContext) {
    this.audioContext = audioContext;
    this.queue = [];
    this.currentSource = null;
    this.isPlaying = false;
  }

  queueAudio(pcm_b64) {
    const pcmBytes = this._base64ToArrayBuffer(pcm_b64);
    const int16 = new Int16Array(pcmBytes);

    // Convert Int16 to Float32
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768.0;
    }

    this.queue.push(float32);
    this._playNext();
  }

  _playNext() {
    if (this.isPlaying || this.queue.length === 0) return;

    const float32 = this.queue.shift();
    const buffer = this.audioContext.createBuffer(1, float32.length, VOICE_CONFIG.SAMPLE_RATE);
    buffer.getChannelData(0).set(float32);

    this.currentSource = this.audioContext.createBufferSource();
    this.currentSource.buffer = buffer;
    this.currentSource.connect(this.audioContext.destination);

    this.isPlaying = true;
    this.currentSource.onended = () => {
      this.isPlaying = false;
      this._playNext();
    };

    this.currentSource.start(0);
  }

  stopAndClear() {
    if (this.currentSource) {
      try {
        this.currentSource.stop();
      } catch (e) {
        // Already stopped
      }
    }
    this.queue = [];
    this.isPlaying = false;
  }

  _base64ToArrayBuffer(b64) {
    const binaryString = atob(b64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes.buffer;
  }
}

class VoiceInterface {
  constructor(sessionId) {
    this.sessionId = sessionId;
    this.ws = null;
    this.mic = new MicrophoneCapture();
    this.ttsPlayer = null;
    this.audioSeq = 0;
    this.isSending = false;
    this.pressing = false;   // PTT: true while button held
    this.status = 'idle'; // idle, listening, speaking, error
    this.voiceMode = false;
    this.allowTextSwitch = false;
  }

  async initialize() {
    const voiceBar = document.getElementById('voice-bar');
    if (!voiceBar) return;

    this.voiceMode = voiceBar.getAttribute('data-voice-mode') === 'on';
    this.allowTextSwitch = voiceBar.getAttribute('data-allow-text-switch') === 'true';
    if (!this.voiceMode) {
      console.log('Voice mode disabled');
      return;
    }

    // PTT: wire up hold-to-speak button; do NOT start mic here.
    this._setupPTT();
    console.log('Voice interface ready (PTT mode)');
  }

  _setupPTT() {
    const btn = document.getElementById('mic-btn');
    if (!btn) return;

    const startSpeaking = async () => {
      if (this.pressing) return;
      this.pressing = true;
      this.audioSeq = 0;
      btn.classList.add('pressed');
      try {
        await this.mic.start();
        this._setupMicCallbacks();
        this.connectWebSocket();
        this.setStatus('listening');
      } catch (err) {
        console.error('Mic error:', err);
        this.pressing = false;
        btn.classList.remove('pressed');
        this.setStatus('error');
      }
    };

    const stopSpeaking = () => {
      if (!this.pressing) return;
      this.pressing = false;
      btn.classList.remove('pressed');
      this.sendEndOfStream();
      this.mic.stop();
      this.setStatus('idle');
    };

    btn.addEventListener('pointerdown', (e) => { e.preventDefault(); startSpeaking(); });
    btn.addEventListener('pointerup',   () => stopSpeaking());
    btn.addEventListener('pointercancel', () => stopSpeaking());
    // Prevent context menu on long-press (mobile).
    btn.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  _setupMicCallbacks() {
    this.mic.onAudioChunk = (int16Array) => {
      if (!this.isSending) return;
      const b64 = this._int16ToBase64(int16Array);
      this.send({ type: 'audio_chunk', seq: this.audioSeq++, pcm_b64: b64 });
    };
    // No VAD energy callback needed in PTT mode.
    this.mic.onEnergy = null;
  }

  connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws/sessions/${this.sessionId}/voice`;

    this.ws = new WebSocket(url);
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.isSending = true;
      this.audioSeq = 0;
      this.bargingIn = false;
    };

    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      this.handleServerMessage(msg);
    };

    this.ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      this.setStatus('error');
    };

    this.ws.onclose = () => {
      console.log('WebSocket closed');
      this.isSending = false;
    };
  }

  handleServerMessage(msg) {
    if (msg.type === 'partial_transcript') {
      this.updateTranscript(msg.text, msg.is_final);
    } else if (msg.type === 'tts_chunk') {
      this.handleTtsChunk(msg.pcm_b64, msg.end_of_utterance);
    } else if (msg.type === 'stage_change') {
      this.handleStageChange(msg.show_text_panel);
    }
  }

  updateTranscript(text, isFinal) {
    const transcript = document.getElementById('voice-transcript');
    if (transcript) {
      transcript.textContent = text;
      if (isFinal) {
        console.log('Transcript finalized:', text);
      }
    }
  }

  handleTtsChunk(pcm_b64, endOfUtterance) {
    if (!this.ttsPlayer) {
      this.ttsPlayer = new TtsPlayer(this.mic.getAudioContext());
    }

    if (pcm_b64) {
      this.ttsPlayer.queueAudio(pcm_b64);
      this.setStatus('speaking');
    }

    if (endOfUtterance) {
      console.log('TTS utterance complete');
      // Wait for playback to finish
      const waitLoop = setInterval(() => {
        if (!this.ttsPlayer.isPlaying) {
          clearInterval(waitLoop);
          this.setStatus('idle');
          this.isSending = true;
          this.audioSeq = 0;
          this.bargingIn = false;
        }
      }, 50);
    }
  }

  handleStageChange(showTextPanel) {
    if (showTextPanel) {
      const details = document.querySelector('details[data-mode="text"]');
      if (details && !details.open) {
        details.open = true;
        const textarea = details.querySelector('textarea#answer');
        if (textarea) {
          textarea.focus();
          textarea.placeholder = 'Type your code/analysis here';
        }
      }
    }
  }

  sendEndOfStream() {
    if (!this.isSending || this.ws.readyState !== WebSocket.OPEN) return;

    this.isSending = false;
    const msg = {
      type: 'audio_chunk',
      seq: -1,
      pcm_b64: '',
    };
    this.send(msg);
  }

  switchToText() {
    if (!this.allowTextSwitch) return;
    console.log('Switching to text mode');
    document.cookie = 'interview_mode=text; path=/';
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({ type: 'mode_switch', mode: 'text' });
    }
    // Show text composer immediately (cookie persisted on next page load).
    const details = document.querySelector('details[data-mode="text"]');
    if (details) { details.open = true; }
    const textarea = document.getElementById('answer');
    if (textarea) { textarea.focus(); textarea.placeholder = 'Type your answer here'; }
    // Hide voice bar.
    const voiceBar = document.getElementById('voice-bar');
    if (voiceBar) { voiceBar.setAttribute('data-voice-mode', 'off'); }
  }

  switchToVoice() {
    console.log('Switching to voice mode');
    document.cookie = 'interview_mode=voice; path=/';
    window.location.reload();
  }

  send(msg) {
    try {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(msg));
      }
    } catch (e) {
      console.error('WebSocket send error:', e);
    }
  }

  setStatus(newStatus) {
    this.status = newStatus;
    const statusChip = document.getElementById('voice-status');
    if (statusChip) {
      statusChip.textContent = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
      statusChip.className = `voice-status status-${newStatus}`;
    }
  }

  _int16ToBase64(int16Array) {
    const uint8 = new Uint8Array(int16Array.buffer, int16Array.byteOffset, int16Array.byteLength);
    let binary = '';
    for (let i = 0; i < uint8.length; i++) {
      binary += String.fromCharCode(uint8[i]);
    }
    return btoa(binary);
  }

  cleanup() {
    if (this.mic) {
      this.mic.stop();
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {
        // Already closed
      }
    }
    if (this.ttsPlayer) {
      this.ttsPlayer.stopAndClear();
    }
  }
}

// Initialize on page load
let voiceInterface = null;
document.addEventListener('DOMContentLoaded', async () => {
  const voiceBar = document.getElementById('voice-bar');
  if (voiceBar && voiceBar.getAttribute('data-voice-mode') === 'on') {
    voiceInterface = new VoiceInterface(window.SESSION_ID);
    await voiceInterface.initialize();
  }

  // Mode-toggle buttons (present when allow_text_switch=true).
  const btnVoice = document.getElementById('btn-use-voice');
  const btnText  = document.getElementById('btn-use-text');
  if (btnVoice) {
    btnVoice.addEventListener('click', () => {
      if (voiceInterface) voiceInterface.switchToVoice();
      else { document.cookie = 'interview_mode=voice; path=/'; window.location.reload(); }
    });
  }
  if (btnText) {
    btnText.addEventListener('click', () => {
      if (voiceInterface) voiceInterface.switchToText();
      else { document.cookie = 'interview_mode=text; path=/'; window.location.reload(); }
    });
  }
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
  if (voiceInterface) {
    voiceInterface.cleanup();
  }
});
