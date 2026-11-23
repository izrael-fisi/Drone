import { useEffect, useRef, useState, type RefObject } from "react";
import { Cctv, RotateCcw } from "lucide-react";
import type Hls from "hls.js";
import type { Recording, Sensor } from "./monitoringModel";

type PlaybackState = "Connecting" | "Playing" | "Buffering" | "Ready to play" | "Paused" | "Feed ended" | "Feed unavailable";
const STARTUP_TIMEOUT_MS = 20_000;

export function CameraFeed({ camera, preferGrid = false, announce = true }: {
  camera: Pick<Sensor, "id" | "name" | "demo" | "cameraFeedUrl" | "cameraGridUrl">; preferGrid?: boolean; announce?: boolean;
}) {
  const url = (preferGrid ? camera.cameraGridUrl : camera.cameraFeedUrl) || camera.cameraFeedUrl || camera.cameraGridUrl;
  if (url) return <StreamPlayer url={url} name={camera.name} announce={announce} />;
  if (camera.demo) return <DemoFeed cameraId={camera.id} />;
  return <div className="gv-camera-feed gv-camera-feed-empty">
    <Cctv size={22} /><p>No feed configured</p><span>Add an HLS feed URL in Edit device.</span>
  </div>;
}

export function RecordedFeed({ recording, name, synchronized }: { recording: Recording; name: string; synchronized?: RefObject<HTMLVideoElement> }) {
  return <StreamPlayer url={recording.url} name={name} format={recording.format} startAt={recording.eventOffsetSeconds} synchronized={synchronized} />;
}

function StreamPlayer({ url, name, format = "hls", startAt, announce = true, synchronized }: {
  url: string; name: string; format?: Recording["format"]; startAt?: number; announce?: boolean; synchronized?: RefObject<HTMLVideoElement>;
}) {
  const localVideo = useRef<HTMLVideoElement>(null);
  const videoRef = synchronized ?? localVideo;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<PlaybackState>("Connecting");
  const stopped = state === "Feed unavailable" || (state === "Feed ended" && startAt === undefined);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let hls: Hls | undefined;
    let timer: number | undefined;
    let closed = false;
    let failed = false;
    let autoplayAttempted = false;
    setState("Connecting");

    const stopMedia = () => {
      hls?.destroy();
      hls = undefined;
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
    const fail = () => {
      if (closed || failed) return;
      failed = true;
      window.clearTimeout(timer);
      stopMedia();
      setState("Feed unavailable");
    };
    const armTimeout = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(fail, STARTUP_TIMEOUT_MS);
    };
    const onCanPlay = () => {
      if (failed || autoplayAttempted) return;
      autoplayAttempted = true;
      if (startAt !== undefined) {
        if (video.duration === Infinity || (Number.isFinite(video.duration) && startAt >= video.duration)) { fail(); return; }
        if (startAt > 0) video.currentTime = startAt;
      }
      if (synchronized) {
        window.clearTimeout(timer);
        setState("Ready to play");
        return;
      }
      void video.play().catch((error: unknown) => {
        if (!closed && !failed && error instanceof DOMException && error.name === "NotAllowedError") {
          window.clearTimeout(timer);
          setState("Ready to play");
        }
      });
    };
    const onPlaying = () => { if (!failed) { window.clearTimeout(timer); setState("Playing"); } };
    const onWaiting = () => { if (!failed && !video.paused && video.currentTime > 0) { setState("Buffering"); armTimeout(); } };
    const onPause = () => { if (!failed && !video.ended && video.currentTime > 0) { window.clearTimeout(timer); setState("Paused"); } };
    const onEnded = () => { if (!failed) { window.clearTimeout(timer); setState("Feed ended"); } };
    const listeners = { canplay: onCanPlay, playing: onPlaying, waiting: onWaiting, pause: onPause, ended: onEnded, error: fail };
    for (const [event, listener] of Object.entries(listeners)) video.addEventListener(event, listener);
    armTimeout();

    void (async () => {
      const native = video.canPlayType("application/vnd.apple.mpegurl");
      if (format === "mp4" || native === "probably") {
        video.src = url;
        return;
      }
      const { default: Player } = await import("hls.js");
      if (closed || failed) return;
      if (Player.isSupported()) {
        hls = new Player({ capLevelToPlayerSize: true, backBufferLength: 20 });
        hls.on(Player.Events.ERROR, (_, data) => { if (data.fatal) fail(); });
        if (startAt !== undefined) hls.on(Player.Events.LEVEL_LOADED, (_, { details }) => { if (details.live) fail(); });
        hls.loadSource(url);
        hls.attachMedia(video);
      } else if (native) {
        video.src = url;
      } else {
        fail();
      }
    })().catch(fail);

    return () => {
      closed = true;
      window.clearTimeout(timer);
      for (const [event, listener] of Object.entries(listeners)) video.removeEventListener(event, listener);
      stopMedia();
    };
  }, [url, attempt, format, startAt, synchronized, videoRef]);

  return <div className="gv-camera-feed">
    <div className="gv-feed-frame">
      <video ref={videoRef} controls={!stopped && !synchronized} muted playsInline preload="auto" aria-label={name + " feed"} hidden={stopped} />
      {stopped && <div className="gv-feed-overlay"><Cctv size={22} /><span>{state}</span></div>}
    </div>
    <div className="gv-feed-status">
      <span role={announce ? "status" : undefined} data-playing={state === "Playing"}><i />{state === "Feed ended" && startAt !== undefined ? "Recording ended" : state}</span>
      {stopped && <button className="gv-feed-retry" type="button" onClick={() => setAttempt((count) => count + 1)}><RotateCcw size={13} /> Retry</button>}
    </div>
  </div>;
}

function DemoFeed({ cameraId }: { cameraId: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const scene = canvas?.getContext("2d");
    if (!canvas || !scene) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame: number;
    let lastFrame = -100;

    const draw = (time: number) => {
      if (!reduceMotion) frame = window.requestAnimationFrame(draw);
      if (time - lastFrame < 65) return;
      lastFrame = time;
      scene.fillStyle = "#202022";
      scene.fillRect(0, 0, canvas.width, canvas.height);

      scene.fillStyle = "#373739";
      scene.fillRect(0, 97, 640, 184);
      scene.fillStyle = "#2b2b2d";
      scene.fillRect(19, 20, 602, 62);
      scene.fillRect(24, 295, 592, 47);
      scene.strokeStyle = "#6b6b60";
      scene.lineWidth = 2;
      for (let slot = 0; slot < 10; slot++) {
        const x = 40 + slot * 56;
        scene.strokeRect(x, 109, 40, 44);
        scene.strokeRect(x, 225, 40, 44);
      }
      scene.setLineDash([16, 15]);
      scene.strokeStyle = "#a4a28e";
      scene.beginPath();
      scene.moveTo(0, 190);
      scene.lineTo(640, 190);
      scene.stroke();
      scene.setLineDash([]);

      const progress = (time / 16) % 740;
      const x = cameraId === "cam-02" ? 650 - progress : progress - 80;
      scene.fillStyle = "#d0cecc";
      scene.fillRect(x, 164, 47, 15);
      scene.fillStyle = "#797777";
      scene.fillRect(x + 8, 164, 8, 15);

      scene.fillStyle = "#09090bdd";
      scene.fillRect(12, 12, 205, 28);
      scene.font = "16px monospace";
      scene.fillStyle = "#e4e4e7";
      scene.fillText("SIMULATED CAMERA", 21, 31);
    };
    draw(0);
    return () => window.cancelAnimationFrame(frame);
  }, [cameraId]);

  return <div className="gv-camera-feed">
    <div className="gv-feed-frame"><canvas ref={canvasRef} width={640} height={360} role="img" aria-label="Simulated camera feed" /></div>
    <div className="gv-feed-status"><span><i />Demo feed</span></div>
  </div>;
}
