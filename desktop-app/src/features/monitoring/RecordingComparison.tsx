import { useEffect, useRef, useState } from "react";
import { RecordedFeed } from "./CameraFeed";
import type { Recording, Sensor } from "./monitoringModel";
import type { VideoCheck } from "./VideoPosture";

export function RecordingComparison({ recordings, sensors, eventTime, videoChecks }: {
  recordings: [Recording, Recording]; sensors: Sensor[]; eventTime: string;
  videoChecks?: ReadonlyMap<string, VideoCheck>;
}) {
  const first = useRef<HTMLVideoElement>(null);
  const second = useRef<HTMLVideoElement>(null);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [message, setMessage] = useState("");
  const start = Math.max(...recordings.map((recording) => -recording.eventOffsetSeconds));
  const end = Math.min(...recordings.map((recording) => (recording.durationSeconds ?? 0) - recording.eventOffsetSeconds));
  const clockWarnings = recordings.flatMap(({ cameraId }) => {
    const check = videoChecks?.get(cameraId);
    const offset = check?.clockOffsetSeconds;
    if (offset === undefined || !check?.clockCheckedAt || Math.abs(check.clockCheckedAt - Date.parse(eventTime)) > 86_400_000 || Math.abs(offset) < 2) return [];
    const name = sensors.find((camera) => camera.id === cameraId)?.name ?? cameraId;
    return [`${name} clock differed by ${Math.abs(offset).toFixed(1)} seconds at its last check.`];
  });

  function seek(offset: number) {
    const position = Math.max(start, Math.min(end - .05, offset));
    setProgress(position);
    [first.current, second.current].forEach((video, index) => {
      if (video && video.readyState > 0) video.currentTime = recordings[index].eventOffsetSeconds + position;
    });
  }

  function toggle() {
    const videos = [first.current, second.current];
    if (playing) {
      videos.forEach((video) => video?.pause());
      setPlaying(false);
      return;
    }
    if (videos.some((video) => !video || video.readyState < 2 || video.hidden)) {
      setMessage("Waiting for both recordings to load.");
      return;
    }
    seek(progress >= end - .1 ? start : progress);
    setMessage("");
    void Promise.all(videos.map((video) => video!.play())).then(() => setPlaying(true)).catch(() => {
      videos.forEach((video) => video?.pause());
      setMessage("Both recordings must be available to play together.");
    });
  }

  useEffect(() => {
    const a = first.current, b = second.current;
    if (!playing || !a || !b) return;
    const timer = window.setInterval(() => {
      const offset = a.currentTime - recordings[0].eventOffsetSeconds;
      if (a.paused || b.paused || a.hidden || b.hidden || offset >= end || a.ended || b.ended) {
        a.pause();
        b.pause();
        setPlaying(false);
        setProgress(Math.min(end, offset));
        if (a.hidden || b.hidden) setMessage("A recording became unavailable.");
        return;
      }
      if (a.readyState < 3 || b.readyState < 3) return;
      const target = offset + recordings[1].eventOffsetSeconds;
      if (Math.abs(b.currentTime - target) > .25 && !b.seeking) b.currentTime = target;
      setProgress(offset);
    }, 120);
    return () => window.clearInterval(timer);
  }, [end, playing, recordings]);

  return <section aria-label="Synchronized recordings" className="gv-camera-evidence">
    <div className="gv-detail-actions">
      <button type="button" className="gv-button" onClick={toggle}>{playing ? "Pause both" : "Play both"}</button>
      <button type="button" className="gv-button" onClick={() => seek(0)}>Seek to event</button>
    </div>
    <label className="gv-incident-field">Recording time
      <input type="range" min={start} max={end} step=".01" value={progress}
        aria-valuetext={new Date(Date.parse(eventTime) + progress * 1000).toISOString()}
        onChange={(event) => seek(Number(event.target.value))} />
    </label>
    <p>{new Date(Date.parse(eventTime) + progress * 1000).toISOString()}</p>
    <p>Aligned to recorded timestamps. Camera clock accuracy is unverified.</p>
    {clockWarnings.map((warning) => <p key={warning}>{warning}</p>)}
    <p role="status">{message}</p>
    {recordings.map((recording, index) => {
      const name = sensors.find((sensor) => sensor.id === recording.cameraId)?.name ?? recording.cameraId;
      return <div key={recording.cameraId}><h4>{name}</h4>
        <RecordedFeed recording={recording} name={name + " synchronized recording"} synchronized={index === 0 ? first : second} /></div>;
    })}
  </section>;
}
