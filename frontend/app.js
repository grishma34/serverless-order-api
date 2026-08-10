"use strict";

/*
 * Client for the Order API.
 *
 * Every request below is a relative `/api/...` path. CloudFront serves this page
 * and the API from the same domain, so the browser treats these as same-origin:
 * no preflight, no CORS headers, no configuration to get wrong (REQ-0021).
 * Hardcoding an execute-api or CloudFront hostname here would silently
 * reintroduce cross-origin requests — tests/unit/infra/test_frontend.py fails
 * the build if an absolute API origin ever appears in this directory.
 */

const API = "/api";

/*
 * A UI affordance only: the server owns the state machine and is the sole
 * authority on what is legal (services/status_machine.py). This map exists so
 * the buttons offered are usually the ones that will work — a test asserts it
 * matches the server's table exactly, so the two cannot drift.
 */
const ALLOWED_TRANSITIONS = {
  "PLACED": ["PAID", "CANCELLED"],
  "PAID": ["SHIPPED", "CANCELLED"],
  "SHIPPED": ["DELIVERED"],
  "DELIVERED": [],
  "CANCELLED": []
};

const STATUSES = Object.keys(ALLOWED_TRANSITIONS);

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- requests ---

/**
 * Issue a request and normalise both success and failure into one shape.
 *
 * The API's error envelope is JSON too, so the caller inspects `ok` rather than
 * needing a try/catch around every call.
 */
async function api(path, options = {}) {
  const started = performance.now();
  let response;

  try {
    response = await fetch(`${API}${path}`, options);
  } catch (networkError) {
    // fetch only rejects when the request never completed.
    log("ERR", options.method || "GET", path, networkError.message, null);
    return { ok: false, status: 0, body: { message: `network error: ${networkError.message}` } };
  }

  const requestId = response.headers.get("X-Request-Id");
  const text = await response.text();

  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { message: text || "(empty response)" };
  }

  const elapsed = Math.round(performance.now() - started);
  log(response.status, options.method || "GET", path, `${elapsed}ms`, requestId);

  return { ok: response.ok, status: response.status, body, requestId };
}

function jsonRequest(method, payload, extraHeaders = {}) {
  return {
    method,
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(payload)
  };
}

// ------------------------------------------------------------------- chrome ---

function banner(kind, message) {
  const element = $("banner");
  element.className = `banner ${kind}`;
  element.textContent = message;
  element.hidden = false;
}

function describeError(result) {
  const body = result.body || {};
  const parts = [body.error || `HTTP ${result.status}`, body.message].filter(Boolean);
  // from/to accompany a 409; orderId accompanies a 404.
  if (body.from && body.to) parts.push(`(${body.from} → ${body.to})`);
  if (result.requestId) parts.push(`[${result.requestId}]`);
  return parts.join(" — ");
}

function log(status, method, path, detail, requestId) {
  const item = document.createElement("li");
  const bucket =
    typeof status === "number" && status >= 200 && status < 300 ? "s2xx"
      : typeof status === "number" && status >= 500 ? "s5xx"
        : "s4xx";
  item.className = bucket;
  item.textContent = `${status} ${method} ${API}${path} · ${detail}`
    + (requestId ? ` · ${requestId}` : "");
  $("log").prepend(item);
}

function money(cents, currency) {
  return `${(cents / 100).toFixed(2)} ${currency}`;
}

// ------------------------------------------------------------- idempotency ---

/*
 * One key per *submission attempt sequence*, not per click.
 *
 * The key is minted when the form is first submitted and deliberately kept if
 * the request fails: retrying a timed-out or 500'd create with the same key is
 * exactly what makes the retry safe (REQ-0010) — the server replays the
 * original order instead of creating a second one. A fresh key is minted only
 * once a create has been confirmed, so the next order is a genuinely new one.
 */
let pendingKey = null;   // key the next create attempt will send
let lastUsedKey = null;  // key of the most recent attempt, for the replay button

function currentKey() {
  if (pendingKey === null) {
    pendingKey = crypto.randomUUID();
    $("idempotency-key").textContent = pendingKey;
  }
  return pendingKey;
}

/** Called only after the server confirms a create, so the next order is new. */
function retireKey() {
  pendingKey = null;
  currentKey();
}

// -------------------------------------------------------------- line items ---

function addItemRow(values = {}) {
  const row = document.createElement("tr");
  row.innerHTML = `
    <td><input name="sku" value="${values.sku ?? "WIDGET-9"}" aria-label="SKU"></td>
    <td><input name="name" value="${values.name ?? "Widget"}" aria-label="Name"></td>
    <td><input name="quantity" type="number" min="1" value="${values.quantity ?? 1}"
               aria-label="Quantity"></td>
    <td><input name="unitPriceCents" type="number" min="0" value="${values.unitPriceCents ?? 4999}"
               aria-label="Unit price in cents"></td>
    <td><button type="button" class="secondary tiny" data-remove>×</button></td>
  `;
  row.querySelector("[data-remove]").addEventListener("click", () => {
    if ($("items-body").rows.length > 1) row.remove();
    else banner("warn", "An order needs at least one line item.");
  });
  $("items-body").append(row);
}

function readItems() {
  return [...$("items-body").rows].map((row) => {
    const field = (name) => row.querySelector(`[name="${name}"]`).value;
    return {
      sku: field("sku").trim(),
      name: field("name").trim(),
      // Sent as numbers: the API rejects numeric strings, and it should.
      quantity: Number(field("quantity")),
      unitPriceCents: Number(field("unitPriceCents"))
    };
  });
}

// ----------------------------------------------------------------- rendering ---

function renderOrderSummary(order, container) {
  const row = document.createElement("div");
  row.className = "order";
  row.innerHTML = `
    <span class="id">${order.orderId}</span>
    <span class="badge" data-status="${order.status}">${order.status}</span>
    <span class="grow">${money(order.totalCents, order.currency)} · ${order.customerId}</span>
    <button type="button" class="secondary tiny" data-open>Open</button>
  `;
  row.querySelector("[data-open]").addEventListener("click", () => showOrder(order.orderId));
  container.append(row);
}

function renderPage(result, container, { append }) {
  if (!append) container.replaceChildren();

  const orders = result.body?.orders ?? [];
  if (!orders.length && !append) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No orders matched.";
    container.append(empty);
  }
  orders.forEach((order) => renderOrderSummary(order, container));
}

function renderDetail(order) {
  const detail = $("detail");
  detail.replaceChildren();

  const heading = document.createElement("div");
  heading.className = "order";
  heading.innerHTML = `
    <span class="id">${order.orderId}</span>
    <span class="badge" data-status="${order.status}">${order.status}</span>
    <span class="grow">${money(order.totalCents, order.currency)} · ${order.customerId}</span>
  `;
  detail.append(heading);

  const dump = document.createElement("pre");
  dump.textContent = JSON.stringify(order, null, 2);
  detail.append(dump);

  const actions = document.createElement("div");
  actions.className = "transitions";

  const next = ALLOWED_TRANSITIONS[order.status] ?? [];
  if (!next.length) {
    const done = document.createElement("span");
    done.className = "empty";
    done.textContent = `${order.status} is terminal — no further transitions.`;
    actions.append(done);
  }
  next.forEach((status) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary tiny";
    button.textContent = `→ ${status}`;
    button.addEventListener("click", () => transition(order.orderId, status));
    actions.append(button);
  });

  // Always offer one move the server will refuse, so the 409 envelope is
  // visible in the UI rather than only in the test suite.
  const illegal = STATUSES.find((s) => s !== order.status && !next.includes(s));
  if (illegal) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary tiny";
    button.textContent = `→ ${illegal} (expect 409)`;
    button.addEventListener("click", () => transition(order.orderId, illegal));
    actions.append(button);
  }

  detail.append(actions);
}

// ------------------------------------------------------------------ actions ---

async function createOrder({ reuseLastKey = false } = {}) {
  const payload = {
    customerId: $("create-customer").value.trim(),
    currency: $("create-currency").value.trim().toUpperCase(),
    items: readItems()
  };

  const key = reuseLastKey && lastUsedKey ? lastUsedKey : currentKey();
  const result = await api("/orders", jsonRequest("POST", payload, { "Idempotency-Key": key }));
  lastUsedKey = key;
  $("replay-submit").disabled = false;

  if (!result.ok) {
    // The key is deliberately NOT retired here. The next attempt reuses it,
    // which is what makes retrying a failed create safe: if the write actually
    // landed before the failure, the retry replays it instead of duplicating.
    banner("err", `Create failed — ${describeError(result)}. Retry reuses key ${key}.`);
    return;
  }

  if (result.status === 200) {
    banner("warn", `Replayed: key ${key} already created ${result.body.orderId}. No second order.`);
  } else {
    banner("ok", `Created ${result.body.orderId} (201).`);
  }

  // Confirmed: the next submission is a different order and needs its own key.
  if (!reuseLastKey) retireKey();

  renderDetail(result.body);
}

async function showOrder(orderId) {
  const result = await api(`/orders/${encodeURIComponent(orderId)}`);
  if (!result.ok) {
    banner("err", `Lookup failed — ${describeError(result)}`);
    return;
  }
  banner("ok", `Loaded ${result.body.orderId}.`);
  renderDetail(result.body);
}

async function transition(orderId, status) {
  const result = await api(
    `/orders/${encodeURIComponent(orderId)}`,
    jsonRequest("PATCH", { status })
  );

  if (!result.ok) {
    banner("err", `Transition refused — ${describeError(result)}`);
    return;
  }
  banner("ok", `${orderId} is now ${result.body.status}.`);
  renderDetail(result.body);
}

/* Cursors live here rather than in the DOM: they are opaque server state that
   nothing else should read or construct. */
const cursors = { customer: null, ops: null };

async function listCustomerOrders({ append = false } = {}) {
  const params = new URLSearchParams();
  const status = $("customer-status").value;
  const limit = $("customer-limit").value;
  if (status) params.set("status", status);
  if (limit) params.set("limit", limit);
  if (append && cursors.customer) params.set("cursor", cursors.customer);

  const customerId = encodeURIComponent($("customer-id").value.trim());
  const result = await api(`/customers/${customerId}/orders?${params}`);

  if (!result.ok) {
    banner("err", `Listing failed — ${describeError(result)}`);
    return;
  }

  renderPage(result, $("customer-results"), { append });
  cursors.customer = result.body.nextCursor ?? null;
  $("customer-more").hidden = !cursors.customer;
}

async function listByStatus({ append = false } = {}) {
  const params = new URLSearchParams({ status: $("ops-status").value });
  if (append && cursors.ops) params.set("cursor", cursors.ops);

  const result = await api(`/orders?${params}`);

  if (!result.ok) {
    banner("err", `Listing failed — ${describeError(result)}`);
    return;
  }

  renderPage(result, $("ops-results"), { append });
  cursors.ops = result.body.nextCursor ?? null;
  $("ops-more").hidden = !cursors.ops;
}

// --------------------------------------------------------------------- init ---

function populateStatusSelects() {
  STATUSES.forEach((status) => {
    $("customer-status").append(new Option(status, status));
    $("ops-status").append(new Option(status, status));
  });
}

function wire() {
  populateStatusSelects();
  addItemRow();

  $("add-item").addEventListener("click", () => addItemRow());

  $("create-form").addEventListener("submit", (event) => {
    event.preventDefault();
    createOrder();
  });

  // Deliberate duplicate submit: proves a retry with the same key returns the
  // original order and creates nothing new.
  $("replay-submit").addEventListener("click", () => createOrder({ reuseLastKey: true }));

  $("lookup-form").addEventListener("submit", (event) => {
    event.preventDefault();
    showOrder($("lookup-id").value.trim());
  });

  $("customer-form").addEventListener("submit", (event) => {
    event.preventDefault();
    cursors.customer = null;
    listCustomerOrders({ append: false });
  });
  $("customer-more").addEventListener("click", () => listCustomerOrders({ append: true }));

  $("ops-form").addEventListener("submit", (event) => {
    event.preventDefault();
    cursors.ops = null;
    listByStatus({ append: false });
  });
  $("ops-more").addEventListener("click", () => listByStatus({ append: true }));

  // Minting the key up front makes it visible before the first submit, so the
  // "same key on retry" behaviour is observable rather than implied.
  currentKey();
}

document.addEventListener("DOMContentLoaded", wire);
