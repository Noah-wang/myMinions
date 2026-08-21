const metricStrip = document.querySelector("#metricStrip");
const dataTabs = document.querySelector("#dataTabs");
const dataBoard = document.querySelector("#dataBoard");

let sections = [];
let activeKey = "";

function askUrl(prompt) {
  return `/?prompt=${encodeURIComponent(prompt)}`;
}

function renderMetrics(summary) {
  metricStrip.replaceChildren();
  for (const metric of summary || []) {
    const item = document.createElement("article");
    item.className = "metric-item";
    item.innerHTML = `
      <span></span>
      <strong></strong>
      <small></small>
    `;
    item.querySelector("span").textContent = metric.label || "";
    item.querySelector("strong").textContent = metric.value || "0";
    item.querySelector("small").textContent = metric.detail || "";
    metricStrip.appendChild(item);
  }
}

function renderTabs() {
  dataTabs.replaceChildren();
  for (const section of sections) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = section.key === activeKey ? "active" : "";
    button.textContent = section.title;
    button.addEventListener("click", () => {
      activeKey = section.key;
      renderTabs();
      renderActiveSection();
    });
    dataTabs.appendChild(button);
  }
}

function renderActiveSection() {
  const section = sections.find((item) => item.key === activeKey);
  dataBoard.replaceChildren();
  if (!section) return;

  const header = document.createElement("header");
  header.className = "data-board-head";
  header.innerHTML = `
    <div>
      <p class="eyebrow"></p>
      <h3></h3>
      <p></p>
    </div>
  `;
  header.querySelector(".eyebrow").textContent = "Read only";
  header.querySelector("h3").textContent = section.title;
  header.querySelector("p:last-child").textContent = section.description || "";
  dataBoard.appendChild(header);

  const items = Array.isArray(section.items) ? section.items : [];
  if (!items.length) {
    const empty = document.createElement("section");
    empty.className = "empty-state";
    empty.innerHTML = `
      <h4>暂无数据</h4>
      <p>这个模块还没有写入内容。后续在 Discord 里上传、导入或同步后，会自动出现在这里。</p>
    `;
    dataBoard.appendChild(empty);
    return;
  }

  const grid = document.createElement("div");
  grid.className = section.key === "personal-bests" ? "record-grid pb-grid" : "record-grid";
  for (const item of items) {
    grid.appendChild(renderRecord(item, section.key));
  }
  dataBoard.appendChild(grid);
}

function renderRecord(item, sectionKey) {
  const article = document.createElement("article");
  article.className = `record-card ${item.state === "empty" ? "muted-card" : ""}`;
  article.innerHTML = `
    <div class="record-main">
      <span class="record-type"></span>
      <h4></h4>
      <p class="record-meta"></p>
      <p class="record-desc"></p>
      <div class="fact-row"></div>
    </div>
    <div class="image-grid"></div>
    <a class="record-action"></a>
  `;
  article.querySelector(".record-type").textContent = labelFor(sectionKey);
  article.querySelector("h4").textContent = item.title || "未命名数据";
  article.querySelector(".record-meta").textContent = item.meta || "";
  article.querySelector(".record-desc").textContent = item.description || "";

  const factRow = article.querySelector(".fact-row");
  const facts = Array.isArray(item.facts) ? item.facts : [];
  if (!facts.length) factRow.remove();
  for (const fact of facts) {
    const factEl = document.createElement("span");
    factEl.innerHTML = `<small></small><strong></strong>`;
    factEl.querySelector("small").textContent = fact.label || "";
    factEl.querySelector("strong").textContent = fact.value || "-";
    factRow.appendChild(factEl);
  }

  const imageGrid = article.querySelector(".image-grid");
  const images = Array.isArray(item.images) ? item.images : [];
  if (!images.length) {
    imageGrid.remove();
  } else {
    for (const src of images.slice(0, 6)) {
      const img = document.createElement("img");
      img.src = src;
      img.alt = item.title || "数据图片";
      img.loading = "lazy";
      imageGrid.appendChild(img);
    }
  }

  const action = article.querySelector(".record-action");
  action.textContent = "用它提问";
  action.href = askUrl(item.prompt || item.title || "");
  return article;
}

function labelFor(key) {
  return {
    profile: "画像",
    "personal-bests": "PB",
    photos: "照片",
    rag: "知识",
    coros: "COROS",
    kitchen: "厨房",
  }[key] || "数据";
}

async function boot() {
  try {
    const response = await fetch("/api/data");
    if (!response.ok) throw new Error("读取失败");
    const payload = await response.json();
    sections = Array.isArray(payload.sections) ? payload.sections : [];
    activeKey = sections[0]?.key || "";
    renderMetrics(payload.summary || []);
    renderTabs();
    renderActiveSection();
  } catch {
    dataBoard.innerHTML = `
      <section class="empty-state">
        <h4>数据暂时不可用</h4>
        <p>后端没有返回数据，请确认 AgentDeck 服务正在运行。</p>
      </section>
    `;
  }
}

boot();
