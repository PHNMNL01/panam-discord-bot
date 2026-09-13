/* Manual annotation only: no microphone, provider calls, audio, or secrets. */
"use strict";
const QUESTIONS = [
  "Kolik dní má běžný týden?", "Jaké je hlavní město Francie?", "Kolik je sedm plus osm?",
  "Jak se anglicky řekne dobré ráno?", "Která planeta je nejblíže Slunci?",
  "Jakou barvu získám smícháním modré a žluté?", "Kolik minut má jedna hodina?",
  "Co je opak slova pomalý?", "Vyjmenuj tři druhy ovoce.", "K čemu slouží teploměr?",
  "Které roční období následuje po jaru?", "Kolik stran má trojúhelník?",
  "Řekni jednou větou, co je duha.", "Jaké zvíře mňouká?",
  "Jak se jmenuje přirozená družice Země?", "Kolik je dvacet děleno čtyřmi?",
  "Uveď dvě věci, které si vezmu do deště.", "Co znamená slovo synonymum?",
  "Který den následuje po úterý?", "Jak se česky řekne anglické thank you?"
];
const FIELDS = ["id", "attempt", "status", "audible_seconds", "uncertainty_seconds", "method",
  "comment_code", "question_end_ms", "substantive_audible_ms"];

class ManualTiming {
  constructor(saved = null) {
    this.rows = saved || QUESTIONS.map((_, i) => ({id: `Q${String(i + 1).padStart(2, "0")}`,
      attempt: "1", status: "NOT_TESTED", audible_seconds: "", uncertainty_seconds: "0.3",
      method: "human_monotonic_panel", comment_code: "", question_end_ms: "", substantive_audible_ms: ""}));
    if (!Array.isArray(this.rows) || this.rows.length !== 20 || this.rows.some((r, i) =>
      r.id !== `Q${String(i + 1).padStart(2, "0")}` || r.attempt !== "1" ||
      !["NOT_TESTED", "PASS", "FAIL", "NOT_VERIFIED"].includes(r.status))) throw new Error("Invalid timing metadata");
    this.index = -1; this.phase = "idle"; this.t0 = null;
  }
  begin() {
    if (!["idle", "review"].includes(this.phase)) return false;
    this.index = this.rows.findIndex(r => r.status === "NOT_TESTED");
    if (this.index < 0) { this.phase = "complete"; return false; }
    Object.assign(this.rows[this.index], {status: "NOT_VERIFIED", comment_code: "INCOMPLETE_MARKS"});
    this.phase = "question"; this.t0 = null; return true;
  }
  end(now) {
    if (this.phase !== "question" || !Number.isFinite(now)) return false;
    this.t0 = now; this.rows[this.index].question_end_ms = now.toFixed(3);
    this.phase = "waiting"; return true;
  }
  heard(now) {
    if (this.phase !== "waiting" || now < this.t0 || !Number.isFinite(now)) return false;
    if (this.timeout(now)) return false;
    Object.assign(this.rows[this.index], {status: "PASS", comment_code: "HUMAN_SUBSTANTIVE_MARK",
      audible_seconds: ((now - this.t0) / 1000).toFixed(3), substantive_audible_ms: now.toFixed(3)});
    this.phase = "review"; return true;
  }
  timeout(now) {
    if (this.phase !== "waiting" || now - this.t0 < 15000) return false;
    this.verdict("FAIL", "TIMEOUT_15S"); return true;
  }
  verdict(status, code) {
    if (!["question", "waiting", "review"].includes(this.phase)) return false;
    const row = this.rows[this.index];
    // A timing mistake may invalidate a PASS; it must never erase a recorded failure.
    if (row.status === "FAIL" && status !== "FAIL") return false;
    Object.assign(row, {status, comment_code: code}); this.phase = "review"; return true;
  }
  finish() {
    if (["question", "waiting"].includes(this.phase)) this.verdict("NOT_VERIFIED", "SESSION_ENDED_INCOMPLETE");
    this.phase = "complete";
  }
  csv() {
    const cell = v => `"${String(v ?? "").replaceAll('"', '""')}"`;
    return FIELDS.join(",") + "\n" + this.rows.map(r => FIELDS.map(f => cell(r[f])).join(",")).join("\n") + "\n";
  }
}

if (typeof module !== "undefined") module.exports = {ManualTiming, QUESTIONS};
if (typeof document !== "undefined") {
  const $ = id => document.getElementById(id);
  const KEY = "panam-companion-latency-q01-q20-v1";
  let engine, live = false, practice = false, trained = false, storageOK = true;
  try {
    engine = new ManualTiming(JSON.parse(localStorage.getItem(KEY) || "null"));
    localStorage.setItem(KEY, JSON.stringify(engine.rows));
  }
  catch (_) { engine = new ManualTiming(); storageOK = false; }
  const stored = engine.rows.some(r => r.status !== "NOT_TESTED");
  if (stored) { live = true; trained = true; }
  function save() {
    if (!live || !storageOK) return;
    try { localStorage.setItem(KEY, JSON.stringify(engine.rows)); }
    catch (_) { storageOK = false; engine.finish(); }
  }
  function render() {
    const p = engine.phase;
    $("mode").textContent = live
      ? (p === "complete" ? "MĚŘENÁ SADA UKONČENA" :
        p === "idle" ? "ULOŽENÁ SADA — pokračování zatím není připravené" :
        `MĚŘENÁ SADA · ${engine.rows[engine.index].id} · ${p === "question" ? "připraveno pro E" : p === "waiting" ? "čeká na A" : "výsledek zapsán"}`)
      : "MĚŘENÍ NEBĚŽÍ — nácvik nezapisuje Q01–Q20";
    $("practice").disabled = live;
    $("start").disabled = !storageOK || !trained || live;
    $("end").disabled = p !== "question";
    $("heard").disabled = p !== "waiting";
    $("next").disabled = !live || !["review", "idle"].includes(p);
    $("next").textContent = stored && p === "idle" ? "Pokračovat dalším neprovedeným pokusem" : "Další otázka";
    $("wrong").disabled = !["question", "waiting", "review"].includes(p);
    $("invalid").disabled = $("wrong").disabled;
    $("finish").disabled = !live || p === "complete";
    $("storage").textContent = storageOK ? "" : "Ukládání metadat není dostupné nebo uložená data nelze načíst. Měřenou sadu nespouštějte; oznamte to agentovi.";
    if (engine.index >= 0) {
      $("trial").textContent = practice ? "NÁCVIK · bez výsledků" : `${engine.rows[engine.index].id} / Q20`;
      $("question").textContent = practice ? "Bez mluvení stiskněte E a za chvíli A." : QUESTIONS[engine.index];
    }
    const current = engine.rows[engine.index];
    const statuses = {idle: stored ? "Uložené pokusy jsou zachované. Na pokyn pokračujte dalším neprovedeným." : "Nejdříve vyzkoušejte nácvik.",
      question: practice ? "Nácvik: bez mluvení stiskněte E, potom A do 15 sekund."
        : "Panel je připraven pro měřenou otázku. Až agent potvrdí připojení bota, přečtěte otázku nahlas; při jejím dokončení stiskněte E.",
      waiting: "Čekám na první věcné slyšitelné slovo — stiskněte A.",
      review: practice ? (trained ? "Nácvik hotov, měření ještě NEBĚŽÍ. Zvolte Připravit měřenou sadu Q01–Q20. Agent musí před placeným startem ověřit zobrazené Q01 a povolené E. Zatím nemluvte."
        : "Nácvik nebyl dokončen dvěma platnými značkami. Zvolte Nácvik znovu a stiskněte E, potom A do 15 sekund.")
        : `Zapsáno: ${current?.status}. Nechte odpověď doznít, opravte případné chybné hodnocení a pokračujte.`,
      complete: "Sada ukončena. Zastavte také bota a napište agentovi: Sada hotova. Výsledky zůstávají zde."};
    $("status").textContent = statuses[p];
    if (p !== "waiting") $("clock").textContent = current?.audible_seconds ? `${current.audible_seconds} s` : "—";
    const shown = live ? engine : new ManualTiming();
    $("rows").replaceChildren(...shown.rows.map(r => {
      const tr = document.createElement("tr");
      for (const value of [r.id, r.status, r.audible_seconds || "—", r.comment_code || "—"]) {
        const td = document.createElement("td"); td.textContent = value; tr.append(td);
      }
      return tr;
    }));
    $("csv").value = shown.csv();
  }
  function act(fn) { $("feedback").textContent = ""; fn(); save(); render(); }
  $("practice").onclick = () => { engine = new ManualTiming(); practice = true; trained = false; act(() => engine.begin()); };
  $("start").onclick = () => { engine = new ManualTiming(); practice = false; live = true; act(() => engine.begin()); };
  $("end").onclick = () => act(() => engine.end(performance.now()));
  $("heard").onclick = () => act(() => { if (engine.heard(performance.now()) && practice) trained = true; });
  $("next").onclick = () => act(() => engine.begin());
  $("wrong").onclick = () => act(() => engine.verdict("FAIL", "WRONG_OR_NO_ANSWER"));
  $("invalid").onclick = () => act(() => engine.verdict("NOT_VERIFIED", "HUMAN_MARK_ERROR"));
  $("finish").onclick = () => act(() => engine.finish());
  document.addEventListener("keydown", event => {
    if (event.repeat || event.ctrlKey || event.altKey || event.metaKey || event.target.tagName === "TEXTAREA") return;
    const id = {e: "end", a: "heard"}[event.key.toLowerCase()];
    if (!id) return;
    event.preventDefault();
    if (!$(id).disabled) { $(id).click(); return; }
    $("feedback").textContent = !live
      ? "Značka se NEZAPSALA: měřená sada neběží. Dokončete nácvik a zvolte Připravit měřenou sadu Q01–Q20."
      : engine.phase === "question" ? "Značka se NEZAPSALA: nejdříve označte konec otázky klávesou E."
      : engine.phase === "waiting" ? "Značka se NEZAPSALA: konec otázky již máte. Nyní čekám na věcné slyšitelné slovo a klávesu A."
      : "Značka se NEZAPSALA: tento pokus už skončil. Zkontrolujte výsledek a pokračujte tlačítkem Další otázka, pokud sada ještě běží.";
  });
  // Losing the focused page makes human reaction timing indeterminate.
  window.addEventListener("blur", () => {
    if (engine.phase === "waiting") act(() => {
      if (!engine.timeout(performance.now())) engine.verdict("NOT_VERIFIED", "FOCUS_LOST_DURING_TIMING");
    });
  });
  setInterval(() => {
    if (engine.phase !== "waiting") return;
    const now = performance.now();
    if (engine.timeout(now)) { save(); render(); }
    else $("clock").textContent = `${((now - engine.t0) / 1000).toFixed(1)} s`;
  }, 50);
  render();
}
