import { useEffect, useState } from "react";
import { buildRoute, getHealth } from "../core/api";
import { getUser } from "../core/session";
import LeafletRouteMap from "../components/map/LeafletRouteMap";

const ROUTE_COLORS = {
  astar: "#0b7285",
  astar_manhattan: "#5f3dc4",
  alt: "#1d3557",
  mlp: "#d9480f",
  gnn: "#ff9f1c",
};

const ALGO_LABELS = {
  astar: "A* (Евклідова)",
  astar_manhattan: "A* (Манхеттенська)",
  alt: "ALT",
  mlp: "MLP",
  gnn: "GNN",
};

const ALL_ALGO_OPTIONS = [
  { key: "astar", label: "A* (Евклідова)" },
  { key: "astar_manhattan", label: "A* (Манхеттенська)" },
  { key: "alt", label: "ALT" },
  { key: "mlp", label: "MLP" },
  { key: "gnn", label: "GNN" },
];

function AlgorithmBadge({ algorithm }) {
  return <span className="badge">{ALGO_LABELS[algorithm] || algorithm}</span>;
}

export default function MapPage() {
  const [startNode, setStartNode] = useState("50.450100, 30.523400");
  const [endNode, setEndNode] = useState("50.454660, 30.523800");
  const [algorithm, setAlgorithm] = useState("astar");
  const [availableAlgorithms, setAvailableAlgorithms] = useState(["astar", "astar_manhattan", "alt"]);
  const [pickMode, setPickMode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [healthMessage, setHealthMessage] = useState("");
  const [graphSource, setGraphSource] = useState("unknown");
  const [routeLayers, setRouteLayers] = useState([]);
  const [activeStats, setActiveStats] = useState(null);
  const [startMarker, setStartMarker] = useState(null);
  const [endMarker, setEndMarker] = useState(null);
  const [user] = useState(getUser());

  useEffect(() => {
    async function loadHealth() {
      try {
        const payload = await getHealth();
        const allowed = payload.available_algorithms || ["astar", "astar_manhattan", "alt"];

        setGraphSource(payload.graph_source || "unknown");
        setAvailableAlgorithms(allowed);

        const hints = [];
        if (payload.graph_source !== "osm_cache") {
          hints.push("OSM-граф ще не завантажено для поточного району.");
        }

        const missing = [];
        if (!allowed.includes("mlp")) {
          missing.push("MLP");
        }
        if (!allowed.includes("gnn")) {
          missing.push("GNN");
        }
        if (missing.length > 0) {
          hints.push(`На сервері не знайдено моделі: ${missing.join(", ")}.`);
        }

        setHealthMessage(hints.join(" "));
      } catch {
        setGraphSource("unknown");
        setAvailableAlgorithms(["astar", "astar_manhattan", "alt"]);
        setHealthMessage("Сервер недоступний. Перевірте запуск бекенду на http://127.0.0.1:8000.");
      }
    }

    loadHealth();
  }, []);

  function getSelectableAlgorithms() {
    const rows = [];
    for (const option of ALL_ALGO_OPTIONS) {
      if (availableAlgorithms.includes(option.key)) {
        rows.push(option);
      }
    }
    return rows;
  }

  function pickFromMap(mode, value, markerLatLng) {
    if (mode === "start") {
      setStartNode(value);
      setStartMarker(markerLatLng);
    }

    if (mode === "end") {
      setEndNode(value);
      setEndMarker(markerLatLng);
    }

    setPickMode("");
  }

  async function runSingle(algoKey) {
    return buildRoute({
      start_node: startNode,
      end_node: endNode,
      algorithm: algoKey,
    });
  }

  async function handleFindRoute() {
    setLoading(true);
    setError("");

    try {
      if (algorithm === "all") {
        const targets = [];
        for (const row of ALL_ALGO_OPTIONS) {
          if (availableAlgorithms.includes(row.key)) {
            targets.push(row.key);
          }
        }

        const rows = [];
        let firstErrorText = "";

        for (const algoKey of targets) {
          try {
            const result = await runSingle(algoKey);
            if (result.path && result.path.length > 0) {
              rows.push(result);
            }
          } catch (err) {
            if (!firstErrorText) {
              firstErrorText = err?.message || "";
            }
          }
        }

        if (rows.length === 0) {
          throw new Error(firstErrorText || "Не вдалося побудувати маршрут");
        }

        setRouteLayers(rows);
        setActiveStats(null);
        setStartMarker([rows[0].start_resolved.lat, rows[0].start_resolved.lng]);
        setEndMarker([rows[0].end_resolved.lat, rows[0].end_resolved.lng]);
      } else {
        const row = await runSingle(algorithm);
        setRouteLayers([row]);
        setActiveStats(row);
        setStartMarker([row.start_resolved.lat, row.start_resolved.lng]);
        setEndMarker([row.end_resolved.lat, row.end_resolved.lng]);
      }
    } catch (err) {
      const rawMessage = err?.message || "";
      if (/failed to fetch|networkerror|api/i.test(rawMessage)) {
        setError("Не вдалося підключитися до сервера. Перевірте запуск бекенду на http://127.0.0.1:8000.");
      } else {
        setError(rawMessage || "Помилка побудови маршруту");
      }
      setRouteLayers([]);
      setActiveStats(null);
    } finally {
      setLoading(false);
    }
  }

  function handleSwap() {
    const oldStart = startNode;
    setStartNode(endNode);
    setEndNode(oldStart);

    const oldStartMarker = startMarker;
    setStartMarker(endMarker);
    setEndMarker(oldStartMarker);
  }

  const canBuildRoute = graphSource !== "unknown";
  const selectableAlgorithms = getSelectableAlgorithms();

  return (
    <div className="map-shell">
      <LeafletRouteMap
        routeLayers={routeLayers}
        routeColors={ROUTE_COLORS}
        pickMode={pickMode}
        onPick={pickFromMap}
        startMarker={startMarker}
        endMarker={endMarker}
      />

      <section className="control-panel" aria-label="Панель керування маршрутом">
        <h2>Вхідні дані</h2>

        <label>
          Оберіть метрику / алгоритм
          <select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
            {selectableAlgorithms.map((row) => (
              <option key={row.key} value={row.key}>
                {row.label}
              </option>
            ))}
            <option value="all">Усі доступні алгоритми</option>
          </select>
        </label>

        <div className="pick-row">
          <button
            className={`secondary-btn ${pickMode === "start" ? "active" : ""}`}
            type="button"
            onClick={() => setPickMode((prev) => (prev === "start" ? "" : "start"))}
          >
            Обрати старт на мапі
          </button>
          <button
            className={`secondary-btn ${pickMode === "end" ? "active" : ""}`}
            type="button"
            onClick={() => setPickMode((prev) => (prev === "end" ? "" : "end"))}
          >
            Обрати фініш на мапі
          </button>
        </div>

        {pickMode ? <p className="picker-hint">Клікніть на мапі для вибору точки.</p> : null}

        <div className="form-grid">
          <label>
            Початкова точка
            <input
              type="text"
              value={startNode}
              onChange={(e) => setStartNode(e.target.value)}
              placeholder="50.450100, 30.523400"
            />
          </label>

          <label>
            Кінцева точка
            <input
              type="text"
              value={endNode}
              onChange={(e) => setEndNode(e.target.value)}
              placeholder="50.454660, 30.523800"
            />
          </label>
        </div>

        <div className="actions-row" style={{ marginTop: "0.7rem" }}>
          <button className="primary-btn" type="button" onClick={handleFindRoute} disabled={loading || !canBuildRoute}>
            {loading ? "Побудова..." : "Побудувати маршрут"}
          </button>
          <button className="secondary-btn" type="button" onClick={handleSwap} disabled={loading}>
            Поміняти точки місцями
          </button>
        </div>

        {healthMessage ? (
          <p className="muted" style={{ marginTop: "0.7rem" }}>
            {healthMessage}
          </p>
        ) : null}

        {!canBuildRoute ? (
          <p className="field-error" style={{ marginTop: "0.5rem" }}>
            Побудову маршруту вимкнено, бо сервер недоступний.
          </p>
        ) : null}

        {user ? (
          <p className="muted" style={{ marginBottom: 0 }}>
            Історія буде збережена для: {user.name || user.email}
          </p>
        ) : (
          <p className="muted" style={{ marginBottom: 0 }}>
            Увійдіть у профіль, щоб зберігати історію маршрутів.
          </p>
        )}
      </section>

      {loading ? (
        <div className="blocking-overlay">
          <span className="spinner" />
          <strong>Обчислення маршруту...</strong>
        </div>
      ) : null}

      {error ? (
        <section className="stats-card">
          <strong className="field-error">{error}</strong>
        </section>
      ) : null}

      {activeStats ? (
        <section className="stats-card">
          <AlgorithmBadge algorithm={activeStats.algorithm} />
          <div className="stat-row">
            <span>Відстань</span>
            <strong>{activeStats.distance_km} км</strong>
          </div>
          <div className="stat-row">
            <span>Час у дорозі</span>
            <strong>{activeStats.time_min} хв</strong>
          </div>
          <div className="stat-row">
            <span>Виконання</span>
            <strong>{activeStats.execution_ms} мс</strong>
          </div>
          <div className="stat-row">
            <span>Розкриті вузли</span>
            <strong>{activeStats.expanded_nodes}</strong>
          </div>
        </section>
      ) : null}

      {!activeStats && routeLayers.length > 0 ? (
        <section className="stats-card compare-card">
          <div className="compare-table-wrap">
            <table className="compare-table">
              <thead>
                <tr>
                  <th>Алгоритм</th>
                  <th>Довжина</th>
                  <th>Час</th>
                  <th>Виконання</th>
                  <th>Вузли</th>
                </tr>
              </thead>
              <tbody>
                {routeLayers.map((row) => (
                  <tr key={row.algorithm}>
                    <td>
                      <span className="route-dot" style={{ backgroundColor: ROUTE_COLORS[row.algorithm] }} />
                      {ALGO_LABELS[row.algorithm] || row.algorithm}
                    </td>
                    <td>{row.distance_km} км</td>
                    <td>{row.time_min} хв</td>
                    <td>{row.execution_ms} мс</td>
                    <td>{row.expanded_nodes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
