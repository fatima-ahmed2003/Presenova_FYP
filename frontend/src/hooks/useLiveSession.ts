import { useState, useEffect, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import { API_BASE_URL, submitPresentationSession } from '../services/api';

export type SessionStatus = 'IDLE' | 'CONNECTING' | 'STREAMING' | 'INTERRUPTED_Q&A' | 'REPORT_GENERATING' | 'FINISHED';

export interface RealtimeFeedback {
  eyeContact: number | null;
  posture: number | null;
  headMovement: number | null;
  gazeCalibrationStatus: 'calibrating' | 'calibrated' | 'unavailable';
  faceDetected: boolean;
  hint: string;
  confidence: number | null;
  emotion: string;
  facialBlendshapes: Record<string, number> | null;
  metricConfidence: { eyeContact: number | null; posture: number | null; headMovement: number | null };
}

export interface UseLiveSessionProps {
  userId: string;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  videoOnly?: boolean;
}

interface MicrophoneAcquisition {
  promise: Promise<MediaStream>;
  stream: MediaStream | null;
  leases: number;
  settled: boolean;
}

export const useLiveSession = ({ userId, videoRef, videoOnly = false }: UseLiveSessionProps) => {
  const [status, setStatus] = useState<SessionStatus>('IDLE');
  const statusRef = useRef<SessionStatus>('IDLE');
  useEffect(() => {
    statusRef.current = status;
  }, [status]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentQuestion, setCurrentQuestion] = useState<string | null>(null);
  const [realtimeFeedback, setRealtimeFeedback] = useState<RealtimeFeedback>({
    eyeContact: null,
    posture: null,
    headMovement: null,
    gazeCalibrationStatus: 'unavailable',
    faceDetected: false,
    hint: 'Preparing session...',
    confidence: null,
    emotion: 'N/A',
    facialBlendshapes: null,
    metricConfidence: { eyeContact: null, posture: null, headMovement: null },
  });
  const [micActive, setMicActive] = useState(false);
  const [micEnabled, setMicEnabled] = useState(true);
  const [micLoading, setMicLoading] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const [micRetryKey, setMicRetryKey] = useState(0);
  const [hasHistory, setHasHistory] = useState(false);
  const [historySummary, setHistorySummary] = useState<any>(null);
  const [finalReport, setFinalReport] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  // FIX: true until the backend tells us otherwise, so we don't flash a
  // warning before session_started arrives. Reflects whether GROQ_API_KEY is
  // configured server-side (speech-to-text availability for WPM/fillers/pitch).
  const [sttAvailable, setSttAvailable] = useState(true);

  const clearVisualFeedback = (hint = 'Waiting for a readable video frame.') => {
    setRealtimeFeedback({
      eyeContact: null,
      posture: null,
      headMovement: null,
      gazeCalibrationStatus: 'unavailable',
      faceDetected: false,
      hint,
      confidence: null,
      emotion: 'N/A',
      facialBlendshapes: null,
      metricConfidence: { eyeContact: null, posture: null, headMovement: null },
    });
  };

  const socketRef = useRef<Socket | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const sessionStartTimerRef = useRef<any>(null);
  const sessionStartAttemptRef = useRef(0);
  const sentStartAttemptRef = useRef(0);
  const pendingStartRef = useRef<{ attempt: number; payload: { user_id: string; topic: string } } | null>(null);
  const isFinishingRef = useRef(false);
  const videoIntervalRef = useRef<any>(null);
  const videoFrameInFlightRef = useRef<number | null>(null);
  const videoFrameAckTimerRef = useRef<any>(null);
  const videoAckSupportedRef = useRef<boolean | null>(null);
  const lastFeedbackFrameRef = useRef(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioIntervalRef = useRef<any>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioRmsIntervalRef = useRef<any>(null);
  const audioRmsSamplesRef = useRef<number[]>([]);
  const audioContextRef = useRef<AudioContext | null>(null);
  const microphoneRequestRef = useRef<MicrophoneAcquisition | null>(null);
  const firstFrameSentRef = useRef(false);

  const releaseMicrophoneAcquisition = (acquisition: MicrophoneAcquisition, releaseAll = false) => {
    acquisition.leases = releaseAll ? 0 : Math.max(0, acquisition.leases - 1);
    if (acquisition.leases === 0) {
      if (acquisition.stream) {
        acquisition.stream.getTracks().forEach((track) => track.stop());
        if (audioStreamRef.current === acquisition.stream) audioStreamRef.current = null;
      }
      if (acquisition.settled && microphoneRequestRef.current === acquisition) {
        microphoneRequestRef.current = null;
      }
    }
  };

  const clearSessionStartTimer = () => {
    if (sessionStartTimerRef.current) {
      clearTimeout(sessionStartTimerRef.current);
      sessionStartTimerRef.current = null;
    }
  };

  const failSessionStart = (message: string) => {
    if (statusRef.current !== 'CONNECTING') return;
    clearSessionStartTimer();
    pendingStartRef.current = null;
    statusRef.current = 'IDLE';
    setError(message);
    setStatus('IDLE');
  };

  const handleSessionStarted = (data: any) => {
    if (statusRef.current !== 'CONNECTING') return;
    if (data?.status === 'success' && typeof data.session_id === 'string' && data.session_id) {
      clearSessionStartTimer();
      pendingStartRef.current = null;
      sessionIdRef.current = data.session_id;
      setSessionId(data.session_id);
      setHasHistory(data.has_history === true);
      setHistorySummary(data.history_summary || null);
      setSttAvailable(data.stt_available !== false);
      statusRef.current = 'STREAMING';
      setStatus('STREAMING');
      return;
    }
    failSessionStart(data?.message || 'The Live Coach server rejected the session request. Please try again.');
  };

  const emitStartSession = (socket: Socket, attempt: number) => {
    const pending = pendingStartRef.current;
    if (!pending || pending.attempt !== attempt || sentStartAttemptRef.current === attempt || !socket.connected) return;
    sentStartAttemptRef.current = attempt;
    socket.emit('start_session', pending.payload, (ack: any) => {
      if (ack) handleSessionStarted(ack);
    });
  };

  // Initialize socket connection
  const connectSocket = () => {
    if (socketRef.current) return;

    const socketHost = API_BASE_URL.startsWith('http')
      ? API_BASE_URL.replace(/\/api\/?$/, '')
      : window.location.origin;

    const isLocalDev =
      typeof window !== 'undefined' &&
      (window.location.hostname === 'localhost' ||
       window.location.hostname === '127.0.0.1' ||
       window.location.hostname === '::1');
    const isPresenovaAndroidApp = typeof window !== 'undefined' && Boolean((window as any).PresenovaAuth);

    const socket = io(`${socketHost}/ws/live-session`, {
      // Polling first keeps WebView startup reliable while still upgrading to WebSocket when available.
      transports: isLocalDev || isPresenovaAndroidApp ? ['polling', 'websocket'] : ['websocket', 'polling'],
      upgrade: !isLocalDev,
      reconnection: true,
      reconnectionAttempts: 20,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 15000,
    });

    socket.on('connect', () => {
      console.log('Connected to live session socket namespace');
      const pending = pendingStartRef.current;
      if (pending) emitStartSession(socket, pending.attempt);
    });

    socket.on('session_started', handleSessionStarted);

    socket.on('realtime_feedback', (data: any) => {
      if (typeof data.frame_id === 'number') {
        if (data.frame_id < lastFeedbackFrameRef.current) return;
        lastFeedbackFrameRef.current = data.frame_id;
        if (performance.now() - data.frame_id > 2000) {
          clearVisualFeedback('Visual analysis is delayed; waiting for a current frame.');
          return;
        }
      }
      const blendshapeData = data.facial_blendshapes && typeof data.facial_blendshapes === 'object'
        ? Object.fromEntries(Object.entries(data.facial_blendshapes).filter((entry): entry is [string, number] =>
          typeof entry[1] === 'number' && Number.isFinite(entry[1])
        ))
        : null;
      const confidenceData = data.metric_confidence || {};
      setRealtimeFeedback({
        eyeContact: typeof data.eye_contact === 'number' ? data.eye_contact : null,
        posture: typeof data.posture === 'number' ? data.posture : null,
        headMovement: typeof data.head_movement === 'number' ? data.head_movement : null,
        gazeCalibrationStatus: data.gaze_calibration_status === 'calibrated'
          ? 'calibrated'
          : data.gaze_calibration_status === 'calibrating' ? 'calibrating' : 'unavailable',
        faceDetected: data.face_detected === true,
        hint: data.hint || 'Face not detected. Waiting for a valid frame.',
        confidence: typeof data.confidence === 'number' ? data.confidence : null,
        emotion: data.emotion || 'N/A',
        facialBlendshapes: blendshapeData && Object.keys(blendshapeData).length ? blendshapeData : null,
        metricConfidence: {
          eyeContact: typeof confidenceData.eye_contact === 'number' ? confidenceData.eye_contact : null,
          posture: typeof confidenceData.posture === 'number' ? confidenceData.posture : null,
          headMovement: typeof confidenceData.head_movement === 'number' ? confidenceData.head_movement : null,
        },
      });
    });

    socket.on('interruption_trigger', (data: any) => {
      console.log('Panelist interruption triggered:', data.question);
      setCurrentQuestion(data.question);
      setStatus('INTERRUPTED_Q&A');
    });

    socket.on('interruption_resolved', (data: any) => {
      console.log('Interruption resolved:', data);
      setCurrentQuestion(null);
      setStatus('STREAMING');
    });

    socket.on('disconnect', (reason: string) => {
      console.log('Socket disconnected:', reason);
      videoFrameInFlightRef.current = null;
      videoAckSupportedRef.current = null;
      if (videoFrameAckTimerRef.current) clearTimeout(videoFrameAckTimerRef.current);
      if (statusRef.current === 'CONNECTING' || statusRef.current === 'STREAMING' || statusRef.current === 'INTERRUPTED_Q&A') {
        if (statusRef.current === 'CONNECTING') {
          failSessionStart('Connection to Live Coach was interrupted before the session started. Please retry.');
        } else {
          setError('Lost real-time connection to the live coach server. Reconnecting...');
        }
      }
    });

    socket.on('connect_error', (err: any) => {
      console.error('Socket connection error:', err);
      if (statusRef.current === 'CONNECTING' || statusRef.current === 'STREAMING' || statusRef.current === 'INTERRUPTED_Q&A') {
        if (statusRef.current === 'CONNECTING') {
          failSessionStart('Unable to connect to the Live Coach server. Check your connection and retry.');
        } else {
          setError('Unable to reach the live coach server. Please check your connection.');
        }
      }
    });

    socketRef.current = socket;
  };

  // Start live presentation session
  const startSession = (topic: string) => {
    setError(null);
    setFinalReport(null);
    setSessionId(null);
    sessionIdRef.current = null;
    firstFrameSentRef.current = false;
    const attempt = ++sessionStartAttemptRef.current;
    sentStartAttemptRef.current = 0;
    pendingStartRef.current = {
      attempt,
      payload: { user_id: userId || 'guest', topic: topic.trim() },
    };
    videoFrameInFlightRef.current = null;
    videoAckSupportedRef.current = null;
    if (videoFrameAckTimerRef.current) clearTimeout(videoFrameAckTimerRef.current);
    lastFeedbackFrameRef.current = 0;
    isFinishingRef.current = false;
    clearVisualFeedback('Preparing session...');
    statusRef.current = 'CONNECTING';
    setStatus('CONNECTING');
    connectSocket();
    if (socketRef.current?.connected) emitStartSession(socketRef.current, attempt);
    clearSessionStartTimer();
    sessionStartTimerRef.current = window.setTimeout(() => {
      if (sessionStartAttemptRef.current === attempt) {
        failSessionStart('Live Coach did not confirm the session within 30 seconds. Please check the connection and retry.');
      }
    }, 30_000);
  };

  // Send answer to academic panelist interruption
  const sendAnswer = (answer: string) => {
    if (socketRef.current && sessionId && answer.trim()) {
      socketRef.current.emit('submit_answer', {
        session_id: sessionId,
        answer: answer,
      });
    }
  };

  // Stop session & fetch compiled report
  const stopSession = async () => {
    if (!sessionId || isFinishingRef.current) return;
    isFinishingRef.current = true;
    clearSessionStartTimer();
    pendingStartRef.current = null;
    setStatus('REPORT_GENERATING');
    
    // Clear intervals and recorder
    stopMediaStreaming();

    try {
      const response = await submitPresentationSession(sessionId);
      if (response.status === 'success') {
        setFinalReport(response.report);
        setStatus('FINISHED');
      } else {
        setError(response.message || 'Failed to compile presentation report. Retry to generate the final report.');
        setStatus('REPORT_GENERATING');
        isFinishingRef.current = false;
      }
    } catch (err: any) {
      setError(err.message || 'Error compiling report. Retry to generate the final report.');
      setStatus('REPORT_GENERATING');
      isFinishingRef.current = false;
    } finally {
      // Disconnect socket
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
    }
  };

  const toggleMicrophone = () => {
    setMicError(null);
    setMicEnabled((enabled) => !enabled);
  };

  const retryMicrophone = () => {
    setMicError(null);
    setMicEnabled(true);
    setMicRetryKey((key) => key + 1);
  };

  const stopMediaStreaming = () => {
    if (videoIntervalRef.current) {
      clearInterval(videoIntervalRef.current);
      videoIntervalRef.current = null;
    }
    videoFrameInFlightRef.current = null;
    if (videoFrameAckTimerRef.current) {
      clearTimeout(videoFrameAckTimerRef.current);
      videoFrameAckTimerRef.current = null;
    }
    if (audioIntervalRef.current) {
      clearInterval(audioIntervalRef.current);
      audioIntervalRef.current = null;
    }
    if (audioRmsIntervalRef.current) {
      clearInterval(audioRmsIntervalRef.current);
      audioRmsIntervalRef.current = null;
    }
    audioRmsSamplesRef.current = [];
    if (audioContextRef.current) {
      void audioContextRef.current.close().catch(() => undefined);
      audioContextRef.current = null;
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
    }
    // ISSUE-10: Release all microphone audio tracks so hardware indicator turns off
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach((track) => track.stop());
      audioStreamRef.current = null;
    }
    if (microphoneRequestRef.current) releaseMicrophoneAcquisition(microphoneRequestRef.current, true);
    setMicActive(false);
    setMicLoading(false);
  };

  // Loop for video frames and audio chunks streaming
  useEffect(() => {
    let audioActive = true;
    let localMicrophoneAcquisition: MicrophoneAcquisition | null = null;

    const sessionActive = status === 'STREAMING' || status === 'INTERRUPTED_Q&A';
    if (sessionActive && sessionId && videoRef.current) {
      // Keep at most one frame in flight so slow processing cannot queue stale images.
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');

      videoIntervalRef.current = setInterval(() => {
        const video = videoRef.current;
        if (video && ctx && socketRef.current?.connected && ['STREAMING', 'INTERRUPTED_Q&A'].includes(statusRef.current)) {
          // Guard: Only capture when camera stream is actively delivering frames with valid dimensions
          const canSendFrame = videoAckSupportedRef.current === false || videoFrameInFlightRef.current === null;
          if (canSendFrame && !video.paused && !video.ended && video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
            const scale = Math.min(1, 480 / video.videoWidth);
            canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
            canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const frameBase64 = canvas.toDataURL('image/jpeg', 0.7); // 70% quality for clean landmark detection

            const frameId = Math.round(performance.now());
            const expectsAck = videoAckSupportedRef.current !== false;
            if (expectsAck) videoFrameInFlightRef.current = frameId;
            if (!firstFrameSentRef.current) {
              firstFrameSentRef.current = true;
              setRealtimeFeedback((current) => ({ ...current, hint: 'Camera frame sent; waiting for visual feedback.' }));
            }
            socketRef.current.emit('video_frame', {
              session_id: sessionId,
              frame: frameBase64,
              frame_id: frameId,
            }, (ack: any) => {
              videoAckSupportedRef.current = true;
              if (ack?.dropped) setRealtimeFeedback((current) => ({ ...current, hint: 'Frame reached the server; analysis is busy. Waiting for the next frame.' }));
              if (videoFrameAckTimerRef.current) clearTimeout(videoFrameAckTimerRef.current);
              videoFrameAckTimerRef.current = null;
              if (videoFrameInFlightRef.current === frameId) videoFrameInFlightRef.current = null;
            });
            if (expectsAck) {
              videoFrameAckTimerRef.current = window.setTimeout(() => {
                if (videoFrameInFlightRef.current === frameId) {
                  // Older deployed servers may not acknowledge video_frame.
                  // Fall back to the existing server-side drop/backpressure guard.
                  videoAckSupportedRef.current = false;
                  videoFrameInFlightRef.current = null;
                }
                videoFrameAckTimerRef.current = null;
              }, 3000);
            }
          }
        }
      }, 200); // 5 frames per second

      // Uploaded recordings are analyzed visually and do not request live microphone access.
      if (videoOnly || !micEnabled) {
        setMicActive(false);
        if (!micEnabled) setMicError(null);
        setMicLoading(false);
      } else if (!navigator.mediaDevices?.getUserMedia) {
        setMicActive(false);
        setMicLoading(false);
        setMicError('Microphone access is unavailable in this browser or page context.');
      } else {
        setMicLoading(true);
        let acquisition = microphoneRequestRef.current;
        if (!acquisition) {
          acquisition = {
            promise: navigator.mediaDevices.getUserMedia({ audio: true }),
            stream: null,
            leases: 0,
            settled: false,
          };
          microphoneRequestRef.current = acquisition;
          const pendingAcquisition = acquisition;
          pendingAcquisition.promise.then((stream) => {
            pendingAcquisition.stream = stream;
            pendingAcquisition.settled = true;
            if (pendingAcquisition.leases === 0) releaseMicrophoneAcquisition(pendingAcquisition, true);
          }, () => {
            pendingAcquisition.settled = true;
            if (pendingAcquisition.leases === 0 && microphoneRequestRef.current === pendingAcquisition) {
              microphoneRequestRef.current = null;
            }
          });
        }
        acquisition.leases += 1;
        localMicrophoneAcquisition = acquisition;
        acquisition.promise.then((stream) => {
        if (!audioActive || microphoneRequestRef.current !== acquisition || acquisition.leases === 0) return;
        audioStreamRef.current = stream;
        setMicLoading(false);
        setMicActive(true);
        setMicError(null);
        if (typeof MediaRecorder === 'undefined') {
          stream.getTracks().forEach((track) => track.stop());
          audioStreamRef.current = null;
          mediaRecorderRef.current = null;
          setMicActive(false);
          setMicLoading(false);
          setMicError('Audio recording is not supported by this browser.');
          return;
        }
        let mediaRecorder: MediaRecorder;
        try {
          mediaRecorder = new MediaRecorder(stream);
        } catch (recorderError: any) {
          stream.getTracks().forEach((track) => track.stop());
          audioStreamRef.current = null;
          mediaRecorderRef.current = null;
          setMicActive(false);
          setMicLoading(false);
          setMicError(recorderError?.message || 'The microphone could not start recording.');
          return;
        }
        mediaRecorderRef.current = mediaRecorder;

        try {
          const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext;
          if (AudioContextCtor) {
            const audioContext: AudioContext = new AudioContextCtor();
            const analyser = audioContext.createAnalyser();
            analyser.fftSize = 1024;
            audioContext.createMediaStreamSource(stream).connect(analyser);
            const waveform = new Float32Array(analyser.fftSize);
            audioContextRef.current = audioContext;
            audioRmsSamplesRef.current = [];
            audioRmsIntervalRef.current = window.setInterval(() => {
              analyser.getFloatTimeDomainData(waveform);
              let squared = 0;
              for (let i = 0; i < waveform.length; i += 1) squared += waveform[i] * waveform[i];
              audioRmsSamplesRef.current.push(Math.sqrt(squared / waveform.length));
            }, 50);
            if (audioContext.state === 'suspended') void audioContext.resume();
          }
        } catch (signalError) {
          console.warn('Microphone signal sampling unavailable:', signalError);
        }
        if (audioContextRef.current?.state === 'suspended') {
          void audioContextRef.current.resume().catch(() => undefined);
        }

        mediaRecorder.ondataavailable = async (e) => {
          try {
            if (e.data.size > 0 && socketRef.current && ['STREAMING', 'INTERRUPTED_Q&A'].includes(statusRef.current)) {
              const rmsSamples = audioRmsSamplesRef.current;
              audioRmsSamplesRef.current = [];
              // Read blob as base64 string
              const reader = new FileReader();
              reader.readAsDataURL(e.data);
              reader.onloadend = () => {
                const base64Audio = reader.result as string;
                if (!socketRef.current || !['STREAMING', 'INTERRUPTED_Q&A'].includes(statusRef.current)) return;
                socketRef.current.emit('audio_chunk', {
                  session_id: sessionId,
                  audio: base64Audio,
                  transcript_snippet: '', // Voice STT can optionally be generated on client
                  rms_samples: rmsSamples,
                  rms_sample_period_ms: 50,
                });
              };
            }
          } catch (audioErr) {
            console.warn('Audio chunk processing warning:', audioErr);
          }
        };

        // Record in 3-second slices natively
        try {
          mediaRecorder.start(3000);
        mediaRecorder.onerror = () => {
          if (audioRmsIntervalRef.current) clearInterval(audioRmsIntervalRef.current);
          audioRmsIntervalRef.current = null;
          if (audioContextRef.current) {
            void audioContextRef.current.close().catch(() => undefined);
            audioContextRef.current = null;
          }
          stream.getTracks().forEach((track) => track.stop());
          if (audioStreamRef.current === stream) audioStreamRef.current = null;
          if (mediaRecorderRef.current === mediaRecorder) mediaRecorderRef.current = null;
            setMicError('Microphone recording stopped unexpectedly.');
            setMicActive(false);
          setMicLoading(false);
          };
        } catch (recorderError: any) {
          stream.getTracks().forEach((track) => track.stop());
          audioStreamRef.current = null;
          mediaRecorderRef.current = null;
          setMicActive(false);
          setMicError(recorderError?.message || 'Could not start microphone recording.');
          setMicLoading(false);
        }
      }).catch((err: any) => {
        // ISSUE-09: Gracefully handle rejected microphone permissions
        // Do NOT terminate session — visual tracking (Eye contact, posture, confidence) continues!
        console.warn('Microphone access denied or unavailable:', err);
        setMicActive(false);
        setMicError(err?.message || 'Microphone access was denied or is unavailable.');
        setMicActive(false);
        setMicLoading(false);
      }).finally(() => {
        if (acquisition.settled && acquisition.leases === 0 && microphoneRequestRef.current === acquisition) {
          microphoneRequestRef.current = null;
        }
      });
      }
    } else {
      stopMediaStreaming();
    }

    return () => {
      audioActive = false;
      if (localMicrophoneAcquisition) releaseMicrophoneAcquisition(localMicrophoneAcquisition);
      if (videoIntervalRef.current) {
        clearInterval(videoIntervalRef.current);
        videoIntervalRef.current = null;
      }
      if (videoFrameAckTimerRef.current) {
        clearTimeout(videoFrameAckTimerRef.current);
        videoFrameAckTimerRef.current = null;
      }
      if (audioRmsIntervalRef.current) {
        clearInterval(audioRmsIntervalRef.current);
        audioRmsIntervalRef.current = null;
      }
      if (audioContextRef.current) {
        void audioContextRef.current.close().catch(() => undefined);
        audioContextRef.current = null;
      }
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.ondataavailable = null;
        try { mediaRecorderRef.current.stop(); } catch { /* The stream tracks are stopped during cleanup. */ }
      }
      mediaRecorderRef.current = null;
    };
  }, [status === 'STREAMING' || status === 'INTERRUPTED_Q&A', sessionId, videoRef, videoOnly, micEnabled, micRetryKey]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearSessionStartTimer();
      pendingStartRef.current = null;
      stopMediaStreaming();
      if (socketRef.current) {
        if (sessionIdRef.current) {
          socketRef.current.emit('stop_session', { session_id: sessionIdRef.current });
        }
        socketRef.current.disconnect();
        socketRef.current = null;
      }
    };
  }, []);

  return {
    status,
    sessionId,
    currentQuestion,
    realtimeFeedback,
    micActive,
    micEnabled,
    micLoading,
    micError,
    toggleMicrophone,
    retryMicrophone,
    hasHistory,
    historySummary,
    finalReport,
    error,
    sttAvailable,
    clearVisualFeedback,
    startSession,
    sendAnswer,
    stopSession,
  };
};
