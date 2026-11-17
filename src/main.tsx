import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { router } from "./router";
import "./styles/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <RouterProvider router={router} />
    <Toaster
      theme="dark"
      position="bottom-right"
      toastOptions={{ className: "font-sans text-sm" }}
      closeButton
    />
  </React.StrictMode>,
);
