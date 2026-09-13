"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {ManualTiming, QUESTIONS} = require("../timing-panel.js");

test("question order exactly matches preregistration", () => {
  const doc = fs.readFileSync(path.join(__dirname, "../BENCHMARK.md"), "utf8");
  const fixed = [...doc.matchAll(/^\| Q\d{2} \| (.+?) \|$/gm)].map(m => m[1]);
  assert.deepEqual(QUESTIONS, fixed);
});
test("monotonic human marks give the interval; duplicate marks cannot replace it", () => {
  const e = new ManualTiming();
  assert.equal(e.heard(1), false);
  e.begin(); e.end(1200); e.end(1800); e.heard(3500);
  assert.equal(e.rows[0].audible_seconds, "2.300");
  assert.equal(e.rows[0].question_end_ms, "1200.000");
  assert.equal(e.heard(3800), false);
  assert.equal(e.rows[0].method, "human_monotonic_panel");
  assert.equal(e.rows[0].uncertainty_seconds, "0.3");
});
test("late response remains failure even if the timeout callback was delayed", () => {
  const e = new ManualTiming(); e.begin(); e.end(100);
  assert.equal(e.heard(15200), false);
  assert.equal(e.rows[0].status, "FAIL");
  assert.equal(e.rows[0].comment_code, "TIMEOUT_15S");
  assert.equal(e.verdict("NOT_VERIFIED", "HUMAN_MARK_ERROR"), false);
  e.begin(); assert.equal(e.index, 1);
  assert.equal(e.rows[0].status, "FAIL");
});
test("invalid marks and incomplete questions survive reload without replacement", () => {
  const e = new ManualTiming(); e.begin(); e.end(500);
  e.verdict("NOT_VERIFIED", "FOCUS_LOST_DURING_TIMING"); e.begin();
  const restored = new ManualTiming(JSON.parse(JSON.stringify(e.rows)));
  restored.begin(); assert.equal(restored.index, 2);
  assert.equal(restored.rows[0].status, "NOT_VERIFIED");
  assert.equal(restored.rows[1].status, "NOT_VERIFIED");
});
test("finish preserves all originals and leaves untouched trials untested", () => {
  const e = new ManualTiming(); e.begin(); e.end(100); e.finish();
  assert.equal(e.rows[0].status, "NOT_VERIFIED");
  assert.equal(e.rows.filter(r => r.status === "NOT_TESTED").length, 19);
  assert.equal(e.csv().trim().split("\n").length, 21);
  assert.equal(e.begin(), false);
});
test("twenty results are preserved and no twenty-first trial is created", () => {
  const e = new ManualTiming();
  for (let i = 0; i < 20; i++) { e.begin(); e.end(100); e.heard(1200); }
  assert.equal(e.begin(), false); assert.equal(e.phase, "complete");
  assert.equal(e.rows.length, 20); assert.ok(e.rows.every(r => r.status === "PASS"));
});

// Run the actual page controller with a deterministic DOM/clock/storage fixture.
// Engine-only tests did not catch the live handoff staying in practice.
function panel(storage = new Map(), failWrite = false) {
  const html = fs.readFileSync(path.join(__dirname, "../timing-panel.html"), "utf8");
  const script = fs.readFileSync(path.join(__dirname, "../timing-panel.js"), "utf8");
  class Element {
    constructor(tagName = "DIV") { this.tagName = tagName; this.disabled = false; this.textContent = ""; this.value = ""; }
    click() { if (!this.disabled) this.onclick?.(); }
    replaceChildren(...nodes) { this.children = nodes; }
    append(node) { (this.children ||= []).push(node); }
  }
  const elements = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(m => [m[1], new Element()]));
  const listeners = {}, windowListeners = {}, timers = [];
  let now = 0;
  const context = {
    document: {getElementById: id => elements[id], createElement: tag => new Element(tag.toUpperCase()),
      addEventListener: (name, handler) => { listeners[name] = handler; }},
    window: {addEventListener: (name, handler) => { windowListeners[name] = handler; }},
    performance: {now: () => now},
    localStorage: {getItem: key => storage.get(key) || null,
      setItem: (key, value) => { if (failWrite) throw new Error("synthetic storage failure"); storage.set(key, value); }},
    setInterval: handler => { timers.push(handler); }
  };
  vm.runInNewContext(script, context, {timeout: 1000});
  return {elements, storage, at: value => { now = value; },
    key: key => listeners.keydown({key, repeat: false, target: {tagName: "DIV"}, preventDefault() {}}),
    blur: () => windowListeners.blur(), tick: () => timers.forEach(fn => fn()),
    rows: () => JSON.parse([...storage.values()][0])};
}
function practice(p) { p.elements.practice.click(); p.at(100); p.key("e"); p.at(1400); p.key("a"); }

test("page handoff: practice never scores; explicit measured start enables persistent Q01 marks", () => {
  const p = panel(); practice(p);
  assert.ok(p.rows().every(r => r.status === "NOT_TESTED"));
  assert.match(p.elements.mode.textContent, /MĚŘENÍ NEBĚŽÍ/);
  assert.equal(p.elements.start.disabled, false);
  p.key("e"); p.key("a");
  assert.match(p.elements.feedback.textContent, /NEZAPSALA/);
  assert.ok(p.rows().every(r => r.status === "NOT_TESTED"));
  p.elements.start.click();
  assert.match(p.elements.mode.textContent, /Q01.*připraveno pro E/);
  assert.equal(p.elements.end.disabled, false);
  p.at(5000); p.key("e"); p.at(7300); p.key("a");
  assert.equal(p.rows()[0].status, "PASS");
  assert.equal(p.rows()[0].audible_seconds, "2.300");
  assert.equal(p.rows()[1].status, "NOT_TESTED");
  assert.match(p.elements.csv.value, /"Q01","1","PASS","2.300"/);
});
test("page wrong-order and duplicate keys explain rejection without changing the original mark", () => {
  const p = panel(); practice(p); p.elements.start.click();
  p.key("a"); assert.match(p.elements.feedback.textContent, /nejdříve.*E/);
  p.at(5000); p.key("e"); p.at(6000); p.key("e");
  assert.match(p.elements.feedback.textContent, /již máte/);
  p.at(7000); p.key("a"); assert.equal(p.rows()[0].audible_seconds, "2.000");
  p.key("a"); assert.match(p.elements.feedback.textContent, /pokus už skončil/);
  assert.equal(p.rows()[0].audible_seconds, "2.000");
});
test("page reload preserves measured originals and requires explicit continuation", () => {
  const p = panel(); practice(p); p.elements.start.click();
  p.at(5000); p.key("e"); p.at(7000); p.key("a");
  const restored = panel(p.storage);
  assert.equal(restored.elements.start.disabled, true);
  assert.equal(restored.elements.end.disabled, true);
  restored.key("e"); assert.match(restored.elements.feedback.textContent, /NEZAPSALA/);
  restored.elements.next.click();
  assert.match(restored.elements.mode.textContent, /Q02/);
  assert.equal(restored.rows()[0].audible_seconds, "2.000");
});
test("page focus loss retains the incomplete trial instead of awarding a pass", () => {
  const p = panel(); practice(p); p.elements.start.click();
  p.at(5000); p.key("e"); p.blur(); p.at(7000); p.key("a");
  assert.equal(p.rows()[0].status, "NOT_VERIFIED");
  assert.equal(p.rows()[0].comment_code, "FOCUS_LOST_DURING_TIMING");
});
test("page timeout fails without a response and cannot be overwritten by a late A", () => {
  const p = panel(); practice(p); p.elements.start.click();
  p.at(5000); p.key("e"); p.at(20000); p.tick(); p.key("a");
  assert.equal(p.rows()[0].status, "FAIL");
  assert.equal(p.rows()[0].comment_code, "TIMEOUT_15S");
});
test("page storage failure prevents a measurement handoff", () => {
  const p = panel(new Map(), true); practice(p);
  assert.equal(p.elements.start.disabled, true);
  assert.match(p.elements.storage.textContent, /není dostupné/);
});
