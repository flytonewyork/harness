import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import AcuteApp from "./acute/AcuteApp";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><AcuteApp /></StrictMode>);
