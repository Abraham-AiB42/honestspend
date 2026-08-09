const $ = (id) => document.getElementById(id);
let profiles = [];
let categories = [];
let accounts = [];

function money(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return v;
  return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

function apiHeaders(extra = {}) {
  const h = { ...extra };
  try {
    const key = localStorage.getItem("lr_api_key");
    if (key) h["X-API-Key"] = key;
  } catch {
    /* ignore */
  }
  return h;
}

async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const headers = apiHeaders(isForm ? {} : { "Content-Type": "application/json" });
  const res = await fetch(path, {
    ...opts,
    headers: { ...headers, ...(opts.headers || {}) },
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

function fillProfileSelects() {
  const ids = [
    "acct-profile",
    "sched-profile",
    "sched-filter-profile",
    "txn-profile",
    "ledger-profile",
    "tax-profile",
    "review-profile",
    "connect-profile",
  ];
  for (const id of ids) {
    const el = $(id);
    if (!el) continue;
    const keepAll =
      id === "ledger-profile" || id === "sched-filter-profile" || id === "review-profile";
    const cur = el.value;
    el.innerHTML = keepAll ? `<option value="">All</option>` : "";
    for (const p of profiles) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.display_name;
      if (p.is_default && !keepAll) opt.selected = true;
      el.appendChild(opt);
    }
    if (cur) el.value = cur;
  }
  fillRuleCategorySelect();
}

function fillRuleCategorySelect() {
  const sel = $("rule-category");
  if (!sel) return;
  sel.innerHTML = "";
  for (const c of categories) {
    const opt = document.createElement("option");
    opt.value = c.id;
    const tax = c.tax_line ? ` [${c.tax_form}:${c.tax_line}]` : "";
    opt.textContent = `${c.display_name}${tax}`;
    sel.appendChild(opt);
  }
}

async function loadCatStatus() {
  try {
    const s = await api("/api/categorizer/status");
    $("cat-status").textContent = s.grok_enabled
      ? `Grok on · ${s.model}`
      : "Rules only · set FOS_XAI_API_KEY for Grok";
  } catch {
    /* ignore */
  }
}

async function loadRules() {
  const rules = await api("/api/rules");
  const tbody = $("rules-table")?.querySelector("tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  for (const r of rules.filter((x) => x.active !== false)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><code>${escapeHtml(r.pattern)}</code></td>
      <td>${escapeHtml(r.match_type)}</td>
      <td>${escapeHtml(r.category_name || r.category_id)}</td>
      <td>${r.priority}</td>
      <td>${escapeHtml(r.source)}</td>
      <td></td>`;
    if (r.source !== "seed" || true) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-sm danger";
      btn.textContent = "Disable";
      btn.onclick = async () => {
        await api(`/api/rules/${r.id}`, { method: "DELETE" });
        await loadRules();
      };
      tr.children[5].appendChild(btn);
    }
    tbody.appendChild(tr);
  }
}

async function runReview(apply) {
  $("review-msg").textContent = "Working…";
  const body = {
    profile_id: $("review-profile").value ? Number($("review-profile").value) : null,
    limit: 80,
    apply,
    use_grok: $("review-use-grok").checked,
    min_confidence: apply ? 0.85 : null,
  };
  const data = await api("/api/categorize/batch", {
    method: "POST",
    body: JSON.stringify(body),
  });
  const box = $("review-list");
  box.innerHTML = "";
  if (!data.results?.length) {
    box.innerHTML = `<p class="fine">No uncategorized transactions. Nice.</p>`;
    $("review-msg").textContent = "";
    return;
  }
  for (const row of data.results) {
    const sug = row.suggestion || {};
    const div = document.createElement("div");
    div.className = "card-row review-row";
    const conf = Math.round((sug.confidence || 0) * 100);
    div.innerHTML = `
      <div class="row-between">
        <div>
          <strong>${escapeHtml(row.payee || "(no payee)")}</strong>
          <div class="fine">${row.txn_date} · ${money(row.amount)}</div>
        </div>
        <div class="amt ${Number(row.amount) < 0 ? "neg" : "pos"}">${money(row.amount)}</div>
      </div>
      <div class="path">
        Suggest: <strong>${escapeHtml(sug.category_name || "—")}</strong>
        · ${conf}% · ${escapeHtml(sug.source || "")}
        <div class="fine">${escapeHtml(sug.reason || "")}</div>
      </div>
      <div class="actions-cell" style="margin-top:0.4rem"></div>`;
    const actions = div.querySelector(".actions-cell");
    if (sug.category_id && !row.applied) {
      const accept = document.createElement("button");
      accept.type = "button";
      accept.className = "btn-sm primary";
      accept.textContent = "Accept";
      accept.onclick = async () => {
        await api(`/api/transactions/${row.transaction_id}?learn=true`, {
          method: "PATCH",
          body: JSON.stringify({ category_id: sug.category_id }),
        });
        div.remove();
        $("review-msg").textContent = "Accepted — rule learned from payee if possible.";
        await loadIfpp();
      };
      actions.appendChild(accept);
    } else if (row.applied) {
      const ok = document.createElement("span");
      ok.className = "badge";
      ok.textContent = "applied";
      actions.appendChild(ok);
    }
    // Manual category pick
    const sel = document.createElement("select");
    sel.className = "cat-select";
    sel.innerHTML = `<option value="">Pick other…</option>`;
    for (const c of categories) {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.display_name;
      sel.appendChild(opt);
    }
    sel.onchange = async () => {
      if (!sel.value) return;
      await api(`/api/transactions/${row.transaction_id}?learn=true`, {
        method: "PATCH",
        body: JSON.stringify({ category_id: Number(sel.value) }),
      });
      div.remove();
    };
    actions.appendChild(sel);
    box.appendChild(div);
  }
  const applied = data.results.filter((r) => r.applied).length;
  $("review-msg").textContent = apply
    ? `Auto-applied ${applied} of ${data.results.length}.`
    : `${data.results.length} suggestions ready. Grok: ${data.grok_enabled ? "on" : "off"}.`;
}

function fillSchedAccountSelect(profileId) {
  const sel = $("sched-account");
  const kind = $("sched-kind").value;
  const cur = sel.value;
  sel.innerHTML = "";
  const list = accounts.filter((a) => a.profile_id === Number(profileId));
  if (!list.length) {
    sel.innerHTML = `<option value="">No accounts — add one on Spendable tab</option>`;
    return;
  }
  if (kind === "income") {
    const opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "— optional deposit account —";
    sel.appendChild(opt0);
  }
  for (const a of list) {
    const opt = document.createElement("option");
    opt.value = a.id;
    const tag = a.kind === "credit" ? "CARD" : a.kind.toUpperCase();
    opt.textContent = `${a.nickname} [${tag}] bal ${money(a.current_balance)}`;
    sel.appendChild(opt);
  }
  if (cur && [...sel.options].some((o) => o.value === cur)) sel.value = cur;
  sel.required = kind === "expense";
  $("sched-account-label").firstChild.textContent =
    kind === "expense"
      ? "Account / Card (required)"
      : "Deposit account (optional)";
}

function fillSchedCategorySelect(profileId) {
  const sel = $("sched-category");
  const cur = sel.value;
  sel.innerHTML = `<option value="">— optional —</option>`;
  const pid = Number(profileId);
  for (const c of categories) {
    if (c.profile_id && c.profile_id !== pid && c.scope !== "system") continue;
    if (c.scope === "business" && c.profile_id !== pid) continue;
    const opt = document.createElement("option");
    opt.value = c.id;
    const tax = c.tax_line ? ` · ${c.tax_form}:${c.tax_line}` : "";
    opt.textContent = `${c.display_name}${tax}`;
    sel.appendChild(opt);
  }
  if (cur) sel.value = cur;
}

function resetSchedForm() {
  $("sched-edit-id").value = "";
  $("sched-form-title").textContent = "Add recurring";
  $("sched-submit").textContent = "Save recurring";
  $("sched-form").reset();
  $("sched-date").value = new Date().toISOString().slice(0, 10);
  $("sched-kind").value = "expense";
  if (profiles.length) {
    const def = profiles.find((p) => p.is_default) || profiles[0];
    $("sched-profile").value = def.id;
    fillSchedAccountSelect(def.id);
    fillSchedCategorySelect(def.id);
  }
  $("sched-msg").textContent = "";
}

function loadSchedIntoForm(s) {
  $("sched-edit-id").value = s.id;
  $("sched-form-title").textContent = `Edit: ${s.name}`;
  $("sched-submit").textContent = "Update recurring";
  $("sched-kind").value = s.kind || (Number(s.amount) < 0 ? "expense" : "income");
  $("sched-profile").value = s.profile_id;
  fillSchedAccountSelect(s.profile_id);
  fillSchedCategorySelect(s.profile_id);
  $("sched-name").value = s.name;
  $("sched-amt").value = Math.abs(Number(s.amount));
  $("sched-account").value = s.account_id || "";
  $("sched-category").value = s.category_id || "";
  $("sched-date").value = s.next_date;
  $("sched-end-date").value = s.end_date || "";
  $("sched-cadence").value = s.cadence || "monthly";
  $("sched-certainty").value = s.certainty || "fixed";
  $("sched-notes").value = s.notes || "";
  $("sched-form-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadProfiles() {
  profiles = await api("/api/profiles");
  const ul = $("profiles");
  ul.innerHTML = "";
  for (const p of profiles) {
    const li = document.createElement("li");
    li.textContent = `${p.display_name} · ${p.tax_form_primary} · ${p.entity_type}${p.is_default ? " (default)" : ""}`;
    ul.appendChild(li);
  }
  fillProfileSelects();
}

async function loadAccounts() {
  accounts = await api("/api/accounts");
  const byProf = Object.fromEntries(profiles.map((p) => [p.id, p.display_name]));

  const dash = $("accounts-dash");
  if (dash) {
    dash.innerHTML = "";
    const cash = accounts.filter((a) => a.kind !== "credit");
    if (!cash.length) {
      dash.innerHTML = `<li class="fine">No cash accounts yet — use Accounts tab or setup wizard.</li>`;
    } else {
      for (const a of cash) {
        const li = document.createElement("li");
        li.textContent = `${a.nickname} · ${money(a.current_balance)}${a.is_cash_for_ifpp ? " · IFPP" : ""}`;
        dash.appendChild(li);
      }
    }
  }

  // Full accounts table
  const tbody = $("acct-table")?.querySelector("tbody");
  if (tbody) {
    tbody.innerHTML = "";
    if (!accounts.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="fine">No accounts — add one below or finish setup.</td></tr>`;
    }
    for (const a of accounts) {
      const tr = document.createElement("tr");
      const due =
        a.kind === "credit"
          ? `due ${a.payment_due_day || "?"} · promo ${a.promo_end_date || "—"}`
          : "—";
      tr.innerHTML = `
        <td><strong>${escapeHtml(a.nickname)}</strong><div class="fine">${escapeHtml(a.institution || "")}</div></td>
        <td>${escapeHtml(byProf[a.profile_id] || "")}</td>
        <td><span class="pill ${a.kind === "credit" ? "card" : ""}">${a.kind}</span></td>
        <td class="amt">${money(a.current_balance)}</td>
        <td>${a.is_cash_for_ifpp ? "yes" : "—"}</td>
        <td class="fine">${due}</td>
        <td class="actions-cell"></td>`;
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "btn-sm ghost";
      edit.textContent = "Edit";
      edit.onclick = () => loadAcctIntoForm(a);
      tr.querySelector(".actions-cell").appendChild(edit);
      tbody.appendChild(tr);
    }
  }

  const sel = $("txn-account");
  if (sel) {
    sel.innerHTML = "";
    for (const a of accounts) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = `${a.nickname} (${byProf[a.profile_id] || ""})`;
      sel.appendChild(opt);
    }
  }
  const csvSel = $("csv-account");
  if (csvSel) {
    csvSel.innerHTML = "";
    for (const a of accounts) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = `${a.nickname} (${byProf[a.profile_id] || ""}) · ${a.kind}`;
      csvSel.appendChild(opt);
    }
  }
  for (const selId of ["im-from", "im-to"]) {
    const sel = $(selId);
    if (!sel) continue;
    sel.innerHTML = "";
    for (const a of accounts) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = `${a.nickname} · ${byProf[a.profile_id] || ""} · ${a.kind}`;
      sel.appendChild(opt);
    }
  }
}

function resetAcctForm() {
  $("acct-edit-id").value = "";
  $("acct-form-title").textContent = "Add account";
  $("acct-submit").textContent = "Save account";
  $("acct-form").reset();
  $("acct-ifpp").checked = true;
  $("acct-msg").textContent = "";
  if (profiles.length) {
    const def = profiles.find((p) => p.is_default) || profiles[0];
    $("acct-profile").value = def.id;
  }
}

function loadAcctIntoForm(a) {
  $("acct-edit-id").value = a.id;
  $("acct-form-title").textContent = `Edit: ${a.nickname}`;
  $("acct-submit").textContent = "Update account";
  $("acct-profile").value = a.profile_id;
  $("acct-kind").value = a.kind;
  $("acct-name").value = a.nickname;
  $("acct-inst").value = a.institution || "";
  $("acct-bal").value = a.current_balance;
  $("acct-ifpp").checked = !!a.is_cash_for_ifpp;
  $("acct-limit").value = a.credit_limit ?? "";
  $("acct-due").value = a.payment_due_day ?? "";
  $("acct-close").value = a.statement_close_day ?? "";
  $("acct-apr").value = a.apr ?? "";
  $("acct-promo-apr").value = a.promo_apr ?? "";
  $("acct-promo-end").value = a.promo_end_date || "";
  $("acct-promo-bal").value = a.promo_balance ?? "";
  $("acct-min").value = a.min_payment ?? "";
  $("acct-opened").value = a.opened_date || "";
  $("acct-priority").value = a.priority_rank ?? 100;
  $("acct-apy").value = a.apy ?? "";
  $("acct-form-card").scrollIntoView({ behavior: "smooth" });
}

async function checkOnboarding() {
  try {
    const s = await api("/api/onboarding");
    if (s.product_name && $("product-title")) {
      $("product-title").textContent = s.product_name;
      document.title = `${s.product_name} — Spendable Now`;
    }
    if (s.needs_setup || (!s.complete && s.account_count === 0)) {
      $("onboard").classList.remove("hidden");
    } else {
      $("onboard").classList.add("hidden");
    }
  } catch {
    /* ignore */
  }
}

async function loadPlaid() {
  try {
    const st = await api("/api/plaid/status");
    $("plaid-status").textContent = st.enabled
      ? `Plaid ON · env ${st.env}`
      : `Plaid off — ${st.hint}`;
    const items = st.enabled || true ? await api("/api/plaid/items") : [];
    const box = $("plaid-items");
    if (!box) return;
    box.innerHTML = "";
    if (!items.length) {
      box.innerHTML = `<p class="fine">No linked banks yet.</p>`;
      return;
    }
    for (const it of items) {
      const div = document.createElement("div");
      div.className = "card-row";
      div.innerHTML = `
        <strong>${escapeHtml(it.institution_name || "Bank")}</strong>
        <div class="fine">${it.accounts} accounts · last sync ${it.last_synced_at || "never"} · ${it.status}</div>
        <div class="actions-cell" style="margin-top:0.4rem"></div>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-sm primary";
      btn.textContent = "Sync now";
      btn.onclick = async () => {
        const res = await api(`/api/plaid/sync/${it.id}`, { method: "POST" });
        $("plaid-token-out").textContent = JSON.stringify(res, null, 2);
        await refresh();
      };
      div.querySelector(".actions-cell").appendChild(btn);
      box.appendChild(div);
    }
  } catch (err) {
    if ($("plaid-status")) $("plaid-status").textContent = String(err.message || err);
  }
}

async function loadCategories() {
  categories = await api("/api/categories");
  const sel = $("txn-category");
  const pid = Number($("txn-profile").value);
  sel.innerHTML = `<option value="">Uncategorized</option>`;
  for (const c of categories) {
    if (c.profile_id && c.profile_id !== pid && c.scope !== "system") continue;
    if (!c.profile_id && c.scope !== "system" && c.scope !== "personal") continue;
    const opt = document.createElement("option");
    opt.value = c.id;
    const tax = c.tax_line ? ` · ${c.tax_form}:${c.tax_line}` : "";
    opt.textContent = `${c.display_name}${tax}`;
    sel.appendChild(opt);
  }
}

async function loadDigest() {
  try {
    const d = await api("/api/digest");
    $("digest-msg").textContent = d.message + (d.minutes_needed ? ` (~${d.minutes_needed} min)` : "");
    const ul = $("digest-alerts");
    ul.innerHTML = "";
    if (!d.alerts?.length) {
      ul.innerHTML = `<li style="color:var(--good)">No critical alerts</li>`;
    } else {
      for (const a of d.alerts) {
        const li = document.createElement("li");
        li.textContent = `[${a.level}] ${a.message}`;
        ul.appendChild(li);
      }
    }
  } catch {
    /* ignore */
  }
}

async function loadPromoClock() {
  try {
    const d = await api("/api/promo-clock");
    const box = $("promo-clock");
    if (!box) return;
    if (!d.items?.length) {
      box.innerHTML = `<p class="fine">No active 0% promos tracked. Set promo APR=0 + end date on cards.</p>`;
      return;
    }
    box.innerHTML = "";
    for (const p of d.items) {
      const div = document.createElement("div");
      div.className = "card-row";
      const urg = p.urgency === "critical" || p.urgency === "expired" ? "expense" : "card";
      div.innerHTML = `
        <strong>${escapeHtml(p.name)}</strong>
        <span class="pill ${urg}">${p.urgency} · ${p.days_left}d</span>
        <div>Balloon ${money(p.promo_balance)} by ${p.promo_end}</div>
        <div class="fine">Sink ${money(p.sinking_fund.monthly)}/mo or ${money(p.sinking_fund.weekly)}/wk</div>
        <div class="path">${escapeHtml(p.payoff_summary || "")}</div>`;
      box.appendChild(div);
    }
  } catch {
    /* ignore */
  }
}

async function loadPermissions() {
  try {
    const me = await api("/api/permissions/me");
    $("perm-me").textContent = `${me.display_name} · role ${me.role} · caps: ${me.capabilities.join(", ")}`;
    const roles = await api("/api/permissions/roles");
    $("perm-roles").textContent = JSON.stringify(roles.roles, null, 2);
  } catch {
    /* ignore */
  }
}

async function loadTaxVault() {
  try {
    const v = await api("/api/tax-vault");
    $("tv-enabled").checked = !!v.enabled;
    $("tv-bal").value = v.balance || 0;
    $("tv-rate").value = v.income_rate || "";
    $("tv-balance").textContent = money(v.balance || 0);
    $("tv-note").textContent = v.note || "";
  } catch {
    /* ignore */
  }
}

async function loadCapitalDesk() {
  try {
    const d = await api("/api/capital-desk");
    const h = d.headline || {};
    $("cd-action").textContent = (h.action || "").replace(/_/g, " ").toUpperCase();
    $("cd-title").textContent = h.title || "—";
    $("cd-amount").textContent = h.amount_hint || "—";
    $("cd-reason").textContent = h.reason || "";
    const alts = $("cd-alts");
    alts.innerHTML = "";
    for (const a of h.alternatives || []) {
      const li = document.createElement("li");
      li.textContent = `Alt: ${a}`;
      alts.appendChild(li);
    }
    const ol = $("cd-steps");
    ol.innerHTML = "";
    for (const s of d.steps || []) {
      const li = document.createElement("li");
      li.innerHTML = `<strong>${escapeHtml(s.title)}</strong> <span class="pill">${escapeHtml(s.priority)}</span>
        <div>${escapeHtml(s.amount_hint || "")}</div>
        <div class="fine">${escapeHtml(s.reason || "")}</div>`;
      ol.appendChild(li);
    }
  } catch (err) {
    if ($("cd-title")) $("cd-title").textContent = "Capital desk unavailable";
  }
}

async function loadIfpp() {
  const mode = $("mode").value;
  const data = await api(`/api/ifpp?mode=${encodeURIComponent(mode)}`);
  $("combined").textContent = money(data.combined_purchasing_power);
  $("cash").textContent = money(data.cash_spendable);
  $("float").textContent = money(data.card_float_interest_free);
  $("red").textContent = data.next_red_day || "None";
  const vault = data.details?.tax_vault && Number(data.details.tax_vault) > 0
    ? ` · tax vault $${data.details.tax_vault}`
    : "";
  $("meta").textContent = `As of ${data.as_of} · mode ${data.mode} · buffer $${data.details?.safety_buffer || "?"} · scope ${data.details?.never_negative_scope || "checking"} · 0% float OK${vault}`;

  const cards = $("cards");
  cards.innerHTML = "";
  if (!data.cards?.length) {
    cards.innerHTML = `<p class="fine">No credit cards yet.</p>`;
  } else {
    for (const c of data.cards) {
      const div = document.createElement("div");
      div.className = "card-row";
      const badgeClass = c.risk === "blocked" ? "badge blocked" : "badge";
      const plan = c.payoff_plan;
      let planHtml = "";
      if (plan) {
        const steps = (plan.steps || [])
          .slice(0, 4)
          .map(
            (s) =>
              `<li><strong>${s.date}</strong> · ${s.action.replace(/_/g, " ")} · ${money(s.amount)}<div class="fine">${escapeHtml(s.note || "")}</div></li>`
          )
          .join("");
        planHtml = `
          <div class="payoff-box">
            <div class="path"><strong>Plan:</strong> ${escapeHtml(plan.summary)}</div>
            <ul class="payoff-steps">${steps}</ul>
            ${plan.balloon_amount && plan.strategy === "promo_min_then_balloon"
              ? `<div class="fine">Balloon <strong>${money(plan.balloon_amount)}</strong> by ${plan.balloon_date || "promo end"}</div>`
              : ""}
          </div>`;
      }
      div.innerHTML = `
        <strong>${escapeHtml(c.name)}</strong>
        <div>${money(c.safe_to_charge)} safe to charge · util ${c.utilization_pct}%</div>
        <div class="path">${escapeHtml(c.payoff_path)}</div>
        <span class="${badgeClass}">${c.risk}</span>
        ${planHtml}`;
      cards.appendChild(div);
    }
  }

  const w = $("warnings");
  w.innerHTML = "";
  if (!data.warnings?.length) w.innerHTML = `<li>No warnings</li>`;
  else for (const line of data.warnings) {
    const li = document.createElement("li");
    li.textContent = line;
    w.appendChild(li);
  }
}

async function loadSchedule() {
  const showEnded = $("sched-show-ended")?.checked;
  const pid = $("sched-filter-profile")?.value;
  const kind = $("sched-filter-kind")?.value;
  let path = `/api/scheduled?active_only=${showEnded ? "false" : "true"}`;
  if (pid) path += `&profile_id=${pid}`;
  if (kind) path += `&kind=${kind}`;
  let items = await api(path);
  // When showing all, still prefer active first
  items = items.sort((a, b) => Number(b.active) - Number(a.active) || a.next_date.localeCompare(b.next_date));

  const tbody = $("sched-table").querySelector("tbody");
  tbody.innerHTML = "";
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="fine">No recurring items yet. Add one below.</td></tr>`;
    return;
  }

  for (const s of items) {
    if (!showEnded && !s.active) continue;
    const tr = document.createElement("tr");
    if (!s.active) tr.classList.add("ended");
    const n = Number(s.amount);
    const amtClass = n < 0 ? "neg" : "pos";
    const acctLabel = s.account_nickname
      ? `${s.account_nickname}${s.account_kind === "credit" ? " (card)" : ""}`
      : "— missing —";
    const acctClass = s.account_kind === "credit" ? "pill card" : "pill";
    tr.innerHTML = `
      <td>
        <strong>${escapeHtml(s.name)}</strong>
        <div class="fine">${escapeHtml(s.profile_name || "")}${s.notes ? " · " + escapeHtml(s.notes) : ""}</div>
        ${!s.active ? `<div class="fine">Ended${s.ended_reason ? ": " + escapeHtml(s.ended_reason) : ""}</div>` : ""}
      </td>
      <td><span class="pill ${s.kind}">${s.kind}</span></td>
      <td class="amt ${amtClass}">${money(s.amount)}</td>
      <td><span class="${acctClass}">${escapeHtml(acctLabel)}</span></td>
      <td>${escapeHtml(s.category_name || "—")}</td>
      <td>${s.next_date}</td>
      <td>${s.cadence}<div class="fine">${s.certainty}</div></td>
      <td>${s.end_date || (s.active ? "ongoing" : "ended")}</td>
      <td class="actions-cell"></td>`;

    const actions = tr.querySelector(".actions-cell");
    if (s.active) {
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "btn-sm ghost";
      editBtn.textContent = "Edit";
      editBtn.onclick = () => loadSchedIntoForm(s);
      actions.appendChild(editBtn);

      const endBtn = document.createElement("button");
      endBtn.type = "button";
      endBtn.className = "btn-sm danger";
      endBtn.textContent = "End";
      endBtn.onclick = () => endRecurring(s);
      actions.appendChild(endBtn);
    } else {
      const reopenBtn = document.createElement("button");
      reopenBtn.type = "button";
      reopenBtn.className = "btn-sm ghost";
      reopenBtn.textContent = "Reopen";
      reopenBtn.onclick = async () => {
        const body = {
          profile_id: s.profile_id,
          name: s.name,
          amount: Math.abs(Number(s.amount)),
          next_date: s.next_date,
          end_date: null,
          cadence: s.cadence,
          certainty: s.certainty,
          kind: s.kind,
          account_id: s.account_id,
          category_id: s.category_id,
          notes: s.notes,
          active: true,
        };
        await api(`/api/scheduled/${s.id}`, { method: "PUT", body: JSON.stringify(body) });
        await Promise.all([loadSchedule(), loadIfpp()]);
      };
      actions.appendChild(reopenBtn);
    }
    tbody.appendChild(tr);
  }
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function endRecurring(s) {
  const today = new Date().toISOString().slice(0, 10);
  const endDate = prompt(
    `End "${s.name}" — last date this should count (YYYY-MM-DD)?\nLeave blank for today.`,
    today
  );
  if (endDate === null) return; // cancelled
  const reason = prompt("Optional reason (subscription cancelled, paid off, etc.)", "") ?? "";
  try {
    await api(`/api/scheduled/${s.id}/end`, {
      method: "POST",
      body: JSON.stringify({
        end_date: endDate || today,
        reason: reason || "Ended by user",
      }),
    });
    $("sched-msg").textContent = `Ended: ${s.name}`;
    await Promise.all([loadSchedule(), loadIfpp()]);
  } catch (err) {
    alert(err.message || err);
  }
}

async function loadLedger() {
  const pid = $("ledger-profile").value;
  const uncat = $("uncat-only").checked;
  let path = `/api/transactions?limit=300`;
  if (pid) path += `&profile_id=${pid}`;
  if (uncat) path += `&uncategorized=true`;
  const txns = await api(path);
  const tbody = $("ledger-table").querySelector("tbody");
  tbody.innerHTML = "";
  const catById = Object.fromEntries(categories.map((c) => [c.id, c]));

  for (const t of txns) {
    const tr = document.createElement("tr");
    const n = Number(t.amount);
    const amtClass = n < 0 ? "neg" : "pos";
    const sel = document.createElement("select");
    sel.className = "cat-select";
    sel.innerHTML = `<option value="">Uncategorized</option>`;
    for (const c of categories) {
      if (c.profile_id && c.profile_id !== t.profile_id && c.scope !== "system") continue;
      const opt = document.createElement("option");
      opt.value = c.id;
      const tax = c.tax_line ? ` [${c.tax_form}:${c.tax_line}]` : "";
      opt.textContent = `${c.display_name}${tax}`;
      if (t.category_id === c.id) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.onchange = async () => {
      const category_id = sel.value ? Number(sel.value) : null;
      await api(`/api/transactions/${t.id}`, {
        method: "PATCH",
        body: JSON.stringify({ category_id }),
      });
    };
    tr.innerHTML = `
      <td>${t.txn_date}</td>
      <td>${t.payee || "—"}</td>
      <td class="amt ${amtClass}">${money(t.amount)}</td>
      <td></td>`;
    tr.children[3].appendChild(sel);
    const cat = t.category_id ? catById[t.category_id] : null;
    if (cat?.partial_rule) {
      const tip = document.createElement("div");
      tip.className = "fine";
      tip.textContent = cat.partial_rule;
      tr.children[3].appendChild(tip);
    }
    tbody.appendChild(tr);
  }
}

async function loadCoa() {
  const s = await api("/api/tax/coa-summary");
  $("coa").textContent = JSON.stringify(s, null, 2);
}

async function loadSettings() {
  const s = await api("/api/settings");
  $("set-mode").value = s.ifpp_mode;
  $("set-buffer").value = s.safety_buffer;
  $("set-horizon").value = s.horizon_days;
  $("set-soft").value = s.utilization_warn_soft;
  $("set-hard").value = s.utilization_warn_hard;
  $("mode").value = s.ifpp_mode;
  if ($("debt-strategy") && s.debt_strategy) $("debt-strategy").value = s.debt_strategy;
  if ($("debt-extra") && s.debt_extra_monthly != null) $("debt-extra").value = s.debt_extra_monthly;
  if ($("debt-opp-aware") && s.opportunity_cost_aware != null) {
    $("debt-opp-aware").checked = !!s.opportunity_cost_aware;
  }
  if ($("debt-hurdle") && s.opportunity_rate != null) $("debt-hurdle").value = s.opportunity_rate;
  if ($("debt-tax") && s.opportunity_tax_rate != null) $("debt-tax").value = s.opportunity_tax_rate;
  if ($("ch-ontime")) {
    $("ch-ontime").value = s.credit_on_time_rate ?? 1;
    $("ch-30").value = s.credit_late_30 ?? 0;
    $("ch-60").value = s.credit_late_60 ?? 0;
    $("ch-90").value = s.credit_late_90 ?? 0;
    $("ch-inq").value = s.credit_hard_inquiries ?? 0;
    $("ch-new").value = s.credit_new_accounts ?? 0;
    $("ch-reported").value = s.credit_reported_vantage ?? "";
  }
  if ($("cliff-on")) {
    $("cliff-on").checked = !!s.income_cliff_enabled;
    $("cliff-factor").value = s.income_cliff_factor ?? 1;
  }
}

async function loadCreditHealth() {
  try {
    const st = await api("/api/credit/status");
    if ($("credit-api-msg")) $("credit-api-msg").textContent = st.message;
    const h = await api("/api/credit/health");
    $("credit-score").textContent = h.score;
    $("credit-band").textContent = `${h.band} · ${h.model}`;
    $("credit-util").textContent = `Overall util ${h.utilization_overall_pct}% · revolving ${money(h.total_revolving_balance)} / ${money(h.total_revolving_limit)}`;
    $("credit-disclaimer").textContent = h.disclaimer;
    const factors = $("credit-factors");
    factors.innerHTML = "";
    for (const f of h.factors || []) {
      const div = document.createElement("div");
      div.className = "factor-row";
      div.innerHTML = `
        <div class="row-between">
          <strong>${escapeHtml(f.name)}</strong>
          <span>${f.score_0_100}/100 · weight ${f.weight_pct}</span>
        </div>
        <div class="fine">${escapeHtml(f.detail)}</div>
        <div class="bar"><span style="width:${Math.min(100, f.score_0_100)}%"></span></div>`;
      factors.appendChild(div);
    }
    const wi = $("credit-whatif");
    wi.innerHTML = "";
    for (const w of h.what_if || []) {
      const li = document.createElement("li");
      const sign = w.delta >= 0 ? "+" : "";
      li.textContent = `${w.label} → ${w.score} (${sign}${w.delta})`;
      wi.appendChild(li);
    }
    if (h.your_reported_vantage) {
      const li = document.createElement("li");
      li.textContent = `Your reported Vantage ${h.your_reported_vantage} vs sim ${h.score} (Δ ${h.vs_reported_delta})`;
      wi.prepend(li);
    }
    const tips = $("credit-tips");
    tips.innerHTML = "";
    for (const t of h.suggestions || []) {
      const li = document.createElement("li");
      li.textContent = t;
      tips.appendChild(li);
    }
  } catch (err) {
    if ($("credit-band")) $("credit-band").textContent = String(err.message || err);
  }
}

async function runDebtPlan() {
  const strategy = $("debt-strategy").value;
  const extra = Number($("debt-extra").value || 0);
  const save = $("debt-save").checked;
  const oppAware = $("debt-opp-aware")?.checked !== false;

  if (save) {
    const cur = await api("/api/settings");
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        ...cur,
        debt_strategy: strategy,
        debt_extra_monthly: extra,
        opportunity_cost_aware: oppAware,
        opportunity_rate: $("debt-hurdle").value !== "" ? Number($("debt-hurdle").value) : null,
        opportunity_tax_rate: $("debt-tax").value !== "" ? Number($("debt-tax").value) : null,
      }),
    });
  }

  const plan = await api("/api/debt/plan", {
    method: "POST",
    body: JSON.stringify({
      strategy,
      extra_monthly: extra,
      save_preference: false,
      opportunity_cost_aware: oppAware,
    }),
  });

  const hurdle = plan.opportunity_rate_pct || "—";
  $("debt-opp-banner").textContent = plan.opportunity_cost_aware
    ? `Hurdle ${hurdle} · ${plan.opportunity_rate_source || ""} · Extra not forced onto cheaper debt: ${money(plan.extra_to_yield_not_debt)}/mo → keep earning`
    : "Opportunity-cost off — pure strategy order (may suggest prepaying cheap debt).";

  $("debt-summary").innerHTML = `
    <div><p class="label">Total debt</p><p class="num">${money(plan.total_balance)}</p></div>
    <div><p class="label">Est. months (high-cost)</p><p class="num">${plan.estimated_months ?? "—"}</p></div>
    <div><p class="label">Est. interest</p><p class="num">${money(plan.estimated_interest)}</p></div>`;

  const tbody = $("debt-order-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const o of plan.order || []) {
    const tr = document.createElement("tr");
    if (o.recommendation === "minimum_only") tr.classList.add("ended");
    const action =
      o.recommendation === "minimum_only"
        ? "MIN ONLY"
        : o.recommendation === "promo_watch"
          ? "PROMO WATCH"
          : "EXTRA OK";
    const vs =
      o.spread_vs_opportunity_pp != null
        ? `${Number(o.spread_vs_opportunity_pp) >= 0 ? "+" : ""}${o.spread_vs_opportunity_pp} pp`
        : "—";
    tr.innerHTML = `
      <td>${o.rank}</td>
      <td><strong>${escapeHtml(o.name)}</strong>${o.promo_days_left != null ? `<div class="fine">promo ${o.promo_days_left}d left</div>` : ""}</td>
      <td class="amt">${money(o.balance)}</td>
      <td>${o.effective_apr_pct}</td>
      <td>${vs}</td>
      <td><span class="pill ${o.recommendation === "extra_ok" ? "income" : "expense"}">${action}</span></td>
      <td class="fine">${escapeHtml(o.reason)}</td>`;
    tbody.appendChild(tr);
  }

  const iv = $("invest-vs-debt");
  if (iv) {
    if (!plan.invest_vs_debt?.length) {
      iv.innerHTML = `<p class="fine">Set APY on savings/HYSA (Accounts) or a manual hurdle to see invest-vs-prepay.</p>`;
    } else {
      let html = `<table><thead><tr><th>Debt</th><th>Debt rate</th><th>Yield</th><th>Verdict</th><th>$1k edge keep cash</th></tr></thead><tbody>`;
      for (const r of plan.invest_vs_debt) {
        html += `<tr class="${r.recommendation === "minimum_only" ? "ended" : ""}">
          <td>${escapeHtml(r.name)}</td>
          <td>${r.effective_apr_pct}</td>
          <td>${r.opportunity_rate_pct}</td>
          <td class="fine">${escapeHtml(r.verdict)}</td>
          <td class="amt">${money(r.example_per_1000?.edge_of_keeping_cash)}</td>
        </tr>`;
      }
      html += `</tbody></table>`;
      if (plan.yield_accounts?.length) {
        html += `<p class="fine" style="margin-top:0.5rem">Yield sources: ${plan.yield_accounts
          .map((y) => `${y.name} ${y.apy_pct}`)
          .join(" · ")}</p>`;
      }
      iv.innerHTML = html;
    }
  }

  if (plan.notes?.length) {
    $("debt-compare-out").textContent = plan.notes.join("\n");
  }
}

async function refresh() {
  await checkOnboarding();
  await loadProfiles();
  await loadCategories();
  await loadAccounts();
  fillSchedAccountSelect($("sched-profile").value || (profiles[0] && profiles[0].id));
  fillSchedCategorySelect($("sched-profile").value || (profiles[0] && profiles[0].id));
  await Promise.all([
    loadIfpp(),
    loadCapitalDesk(),
    loadDigest(),
    loadPromoClock(),
    loadPermissions(),
    loadSchedule(),
    loadCoa(),
    loadSettings(),
    loadCatStatus(),
    loadRules(),
    loadPlaid(),
    loadCreditHealth(),
    loadTaxVault(),
  ]);
  await loadLedger();
  try {
    await runDebtPlan();
  } catch {
    /* no debts yet */
  }
}

// Tabs
document.querySelectorAll("#tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

$("refresh").addEventListener("click", () => refresh().catch(alert));
$("mode").addEventListener("change", () => loadIfpp().catch(alert));
$("uncat-only").addEventListener("change", () => loadLedger().catch(alert));
$("ledger-profile").addEventListener("change", () => loadLedger().catch(alert));
$("txn-profile").addEventListener("change", () => loadCategories().catch(alert));

$("acct-new-btn")?.addEventListener("click", () => {
  resetAcctForm();
  $("acct-form-card").scrollIntoView({ behavior: "smooth" });
});
$("acct-cancel")?.addEventListener("click", () => resetAcctForm());

$("acct-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const kind = $("acct-kind").value;
  const body = {
    profile_id: Number($("acct-profile").value),
    kind,
    nickname: $("acct-name").value.trim(),
    institution: $("acct-inst").value.trim() || null,
    current_balance: Number($("acct-bal").value || 0),
    is_cash_for_ifpp: kind !== "credit" && $("acct-ifpp").checked,
    credit_limit: $("acct-limit").value ? Number($("acct-limit").value) : null,
    available_credit: null,
    statement_close_day: $("acct-close").value ? Number($("acct-close").value) : null,
    payment_due_day: $("acct-due").value ? Number($("acct-due").value) : null,
    apr: $("acct-apr").value !== "" ? Number($("acct-apr").value) : null,
    promo_apr: $("acct-promo-apr").value !== "" ? Number($("acct-promo-apr").value) : null,
    promo_end_date: $("acct-promo-end").value || null,
    promo_balance: $("acct-promo-bal").value !== "" ? Number($("acct-promo-bal").value) : null,
    min_payment: $("acct-min").value !== "" ? Number($("acct-min").value) : null,
    opened_date: $("acct-opened").value || null,
    priority_rank: $("acct-priority").value ? Number($("acct-priority").value) : 100,
    apy: $("acct-apy").value !== "" ? Number($("acct-apy").value) : null,
  };
  if (kind === "credit") {
    body.is_cash_for_ifpp = false;
    if (body.credit_limit != null) {
      body.available_credit = Math.max(0, body.credit_limit - Math.max(0, body.current_balance));
    }
  }
  const editId = $("acct-edit-id").value;
  try {
    if (editId) {
      await api(`/api/accounts/${editId}`, { method: "PUT", body: JSON.stringify(body) });
      $("acct-msg").textContent = "Account updated.";
    } else {
      await api("/api/accounts", { method: "POST", body: JSON.stringify(body) });
      $("acct-msg").textContent = "Account added.";
    }
    resetAcctForm();
    await refresh();
  } catch (err) {
    $("acct-msg").textContent = String(err.message || err);
  }
});

$("ob-add-card")?.addEventListener("change", () => {
  $("ob-card-fields").classList.toggle("hidden", !$("ob-add-card").checked);
});
$("onboard-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    profile_slug: "personal",
    cash_name: $("ob-cash-name").value.trim(),
    cash_balance: Number($("ob-cash-bal").value || 0),
    cash_institution: $("ob-cash-inst").value.trim() || null,
    safety_buffer: Number($("ob-buffer").value || 0),
    ifpp_mode: "conservative",
  };
  if ($("ob-add-card").checked) {
    body.card_name = $("ob-card-name").value.trim() || "Credit card";
    body.card_balance = Number($("ob-card-bal").value || 0);
    body.card_limit = $("ob-card-limit").value ? Number($("ob-card-limit").value) : null;
    body.card_due_day = $("ob-card-due").value ? Number($("ob-card-due").value) : null;
    body.card_close_day = $("ob-card-close").value ? Number($("ob-card-close").value) : null;
    if ($("ob-card-promo").checked) {
      body.card_promo_apr = 0;
      body.card_promo_end = $("ob-card-promo-end").value || null;
    }
  }
  try {
    await api("/api/onboarding/quick-setup", { method: "POST", body: JSON.stringify(body) });
    $("onboard").classList.add("hidden");
    await refresh();
  } catch (err) {
    $("ob-msg").textContent = String(err.message || err);
  }
});
$("ob-skip")?.addEventListener("click", async () => {
  await api("/api/onboarding/complete", { method: "POST" });
  $("onboard").classList.add("hidden");
});
$("reset-onboard")?.addEventListener("click", async () => {
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      ifpp_mode: $("set-mode").value || "conservative",
      safety_buffer: Number($("set-buffer").value || 0),
      never_negative_scope: "all_cash",
      horizon_days: Number($("set-horizon").value || 45),
      utilization_warn_soft: Number($("set-soft").value || 10),
      utilization_warn_hard: Number($("set-hard").value || 30),
      onboarding_complete: false,
    }),
  });
  $("onboard").classList.remove("hidden");
});

$("sched-kind").addEventListener("change", () => {
  fillSchedAccountSelect($("sched-profile").value);
});
$("sched-profile").addEventListener("change", () => {
  fillSchedAccountSelect($("sched-profile").value);
  fillSchedCategorySelect($("sched-profile").value);
});
$("sched-filter-profile")?.addEventListener("change", () => loadSchedule().catch(alert));
$("sched-filter-kind")?.addEventListener("change", () => loadSchedule().catch(alert));
$("sched-show-ended")?.addEventListener("change", () => loadSchedule().catch(alert));
$("sched-new-btn")?.addEventListener("click", () => {
  resetSchedForm();
  $("sched-form-card").scrollIntoView({ behavior: "smooth" });
  $("sched-name").focus();
});
$("sched-cancel")?.addEventListener("click", () => resetSchedForm());

$("sched-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const kind = $("sched-kind").value;
  const accountId = $("sched-account").value;
  if (kind === "expense" && !accountId) {
    $("sched-msg").textContent = "Pick the account or card this expense hits.";
    return;
  }
  const body = {
    profile_id: Number($("sched-profile").value),
    name: $("sched-name").value.trim(),
    amount: Number($("sched-amt").value),
    next_date: $("sched-date").value,
    end_date: $("sched-end-date").value || null,
    cadence: $("sched-cadence").value,
    certainty: $("sched-certainty").value,
    kind,
    account_id: accountId ? Number(accountId) : null,
    category_id: $("sched-category").value ? Number($("sched-category").value) : null,
    notes: $("sched-notes").value.trim() || null,
    active: true,
  };
  const editId = $("sched-edit-id").value;
  try {
    if (editId) {
      await api(`/api/scheduled/${editId}`, { method: "PUT", body: JSON.stringify(body) });
      $("sched-msg").textContent = "Updated.";
    } else {
      await api("/api/scheduled", { method: "POST", body: JSON.stringify(body) });
      $("sched-msg").textContent = "Recurring saved.";
    }
    resetSchedForm();
    await Promise.all([loadSchedule(), loadIfpp()]);
  } catch (err) {
    $("sched-msg").textContent = String(err.message || err);
  }
});

$("txn-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    profile_id: Number($("txn-profile").value),
    account_id: Number($("txn-account").value),
    category_id: $("txn-category").value ? Number($("txn-category").value) : null,
    txn_date: $("txn-date").value,
    amount: Number($("txn-amt").value),
    payee: $("txn-payee").value || null,
    status: "cleared",
    is_transfer: false,
  };
  try {
    await api("/api/transactions", { method: "POST", body: JSON.stringify(body) });
    $("txn-msg").textContent = "Saved.";
    await Promise.all([loadLedger(), loadAccounts(), loadIfpp()]);
  } catch (err) {
    $("txn-msg").textContent = String(err.message || err);
  }
});

$("import-path-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("import-result").textContent = "Importing…";
  try {
    const body = {
      path: $("import-path").value,
      profile_slug: "personal",
      sheet_name: "Budget",
      since: $("import-since").value || null,
      dry_run: $("import-dry").checked,
    };
    const res = await api("/api/import/budget-xlsx", {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("import-result").textContent = JSON.stringify(res, null, 2);
    if (!body.dry_run) await loadLedger();
  } catch (err) {
    $("import-result").textContent = String(err.message || err);
  }
});

$("import-upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("import-file").files[0];
  if (!file) return alert("Choose a file");
  const fd = new FormData();
  fd.append("file", file);
  $("import-result").textContent = "Uploading…";
  try {
    const res = await fetch("/api/import/budget-xlsx/upload?profile_slug=personal", {
      method: "POST",
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(JSON.stringify(data));
    $("import-result").textContent = JSON.stringify(data, null, 2);
    await loadLedger();
  } catch (err) {
    $("import-result").textContent = String(err.message || err);
  }
});

$("tv-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const body = {
      enabled: $("tv-enabled").checked,
      balance: Number($("tv-bal").value || 0),
    };
    if ($("tv-rate").value !== "") body.income_rate = Number($("tv-rate").value);
    else body.clear_income_rate = true;
    const v = await api("/api/tax-vault", { method: "PUT", body: JSON.stringify(body) });
    $("tv-msg").textContent = "Vault saved — Spendable updated.";
    $("tv-balance").textContent = money(v.balance);
    await loadIfpp();
    await loadCapitalDesk();
  } catch (err) {
    $("tv-msg").textContent = String(err.message || err);
  }
});
$("tv-adj-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const v = await api("/api/tax-vault/adjust", {
      method: "POST",
      body: JSON.stringify({ delta: Number($("tv-delta").value) }),
    });
    $("tv-msg").textContent = `Adjusted by ${$("tv-delta").value}. Vault ${money(v.balance)}.`;
    $("tv-bal").value = v.balance;
    $("tv-balance").textContent = money(v.balance);
    await loadIfpp();
    await loadCapitalDesk();
  } catch (err) {
    $("tv-msg").textContent = String(err.message || err);
  }
});
$("cliff-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const cur = await api("/api/settings");
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        ...cur,
        income_cliff_enabled: $("cliff-on").checked,
        income_cliff_factor: Number($("cliff-factor").value || 1),
      }),
    });
    $("cliff-msg").textContent = "Cliffs saved — reloading Spendable.";
    await loadIfpp();
    await loadCapitalDesk();
  } catch (err) {
    $("cliff-msg").textContent = String(err.message || err);
  }
});

$("tax-preview").addEventListener("click", async () => {
  const pid = $("tax-profile").value;
  const year = $("tax-year").value;
  const data = await api(`/api/tax/packet?profile_id=${pid}&year=${year}`);
  $("tax-result").textContent = JSON.stringify(
    {
      profile: data.profile,
      year: data.year,
      counts: data.counts,
      summary_by_tax_line: data.summary_by_tax_line,
      disclaimer: data.disclaimer,
    },
    null,
    2
  );
});

$("tax-download").addEventListener("click", () => {
  const pid = $("tax-profile").value;
  const year = $("tax-year").value;
  window.location.href = `/api/tax/packet/download?profile_id=${pid}&year=${year}`;
});

$("tax-write").addEventListener("click", async () => {
  const pid = $("tax-profile").value;
  const year = $("tax-year").value;
  const data = await api(`/api/tax/packet/write?profile_id=${pid}&year=${year}`, {
    method: "POST",
  });
  $("tax-result").textContent = JSON.stringify(data, null, 2);
});

$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    ifpp_mode: $("set-mode").value,
    safety_buffer: Number($("set-buffer").value || 0),
    never_negative_scope: "all_cash",
    horizon_days: Number($("set-horizon").value || 45),
    utilization_warn_soft: Number($("set-soft").value || 10),
    utilization_warn_hard: Number($("set-hard").value || 30),
  };
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    $("set-msg").textContent = "Saved.";
    $("mode").value = body.ifpp_mode;
    await loadIfpp();
  } catch (err) {
    $("set-msg").textContent = String(err.message || err);
  }
});

$("plaid-sandbox")?.addEventListener("click", async () => {
  const pid = $("connect-profile").value;
  if (!pid) return alert("Pick a profile");
  $("plaid-token-out").textContent = "Linking sandbox…";
  try {
    const res = await api(`/api/plaid/sandbox-link?profile_id=${pid}`, { method: "POST" });
    $("plaid-token-out").textContent = JSON.stringify(res, null, 2);
    await refresh();
  } catch (err) {
    $("plaid-token-out").textContent = String(err.message || err);
  }
});
$("plaid-link-token")?.addEventListener("click", async () => {
  try {
    const res = await api("/api/plaid/link-token", { method: "POST" });
    $("plaid-token-out").textContent =
      JSON.stringify(res, null, 2) +
      "\n\n// Use this link_token with Plaid Link JS in a future desktop shell.";
  } catch (err) {
    $("plaid-token-out").textContent = String(err.message || err);
  }
});
$("csv-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("csv-file").files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const accountId = $("csv-account").value;
  const sign = $("csv-sign").value;
  $("csv-result").textContent = "Importing…";
  try {
    const res = await fetch(
      `/api/import/bank-csv?account_id=${accountId}&amount_sign=${sign}&auto_categorize=true`,
      { method: "POST", body: fd }
    );
    const data = await res.json();
    if (!res.ok) throw new Error(JSON.stringify(data));
    $("csv-result").textContent = JSON.stringify(data, null, 2);
    await refresh();
  } catch (err) {
    $("csv-result").textContent = String(err.message || err);
  }
});

$("buy-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const res = await api("/api/pre-purchase", {
      method: "POST",
      body: JSON.stringify({
        amount: Number($("buy-amt").value),
        prefer: $("buy-prefer").value,
      }),
    });
    $("buy-result").textContent = JSON.stringify(
      {
        verdict: res.verdict,
        recommended: res.recommended,
        options: res.options,
        ifpp: res.ifpp_snapshot,
      },
      null,
      2
    );
  } catch (err) {
    $("buy-result").textContent = String(err.message || err);
  }
});

$("intermix-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const res = await api("/api/intermix", {
      method: "POST",
      body: JSON.stringify({
        kind: $("im-kind").value,
        amount: Number($("im-amt").value),
        from_account_id: Number($("im-from").value),
        to_account_id: Number($("im-to").value),
        memo: $("im-memo").value || null,
      }),
    });
    $("im-result").textContent = JSON.stringify(res, null, 2);
    await refresh();
  } catch (err) {
    $("im-result").textContent = String(err.message || err);
  }
});

$("perm-user-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const res = await api("/api/permissions/users", {
      method: "POST",
      body: JSON.stringify({
        username: $("perm-user").value.trim(),
        display_name: $("perm-name").value.trim(),
        role: $("perm-role").value,
        issue_token: true,
      }),
    });
    $("perm-msg").textContent = "User added. Copy token now.";
    $("perm-token-out").textContent = res.api_token
      ? `X-API-Key: ${res.api_token}\n\n${res.hint || ""}`
      : JSON.stringify(res, null, 2);
    $("perm-user-form").reset();
    await loadPermissions();
  } catch (err) {
    $("perm-msg").textContent = String(err.message || err);
  }
});
$("api-key-save")?.addEventListener("click", () => {
  const k = $("api-key-store").value.trim();
  if (k) localStorage.setItem("lr_api_key", k);
  $("perm-msg").textContent = k ? "API key stored in this browser." : "Empty key.";
});
$("api-key-clear")?.addEventListener("click", () => {
  localStorage.removeItem("lr_api_key");
  $("api-key-store").value = "";
  $("perm-msg").textContent = "API key cleared.";
});

$("debt-run")?.addEventListener("click", () => runDebtPlan().catch(alert));
$("debt-compare")?.addEventListener("click", async () => {
  const extra = Number($("debt-extra").value || 0);
  const data = await api(`/api/debt/compare?extra_monthly=${extra}`);
  $("debt-compare-out").textContent = JSON.stringify(data.comparisons, null, 2);
});
$("credit-hist-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const cur = await api("/api/settings");
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        ...cur,
        credit_on_time_rate: Number($("ch-ontime").value || 1),
        credit_late_30: Number($("ch-30").value || 0),
        credit_late_60: Number($("ch-60").value || 0),
        credit_late_90: Number($("ch-90").value || 0),
        credit_hard_inquiries: Number($("ch-inq").value || 0),
        credit_new_accounts: Number($("ch-new").value || 0),
        credit_reported_vantage: $("ch-reported").value
          ? Number($("ch-reported").value)
          : null,
      }),
    });
    $("ch-msg").textContent = "Saved — recalculating…";
    await loadCreditHealth();
    $("ch-msg").textContent = "Saved.";
  } catch (err) {
    $("ch-msg").textContent = String(err.message || err);
  }
});

$("review-suggest")?.addEventListener("click", () => runReview(false).catch(alert));
$("review-apply-high")?.addEventListener("click", () => runReview(true).catch(alert));
$("rule-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/rules", {
      method: "POST",
      body: JSON.stringify({
        pattern: $("rule-pattern").value.trim(),
        match_type: $("rule-match").value,
        category_id: Number($("rule-category").value),
        priority: Number($("rule-priority").value || 100),
        active: true,
      }),
    });
    $("rule-msg").textContent = "Rule added.";
    $("rule-form").reset();
    $("rule-priority").value = "100";
    await loadRules();
  } catch (err) {
    $("rule-msg").textContent = String(err.message || err);
  }
});

// defaults
const today = new Date().toISOString().slice(0, 10);
$("txn-date").value = today;
$("sched-date").value = today;
$("tax-year").value = new Date().getFullYear();

refresh().catch((err) => {
  console.error(err);
  $("meta").textContent = "Failed to load API.";
});
