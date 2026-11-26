/* Shared presentation and request handling. Trading decisions stay on the server. */
(() => {
  "use strict";
  let theme = "dark";
  try { theme = localStorage.getItem("workstation-theme") || "dark"; } catch { /* Storage may be unavailable. */ }
  document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";

  const $ = (id) => document.getElementById(id);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const numeric = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const money = (value) => numeric(value) ? Number(value).toLocaleString(undefined, {style: "currency", currency: "USD"}) : "—";
  const cents = (value) => numeric(value) ? `${Number(value).toLocaleString(undefined, {maximumFractionDigits: 2})}¢` : "—";
  const time = (value) => {
    if (!value) return "Awaiting data";
    const date = new Date(typeof value === "number" ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? "Awaiting data" : date.toLocaleTimeString();
  };
  const state = {busy: false, connected: false, dryRun: null};

  async function api(path, method = "GET", body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let response;
    try {
      response = await fetch(path, {
        method, signal: controller.signal, cache: "no-store",
        headers: body === undefined ? {} : {"Content-Type": "application/json"},
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok || data.error || data.success === false) {
        const detail = data.error || data.detail || `Request failed (${response.status})`;
        const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        error.code = data.code;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.code === "configuration_save_failed") throw error;
      const detail = error.name === "AbortError" ? "Request timed out." : error.message;
      throw new Error(method === "GET" ? detail : `${detail} The action may have reached the server. Check current settings, orders, and positions before retrying.`);
    } finally { clearTimeout(timeout); }
  }

  function lockControls() {
    document.querySelectorAll("[data-mutation]").forEach((button) => {
      button.disabled = state.busy || !state.connected || button.dataset.unavailable === "true";
    });
  }
  function notify(message, kind = "info") {
    const node = $("actionNotice");
    node.textContent = message;
    node.className = `notice ${kind}`;
    node.hidden = false;
  }
  async function action(operation) {
    if (state.busy || !state.connected) return;
    state.busy = true;
    lockControls();
    try { await operation(); }
    catch (error) { notify(error.message, "error"); }
    finally { state.busy = false; lockControls(); }
  }
  function connection(connected, message) {
    state.connected = connected;
    const badge = $("connectionStatus");
    badge.className = `pill ${connected ? "good" : "warning"}`;
    badge.textContent = connected ? "API connected" : "Reconnecting";
    $("connectionNotice").hidden = connected;
    if (!connected) $("connectionNotice").textContent = `Updates unavailable. Displayed values may be old; retrying automatically. ${message || ""}`;
    lockControls();
  }
  function poll(refresh, onError = (error) => connection(false, error.message), interval = 2000) {
    const tick = async () => {
      try { await refresh(); } catch (error) { onError(error); }
      setTimeout(tick, interval);
    };
    tick();
  }
  function mode(dryRun) {
    state.dryRun = dryRun;
    if (!$("modeBadge")) return;
    const badge = $("modeBadge");
    badge.textContent = dryRun ? "DRY RUN" : "LIVE ORDERS";
    badge.className = `pill ${dryRun ? "warning" : "live"}`;
    $("dryRun").checked = dryRun;
  }
  function account(user = {}, lastPull) {
    $("cashValue").textContent = money(numeric(user.balance) ? Number(user.balance) / 100 : null);
    $("positionValue").textContent = money(numeric(user.portfolio_value) ? Number(user.portfolio_value) / 100 : null);
    $("totalValue").textContent = money(numeric(user.balance) && numeric(user.portfolio_value) ? (Number(user.balance) + Number(user.portfolio_value)) / 100 : null);
    $("lastPull").textContent = time(lastPull);
    $("lastPull").title = lastPull || "No market poll received";
  }
  function empty(title, description = "") {
    return `<div class="empty"><strong>${escape(title)}</strong>${escape(description)}</div>`;
  }
  function messages(items = []) {
    $("messages").innerHTML = items.slice(0, 75).map((item) => `<div class="log-entry">
      <time>${escape(time(item.timestamp))}</time><div class="${item.type === "ERROR" ? "negative" : ""}"><b>${escape(item.type)}</b> · ${escape(item.text)}</div>
    </div>`).join("") || empty("No activity yet", "Order results and system messages appear here.");
  }
  // Polls preserve entered values (owned by each page) and keyboard focus.
  function render(id, html) {
    const container = $(id), active = document.activeElement;
    if (container.contains(active) && active.tagName === "SELECT") return;
    const key = container.contains(active) ? active.dataset.focus : null;
    const scroll = container.scrollLeft;
    container.innerHTML = html;
    container.scrollLeft = scroll;
    if (key) [...container.querySelectorAll("[data-focus]")].find((node) => node.dataset.focus === key)?.focus({preventScroll: true});
    lockControls();
  }
  function quantity(input) {
    if (!input.reportValidity()) return null;
    const value = Number(input.value);
    if (!Number.isSafeInteger(value) || value < 1) { input.focus(); return null; }
    return value;
  }
  function confirm(title, description, label = "Confirm") {
    const dialog = $("confirmDialog");
    $("confirmTitle").textContent = title;
    $("confirmDescription").textContent = description;
    $("confirmAccept").textContent = label;
    dialog.returnValue = "cancel";
    dialog.showModal();
    return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true}));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const themeButton = $("themeToggle");
    const updateThemeLabel = () => {
      themeButton.textContent = document.documentElement.dataset.theme === "dark" ? "☀ Light mode" : "☾ Dark mode";
    };
    updateThemeLabel();
    themeButton.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("workstation-theme", next); } catch { /* The toggle still works for this page. */ }
      updateThemeLabel();
    });
    // Pages contributed by optional extensions (none in a standard install).
    api("/api/pages").then(({pages = []}) => {
      const nav = document.querySelector("nav");
      for (const page of pages) {
        if (nav.querySelector(`a[href="${CSS.escape(page.path)}"]`)) continue;
        const link = document.createElement("a");
        link.href = page.path;
        link.textContent = page.label;
        nav.append(link);
      }
    }).catch(() => {});
    $("dryRun")?.addEventListener("change", () => {
      const next = $("dryRun").checked, previous = state.dryRun;
      action(async () => {
        if (!next && !await confirm("Enable live orders?", "Manual trades and arbitrage will submit orders to the configured exchange account. Enabled arbitrage automation will also use this mode.", "Enable live orders")) {
          mode(previous); return;
        }
        try {
          const result = await api("/api/settings/dry_run", "PUT", {dry_run: next});
          mode(result.dry_run);
          notify(result.dry_run ? "Manual and arbitrage dry run enabled." : "Manual and arbitrage live orders enabled.");
        } catch (error) { mode(previous); throw error; }
      });
    });
    lockControls();
  });
  window.Workstation = {$, escape, numeric, money, cents, time, state, api, action, notify, connection, poll, mode, account, empty, messages, render, quantity, confirm, lockControls};
})();
