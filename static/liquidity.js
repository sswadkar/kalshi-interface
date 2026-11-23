/* Liquidity monitor. Prices in cents; rewards are estimated dollars per day. */
(() => {
  "use strict";
  const W = window.Workstation, {$, escape: e} = W;
  const CATEGORY = {
    MACRO: ["Macro release", "Scheduled data releases: least exposed to better-informed traders"],
    WEATHER: ["Weather", "Forecast-driven: moderately exposed"],
    NEWS: ["News-driven", "News, sports, politics: most exposed to better-informed traders"],
  };
  let orders = [];

  const status = (order) => order.only ? ["Only quote", "error"]
    : order.at_top && order.queue_ahead === 0 ? ["Next to fill", "error"]
    : order.at_top ? ["At top", "warning"] : null;
  const urgency = (order) => order.only ? 0 : order.at_top && order.queue_ahead === 0 ? 1 : order.at_top ? 2 : 3;
  const whole = (value) => W.numeric(value) ? Math.round(Number(value)).toLocaleString() : "—";
  const reward = (value) => value === null || value === undefined ? "—" : W.money(value);

  function renderOrders() {
    const events = new Map();
    for (const order of orders) events.set(order.event, [...(events.get(order.event) || []), order]);
    W.render("orders", [...events].map(([event, list]) => {
      list.sort((a, b) => urgency(a) - urgency(b) || (a.queue_ahead ?? Infinity) - (b.queue_ahead ?? Infinity));
      const daily = list.reduce((sum, order) => sum + (order.reward_daily || 0), 0);
      return `<section class="group">
        <div class="group-heading"><h3>${e(event)} <span class="count">${list.length} order${list.length === 1 ? "" : "s"}</span></h3>${daily > 0 ? `<span class="pill good">${W.money(daily)} / day</span>` : ""}</div>
        <div class="table-scroll"><table><thead><tr><th>Market</th><th>Order</th><th>Price</th><th>Best</th><th>Queue ahead</th><th>Remaining</th><th>Reward / day</th><th></th></tr></thead><tbody>${list.map((order) => {
          const flag = status(order);
          return `<tr>
            <td class="market-name">${e(order.sub_title || order.title || order.ticker)}<span class="ticker mono">${e(order.ticker)}</span>${flag ? ` <span class="pill ${flag[1]}">${flag[0]}</span>` : ""}</td>
            <td>${e(String(order.action).toUpperCase())} ${e(String(order.side).toUpperCase())}</td>
            <td class="quote">${W.cents(order.price)}</td>
            <td class="quote">${W.cents(order.best)}</td>
            <td class="number">${order.at_top ? whole(order.queue_ahead) : "—"}</td>
            <td class="number">${whole(order.remaining)}</td>
            <td class="number">${reward(order.reward_daily)}</td>
            <td>${order.automated ? `<span class="hint" title="Managed by an automated strategy">Automated</span>`
              : `<button class="small" data-requeue="${e(order.order_id)}" data-focus="requeue-${e(order.order_id)}" data-mutation>Requeue</button>`}</td>
          </tr>`;
        }).join("")}</tbody></table></div>
      </section>`;
    }).join("") || W.empty("No resting orders", "Limit orders waiting in the book will appear here."));
  }

  async function refresh() {
    const data = await W.api("/api/liquidity/orders");
    W.connection(true);
    if (W.state.busy) return;
    orders = data.orders || [];
    $("orderCount").textContent = data.count ?? 0;
    $("topCount").textContent = data.at_top ?? 0;
    $("incentivizedCount").textContent = data.incentivized_count ?? 0;
    $("rewardTotal").textContent = W.money(data.reward_daily_total ?? 0);
    $("ordersState").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    renderOrders();
  }

  function renderScan(rows) {
    W.render("scanResults", rows.length ? `<table><thead><tr><th>Market</th><th>Category</th><th>Pool / day</th><th>Cheaper side</th><th>Best bid</th><th>Est. reward / day</th><th>Both sides qualify</th></tr></thead><tbody>${rows.map((row) => {
      const [label, description] = CATEGORY[row.risk] || [row.risk, ""];
      return `<tr><td class="mono">${e(row.ticker)}</td><td><span class="pill" title="${e(description)}">${e(label)}</span></td>
        <td class="number">${W.money(row.daily_dollars)}</td><td>${e(String(row.cheap_side).toUpperCase())}</td>
        <td class="quote">${W.cents(Math.round(row.cheap_ref_price * 100))}</td><td class="number">${W.money(row.est_daily)}</td>
        <td>${row.two_sided ? "Yes" : "No"}</td></tr>`;
    }).join("")}</tbody></table>` : W.empty("No qualifying markets", "No active program matched, or no matching book reaches its target size."));
  }

  $("scanForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.target, size = W.quantity(form.elements.my_size);
    if (size === null) return;
    const query = new URLSearchParams({series: form.elements.series.value, tickers: form.elements.tickers.value, my_size: size});
    W.action(async () => {
      W.render("scanResults", `<div class="empty">Scanning order books…</div>`);
      renderScan((await W.api(`/api/liquidity/scan?${query}`)).rows || []);
    });
  });

  $("orders").addEventListener("click", (event) => {
    const button = event.target.closest("[data-requeue]");
    if (!button) return;
    const order = orders.find((item) => item.order_id === button.dataset.requeue);
    if (!order) return;
    W.action(async () => {
      if (!await W.confirm("Requeue order?", `${String(order.action).toUpperCase()} ${String(order.side).toUpperCase()} · ${order.ticker} at ${order.price}¢\n\nCancels this order and places the same post-only order again, at the back of its price level. If the market has moved through the price, the new order is rejected and the original stays cancelled.`, "Requeue")) return;
      await W.api(`/api/orders/reset/${encodeURIComponent(order.order_id)}`, "POST");
      W.notify(`Requeued ${order.ticker}. Refreshing orders.`);
      await refresh();
    });
  });

  W.poll(refresh, undefined, 10000);
})();
