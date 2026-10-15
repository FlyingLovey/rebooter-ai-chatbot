// Rebooter AI — หน้าแชทของร้าน Rebooter (vanilla JS)
// ไม่ใช้ streaming เพราะคำตอบต้องผ่าน Guard ขาออกทั้งก้อนก่อนแสดง ระหว่างรอจึงแสดงขั้นตอนประมวลผลแทน
// window.AURA_API (ถ้ามี) ใช้แทน API จริง — สำหรับหน้าสาธิตที่ไม่มี backend

const SHOP_PHONE = "02-123-4567";
const MAX_MB = 5;
const ALLOWED = ["image/jpeg", "image/png", "image/webp"];

const CAT_LABEL = {
  BIZ_DISCOUNT: "นโยบายราคา", BIZ_PRIVACY: "ข้อมูลส่วนบุคคล", BIZ_POLICY: "นโยบายร้าน",
  BIZ_INJECTION: "คำสั่งที่ไม่อนุญาต", BIZ_PRICE_NOT_IN_KB: "ราคาไม่ตรงข้อมูลร้าน",
  BIZ_PII_LEAK: "ข้อมูลส่วนบุคคล", BLOCKLIST: "คำต้องห้าม",
  S1: "ความรุนแรง", S2: "การกระทำผิดกฎหมาย", S7: "ข้อมูลส่วนบุคคล", S10: "ความเกลียดชัง",
};
const FOLLOW_UPS = {
  S01: ["ตรวจเช็คใช้เวลานานไหม", "จองคิวยังไง"],
  S02: ["ข้อมูลเดิมจะหายไหม", "ใช้เวลานานไหม"],
  S03: ["ส่งภาพหน้าจอให้ดู", "ตรวจเช็คฟรีไหม", "จองคิวยังไง"],
  S04: ["ถ้ากู้ไม่ได้เสียเงินไหม", "ฮาร์ดดิสก์ตกน้ำกู้ได้ไหม"],
  S08: ["เปลี่ยนจอใช้เวลากี่วัน", "มีประกันไหม"],
  default: ["ร้านเปิดกี่โมง", "จองคิวยังไง"],
};
const BLOCK_FOLLOW_UPS = {
  BIZ_DISCOUNT: ["ราคาลง Windows เท่าไหร่", "ตรวจเช็คฟรีไหม"],
  BIZ_POLICY: ["รับประกันกี่วัน", "ซ่อมไม่หายคืนเงินไหม"],
  BIZ_INJECTION: ["มีบริการอะไรบ้าง"],
  default: ["มีบริการอะไรบ้าง", "ร้านเปิดกี่โมง"],
};

const $ = (id) => document.getElementById(id);
const chat = $("chat"), welcome = $("welcome"), form = $("composer"), input = $("input");
const sendBtn = $("send-btn"), fileInput = $("file"), counter = $("counter");

let pendingFile = null;
let fileProblem = "";
let controller = null;
let lastRequest = null;

// ---------- API ----------
const api = window.AURA_API || {
  async chat(fd, signal) {
    const res = await fetch("/api/v1/chat", { method: "POST", body: fd, signal });
    let data = null;
    try { data = await res.json(); } catch { /* ไม่ใช่ JSON */ }
    return { ok: res.ok && data && !data.error, status: res.status, data };
  },
  async reset() { await fetch("/api/v1/chat/reset", { method: "POST" }); },
  async health() { const r = await fetch("/health"); return r.json(); },
};

// ---------- helpers ----------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function svg(path, size = 16) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", size); s.setAttribute("height", size); s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "2");
  s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
  s.innerHTML = path;
  return s;
}
const ICON = {
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
  wifiOff: '<path d="M2 8.5a15 15 0 0 1 20 0"/><path d="M5 12a10 10 0 0 1 14 0"/><path d="M3 3l18 18"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  retry: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
};
function md(text) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text, { breaks: true }));
  const d = document.createElement("div"); d.textContent = text; return d.innerHTML.replace(/\n/g, "<br>");
}
function scrollDown() { chat.scrollTop = chat.scrollHeight; }
function hideWelcome() { welcome.hidden = true; }

function chipsRow(items, primaryFirst = false) {
  const row = el("div", "chips");
  items.forEach((q, i) => {
    const b = el("button", "chip" + (primaryFirst && i === 0 ? " primary" : ""), q);
    b.type = "button";
    b.addEventListener("click", () => (q === "ส่งภาพหน้าจอให้ดู" ? fileInput.click() : send(q, null)));
    row.appendChild(b);
  });
  return row;
}

function phoneBlock() {
  const wrap = el("div", "actions");
  const call = el("a", "btn solid");
  call.href = "tel:" + SHOP_PHONE.replace(/-/g, "");
  call.append(svg(ICON.phone), document.createTextNode("โทรจองคิว"));
  const hours = el("button", "btn line", "เวลาเปิดร้าน");
  hours.type = "button";
  hours.addEventListener("click", () => send("ร้านเปิดกี่โมง", null));
  wrap.append(call, hours);
  const note = el("p", "phone-note");
  note.style.margin = "0";
  note.append("โทร ");
  note.appendChild(el("strong", null, SHOP_PHONE));
  note.append(" · ทุกวัน 10:00–20:00");
  return [wrap, note];
}

// ---------- render: messages ----------
function addUser(text, file) {
  hideWelcome();
  const m = el("div", "msg user");
  if (file) {
    const img = el("img", "thumb");
    img.src = URL.createObjectURL(file); img.alt = "ภาพที่ส่ง";
    m.appendChild(img);
  }
  if (text) m.appendChild(el("div", "bubble", text));
  chat.appendChild(m); scrollDown();
}

function addProgress(hasImage, hasText) {
  const m = el("div", "msg bot");
  const b = el("div", "bubble");
  b.setAttribute("role", "status");
  const head = el("div", "steps-title");
  const pulse = el("span", "pulse"); pulse.innerHTML = "<i></i><i></i><i></i>";
  head.append(pulse, document.createTextNode(hasImage ? "กำลังดูภาพให้ครับ" : "กำลังหาคำตอบให้ครับ"));
  const labels = [
    ...(hasText ? ["ตรวจความปลอดภัยของข้อความ"] : []),
    ...(hasImage ? ["วิเคราะห์ภาพ"] : []),
    "ค้นข้อมูลบริการของร้าน", "เรียบเรียงคำตอบ", "ตรวจคำตอบก่อนส่ง",
  ];
  const ol = el("ol", "steps");
  const items = labels.map((t) => { const li = el("li"); li.append(el("span", "mark"), document.createTextNode(t)); ol.appendChild(li); return li; });
  b.append(head, ol);
  if (hasImage) b.appendChild(el("span", "steps-note", "ภาพใช้เวลานานกว่าข้อความเล็กน้อย ประมาณ 3–6 วินาที"));
  m.appendChild(b); chat.appendChild(m); scrollDown();

  let i = 0;
  const paint = () => items.forEach((li, k) => {
    li.className = k < i ? "done" : k === i ? "active" : "";
    const mark = li.querySelector(".mark"); mark.innerHTML = "";
    if (k < i) mark.appendChild(svg(ICON.check, 12));
  });
  paint();
  const timer = setInterval(() => { if (i < items.length - 1) { i++; paint(); } }, hasImage ? 1500 : 900);
  return { stop: () => { clearInterval(timer); m.remove(); } };
}

function serviceCard(s) {
  const card = el("article", "svc");
  const head = el("div", "svc-head");
  head.append(el("span", "tag", s.id), el("span", null, "บริการของร้าน"));
  card.append(head, el("h3", null, s.title), el("div", "price", s.price));
  const ul = el("ul");
  if (s.parts_excluded) ul.appendChild(el("li", "warn", "ไม่รวมค่าอะไหล่ — ช่างแจ้งราคาก่อนซ่อม"));
  (s.notes || []).forEach((n) => ul.appendChild(el("li", null, n)));
  if (ul.children.length) card.appendChild(ul);
  return card;
}

function visionCard(v) {
  const sec = el("section", "seen");
  const h = el("h4"); h.append(svg(ICON.eye), document.createTextNode("สิ่งที่เห็นในภาพ"));
  const dl = el("dl");
  const row = (k, valNode) => { dl.appendChild(el("dt", null, k)); const dd = el("dd"); dd.appendChild(valNode); dl.appendChild(dd); };
  if (v.symptoms?.length) row("อาการ", el("strong", null, v.symptoms.join(", ")));
  if (v.visible_text) row("ข้อความในภาพ", el("code", null, v.visible_text));
  if (v.device) row("อุปกรณ์", document.createTextNode(v.device));
  sec.append(h, dl);
  return sec;
}

function addBot(data) {
  const m = el("div", "msg bot");
  const b = el("div", "bubble" + (data.blocked ? " blocked" : ""));
  const cats = data.categories || [];

  if (data.blocked) {
    const why = el("span", "why");
    const privacy = cats.some((c) => c === "BIZ_PRIVACY" || c === "BIZ_PII_LEAK" || c === "S7");
    why.append(svg(privacy ? ICON.lock : ICON.shield, 14), document.createTextNode(CAT_LABEL[cats[0]] || "ระบบความปลอดภัย"));
    b.appendChild(why);
  }
  if (!data.blocked && data.vision && data.vision.is_it_related !== false && data.vision.image_quality !== "unclear") {
    b.append(visionCard(data.vision), el("div", "divider"));
  }
  const body = el("div", "md"); body.innerHTML = md(data.reply); b.appendChild(body);
  if (!data.blocked) (data.services || []).forEach((s) => b.appendChild(serviceCard(s)));
  if (!data.blocked && data.vision && data.vision.is_it_related !== false) b.append(...phoneBlock());
  m.appendChild(b);

  const meta = el("div", "meta");
  if (data.timings_ms?.total != null) meta.appendChild(el("span", null, `ตอบใน ${(data.timings_ms.total / 1000).toFixed(1)} วิ`));
  const src = (data.sources || []).slice(0, 3);
  if (!data.blocked && src.length) {
    meta.appendChild(el("span", null, "· อ้างอิง"));
    src.forEach((s) => { const t = el("span", "tag", s.id); t.title = s.title; meta.appendChild(t); });
  }
  m.appendChild(meta);

  if (data.blocked) {
    const isPrivacy = cats.includes("BIZ_PRIVACY");
    if (isPrivacy) m.append(...phoneBlock().slice(1));
    else m.appendChild(chipsRow(BLOCK_FOLLOW_UPS[cats[0]] || BLOCK_FOLLOW_UPS.default));
  } else {
    const first = (data.services || [])[0]?.id || src[0]?.id;
    m.appendChild(chipsRow(FOLLOW_UPS[first] || FOLLOW_UPS.default, first === "S03"));
  }
  chat.appendChild(m); scrollDown();
}

function addError(message, canRetry) {
  const m = el("div", "msg bot");
  const b = el("div", "bubble error"); b.setAttribute("role", "alert");
  const offline = /เชื่อมต่อ/.test(message);
  const t = el("div", "err-title"); t.append(svg(offline ? ICON.wifiOff : ICON.shield, 18), document.createTextNode(offline ? "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้" : "ยังตอบไม่ได้"));
  b.append(t, el("p", null, message));
  b.lastChild.style.margin = "0";
  if (canRetry && lastRequest) {
    const acts = el("div", "err-actions");
    const r = el("button", "retry"); r.type = "button"; r.append(svg(ICON.retry, 15), document.createTextNode(" ลองใหม่"));
    r.addEventListener("click", () => { m.remove(); send(lastRequest.text, lastRequest.file, true); });
    const e = el("button", "edit", "แก้ข้อความ"); e.type = "button";
    e.addEventListener("click", () => { m.remove(); input.value = lastRequest.text || ""; autosize(); input.focus(); });
    acts.append(r, e); b.appendChild(acts);
  }
  m.appendChild(b); chat.appendChild(m); scrollDown();
}

// ---------- state ----------
function setBusy(busy) {
  input.disabled = busy;
  fileInput.disabled = busy;
  sendBtn.classList.toggle("stop", busy);
  sendBtn.type = busy ? "button" : "submit";
  sendBtn.setAttribute("aria-label", busy ? "หยุด" : "ส่ง");
  sendBtn.innerHTML = busy
    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>หยุด'
    : '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>';
  if (!busy) updateSendState();
}
function updateSendState() { sendBtn.disabled = !!fileProblem; }

function setFile(file) {
  const box = $("attachment"), prob = $("att-problem");
  pendingFile = null; fileProblem = ""; fileInput.value = "";
  if (!file) { box.hidden = true; box.classList.remove("bad"); updateSendState(); return; }
  const sizeMb = file.size / 1024 / 1024;
  $("att-name").textContent = `${file.name || "ภาพจากคลิปบอร์ด"} · ${sizeMb.toFixed(1)} MB`;
  $("att-img").src = URL.createObjectURL(file);
  if (!ALLOWED.includes(file.type)) fileProblem = "รองรับเฉพาะไฟล์ .jpg .png หรือ .webp";
  else if (sizeMb > MAX_MB) fileProblem = `ไฟล์ใหญ่เกิน ${MAX_MB} MB — ลดขนาดภาพหรือแคปหน้าจอแทน`;
  else pendingFile = file;
  prob.textContent = fileProblem; prob.hidden = !fileProblem;
  box.classList.toggle("bad", !!fileProblem);
  box.hidden = false;
  updateSendState();
}

// ---------- send ----------
async function send(text, file, isRetry = false) {
  text = (text || "").trim();
  if ((!text && !file) || controller || fileProblem) return;
  lastRequest = { text, file };
  if (!isRetry) addUser(text, file);
  input.value = ""; autosize(); setFile(null);
  setBusy(true);
  const progress = addProgress(!!file, !!text);
  controller = new AbortController();

  const fd = new FormData();
  fd.append("message", text);
  if (file) fd.append("image", file, file.name || "image.png");

  try {
    const { ok, status, data } = await api.chat(fd, controller.signal);
    progress.stop();
    if (ok) { addBot(data); lastRequest = null; }
    else addError(data?.message || `เกิดข้อผิดพลาด (HTTP ${status})`, status !== 400);
    if (status === 503) showGuardDown(true);
  } catch (err) {
    progress.stop();
    if (err.name === "AbortError") addError("หยุดการตอบแล้ว ข้อความนี้ยังไม่ได้คำตอบ", true);
    else addError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ข้อความของคุณยังไม่ถูกส่ง ตรวจสอบอินเทอร์เน็ตแล้วกดลองใหม่ได้เลยครับ", true);
  } finally {
    controller = null; setBusy(false); input.focus();
  }
}

function showGuardDown(down) {
  $("banner").hidden = !down;
  $("status").className = "status " + (down ? "down" : "ok");
  $("status-text").textContent = down ? "ระบบขัดข้องชั่วคราว" : "ออนไลน์ · เปิดทุกวัน 10:00–20:00";
}
async function checkHealth() {
  try { const j = await api.health(); showGuardDown(j.guard === "down"); }
  catch { $("status").className = "status down"; $("status-text").textContent = "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้"; }
}

// ---------- events ----------
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; counter.textContent = `${input.value.length}/1000`; }

form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value, pendingFile); });
sendBtn.addEventListener("click", () => { if (controller) controller.abort(); });
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!controller) form.requestSubmit(); }
});
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
$("att-remove").addEventListener("click", () => setFile(null));
$("cta-photo").addEventListener("click", () => fileInput.click());
document.querySelectorAll("#popular .chip").forEach((b) => b.addEventListener("click", () => send(b.textContent, null)));
input.addEventListener("paste", (e) => {
  const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
  if (item) { e.preventDefault(); setFile(item.getAsFile()); }
});
["dragover", "dragleave", "drop"].forEach((ev) => form.addEventListener(ev, (e) => {
  e.preventDefault(); form.classList.toggle("drag", ev === "dragover");
  if (ev === "drop" && e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
}));
$("reset-btn").addEventListener("click", async () => {
  if (controller) controller.abort();
  try { await api.reset(); } catch { /* ไม่เป็นไร เริ่มใหม่ฝั่งหน้าเว็บได้ */ }
  chat.querySelectorAll(".msg").forEach((m) => m.remove());
  welcome.hidden = false; setFile(null); input.focus();
});

checkHealth();
