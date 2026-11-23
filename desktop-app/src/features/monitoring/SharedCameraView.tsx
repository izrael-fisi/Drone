import { useLocation } from "react-router-dom";
import { CameraFeed } from "./CameraFeed";
import { httpEndpointUrl } from "./monitoringModel";

export function SharedCameraView() {
  const { hash } = useLocation();
  let feed: string | null = null;
  try {
    const url = httpEndpointUrl(decodeURIComponent(hash.slice(1)));
    if (url && /\/view\/[a-f0-9]{48}\/index\.m3u8$/.test(new URL(url).pathname)) feed = url;
  } catch { /* Malformed links show an unavailable state. */ }
  return <main className="gv-app gv-shared-camera">
    <section className="gv-details" aria-label="Shared camera view">
      <h1>Shared camera view</h1>
      {feed ? <CameraFeed camera={{ id: "shared", name: "Shared camera", demo: false, cameraFeedUrl: feed }} />
        : <p role="status">This camera viewer link is invalid.</p>}
    </section>
  </main>;
}
