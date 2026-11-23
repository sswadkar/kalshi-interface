/* Arbitrage view. Quotes and gross edge arrive in cents; the server owns execution. */
(() => {
  "use strict";
  const W = window.Workstation, {$, escape: e} = W;
  const quantities = new Map();
  let snapshot = {groups: [], settings: {}}, settingsDirty = false, editor = null;
  const settingsForm = $("settingsForm");
  const groupPath = (id) => `/api/arb/groups/${encodeURIComponent(id)}`;

  function renderOpportunities() {
    const candidates = snapshot.groups.filter((group) => group.opportunity);
    $("candidateCount").textContent = candidates.length;
    W.render("opportunities", candidates.map((group) => {
      const opportunity = group.opportunity;
      return `<article class="opportunity">
        <div class="row"><div><h3>${e(group.name)}</h3><small>${opportunity.is_reverse_arb ? "Reverse outcome pair" : "Paired outcome"}</small></div><div><span class="edge">${W.cents(opportunity.profit)}</span> <small>gross / pair</small></div></div>
        <div class="legs">${["a", "b"].map((leg) => `<div class="leg"><small>BUY ${e(String(opportunity[`market_${leg}_side`]).toUpperCase())}</small><span class="mono">${e(opportunity[`market_${leg}`])}</span><span class="leg-price number">${W.cents(opportunity[`market_${leg}_price`])}</span></div>`).join("")}</div>
        <div class="row"><span class="hint">Reference cost ${W.cents(opportunity.total_cost)} / pair</span>
          <form class="ticket" data-group="${e(group.id)}"><label class="hint" for="qty-${e(group.id)}">Qty / leg</label><input id="qty-${e(group.id)}" name="quantity" type="number" min="1" step="1" value="${e(quantities.get(group.id) ?? "10")}" required data-focus="qty-${e(group.id)}"><button type="submit" class="primary" data-mutation data-focus="execute-${e(group.id)}">${snapshot.settings.skip_confirmation ? "Execute pair" : "Review pair"}</button></form>
        </div>
      </article>`;
    }).join("") || W.empty("No candidates above the threshold", "Add related markets to a group, or review your saved settings."));
  }
  function renderGroups() {
    $("groupCount").textContent = snapshot.groups.length;
    W.render("groups", snapshot.groups.map((group) => `<section class="group">
      <div class="group-heading"><h3>${e(group.name)} <span class="count">${group.markets.length} markets</span> <span class="pill ${group.payout_verified ? "good" : ""}" title="${group.payout_verified ? "Automatic execution may trade this group" : "Automatic execution skips this group"}">${group.payout_verified ? "Payouts verified · auto-eligible" : "Unverified · manual only"}</span></h3>
        <div class="group-actions"><button class="quiet small" data-group="${e(group.id)}" data-action="verify" data-focus="verify-${e(group.id)}" data-mutation>${group.payout_verified ? "Unverify" : "Verify payouts"}</button><button class="quiet small" data-group="${e(group.id)}" data-action="rename" data-focus="rename-${e(group.id)}" data-mutation>Rename</button><button class="quiet small danger" data-group="${e(group.id)}" data-action="delete" data-focus="delete-${e(group.id)}" data-mutation>Delete</button></div></div>
      ${group.markets.length ? `<div class="table-scroll"><table><thead><tr><th>Market</th><th>Mapping</th><th>YES ask</th><th>NO ask</th><th></th></tr></thead><tbody>${group.markets.map((market) => `<tr><td class="market-name">${e(market.yes_sub_title || market.title || market.ticker)}<span class="ticker mono">${e(market.ticker)}</span></td><td><span class="pill">${market.is_reverse ? "Reverse" : "Same"}</span></td><td class="quote">${W.cents(market.yes_ask)}</td><td class="quote">${W.cents(market.no_ask)}</td><td><button class="quiet small" data-action="remove" data-group="${e(group.id)}" data-ticker="${e(market.ticker)}" data-focus="remove-${e(group.id)}-${e(market.ticker)}" data-mutation aria-label="Remove ${e(market.ticker)}">Remove</button></td></tr>`).join("")}</tbody></table></div>` : W.empty("Add markets to this group", "At least two markets are needed to compare prices.")}
      <div class="group-footer"><button class="small" data-action="add" data-group="${e(group.id)}" data-focus="add-${e(group.id)}" data-mutation>+ Add market</button></div>
    </section>`).join("") || W.empty("No market groups yet", "Create a group to begin comparing related contracts."));
  }
  function renderSettings() {
    const settings = snapshot.settings;
    $("autoState").textContent = settings.auto_arb ? "Auto execution on" : "Auto execution off";
    $("autoState").className = `pill ${settings.auto_arb ? "warning" : ""}`;
    if (settingsDirty) return;
    for (const name of ["threshold", "buffer", "auto_arb_quantity", "auto_arb", "skip_confirmation"]) {
      const input = settingsForm.elements[name];
      if (input.type === "checkbox") input.checked = Boolean(settings[name]);
      else input.value = settings[name];
    }
    $("settingsState").textContent = "Saved settings";
  }
  async function refresh() {
    const data = await W.api("/api/arb/status");
    if (W.state.busy) return;
    snapshot = data;
    W.mode(data.dry_run);
    W.account(data.user_info, data.last_pull);
    W.messages(data.messages);
    W.connection(true);
    renderSettings(); renderOpportunities(); renderGroups();
  }
  function openEditor(kind, group) {
    editor = {kind, group};
    $("editTitle").textContent = kind === "add" ? "Add market" : kind === "rename" ? "Rename group" : "New group";
    $("editLabel").textContent = kind === "add" ? "Market ticker" : "Group name";
    $("editValue").value = kind === "rename" ? group.name : "";
    $("editReverse").checked = false;
    $("reverseField").hidden = kind !== "add";
    $("editError").hidden = true;
    $("editDialog").showModal();
    $("editValue").focus();
  }

  $("newGroup").addEventListener("click", () => openEditor("create"));
  $("editCancel").addEventListener("click", () => $("editDialog").close());
  $("editForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = $("editValue").value.trim();
    if (!value) { $("editError").textContent = "Enter a value."; $("editError").hidden = false; return; }
    const {kind, group} = editor;
    W.action(async () => {
      try {
        if (kind === "create") await W.api("/api/arb/groups", "POST", {name: value});
        if (kind === "rename") await W.api(groupPath(group.id), "PUT", {name: value});
        if (kind === "add") await W.api(`${groupPath(group.id)}/markets`, "POST", {ticker: value, is_reverse: $("editReverse").checked});
        $("editDialog").close();
        W.notify(kind === "add" ? "Market added. Refreshing groups." : "Group saved. Refreshing groups.");
      } catch (error) {
        $("editError").textContent = error.message;
        $("editError").hidden = false;
      }
    });
  });
  $("groups").addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const group = snapshot.groups.find((item) => item.id === button.dataset.group);
    if (!group) return;
    const kind = button.dataset.action;
    if (kind === "rename" || kind === "add") { openEditor(kind, group); return; }
    if (kind === "verify") {
      const next = !group.payout_verified;
      W.action(async () => {
        if (next && !await W.confirm("Verify payouts?", `Confirm that the contracts in “${group.name}” pay out as complementary outcomes, so every candidate pair is a true hedge. Winner-style markets (e.g. two different teams to win) are NOT complementary.\n\nVerified groups become eligible for automatic execution. Adding a market clears verification.`, "Mark verified")) return;
        await W.api(groupPath(group.id), "PUT", {payout_verified: next});
        W.notify(next ? "Group verified. Automatic execution may trade it." : "Group unverified. Automatic execution will skip it.");
      });
      return;
    }
    W.action(async () => {
      if (!await W.confirm(kind === "delete" ? "Delete group?" : "Remove market?", kind === "delete" ? `Remove “${group.name}” and its market configuration? Existing exchange orders are unaffected.` : `Remove ${button.dataset.ticker} from “${group.name}”? Existing exchange orders are unaffected.`, "Remove")) return;
      await W.api(kind === "delete" ? groupPath(group.id) : `${groupPath(group.id)}/markets/${encodeURIComponent(button.dataset.ticker)}`, "DELETE");
      W.notify("Configuration removed. Refreshing groups.");
    });
  });
  settingsForm.addEventListener("input", () => {
    settingsDirty = true;
    $("settingsState").textContent = "Unsaved changes";
  });
  settingsForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!settingsForm.reportValidity()) return;
    const settings = {};
    for (const name of ["threshold", "buffer", "auto_arb_quantity"]) settings[name] = Number(settingsForm.elements[name].value);
    for (const name of ["auto_arb", "skip_confirmation"]) settings[name] = settingsForm.elements[name].checked;
    W.action(async () => {
      if (settings.auto_arb && !snapshot.settings.auto_arb && !await W.confirm("Enable automatic execution?", `The server will execute qualifying pairs in groups with verified payouts, using ${settings.auto_arb_quantity} contracts per leg in the current ${W.state.dryRun ? "DRY RUN" : "LIVE"} mode. Leg 2 is sized to leg 1’s fill; gross edge excludes fees.`, "Enable automation")) return;
      await W.api("/api/arb/settings", "PUT", settings);
      snapshot.settings = settings;
      settingsDirty = false;
      renderSettings(); renderOpportunities();
      W.notify("Strategy settings saved.");
    });
  });
  $("opportunities").addEventListener("input", (event) => {
    const form = event.target.closest("form");
    if (form) quantities.set(form.dataset.group, form.elements.quantity.value);
  });
  $("opportunities").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.target, quantity = W.quantity(form.elements.quantity);
    const group = snapshot.groups.find((item) => item.id === form.dataset.group);
    if (!group?.opportunity || quantity === null) return;
    // Capture the reviewed pair so a later poll cannot change what is submitted.
    const opportunity = {...group.opportunity};
    W.action(async () => {
      if (!snapshot.settings.skip_confirmation && !await W.confirm("Review paired orders", `${W.state.dryRun ? "DRY RUN" : "LIVE ORDERS"} · ${quantity} contracts per leg\nBUY ${opportunity.market_a_side.toUpperCase()} · ${opportunity.market_a}\nBUY ${opportunity.market_b_side.toUpperCase()} · ${opportunity.market_b}\n\nGross edge: ${W.cents(opportunity.profit)} per pair, before fees and the ${snapshot.settings.buffer}¢ buffer per leg.\nEach leg is priced from the latest quote at execution. Each leg can fail or fill partially.`, W.state.dryRun ? "Run simulation" : "Submit paired orders")) return;
      const result = await W.api("/api/arb/execute", "POST", {
        ticker_a: opportunity.market_a, side_a: opportunity.market_a_side,
        ticker_b: opportunity.market_b, side_b: opportunity.market_b_side, quantity,
      });
      const notes = result.notes?.length ? ` ${result.notes.join(" ")}` : "";
      if (result.errors?.length) W.notify(`Pair returned errors: ${result.errors.join("; ")}${notes} Check both legs and current positions before retrying.`, "error");
      else W.notify(result.dry_run ? "Dry run complete. No paired orders submitted." : `Pair response received.${notes} Check both fills in activity and current positions.`);
    });
  });
  W.poll(refresh);
})();
