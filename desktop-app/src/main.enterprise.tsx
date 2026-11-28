import React from "react";
import ReactDOM from "react-dom/client";
import { EnterpriseMonitoring } from "./features/monitoring/EnterpriseMonitoring";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <EnterpriseMonitoring />
  </React.StrictMode>,
);
