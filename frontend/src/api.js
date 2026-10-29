// Accès à l'API commune. L'URL vient de config.js (écrit au déploiement).
const BASE = ((window.APP_CONFIG || {}).apiUrl || "").replace(/\/$/, "");
export const configured = Boolean(BASE);

// Les données consultées ne changent pas pendant une session : revenir sur un
// filtre déjà vu est instantané.
const cache = new Map();

async function request(path, options) {
  const response = await fetch(BASE + path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

export async function get(path, { fresh = false } = {}) {
  if (!fresh && cache.has(path)) return cache.get(path);
  const data = await request(path);
  if (!fresh) cache.set(path, data);
  return data;
}

export function post(path, body) {
  return request(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const UNAVAILABLE = "Le service ne répond pas pour l'instant. Réessayez dans quelques minutes.";
