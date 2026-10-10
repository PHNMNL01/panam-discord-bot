"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {view, attach} = require("../web/voice-deck.js");
const tick = () => new Promise(resolve => setImmediate(resolve));
const actions = ["listen", "start", "mute", "unmute", "end", "stop-listening"];
function status(extra = {}) {
  return {runtime_instance_id: "00000000-0000-4000-8000-000000000001", control_revision: 0,
    lifecycle_state: "LISTENING_OFF", allowed_commands: ["listen"], last_call: null,
    current_operation: null, ...extra};
}
function harness(fetch, confirm = () => true, admin = false) {
  const ids = ["deck", "state", "description", "microphone", "cloud", "output", "elapsed", "feedback"];
  if (admin) ids.push("last-call", "runtime-details");
  const elements = Object.fromEntries(ids.map(id => [id, {textContent: "", dataset: {}}]));
  const buttons = admin ? [] : actions.map(action => ({dataset: {action}, addEventListener: () => {}, disabled: true}));
  const scheduled = [], calls = [];
  const document = {body: {dataset: {surface: admin ? "admin" : "companion"}}, getElementById: id => elements[id], querySelectorAll: () => buttons};
  const api = attach(document, {fetch: (...args) => {calls.push(args); return fetch(...args);}, confirm,
    uuid: () => "00000000-0000-4000-8000-000000000002", setTimeout: (fn, delay) => {scheduled.push({fn, delay}); return scheduled.length;}, clearTimeout: () => {}});
  return {api, elements, buttons, scheduled, calls};
}
const ok = value => ({ok: true, status: 200, json: async () => value});

test("disconnected state disables actions and does not claim call ended", () => {
  const model = view(null);
  assert.equal(model.title, "Runtime status unavailable");
  assert.deepEqual(model.allowed, []);
  assert.ok(model.description.includes("pokračovat"));
});
test("off and listening expose only server-authorized controls", () => {
  assert.deepEqual(view(status()).allowed, ["listen"]);
  assert.deepEqual(view(status({lifecycle_state: "LOCAL_LISTENING", allowed_commands: ["start", "stop-listening", "unknown"]})).allowed, ["start", "stop-listening"]);
});
test("active mute and unmute choices follow authoritative state", () => {
  for (const action of ["mute", "unmute"]) assert.deepEqual(view(status({lifecycle_state: "ACTIVE", allowed_commands: [action, "end"]})).allowed, [action, "end"]);
});
test("starting closing and pending submissions disable controls", () => {
  for (const action of actions) assert.deepEqual(view(status({current_operation: action})).allowed, []);
  assert.deepEqual(view(status(), true).allowed, []);
  assert.deepEqual(view(status({shutting_down: true})).allowed, []);
});
test("fault and uncertain cleanup show warning without inventing voice success", () => {
  for (const extra of [{fault: "local_call_failed"}, {cleanup_failed: true}, {last_call: {closure_confirmed: false}}]) assert.equal(view(status(extra)).title, "Fault");
  assert.ok(!view(status({last_call: {closure_confirmed: true}})).title.includes("Fault"));
});
test("page load and refresh only read status", async () => {
  const h = harness(async () => ok(status()));
  await tick();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], "/api/status");
  assert.equal(h.buttons.find(b => b.dataset.action === "listen").disabled, false);
  await h.api.poll();
  assert.ok(h.calls.every(([, options]) => !options.method));
  h.api.stop();
});
test("polls never overlap and schedule approximately every second", async () => {
  let resolve;
  const h = harness(() => new Promise(r => {resolve = r;}));
  await h.api.poll();
  assert.equal(h.calls.length, 1);
  resolve(ok(status()));
  await tick();
  assert.equal(h.scheduled.length, 1);
  assert.equal(h.scheduled[0].delay, 1000);
  h.api.stop();
});
test("connection failure sends no automatic commands", async () => {
  const h = harness(async () => {throw new Error("offline");});
  await tick();
  await h.api.command("start");
  assert.equal(h.elements.state.textContent, "Runtime status unavailable");
  assert.ok(h.buttons.every(b => b.disabled));
  assert.equal(h.calls.length, 1);
  h.api.stop();
});
test("closing browser polling does not end or stop runtime", async () => {
  const h = harness(async () => ok(status({lifecycle_state: "ACTIVE", allowed_commands: ["end"]})));
  await tick();
  h.api.stop();
  await h.api.poll();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], "/api/status");
});
test("paid start requires confirmation and exact consent payload", async () => {
  let consent = false;
  const h = harness(async url => url === "/api/status" ? ok(status({lifecycle_state: "LOCAL_LISTENING", allowed_commands: ["start"]})) : {status: 202}, () => consent);
  await tick();
  await h.api.command("start");
  assert.equal(h.calls.length, 1);
  consent = true;
  await h.api.command("start");
  const [url, options] = h.calls[1];
  assert.equal(url, "/api/commands");
  assert.equal(options.headers["X-Panam-Command"], "1");
  assert.equal(options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(options.body), {command_id: "00000000-0000-4000-8000-000000000002", runtime_instance_id: "00000000-0000-4000-8000-000000000001", expected_control_revision: 0, action: "start", paid_call_consent: true});
  assert.ok(h.buttons.every(b => b.disabled));
  assert.equal(h.elements.state.textContent, "Runtime status unavailable");
  h.api.stop();
});
test("accepted command is not shown completed and double click does not resubmit", async () => {
  let resolve;
  const h = harness(async url => url === "/api/status" ? ok(status()) : await new Promise(r => {resolve = r;}));
  await tick();
  const pending = h.api.command("listen");
  await h.api.command("listen");
  assert.equal(h.calls.length, 2);
  assert.ok(h.buttons.every(b => b.disabled));
  resolve({status: 202});
  await pending;
  assert.ok(h.elements.feedback.textContent.includes("dokončení"));
  h.api.stop();
});
test("unknown command result never retries automatically", async () => {
  const h = harness(async url => {if (url === "/api/status") return ok(status()); throw new Error("lost response");});
  await tick();
  await h.api.command("listen");
  await h.api.command("listen");
  assert.equal(h.calls.filter(([, options]) => options.method === "POST").length, 1);
  assert.ok(h.elements.feedback.textContent.includes("opakovat"));
  h.api.stop();
});
test("pre-command in-flight poll cannot restore stale mutation controls", async () => {
  let resolvePoll;
  let polls = 0;
  const h = harness(async url => {
    if (url !== "/api/status") return {status: 202};
    if (++polls === 1) return ok(status());
    return await new Promise(r => {resolvePoll = r;});
  });
  await tick();
  const oldPoll = h.api.poll();
  await h.api.command("listen");
  resolvePoll(ok(status()));
  await oldPoll;
  assert.equal(h.elements.state.textContent, "Runtime status unavailable");
  assert.ok(h.buttons.every(b => b.disabled));
  h.api.stop();
});
test("cloud ownership and timer render actual supported observations", async () => {
  const h = harness(async () => ok(status({lifecycle_state: "ACTIVE", cloud_session_owned: true, cloud_ready: false, elapsed_session_seconds: 65, microphone_owned: true, muted: true})));
  await tick();
  assert.ok(h.elements.cloud.textContent.includes("nepotvrzena"));
  assert.ok(h.elements.microphone.textContent.includes("ztlumený"));
  assert.equal(h.elements.elapsed.textContent, "1:05");
  h.api.stop();
});
test("diagnostic strings are rendered with textContent", async () => {
  const malicious = "<img src=x onerror=alert(1)>";
  const h = harness(async () => ok(status({last_call: {closure_confirmed: false, synthetic: malicious}})), () => true, true);
  await tick();
  assert.ok(h.elements["last-call"].textContent.includes(malicious));
  assert.equal(h.elements["last-call"].innerHTML, undefined);
  h.api.stop();
});
test("frontend has no browser capture provider connection remote assets or unload mutations", () => {
  const directory = path.join(__dirname, "../web");
  const js = fs.readFileSync(path.join(directory, "voice-deck.js"), "utf8");
  const html = fs.readFileSync(path.join(directory, "index.html"), "utf8");
  assert.doesNotMatch(js, /getUserMedia|AudioContext|MediaRecorder|WebSocket|EventSource|sendBeacon|beforeunload|innerHTML|api\.openai|wss:\/\//);
  assert.doesNotMatch(html, /https?:\/\/|panam_avatar|<audio|<video|onclick=/);
  assert.ok(html.includes('src="/static/panam-selfie.png"'));
});

test("icon controls retain exact actions accessible names icons and hover focus labels", () => {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../web/voice-deck.css"), "utf8");
  const controls = [...html.matchAll(/<button\b([^>]+)>([\s\S]*?)<\/button>/g)];
  assert.equal(controls.length, 6);
  assert.deepEqual(controls.map(match => /data-action="([^"]+)"/.exec(match[1])[1]), actions);
  for (const [, attributes, content] of controls) {
    assert.match(attributes, /aria-label="[^"]+"/);
    assert.match(attributes, /title="[^"]+"/);
    assert.match(attributes, /disabled/);
    assert.match(content, /<svg[^>]+aria-hidden="true"/);
    assert.match(content, /class="control-label"/);
    assert.match(content, /class="control-tooltip"/);
  }
  assert.ok(css.includes(".control:focus-visible .control-tooltip"));
  assert.ok(css.includes("outline: 2px solid var(--teal)"));
});
test("main portrait view contains no raw diagnostic block", async () => {
  const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  assert.doesNotMatch(html, /<pre|id="last-call"|id="runtime-details"|portrait-mark/);
  assert.match(html, /alt="Portrét Panam"/);
  const h = harness(async () => ok(status({last_call: {status: "FAILED", closure_confirmed: false}})));
  await tick();
  assert.equal(h.elements["last-call"], undefined);
  assert.equal(h.elements.state.textContent, "Fault");
  h.api.stop();
});
test("admin remains read-only even when status authorizes lifecycle actions", async () => {
  const h = harness(async () => ok(status()), () => {throw new Error("admin must not prompt");}, true);
  await tick();
  for (const action of actions) await h.api.command(action);
  assert.equal(h.calls.length, 1);
  assert.ok(h.calls.every(([url]) => url === "/api/status"));
  assert.equal(JSON.parse(h.elements["runtime-details"].textContent).runtime_instance_id, status().runtime_instance_id);
  assert.equal(h.buttons.length, 0);
  h.api.stop();
});
test("admin disconnection clears diagnostic observations rather than implying cleanup", async () => {
  let connected = true;
  const h = harness(async () => {if (!connected) throw new Error("offline"); return ok(status({last_call: {closure_confirmed: true}}));}, () => true, true);
  await tick();
  connected = false;
  await h.api.poll();
  assert.equal(h.elements.state.textContent, "Runtime status unavailable");
  assert.equal(h.elements["runtime-details"].textContent, "Runtime status unavailable");
  assert.ok(h.elements["last-call"].textContent.startsWith("Runtime status unavailable"));
  assert.ok(h.calls.every(([, options]) => !options.method));
  h.api.stop();
});
test("human states and ambient tones follow lifecycle including mute and pending", () => {
  assert.equal(view(status()).title, "Listening Off");
  assert.equal(view(status({lifecycle_state: "LOCAL_LISTENING"})).title, "Local Listening");
  assert.equal(view(status({lifecycle_state: "ACTIVE"})).title, "In Conversation");
  const muted = view(status({lifecycle_state: "ACTIVE", muted: true}));
  assert.equal(muted.title, "Muted");
  assert.equal(muted.tone, "muted");
  assert.equal(view(status({current_operation: "start"})).tone, "pending");
  assert.equal(view(status({current_operation: "end"})).title, "Closing");
});
test("ambient effects honor reduced motion and admin has no controls or capture", () => {
  const directory = path.join(__dirname, "../web");
  const css = fs.readFileSync(path.join(directory, "voice-deck.css"), "utf8");
  const html = fs.readFileSync(path.join(directory, "admin.html"), "utf8");
  assert.ok(css.includes("prefers-reduced-motion: reduce"));
  assert.ok(css.includes("animation: none !important"));
  assert.ok(css.includes("transition: none !important"));
  assert.doesNotMatch(html, /<button|data-action|<audio|<video|https?:\/\//);
  assert.ok(html.includes('id="last-call"'));
  assert.ok(html.includes('id="runtime-details"'));
});
