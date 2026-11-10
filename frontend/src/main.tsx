import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import NewRun from "./pages/NewRun";
import RunView from "./pages/RunView";
import ReviewQueue from "./pages/ReviewQueue";
import Reference from "./pages/Reference";
import Settings from "./pages/Settings";
import Inbox from "./pages/Inbox";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="new" element={<NewRun />} />
          <Route path="intake" element={<Inbox />} />
          <Route path="inbox" element={<Navigate to="/intake" replace />} />
          <Route path="analytics" element={<Navigate to="/" replace />} />
          <Route path="runs/:id" element={<RunView />} />
          <Route path="review" element={<ReviewQueue />} />
          <Route path="reference" element={<Reference />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
