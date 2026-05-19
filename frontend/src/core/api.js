import { getToken, setSession } from "./session";

function resolveApiBase() {
  const envBase = (import.meta.env.VITE_API_BASE_URL || "").trim();
  if (envBase) {
    return envBase.replace(/\/+$/, "");
  }

  if (typeof window !== "undefined") {
    const host = window.location.hostname || "127.0.0.1";
    const proto = window.location.protocol || "http:";
    return `${proto}//${host}:8000`;
  }

  return "http://127.0.0.1:8000";
}

const API_BASE = resolveApiBase();

async function request(path, options = {}) {
  const token = getToken();
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "";
    if (/failed to fetch|networkerror|load failed/i.test(message)) {
      throw new Error(
        `Не вдалося підключитися до API (${API_BASE}). Перевірте, що бекенд запущений на http://127.0.0.1:8000 і CORS налаштовано коректно.`
      );
    }
    throw err;
  }

  const contentType = response.headers.get("content-type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const detail = payload?.detail || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

export function getApiBase() {
  return API_BASE;
}

export function getHealth() {
  return request("/api/health", { method: "GET" });
}

export function registerUser(payload) {
  return request("/api/auth/register", { method: "POST", body: JSON.stringify(payload) }).then((res) => {
    setSession(res.token, res.user);
    return res;
  });
}

export function loginUser(payload) {
  return request("/api/auth/login", { method: "POST", body: JSON.stringify(payload) }).then((res) => {
    setSession(res.token, res.user);
    return res;
  });
}

export function getMe() {
  return request("/api/auth/me", { method: "GET" });
}

export function logoutUser() {
  return request("/api/auth/logout", { method: "POST" });
}

export function buildRoute(payload) {
  return request("/api/route", { method: "POST", body: JSON.stringify(payload) });
}

export function getArticles() {
  return request("/api/articles", { method: "GET" });
}

export function createArticle(payload) {
  return request("/api/articles", { method: "POST", body: JSON.stringify(payload) });
}

export function deleteArticle(articleId) {
  return request(`/api/articles/${articleId}`, { method: "DELETE" });
}

export function getArticleComments(articleId, params = {}) {
  const query = new URLSearchParams();
  if (params.offset != null) {
    query.set("offset", String(params.offset));
  }
  if (params.limit != null) {
    query.set("limit", String(params.limit));
  }
  if (params.authorName) {
    query.set("author_name", String(params.authorName));
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request(`/api/articles/${articleId}/comments${suffix}`, { method: "GET" });
}

export function postArticleComment(articleId, payload) {
  return request(`/api/articles/${articleId}/comments`, { method: "POST", body: JSON.stringify(payload) });
}

export function deleteArticleComment(articleId, commentId, authorName = "") {
  const query = new URLSearchParams();
  if (authorName) {
    query.set("author_name", authorName);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request(`/api/articles/${articleId}/comments/${commentId}${suffix}`, { method: "DELETE" });
}

export function getHistory(limit = 100) {
  return request(`/api/history?limit=${encodeURIComponent(limit)}`, { method: "GET" });
}

export function getMyHistory(limit = 100) {
  return request(`/api/history/me?limit=${encodeURIComponent(limit)}`, { method: "GET" });
}
