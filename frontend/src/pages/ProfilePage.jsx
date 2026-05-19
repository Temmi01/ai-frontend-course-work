import { useEffect, useMemo, useState } from "react";
import { getHistory, getMe, loginUser, registerUser } from "../core/api";
import { clearSession, getUser } from "../core/session";

const ALGO_LABELS = {
  astar: "A* (Евклідова)",
  astar_manhattan: "A* (Манхеттенська)",
  alt: "ALT",
  mlp: "MLP",
  gnn: "GNN",
};

function formatHistoryDate(value) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(String(value));
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return new Intl.DateTimeFormat("uk-UA", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(parsed);
}

function formatPoint(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "-";
  }
  const parts = raw.split(/[, ]+/).filter(Boolean);
  if (parts.length < 2) {
    return raw;
  }
  const lat = Number(parts[0]);
  const lng = Number(parts[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return raw;
  }
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

function formatMetric(value, maxDigits = 3) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return String(value ?? "-");
  }
  return n.toLocaleString("uk-UA", {
    minimumFractionDigits: 0,
    maximumFractionDigits: maxDigits,
  });
}

function HistoryTable({ rows }) {
  if (!rows.length) {
    return <p className="muted">Історія відсутня.</p>;
  }

  return (
    <div className="history-table-wrap" role="region" aria-label="Таблиця історії маршрутів">
      <table className="history-table">
        <thead>
          <tr>
            <th>Дата</th>
            <th>Початок</th>
            <th>Кінець</th>
            <th>Алгоритм</th>
            <th>Відстань (км)</th>
            <th>Час (хв)</th>
            <th>Виконання (мс)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id}>
              <td className="history-col-date">{formatHistoryDate(item.date)}</td>
              <td className="history-col-point">{formatPoint(item.start)}</td>
              <td className="history-col-point">{formatPoint(item.end)}</td>
              <td className="history-col-algo">
                <span className="algo-chip">{ALGO_LABELS[item.algorithm] || item.algorithm}</span>
              </td>
              <td className="history-col-number">{formatMetric(item.distance_km)}</td>
              <td className="history-col-number">{formatMetric(item.time_min)}</td>
              <td className="history-col-number">{formatMetric(item.execution_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ProfilePage() {
  const [user, setUser] = useState(getUser());
  const [tab, setTab] = useState("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");

  const [historyRows, setHistoryRows] = useState([]);
  const [historyFilter, setHistoryFilter] = useState("all");
  const [historyLoading, setHistoryLoading] = useState(false);

  async function refreshMe() {
    try {
      const payload = await getMe();
      setUser(payload.user);
    } catch {
      setUser(getUser());
    }
  }

  async function refreshHistory() {
    if (!user) {
      setHistoryRows([]);
      return;
    }
    setHistoryLoading(true);
    try {
      const rows = await getHistory(100);
      setHistoryRows(Array.isArray(rows) ? rows : []);
    } catch {
      setHistoryRows([]);
    } finally {
      setHistoryLoading(false);
    }
  }

  useEffect(() => {
    refreshMe();
  }, []);

  useEffect(() => {
    refreshHistory();
  }, [user?.id, user?.is_admin]);

  const filteredHistory = useMemo(() => {
    if (historyFilter === "all") {
      return historyRows;
    }
    return historyRows.filter((row) => row.algorithm === historyFilter);
  }, [historyRows, historyFilter]);

  async function submitLogin(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const normalizedEmail = email.trim().toLowerCase();
      await loginUser({ email: normalizedEmail, password });
      await refreshMe();
      setEmail("");
      setPassword("");
    } catch (err) {
      setError(err?.message || "Помилка входу");
    } finally {
      setBusy(false);
    }
  }

  async function submitRegister(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      let normalizedEmail = email.trim().toLowerCase();
      let normalizedName = name.trim();

      if (normalizedName.includes("@") && normalizedEmail && !normalizedEmail.includes("@")) {
        const previousEmailField = normalizedEmail;
        normalizedEmail = normalizedName.toLowerCase();
        normalizedName = previousEmailField;
      }

      await registerUser({ email: normalizedEmail, password, name: normalizedName });
      await refreshMe();
      setEmail("");
      setPassword("");
      setName("");
    } catch (err) {
      setError(err?.message || "Помилка реєстрації");
    } finally {
      setBusy(false);
    }
  }

  function handleLogout() {
    clearSession();
    setUser(null);
    setHistoryRows([]);
  }

  return (
    <section className="card auth-card profile-card">
      <h2>Профіль</h2>

      {!user ? (
        <>
          <div className="actions-row" style={{ marginBottom: "0.8rem" }}>
            <button
              type="button"
              className={`secondary-btn ${tab === "login" ? "active" : ""}`}
              onClick={() => setTab("login")}
            >
              Вхід
            </button>
            <button
              type="button"
              className={`secondary-btn ${tab === "register" ? "active" : ""}`}
              onClick={() => setTab("register")}
            >
              Реєстрація
            </button>
          </div>

          {tab === "login" ? (
            <form className="comment-form" onSubmit={submitLogin}>
              <label>
                Email
                <input
                  type="email"
                  name="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </label>
              <label>
                Пароль
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={6}
                />
              </label>
              {error ? <div className="field-error">{error}</div> : null}
              <button type="submit" className="primary-btn" disabled={busy}>
                {busy ? "Вхід..." : "Увійти"}
              </button>
            </form>
          ) : (
            <form className="comment-form" onSubmit={submitRegister}>
              <label>
                Ім'я
                <input
                  type="text"
                  name="name"
                  autoComplete="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  minLength={2}
                />
              </label>
              <label>
                Email
                <input
                  type="email"
                  name="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </label>
              <label>
                Пароль
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={6}
                />
              </label>
              {error ? <div className="field-error">{error}</div> : null}
              <button type="submit" className="primary-btn" disabled={busy}>
                {busy ? "Реєстрація..." : "Зареєструватися"}
              </button>
            </form>
          )}
        </>
      ) : (
        <>
          <div className="actions-row" style={{ marginBottom: "0.8rem" }}>
            <span className="badge">{user.is_admin ? "Адміністратор" : "Користувач"}</span>
            <button type="button" className="secondary-btn" onClick={handleLogout}>
              Вийти
            </button>
          </div>

          <p>
            <strong>{user.name}</strong> · {user.email}
          </p>

          <label className="history-filter">
            Фільтр алгоритму
            <select value={historyFilter} onChange={(e) => setHistoryFilter(e.target.value)}>
              <option value="all">Усі</option>
              <option value="astar">A* (Евклідова)</option>
              <option value="astar_manhattan">A* (Манхеттенська)</option>
              <option value="alt">ALT</option>
              <option value="mlp">MLP</option>
              <option value="gnn">GNN</option>
            </select>
          </label>

          {historyLoading ? <p className="muted">Завантаження історії...</p> : <HistoryTable rows={filteredHistory} />}
        </>
      )}
    </section>
  );
}
