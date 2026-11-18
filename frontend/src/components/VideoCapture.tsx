import React, { useEffect, useState } from 'react';

interface VideoCaptureProps {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  isStreaming: boolean;
  videoFile?: File | null;
  retryKey?: number;
  onError?: (errorMsg: string) => void;
}

const VideoCapture: React.FC<VideoCaptureProps> = ({ videoRef, isStreaming, videoFile = null, retryKey = 0, onError }) => {
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraLoading, setCameraLoading] = useState(false);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let localStream: MediaStream | null = null;
    let objectUrl: string | null = null;

    if (isStreaming) {
      setErrorMessage(null);
      if (videoFile) {
        setCameraLoading(true);
        const video = videoRef.current;
        if (video) {
          objectUrl = URL.createObjectURL(videoFile);
          video.srcObject = null;
          video.src = objectUrl;
          video.loop = false;
          video.onerror = () => {
            if (!active) return;
            const msg = 'The selected video could not be decoded. Try an MP4 or WebM file.';
            setCameraActive(false);
            setCameraLoading(false);
            setErrorMessage(msg);
            onError?.(msg);
          };
          video.onended = () => setCameraActive(false);
          video.play().then(() => {
            if (!active) return;
            if (!video.videoWidth || !video.videoHeight || !Number.isFinite(video.duration) || video.duration <= 0) {
              const msg = 'The selected video contains no readable frames.';
              setCameraActive(false);
              setCameraLoading(false);
              setErrorMessage(msg);
              onError?.(msg);
              return;
            }
            setCameraActive(true);
            setCameraLoading(false);
          }).catch((err) => {
            if (!active) return;
            const msg = err?.message || 'The selected video could not be played.';
            setCameraActive(false);
            setCameraLoading(false);
            setErrorMessage(msg);
            onError?.(msg);
          });
        }
      } else if (!navigator.mediaDevices?.getUserMedia) {
        const msg = 'Camera access is unavailable in this browser or page context.';
        setCameraActive(false);
        setCameraLoading(false);
        setErrorMessage(msg);
        onError?.(msg);
      } else {
        setCameraLoading(true);
        navigator.mediaDevices
          .getUserMedia({ video: { width: 640, height: 480 }, audio: false })
          .then((mediaStream) => {
            if (!active) {
              mediaStream.getTracks().forEach((track) => track.stop());
              return;
            }
            localStream = mediaStream;
            setStream(mediaStream);
            if (videoRef.current) {
              videoRef.current.srcObject = mediaStream;
              mediaStream.getVideoTracks().forEach((track) => {
                track.onended = () => {
                  if (!active) return;
                  const msg = 'The camera stream ended. Reconnect the camera or use a video file.';
                  setCameraActive(false);
                  setErrorMessage(msg);
                  onError?.(msg);
                };
              });
              videoRef.current.play().then(() => {
                if (active) {
                  setCameraActive(true);
                  setCameraLoading(false);
                }
              }).catch((err) => {
                if (!active) return;
                const msg = err?.message || 'The camera preview could not be started.';
                setCameraActive(false);
                setCameraLoading(false);
                setErrorMessage(msg);
                onError?.(msg);
              });
            }
          })
          .catch((err) => {
            console.error('Error accessing webcam:', err);
            setCameraActive(false);
          setCameraLoading(false);
            const msg = err?.message || 'Camera access was denied or device is in use by another app.';
            setErrorMessage(msg);
            onError?.(msg);
          });
      }
    } else {
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        setStream(null);
      }
      if (videoRef.current) {
        videoRef.current.srcObject = null;
        videoRef.current.removeAttribute('src');
        videoRef.current.load();
      }
      setCameraActive(false);
      setCameraLoading(false);
    }

    return () => {
      active = false;
      if (localStream) {
        localStream.getTracks().forEach((track) => track.stop());
      }
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
    };
  }, [isStreaming, videoFile, retryKey]);

  return (
    <div style={containerStyle}>
      {isStreaming ? (
        <video
          ref={videoRef as React.RefObject<HTMLVideoElement>}
          autoPlay
          muted
          playsInline
          style={videoStyle}
        />
      ) : (
        <div style={placeholderStyle}>
          <span style={iconStyle}>Camera</span>
          <p style={textStyle}>{cameraLoading ? 'Starting video source…' : 'Camera is off'}</p>
        </div>
      )}
    {cameraLoading && isStreaming && (
      <div className="lc-video-loading" role="status" aria-live="polite">Starting video source…</div>
    )}
    {errorMessage && (
        <div style={{ position: 'absolute', bottom: '12px', left: '12px', right: '12px', background: 'rgba(239, 68, 68, 0.9)', color: '#ffffff', padding: '8px 12px', borderRadius: '6px', fontSize: '12px', zIndex: 10 }}>
          ⚠️ {errorMessage}
        </div>
      )}
    </div>
  );
};

// Simple clean CSS-in-JS for isolation and robustness
const containerStyle: React.CSSProperties = {
  position: 'relative',
  width: '100%',
  height: '100%',
  minHeight: '320px',
  backgroundColor: '#0f172a',
  borderRadius: '12px',
  overflow: 'hidden',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  border: '1px solid #334155',
  boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
};

const videoStyle: React.CSSProperties = {
  width: '100%',
  height: '100%',
  objectFit: 'cover',
  transform: 'scaleX(-1)', // Mirror effect for user convenience
};

const placeholderStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '1rem',
  color: '#94a3b8',
};

const iconStyle: React.CSSProperties = {
  fontSize: '3rem',
};

const textStyle: React.CSSProperties = {
  fontSize: '1rem',
  fontWeight: 600,
};

export default VideoCapture;
