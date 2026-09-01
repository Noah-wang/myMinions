const automationRows = document.querySelector("#automationRows");
const skillRows = document.querySelector("#skillRows");
const error = document.querySelector("#settingsError");

function statusRow(title, description, enabled) {
  const row = document.createElement("article");
  row.className = "readonly-setting-row";
  row.innerHTML = "<div><strong></strong><small></small></div><span></span>";
  row.querySelector("strong").textContent = title;
  row.querySelector("small").textContent = description;
  const badge = row.querySelector("span");
  badge.textContent = enabled ? "已启用" : "已关闭";
  badge.className = enabled ? "status-on" : "status-off";
  return row;
}

async function load() {
  try {
    const response = await fetch("/api/settings");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    automationRows.append(
      statusRow("运动报告", "检测到稳定的新运动后自动生成并发送复盘。", state.automations.auto_report),
      statusRow("睡眠报告", "睡眠数据就绪后自动生成晨间恢复分析。", state.automations.sleep_report),
    );
    for (const skill of state.skills || []) {
      skillRows.append(statusRow(
        skill.name,
        `${skill.description} · ${skill.source} · v${skill.version}`,
        skill.active,
      ));
    }
  } catch (reason) {
    error.textContent = `读取设置失败：${reason.message}`;
  }
}
load();
