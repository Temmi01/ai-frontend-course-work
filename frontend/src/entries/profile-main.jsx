import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ProfilePage from "../pages/ProfilePage";
import Header from "../components/layout/Header";
import { initShell, toggleDrawer } from "../core/shell";

const headerRootEl = document.getElementById("react-header-root");
if (headerRootEl) {
  const headerRoot = createRoot(headerRootEl);
  headerRoot.render(
    <StrictMode>
      <Header onMenuToggle={toggleDrawer} />
    </StrictMode>
  );
}

initShell();

const rootEl = document.getElementById("react-profile-root");
if (rootEl) {
  const root = createRoot(rootEl);
  root.render(
    <StrictMode>
      <ProfilePage />
    </StrictMode>
  );
}
