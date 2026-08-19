const stage = document.querySelector(".stage");
const chatLog = document.querySelector("#chatLog");
const welcome = document.querySelector("#welcome");
const suggestionsEl = document.querySelector("#suggestions");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const sendButton = document.querySelector("#sendButton");
const newChatButton = document.querySelector("#newChat");

const SESSION_KEY = "agentdeck-session";
const FALLBACK_SUGGESTIONS = [
  "我现在半马 1:40，全马 4:30，想提高全马成绩应该怎么练？",
  "我今天这次训练怎么样？下一次应该怎么练？",
  "我想做番茄牛腩，把食材加进采购清单",
];

let busy = false;
let thinkingEl = null;

/* 会话 */

function newSessionId() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function conversationId() {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = newSessionId();
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function resetConversation() {
  sessionStorage.setItem(SESSION_KEY, newSessionId());
  chatLog.replaceChildren(welcome);
  welcome.hidden = false;
  input.focus();
}

/* 渲染 */

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderInline(text) {
  return text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function renderMarkdown(value) {
  const html = [];
  let list = null;

  const closeList = () => {
    if (list) {
      html.push(`</${list}>`);
      list = null;
    }
  };
  const openList = (kind) => {
    if (list !== kind) {
      closeList();
      html.push(`<${kind}>`);
      list = kind;
    }
  };

  for (const raw of escapeHtml(value).split("\n")) {
    const line = raw.trim();
    if (!line) {
      closeList();
      continue;
    }

    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) {
      closeList();
      html.push(`<h3>${renderInline(heading[1])}</h3>`);
      continue;
    }

    if (line.startsWith("&gt;")) {
      closeList();
      html.push(`<blockquote>${renderInline(line.replace(/^&gt;\s?/, ""))}</blockquote>`);
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      openList("ul");
      html.push(`<li>${renderInline(bullet[1])}</li>`);
      continue;
    }

    const numbered = line.match(/^\d+[.、)]\s+(.*)$/);
    if (numbered) {
      openList("ol");
      html.push(`<li>${renderInline(numbered[1])}</li>`);
      continue;
    }

    closeList();
    html.push(`<p>${renderInline(line)}</p>`);
  }

  closeList();
  return html.join("");
}

function atBottom() {
  return stage.scrollHeight - stage.scrollTop - stage.clientHeight < 120;
}

function scrollToBottom() {
  stage.scrollTop = stage.scrollHeight;
}

function hideWelcome() {
  welcome.hidden = true;
}

function appendMessage(kind, text) {
  hideWelcome();
  const article = document.createElement("article");
  article.className = `message ${kind}-message`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (kind === "user") {
    bubble.textContent = text;
  } else {
    bubble.innerHTML = renderMarkdown(text);
  }
  article.appendChild(bubble);
  chatLog.appendChild(article);
  scrollToBottom();
  return bubble;
}

/* 思考中提示，收到正式回答后消失 */

function showThinking(label) {
  hideWelcome();
  if (!thinkingEl) {
    thinkingEl = document.createElement("article");
    thinkingEl.className = "message thinking";
    thinkingEl.innerHTML =
      '<span class="text"></span><span class="dots"><span></span><span></span><span></span></span>';
    chatLog.appendChild(thinkingEl);
  }
  thinkingEl.querySelector(".text").textContent = label;
  scrollToBottom();
}

function hideThinking() {
  thinkingEl?.remove();
  thinkingEl = null;
}

/* 后端把「正在…」这类进度提示也当普通消息发过来，
   这里识别出来只当临时状态显示，不留在对话记录里。 */
function isProgressNotice(text) {
  return /^正在[^\n]{0,36}$/.test(text.trim());
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 16));

async function streamText(bubble, text) {
  const total = text.length;
  // 按真实时间推进，避免浏览器降帧时逐字渲染被拖得很慢。
  const durationMs = Math.min(1400, Math.max(320, total * 3));
  const startedAt = performance.now();
  let shown = 0;

  while (shown < total) {
    await tick();
    const progress = (performance.now() - startedAt) / durationMs;
    shown = Math.min(total, Math.max(shown + 1, Math.ceil(total * progress)));

    const stick = atBottom();
    bubble.innerHTML = renderMarkdown(text.slice(0, shown));
    // 光标塞进最后一个块级元素里，否则会独占一行。
    const cursor = document.createElement("span");
    cursor.className = "cursor";
    (bubble.lastElementChild || bubble).appendChild(cursor);
    if (stick) scrollToBottom();
  }

  bubble.innerHTML = renderMarkdown(text);
}

/* 网络 */

function parseSseEvents(buffer) {
  const events = [];
  let remaining = buffer;
  let separatorIndex = remaining.indexOf("\n\n");

  while (separatorIndex !== -1) {
    const rawEvent = remaining.slice(0, separatorIndex);
    remaining = remaining.slice(separatorIndex + 2);
    const data = rawEvent
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (data) {
      events.push(JSON.parse(data));
    }
    separatorIndex = remaining.indexOf("\n\n");
  }

  return { events, remaining };
}

async function streamChat(message) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: conversationId() }),
  });

  if (!response.ok || !response.body) {
    let errorMessage = "请求失败";
    try {
      const result = await response.json();
      errorMessage = result.error || errorMessage;
    } catch {
      errorMessage = response.statusText || errorMessage;
    }
    throw new Error(errorMessage);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseEvents(buffer);
    buffer = parsed.remaining;

    for (const event of parsed.events) {
      if (event.type === "done") {
        // 不依赖连接关闭来结束循环，收到 done 就收工。
        await reader.cancel().catch(() => {});
        return;
      }
      if (event.type === "message") {
        const text = event.message || "";
        if (!text.trim()) continue;
        if (isProgressNotice(text)) {
          showThinking(text);
          continue;
        }
        hideThinking();
        await streamText(appendMessage("agent", ""), text);
      } else if (event.type === "status") {
        showThinking(event.message || "思考中");
      } else if (event.type === "error") {
        hideThinking();
        appendMessage("error", `出错了：${event.error || "未知错误"}`);
      }
      // trace 事件是内部执行信息，前端不展示
    }
  }
}

/* 交互 */

function setBusy(value) {
  busy = value;
  sendButton.disabled = value;
}

function autoGrow() {
  // 内容为空时直接交回 CSS 的单行高度，不去读 scrollHeight，
  // 否则清空输入框后高度会停在上一次撑开的值。
  if (!input.value) {
    input.style.height = "";
    return;
  }
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

async function submitMessage(message) {
  if (busy || !message) return;

  appendMessage("user", message);
  input.value = "";
  autoGrow();
  setBusy(true);
  showThinking("思考中");

  try {
    await streamChat(message);
  } catch (error) {
    appendMessage("error", `出错了：${error.message}`);
  } finally {
    hideThinking();
    setBusy(false);
    input.focus();
  }
}

function renderSuggestions(prompts) {
  suggestionsEl.replaceChildren();
  for (const prompt of prompts.slice(0, 3)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = prompt;
    button.addEventListener("click", () => submitMessage(prompt));
    suggestionsEl.appendChild(button);
  }
}

async function loadSuggestions() {
  try {
    const response = await fetch("/api/capabilities");
    if (!response.ok) throw new Error();
    const payload = await response.json();
    const prompts = Object.values(payload.sample_prompts || {});
    renderSuggestions(prompts.length ? prompts : FALLBACK_SUGGESTIONS);
  } catch {
    renderSuggestions(FALLBACK_SUGGESTIONS);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submitMessage(input.value.trim());
});

input.addEventListener("input", autoGrow);

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitMessage(input.value.trim());
  }
});

newChatButton.addEventListener("click", () => {
  if (busy) return;
  resetConversation();
});

conversationId();
loadSuggestions();
input.focus();
