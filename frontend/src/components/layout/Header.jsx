import { useEffect, useMemo, useState } from "react";
import { getUser } from "../../core/session";

function profileLabel(user) {
  if (!user) {
    return "Увійти";
  }
  const trimmedName = String(user.name || "").trim();
  if (!trimmedName) {
    return "Профіль";
  }
  return `Профіль: ${trimmedName}`;
}

export default function Header({ onMenuToggle }) {
  const [user, setUser] = useState(getUser());

  useEffect(() => {
    const handler = () => setUser(getUser());
    window.addEventListener("session-changed", handler);
    return () => window.removeEventListener("session-changed", handler);
  }, []);

  const label = useMemo(() => profileLabel(user), [user]);

  return (
    <div className="topbar">
      <div className="brand">
        <button className="hamburger" type="button" aria-label="Відкрити меню" onClick={onMenuToggle}>
          <span />
          <span />
          <span />
        </button>
        <h1>Система навігації AI</h1>
      </div>

      <a className="profile-btn" href="profile.html">
        {label}
      </a>
    </div>
  );
}
