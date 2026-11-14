// Запускает JS виджета в Node с заглушками DOM и TelegramWidget.
// stdin: {"html": "...", "tw": {chatData, publicData}, "actions": [["click","g"], ["chat", "{...}"], ...]}
// stdout: JSON со снимком элементов по id после каждого действия и списком запросов к боту.
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, _t: "", set textContent(v) { this._t = String(v); }, get textContent() { return this._t; }, hidden: false, disabled: false, className: "", children: [], set innerHTML(v) { this.children = []; }, get innerHTML() { return ""; },
    appendChild(c) { this.children.push(c); }, set onclick(f) { this._click = f; }, get onclick() { return this._click; } };
  return els[id];
}
const handlers = {};
const log = { requests: [], links: [], snaps: [] };
const TW = Object.assign({
  chatData: null, publicData: null,
  onEvent(t, f) { handlers[t] = f; }, watchPublicData() { log.watched = true; }, ready() { log.ready = true; },
  request(d) { log.requests.push(d); return input.reject ? Promise.reject({ code: input.reject }) : Promise.resolve({ result: '{"ok":1}' }); },
  openLink(u) { log.links.push(u); },
}, input.tw);
global.window = { TelegramWidget: TW };
global.document = { getElementById: el, createElement: () => ({ _t: "", set textContent(v) { this._t = String(v); }, get textContent() { return this._t; }, set onclick(f) { this._click = f; } }) };
global.setInterval = () => 0;
const m = input.html.match(/<script>([\s\S]*)<\/script>/);
function snap() {
  const o = {};
  for (const [k, v] of Object.entries(els)) o[k] = { t: v.textContent, h: v.hidden, d: v.disabled, n: v.children.length,
    stars: v.children.map((c) => c.textContent).join("") };
  return o;
}
(async () => {
  new Function(m[1])();
  log.snaps.push(snap());
  for (const [kind, arg] of input.actions || []) {
    if (kind === "click") els[arg]._click();
    if (kind === "star") els.rt.children[arg]._click();
    if (kind === "chat") { TW.chatData = arg; handlers.chatDataChanged(); }
    if (kind === "public") { TW.publicData = arg; handlers.publicDataChanged(); }
    await new Promise((r) => setTimeout(r, 0));
    log.snaps.push(snap());
  }
  process.stdout.write(JSON.stringify(log));
})();
