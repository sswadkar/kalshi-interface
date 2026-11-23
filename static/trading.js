/* Manual trading view. Prices arrive in cents; account values in dollars. */
(() => {
  "use strict";
  const W = window.Workstation, {$, escape: e} = W;
  const drafts = new Map();
  let snapshot = {markets: [], positions: []}, resting = [], restingConnected = false;
  const draft = (ticker) => drafts.get(ticker) || {quantity: "50", side: "yes"};
  const quote = (value) => W.cents(Number(value) > 0 ? value : null);

  function renderMarkets() {
    const markets = snapshot.markets || [];
    $("marketCount").textContent = markets.length;
    W.render("markets", markets.length ? `<table><thead><tr><th>Market</th><th>YES · bid / ask</th><th>NO · bid / ask</th><th>Order ticket</th></tr></thead><tbody>${markets.map((market) => {
      const ticker = market.market_ticker, values = draft(ticker);
      return `<tr><td class="market-name"><strong>${e(market.team || ticker)}</strong><span class="ticker mono">${e(ticker)}</span></td>
        <td class="quote" data-label="YES · bid / ask">${quote(market.yes_bid)}<span class="spread">/</span>${quote(market.yes_ask)}</td>
        <td class="quote" data-label="NO · bid / ask">${quote(market.no_bid)}<span class="spread">/</span>${quote(market.no_ask)}</td>
        <td><form class="ticket" data-ticker="${e(ticker)}">
          <select name="side" aria-label="Side for ${e(ticker)}" data-focus="side-${e(ticker)}"><option value="yes" ${values.side === "yes" ? "selected" : ""}>YES</option><option value="no" ${values.side === "no" ? "selected" : ""}>NO</option></select>
          <input name="quantity" type="number" min="1" step="1" required value="${e(values.quantity)}" aria-label="Quantity for ${e(ticker)}" data-focus="qty-${e(ticker)}">
          <button type="submit" name="action" value="buy" class="buy" data-mutation data-focus="buy-${e(ticker)}">Buy</button>
          <button type="submit" name="action" value="sell" class="sell" data-mutation data-focus="sell-${e(ticker)}">Sell</button>
        </form></td></tr>`;
    }).join("")}</tbody></table>` : W.empty("No markets loaded", "Market data will appear when the configured event is available."));
  }
  function renderPositions() {
    const positions = (snapshot.positions || []).filter((position) => Number(position.net_yes_position) !== 0);
    $("positionCount").textContent = positions.length;
    W.render("positions", positions.length ? `<table><thead><tr><th>Market</th><th>Side</th><th>Contracts</th><th>Average price</th><th>Liquidation value</th><th>Fees paid</th><th>Action</th></tr></thead><tbody>${positions.map((position) => {
      const net = Number(position.net_yes_position), ticker = position.ticker;
      const available = (snapshot.markets || []).some((market) => market.market_ticker === ticker);
      return `<tr><td class="mono">${e(ticker)}</td><td>${net > 0 ? "YES" : "NO"}</td><td class="number">${Math.abs(net)}</td><td>${W.cents(position.avg_share_price)}</td><td>${W.money(position.liquidation_value)}</td><td>${W.money(position.fees_paid_dollars)}</td><td><button class="small sell" data-sell-all="${e(ticker)}" data-focus="close-${e(ticker)}" data-mutation data-unavailable="${!available}" title="${available ? "Sell the current holding using IOC" : "No manual quote available for this market"}">Sell all</button></td></tr>`;
    }).join("")}</tbody></table>` : W.empty("No open positions", "Current holdings will appear here."));
  }
  function renderResting() {
    $("restingCount").textContent = resting.length;
    W.render("restingOrders", resting.length ? `<table><thead><tr><th>Market / side</th><th>Price</th><th>Remaining</th><th>Queue</th><th>Action</th></tr></thead><tbody>${resting.map((order) => `<tr>
      <td><span class="mono">${e(order.ticker)}</span><span class="ticker">${e(order.action)} ${e(order.side)}</span></td><td>${W.cents(order.price)}</td><td>${e(order.remaining ?? "—")}</td><td>${e(order.queue_position ?? "—")}</td>
      <td><button class="small danger" data-cancel="${e(order.order_id)}" data-focus="cancel-${e(order.order_id)}" data-mutation data-unavailable="${!restingConnected}">Cancel</button></td></tr>`).join("")}</tbody></table>` : W.empty("No resting orders", "Orders resting at the exchange will appear here."));
  }
  async function refresh() {
    const data = await W.api("/api/status");
    if (W.state.busy) return;
    snapshot = data;
    W.mode(data.dry_run);
    W.account(data.user_info, data.last_pull);
    W.messages(data.messages);
    W.connection(true);
    renderMarkets(); renderPositions();
  }
  async function refreshResting() {
    const data = await W.api("/api/orders/resting");
    if (W.state.busy) return;
    resting = data.resting_orders || [];
    restingConnected = true;
    $("restingState").textContent = "Live cancellations";
    renderResting();
  }
  async function trade(action, side, ticker, quantity) {
    const result = await W.api(`/api/order/${action}`, "POST", {ticker, side, quantity});
    if (result.dry_run) W.notify(`Dry run: ${action.toUpperCase()} ${quantity} ${side.toUpperCase()} · ${ticker}. No order submitted.`);
    else W.notify(`Exchange response received for ${ticker}. Filled: ${result.fill_count ?? result.order?.fill_count ?? "see activity"}. Check positions and activity for the final result.`);
  }

  $("markets").addEventListener("input", (event) => {
    const form = event.target.closest("form");
    if (form) drafts.set(form.dataset.ticker, {quantity: form.elements.quantity.value, side: form.elements.side.value});
  });
  $("markets").addEventListener("submit", (event) => {
    event.preventDefault();
    // Enter in the quantity field must not silently choose a trade direction.
    if (!event.submitter) return;
    const form = event.target, quantity = W.quantity(form.elements.quantity);
    if (quantity !== null) W.action(() => trade(event.submitter.value, form.elements.side.value, form.dataset.ticker, quantity));
  });
  $("markets").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.tagName === "INPUT") event.preventDefault();
  });
  $("positions").addEventListener("click", (event) => {
    const button = event.target.closest("[data-sell-all]");
    if (!button) return;
    const position = snapshot.positions.find((item) => item.ticker === button.dataset.sellAll);
    if (position) W.action(() => trade("sell", position.net_yes_position > 0 ? "yes" : "no", position.ticker, Math.abs(position.net_yes_position)));
  });
  $("restingOrders").addEventListener("click", (event) => {
    const button = event.target.closest("[data-cancel]");
    if (!button || !restingConnected) return;
    const order = resting.find((item) => item.order_id === button.dataset.cancel);
    if (!order) return;
    W.action(async () => {
      if (!await W.confirm("Cancel resting order?", `${order.action} ${order.side} · ${order.ticker}\nRemaining: ${order.remaining ?? "unknown"}\nThis cancels an exchange order even when manual trading is in dry run.`, "Cancel order")) return;
      await W.api(`/api/orders/cancel/${encodeURIComponent(order.order_id)}`, "DELETE");
      W.notify("Cancellation response received. Refreshing resting orders.");
    });
  });
  W.poll(refresh);
  W.poll(refreshResting, (error) => {
    restingConnected = false;
    $("restingState").textContent = "Updates unavailable · retrying";
    $("restingState").title = error.message;
    renderResting();
  });
})();
