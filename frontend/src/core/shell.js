import { clearSession, getUser } from "./session";

const DRAWER_BACKDROP_ID = "app-drawer-backdrop";
const DRAWER_ID = "app-drawer";

const NAV_ITEMS = [
  { href: "index.html", label: "Карта" },
  { href: "profile.html", label: "Мій профіль" },
  { href: "articles.html", label: "Статті" },
];

function currentPageName() {
  const path = window.location.pathname || "";
  const fileName = path.split("/").pop() || "index.html";
  if (!fileName || fileName === "") {
    return "index.html";
  }
  return fileName;
}

function closeDrawer() {
  const drawer = document.getElementById(DRAWER_ID);
  document.body.classList.remove("drawer-open");
  if (drawer) {
    drawer.setAttribute("aria-hidden", "true");
  }
}

export function toggleDrawer() {
  const drawer = document.getElementById(DRAWER_ID);
  if (!drawer) {
    return;
  }

  const opened = !document.body.classList.contains("drawer-open");
  document.body.classList.toggle("drawer-open", opened);
  drawer.setAttribute("aria-hidden", opened ? "false" : "true");
}

function buildDrawer() {
  if (document.getElementById(DRAWER_ID)) {
    return;
  }

  const backdrop = document.createElement("div");
  backdrop.id = DRAWER_BACKDROP_ID;
  backdrop.className = "drawer-backdrop";
  backdrop.addEventListener("click", closeDrawer);
  document.body.appendChild(backdrop);

  const drawer = document.createElement("aside");
  drawer.id = DRAWER_ID;
  drawer.className = "app-drawer";
  drawer.setAttribute("aria-hidden", "true");

  const header = document.createElement("div");
  header.className = "drawer-header";

  const title = document.createElement("h3");
  title.textContent = "Меню";

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "drawer-close";
  closeButton.setAttribute("aria-label", "Закрити меню");
  closeButton.textContent = "×";
  closeButton.addEventListener("click", closeDrawer);

  header.appendChild(title);
  header.appendChild(closeButton);

  const nav = document.createElement("nav");
  nav.className = "drawer-nav";
  nav.setAttribute("aria-label", "Бічне меню");

  const activeFile = currentPageName();
  NAV_ITEMS.forEach((item) => {
    const link = document.createElement("a");
    link.href = item.href;
    link.textContent = item.label;
    if (item.href === activeFile) {
      link.setAttribute("aria-current", "page");
    }
    link.addEventListener("click", closeDrawer);
    nav.appendChild(link);
  });

  const footer = document.createElement("div");
  footer.className = "drawer-footer";

  const logoutButton = document.createElement("button");
  logoutButton.type = "button";
  logoutButton.className = "drawer-logout";
  logoutButton.textContent = "Вийти з акаунта";
  logoutButton.addEventListener("click", () => {
    clearSession();
    closeDrawer();
    if (window.location.pathname.endsWith("profile.html")) {
      window.location.reload();
      return;
    }
    window.location.href = "profile.html";
  });

  footer.appendChild(logoutButton);
  drawer.appendChild(header);
  drawer.appendChild(nav);
  drawer.appendChild(footer);
  document.body.appendChild(drawer);
}

function updateAuthButtons() {
  const user = getUser();
  const logoutBtn = document.querySelector(".drawer-logout");
  if (logoutBtn) {
    logoutBtn.style.display = user ? "inline-flex" : "none";
  }
}

export function initShell() {
  buildDrawer();
  updateAuthButtons();
  window.addEventListener("session-changed", updateAuthButtons);
}
