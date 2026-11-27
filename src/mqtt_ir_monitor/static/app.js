// Frontend Dashboard JS for MQTT IR Monitor

let registeredDevices = [];
let selectedNotificationIds = new Set();
let activeFilterDeviceId = "";
let activeFilterEvent = "";
let hideNoise = true;
let isReplaying = false;
let activeTab = "monitor";

// Tab Navigation DOM Elements
const tablistEl = document.querySelector('[role="tablist"]');
const tabMonitorBtn = document.getElementById("tab-monitor");
const tabScriptsBtn = document.getElementById("tab-scripts");
const panelMonitorEl = document.getElementById("panel-monitor");
const panelScriptsEl = document.getElementById("panel-scripts");
const tabBadgeMonitorEl = document.getElementById("tab-badge-monitor");
const tabBadgeRecordingEl = document.getElementById("tab-badge-recording");
const tabBadgeScriptsEl = document.getElementById("tab-badge-scripts");
const tabBadgeRunningEl = document.getElementById("tab-badge-running");
const btnStopScriptRun = document.getElementById("btn-stop-script-run");
const scriptProgressBox = document.getElementById("script-progress-box");
const scriptProgressBar = document.getElementById("script-progress-bar");
const scriptStatusMessage = document.getElementById("script-status-message");

function normalizeTabFromHash(hash) {
  const cleaned = (hash || "").replace(/^#/, "").trim().toLowerCase();
  return cleaned === "scripts" ? "scripts" : "monitor";
}

function activateTab(tabName, { updateHash = true, focusTab = false } = {}) {
  const target = tabName === "scripts" ? "scripts" : "monitor";
  activeTab = target;
  const isMonitor = target === "monitor";

  tabMonitorBtn.setAttribute("aria-selected", String(isMonitor));
  tabMonitorBtn.tabIndex = isMonitor ? 0 : -1;
  tabScriptsBtn.setAttribute("aria-selected", String(!isMonitor));
  tabScriptsBtn.tabIndex = isMonitor ? -1 : 0;

  panelMonitorEl.hidden = !isMonitor;
  panelScriptsEl.hidden = isMonitor;

  if (updateHash && window.location.hash !== `#${target}`) {
    history.replaceState(null, "", `#${target}`);
  }
  if (focusTab) {
    (isMonitor ? tabMonitorBtn : tabScriptsBtn).focus();
  }
}

function setRunProgressState({ running, message, percent }) {
  isReplaying = running;
  btnCancelReplay.disabled = !running;
  if (btnStopScriptRun) btnStopScriptRun.disabled = !running;
  if (tabBadgeRunningEl) tabBadgeRunningEl.classList.toggle("hidden", !running);

  [
    { box: replayProgressBox, bar: replayProgressBar, msg: replayStatusMessage },
    { box: scriptProgressBox, bar: scriptProgressBar, msg: scriptStatusMessage },
  ].forEach(({ box, bar, msg }) => {
    if (!box) return;
    if (running || message) {
      box.classList.remove("hidden");
    }
    if (msg && message !== undefined) {
      msg.textContent = message;
    }
    if (bar && typeof percent === "number") {
      bar.style.width = `${percent}%`;
    }
  });
}

function hideRunProgressBoxesIfIdle() {
  if (!isReplaying) {
    replayProgressBox.classList.add("hidden");
    if (scriptProgressBox) scriptProgressBox.classList.add("hidden");
  }
}

tabMonitorBtn.addEventListener("click", () => activateTab("monitor"));
tabScriptsBtn.addEventListener("click", () => activateTab("scripts"));

if (tablistEl) {
  tablistEl.addEventListener("keydown", (e) => {
    const tabs = [tabMonitorBtn, tabScriptsBtn];
    const currentIdx = tabs.indexOf(document.activeElement);
    if (currentIdx === -1) return;
    let nextIdx = currentIdx;
    if (e.key === "ArrowRight") nextIdx = (currentIdx + 1) % tabs.length;
    else if (e.key === "ArrowLeft") nextIdx = (currentIdx - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") nextIdx = 0;
    else if (e.key === "End") nextIdx = tabs.length - 1;
    else return;
    e.preventDefault();
    activateTab(tabs[nextIdx].dataset.tab, { focusTab: true });
  });
}

window.addEventListener("hashchange", () => {
  activateTab(normalizeTabFromHash(window.location.hash), { updateHash: false });
});

// DOM Elements
const brokerIndicator = document.getElementById("broker-indicator");
const brokerStatusText = document.getElementById("broker-status-text");
const brokerAddrText = document.getElementById("broker-addr-text");
const statOnlineCount = document.getElementById("stat-online-count");
const statTotalCount = document.getElementById("stat-total-count");
const deviceListEl = document.getElementById("device-list");
const filterDeviceEl = document.getElementById("filter-device");
const filterEventEl = document.getElementById("filter-event");
const checkHideNoiseEl = document.getElementById("check-hide-noise");
const notificationsTbody = document.getElementById("notifications-tbody");
const selectAllCheckbox = document.getElementById("select-all-notifications");
const selectTargetDeviceEl = document.getElementById("select-target-device");
const inputReplaySpeed = document.getElementById("input-replay-speed");
const btnReplaySelected = document.getElementById("btn-replay-selected");
const btnSaveAsScript = document.getElementById("btn-save-as-script");
const btnCancelReplay = document.getElementById("btn-cancel-replay");

// Live Device Recording DOM Elements
const recordingBarEl = document.getElementById("recording-bar");
const recordingIdleControls = document.getElementById("recording-idle-controls");
const recordingActiveControls = document.getElementById("recording-active-controls");
const selectRecordDeviceEl = document.getElementById("select-record-device");
const checkRecordIncludeTxEl = document.getElementById("check-record-include-tx");
const btnStartRecording = document.getElementById("btn-start-recording");
const recDeviceIdEl = document.getElementById("rec-device-id");
const recElapsedTimerEl = document.getElementById("rec-elapsed-timer");
const recStepCountEl = document.getElementById("rec-step-count");
const btnStopSaveRecording = document.getElementById("btn-stop-save-recording");
const btnStopEditRecording = document.getElementById("btn-stop-edit-recording");
const btnCancelRecording = document.getElementById("btn-cancel-recording");

let recordingState = { active: false, device_id: null, started_at: null, step_count: 0, steps: [] };
let recordingTimerInterval = null;

// Action Scripts DOM Elements
const scriptsListEl = document.getElementById("scripts-list");
const scriptTargetCheckboxesEl = document.getElementById("script-target-checkboxes");
const btnNewScript = document.getElementById("btn-new-script");
const btnImportScript = document.getElementById("btn-import-script");
const inputImportScript = document.getElementById("input-import-script");
const inputInterScriptDelay = document.getElementById("input-inter-script-delay");
const btnRunScriptSequence = document.getElementById("btn-run-script-sequence");
const scriptSequenceCountEl = document.getElementById("script-sequence-count");

// Script Editor Modal Elements
const scriptEditorModal = document.getElementById("script-editor-modal");
const scriptEditorTitle = document.getElementById("script-editor-title");
const editorScriptName = document.getElementById("editor-script-name");
const editorStepsContainer = document.getElementById("editor-steps-container");
const btnAddStep = document.getElementById("btn-add-step");
const btnCloseScriptEditor = document.getElementById("btn-close-script-editor");
const btnCancelScriptEditor = document.getElementById("btn-cancel-script-editor");
const btnSaveScriptEditor = document.getElementById("btn-save-script-editor");

let savedScripts = [];
let selectedScriptTargets = new Set();
let selectedScriptSequence = [];
let editorSteps = [];
const selectedCountEl = document.getElementById("selected-count");
const replayProgressBox = document.getElementById("replay-progress-box");
const replayProgressBar = document.getElementById("replay-progress-bar");
const replayStatusMessage = document.getElementById("replay-status-message");
const btnClearHistory = document.getElementById("btn-clear-history");

// Modal Elements
const settingsModal = document.getElementById("settings-modal");
const btnOpenSettings = document.getElementById("btn-open-settings");
const btnCloseSettings = document.getElementById("btn-close-settings");
const btnCancelSettings = document.getElementById("btn-cancel-settings");
const formSettings = document.getElementById("form-settings");
const settingHost = document.getElementById("setting-host");
const settingPort = document.getElementById("setting-port");
const settingUsername = document.getElementById("setting-username");
const settingPassword = document.getElementById("setting-password");

// Formatting helpers
function formatHex(val, width = 2) {
  if (val === null || val === undefined) return "-";
  return `0x${val.toString(16).toUpperCase().padStart(width, '0')} (${val})`;
}

function formatTime(isoStr) {
  if (!isoStr) return "-";
  const d = new Date(isoStr);
  return d.toLocaleTimeString([], { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

// REST API calls
async function fetchDevices() {
  try {
    const res = await fetch("/api/devices");
    if (!res.ok) return;
    const data = await res.json();
    renderDevices(data.devices, data.total_count, data.online_count);
    updateBrokerStatus(data.broker_connected, data.broker_host, data.broker_port);
  } catch (err) {
    console.error("Failed to fetch devices:", err);
  }
}

async function fetchNotifications() {
  try {
    const params = new URLSearchParams();
    if (activeFilterDeviceId) params.append("device_id", activeFilterDeviceId);
    if (activeFilterEvent) params.append("event_type", activeFilterEvent);
    params.append("hide_noise", hideNoise ? "true" : "false");
    params.append("limit", "200");

    const res = await fetch(`/api/notifications?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    const totalCount = typeof data.total === "number" ? data.total : (data.notifications || []).length;
    if (tabBadgeMonitorEl) tabBadgeMonitorEl.textContent = String(totalCount);
    renderNotifications(data.notifications);
  } catch (err) {
    console.error("Failed to fetch notifications:", err);
  }
}

function updateBrokerStatus(connected, host, port) {
  if (connected) {
    brokerIndicator.classList.add("connected");
    brokerStatusText.textContent = "Connected";
  } else {
    brokerIndicator.classList.remove("connected");
    brokerStatusText.textContent = "Disconnected";
  }
  if (host && port) {
    brokerAddrText.textContent = `${host}:${port}`;
  }
}

function renderDevices(devices, total, online) {
  registeredDevices = devices || [];
  statTotalCount.textContent = `Total: ${total}`;
  statOnlineCount.textContent = `Online: ${online}`;

  // Target device selector options
  const prevTarget = selectTargetDeviceEl.value;
  const prevRecordDev = selectRecordDeviceEl.value;
  selectTargetDeviceEl.innerHTML = '<option value="">-- Choose Target --</option>';
  selectRecordDeviceEl.innerHTML = '<option value="">-- Choose Input Device --</option>';
  filterDeviceEl.innerHTML = '<option value="">All Devices</option>';

  devices.forEach(dev => {
    // Replay target selector (show online status)
    const optTarget = document.createElement("option");
    optTarget.value = dev.device_id;
    optTarget.textContent = `${dev.device_id} ${dev.online ? "(Online)" : "(Offline)"}`;
    selectTargetDeviceEl.appendChild(optTarget);

    // Record input device selector
    const optRec = document.createElement("option");
    optRec.value = dev.device_id;
    optRec.textContent = `${dev.device_id} ${dev.online ? "(Online)" : "(Offline)"}`;
    selectRecordDeviceEl.appendChild(optRec);

    // Filter selector
    const optFilter = document.createElement("option");
    optFilter.value = dev.device_id;
    optFilter.textContent = dev.device_id;
    filterDeviceEl.appendChild(optFilter);
  });

  if (prevTarget && devices.some(d => d.device_id === prevTarget)) {
    selectTargetDeviceEl.value = prevTarget;
  } else {
    // Default to first online device
    const firstOnline = devices.find(d => d.online === 1);
    if (firstOnline) selectTargetDeviceEl.value = firstOnline.device_id;
  }

  if (prevRecordDev && devices.some(d => d.device_id === prevRecordDev)) {
    selectRecordDeviceEl.value = prevRecordDev;
  } else {
    const firstOnline = devices.find(d => d.online === 1);
    if (firstOnline) selectRecordDeviceEl.value = firstOnline.device_id;
  }
  btnStartRecording.disabled = !selectRecordDeviceEl.value;

  filterDeviceEl.value = activeFilterDeviceId;

  // Render multi-device checkboxes for Action Scripts
  renderScriptTargetCheckboxes(devices);

  // Render left sidebar list
  if (devices.length === 0) {
    deviceListEl.innerHTML = '<li class="empty-state">No devices registered yet</li>';
    return;
  }

  deviceListEl.innerHTML = "";
  devices.forEach(dev => {
    const li = document.createElement("li");
    li.className = `device-item ${dev.device_id === activeFilterDeviceId ? "active" : ""}`;
    li.innerHTML = `
      <div class="device-item-top">
        <span class="device-id">${dev.device_id}</span>
        <span class="badge ${dev.online ? 'badge-online' : 'badge-offline'}">${dev.online ? 'ONLINE' : 'OFFLINE'}</span>
      </div>
      <div class="device-item-meta">
        <span>Seen: ${formatTime(dev.last_seen)}</span>
        <span>Msgs: ${dev.notification_count}</span>
      </div>
    `;
    li.onclick = () => {
      if (activeFilterDeviceId === dev.device_id) {
        activeFilterDeviceId = "";
      } else {
        activeFilterDeviceId = dev.device_id;
      }
      filterDeviceEl.value = activeFilterDeviceId;
      fetchDevices();
      fetchNotifications();
    };
    deviceListEl.appendChild(li);
  });
}

function renderNotifications(notifications) {
  if (!notifications || notifications.length === 0) {
    notificationsTbody.innerHTML = '<tr><td colspan="9" class="empty-state">No notifications recorded</td></tr>';
    updateSelectionState();
    return;
  }

  notificationsTbody.innerHTML = "";
  notifications.forEach(notif => {
    const tr = document.createElement("tr");
    const isChecked = selectedNotificationIds.has(notif.id);
    const hasIr = notif.prot !== null && notif.addr !== null && notif.cmd !== null;

    tr.innerHTML = `
      <td>
        <input type="checkbox" class="notif-select" data-id="${notif.id}" ${isChecked ? 'checked' : ''}>
      </td>
      <td class="font-mono">${formatTime(notif.timestamp)}</td>
      <td class="font-mono"><strong>${notif.device_id}</strong></td>
      <td><span class="badge badge-event-${notif.event_type}">${notif.event_type}</span></td>
      <td>${notif.prot_label || (notif.prot !== null ? notif.prot : '-')}</td>
      <td class="font-mono">${formatHex(notif.addr, 4)}</td>
      <td class="font-mono">${formatHex(notif.cmd, 2)}</td>
      <td class="font-mono">${notif.flags !== null ? '0x' + notif.flags.toString(16).toUpperCase() : '-'}</td>
      <td>
        ${hasIr ? `<button class="btn btn-secondary btn-replay-single" data-id="${notif.id}">Replay</button>` : '-'}
      </td>
    `;
    notificationsTbody.appendChild(tr);
  });

  // Attach row event listeners
  document.querySelectorAll(".notif-select").forEach(cb => {
    cb.addEventListener("change", (e) => {
      const id = parseInt(e.target.dataset.id, 10);
      if (e.target.checked) selectedNotificationIds.add(id);
      else selectedNotificationIds.delete(id);
      updateSelectionState();
    });
  });

  document.querySelectorAll(".btn-replay-single").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      const id = parseInt(e.target.dataset.id, 10);
      const targetDev = selectTargetDeviceEl.value;
      if (!targetDev) {
        alert("Please choose a target device first.");
        return;
      }
      try {
        const res = await fetch("/api/replay/single", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ notification_id: id, target_device_id: targetDev }),
        });
        const resData = await res.json();
        if (!res.ok) alert(`Replay error: ${resData.detail || 'Unknown'}`);
      } catch (err) {
        alert(`Replay request failed: ${err}`);
      }
    });
  });

  updateSelectionState();
}

function updateSelectionState() {
  const count = selectedNotificationIds.size;
  selectedCountEl.textContent = count;
  btnReplaySelected.disabled = count === 0 || !selectTargetDeviceEl.value;
  btnSaveAsScript.disabled = count === 0;

  const visibleCheckboxes = document.querySelectorAll(".notif-select");
  if (visibleCheckboxes.length > 0) {
    const allChecked = Array.from(visibleCheckboxes).every(cb => cb.checked);
    selectAllCheckbox.checked = allChecked;
  } else {
    selectAllCheckbox.checked = false;
  }
}

// Event Listeners for Filters
filterDeviceEl.addEventListener("change", (e) => {
  activeFilterDeviceId = e.target.value;
  fetchDevices();
  fetchNotifications();
});

filterEventEl.addEventListener("change", (e) => {
  activeFilterEvent = e.target.value;
  fetchNotifications();
});

checkHideNoiseEl.addEventListener("change", (e) => {
  hideNoise = e.target.checked;
  fetchNotifications();
});

selectAllCheckbox.addEventListener("change", (e) => {
  const isChecked = e.target.checked;
  document.querySelectorAll(".notif-select").forEach(cb => {
    cb.checked = isChecked;
    const id = parseInt(cb.dataset.id, 10);
    if (isChecked) selectedNotificationIds.add(id);
    else selectedNotificationIds.delete(id);
  });
  updateSelectionState();
});

selectTargetDeviceEl.addEventListener("change", () => {
  updateSelectionState();
});

btnClearHistory.addEventListener("click", async () => {
  const msg = activeFilterDeviceId
    ? `Clear history for device "${activeFilterDeviceId}"?`
    : "Clear all notification history?";
  if (!confirm(msg)) return;

  const url = activeFilterDeviceId
    ? `/api/notifications?device_id=${encodeURIComponent(activeFilterDeviceId)}`
    : "/api/notifications";
  await fetch(url, { method: "DELETE" });
  selectedNotificationIds.clear();
  fetchDevices();
  fetchNotifications();
});

// Replay Sequence Actions
btnReplaySelected.addEventListener("click", async () => {
  const targetDev = selectTargetDeviceEl.value;
  if (!targetDev) {
    alert("Please choose a target device.");
    return;
  }
  const ids = Array.from(selectedNotificationIds);
  const speed = parseFloat(inputReplaySpeed.value) || 1.0;

  try {
    const res = await fetch("/api/replay/sequence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notification_ids: ids,
        target_device_id: targetDev,
        speed: speed,
        max_delay_ms: 5000,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Replay error: ${err.detail || 'Failed'}`);
      return;
    }
    setRunProgressState({ running: true, message: "Replay started...", percent: 0 });
  } catch (err) {
    alert(`Replay failed: ${err}`);
  }
});

async function cancelActiveReplayOrScript() {
  await fetch("/api/replay/cancel", { method: "POST" });
  btnCancelReplay.disabled = true;
  if (btnStopScriptRun) btnStopScriptRun.disabled = true;
  replayStatusMessage.textContent = "Cancelling replay...";
  if (scriptStatusMessage) scriptStatusMessage.textContent = "Cancelling...";
}

btnCancelReplay.addEventListener("click", cancelActiveReplayOrScript);
if (btnStopScriptRun) {
  btnStopScriptRun.addEventListener("click", cancelActiveReplayOrScript);
}

// Settings Modal
btnOpenSettings.addEventListener("click", async () => {
  const res = await fetch("/api/settings/mqtt");
  if (res.ok) {
    const data = await res.json();
    settingHost.value = data.host;
    settingPort.value = data.port;
    settingUsername.value = data.username || "";
    settingPassword.value = "";
  }
  settingsModal.classList.remove("hidden");
});

function closeSettings() {
  settingsModal.classList.add("hidden");
}
btnCloseSettings.addEventListener("click", closeSettings);
btnCancelSettings.addEventListener("click", closeSettings);

formSettings.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    host: settingHost.value.trim(),
    port: parseInt(settingPort.value, 10),
    username: settingUsername.value.trim() || null,
    password: settingPassword.value || null,
  };
  try {
    const res = await fetch("/api/settings/mqtt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      closeSettings();
      fetchDevices();
    } else {
      alert("Failed to update MQTT settings.");
    }
  } catch (err) {
    alert("Error updating settings: " + err);
  }
});

// WebSocket Real-time Updates
let ws;
function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

  ws.onopen = () => {
    console.log("WebSocket connected.");
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleWsMessage(msg);
    } catch (err) {
      console.error("Error processing WS message:", err);
    }
  };

  ws.onclose = () => {
    console.log("WebSocket disconnected. Retrying in 2s...");
    setTimeout(connectWebSocket, 2000);
  };
}

function handleWsMessage(msg) {
  if (msg.type === "broker_status") {
    updateBrokerStatus(msg.data.connected, msg.data.host, msg.data.port);
  } else if (msg.type === "device_update") {
    fetchDevices();
  } else if (msg.type === "notification") {
    fetchDevices();
    fetchNotifications();
  } else if (msg.type === "history_cleared") {
    fetchDevices();
    fetchNotifications();
  } else if (msg.type === "recording_status") {
    applyRecordingStatus(msg.data);
  } else if (msg.type === "replay_status" || msg.type === "script_status") {
    const data = msg.data;
    const isFinished = data.status === "completed" || data.status === "cancelled";
    const pct = data.total && data.total > 0 ? Math.round((data.current / data.total) * 100) : undefined;
    setRunProgressState({
      running: !isFinished,
      message: data.message || data.status,
      percent: pct,
    });

    if (isFinished) {
      setTimeout(hideRunProgressBoxesIfIdle, 3000);
    }
  }
}

// ---------------------------------------------------------------------------
// Action Scripts: List, Save from Notifications, Visual Editor, Import/Export
// ---------------------------------------------------------------------------

function renderScriptTargetCheckboxes(devices) {
  if (!devices || devices.length === 0) {
    scriptTargetCheckboxesEl.innerHTML = '<span class="empty-inline">No devices registered</span>';
    return;
  }
  const existingIds = new Set(devices.map((d) => d.device_id));
  for (const id of Array.from(selectedScriptTargets)) {
    if (!existingIds.has(id)) selectedScriptTargets.delete(id);
  }
  if (selectedScriptTargets.size === 0) {
    devices.filter((d) => d.online === 1).forEach((d) => selectedScriptTargets.add(d.device_id));
  }

  scriptTargetCheckboxesEl.innerHTML = "";
  devices.forEach((dev) => {
    const label = document.createElement("label");
    const checked = selectedScriptTargets.has(dev.device_id) ? "checked" : "";
    label.innerHTML = `
      <input type="checkbox" class="script-target-cb" value="${dev.device_id}" ${checked}>
      <span>${dev.device_id} ${dev.online ? "(Online)" : "(Offline)"}</span>
    `;
    const cb = label.querySelector("input");
    cb.addEventListener("change", (e) => {
      if (e.target.checked) selectedScriptTargets.add(dev.device_id);
      else selectedScriptTargets.delete(dev.device_id);
    });
    scriptTargetCheckboxesEl.appendChild(label);
  });
}

async function fetchScripts() {
  try {
    const res = await fetch("/api/scripts");
    if (!res.ok) return;
    const data = await res.json();
    savedScripts = data.scripts || [];
    renderScripts();
  } catch (err) {
    console.error("Failed to fetch scripts:", err);
  }
}

function updateScriptSequenceControls() {
  const existingNames = new Set(savedScripts.map((s) => s.name));
  selectedScriptSequence = selectedScriptSequence.filter((name) => existingNames.has(name));
  scriptSequenceCountEl.textContent = selectedScriptSequence.length;
  btnRunScriptSequence.disabled = selectedScriptSequence.length === 0;
}

function renderScripts() {
  updateScriptSequenceControls();
  if (tabBadgeScriptsEl) tabBadgeScriptsEl.textContent = String((savedScripts || []).length);
  if (!savedScripts || savedScripts.length === 0) {
    scriptsListEl.innerHTML = '<li class="empty-state">No action scripts saved yet</li>';
    return;
  }
  scriptsListEl.innerHTML = "";
  savedScripts.forEach((script) => {
    const seqIndex = selectedScriptSequence.indexOf(script.name);
    const isSelected = seqIndex !== -1;
    const li = document.createElement("li");
    li.className = "script-item";
    li.innerHTML = `
      <div class="script-item-left">
        <input type="checkbox" class="script-seq-cb" data-name="${script.name}" ${isSelected ? "checked" : ""}>
        ${isSelected ? `<span class="script-seq-badge">#${seqIndex + 1}</span>` : ""}
        <div class="script-item-info">
          <span class="script-item-name">${script.name}</span>
          <span class="script-item-meta">${script.step_count} step(s) &middot; Updated ${formatTime(script.updated_at)}</span>
        </div>
      </div>
      <div class="script-item-actions">
        <button class="btn btn-primary btn-run-script" data-name="${script.name}">▶ Run</button>
        <button class="btn btn-secondary btn-edit-script" data-name="${script.name}">Edit</button>
        <button class="btn btn-secondary btn-export-script" data-name="${script.name}">Export JSON</button>
        <button class="btn btn-outline-danger btn-delete-script" data-name="${script.name}">Delete</button>
      </div>
    `;
    scriptsListEl.appendChild(li);
  });

  document.querySelectorAll(".script-seq-cb").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      const name = e.target.dataset.name;
      if (e.target.checked) {
        if (!selectedScriptSequence.includes(name)) selectedScriptSequence.push(name);
      } else {
        selectedScriptSequence = selectedScriptSequence.filter((item) => item !== name);
      }
      renderScripts();
    });
  });

  document.querySelectorAll(".btn-run-script").forEach((btn) => {
    btn.addEventListener("click", () => runSavedScript(btn.dataset.name));
  });
  document.querySelectorAll(".btn-edit-script").forEach((btn) => {
    btn.addEventListener("click", () => {
      const s = savedScripts.find((item) => item.name === btn.dataset.name);
      if (s) openScriptEditor(s.name, s.steps);
    });
  });
  document.querySelectorAll(".btn-export-script").forEach((btn) => {
    btn.addEventListener("click", () => {
      const s = savedScripts.find((item) => item.name === btn.dataset.name);
      if (!s) return;
      const blob = new Blob([JSON.stringify({ name: s.name, steps: s.steps }, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${s.name}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  });
  document.querySelectorAll(".btn-delete-script").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.dataset.name;
      if (!confirm(`Delete action script "${name}"?`)) return;
      const res = await fetch(`/api/scripts/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (res.ok) fetchScripts();
    });
  });
}

async function runSavedScript(name) {
  const targets = Array.from(selectedScriptTargets);
  if (targets.length === 0) {
    alert("Please select at least one target device in the Action Scripts panel.");
    return;
  }
  const speed = parseFloat(inputReplaySpeed.value) || 1.0;
  try {
    const res = await fetch(`/api/scripts/${encodeURIComponent(name)}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_device_ids: targets, speed: speed }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Run script error: ${err.detail || "Failed"}`);
      return;
    }
    setRunProgressState({
      running: true,
      message: `Running script '${name}' on ${targets.length} device(s)...`,
      percent: 0,
    });
  } catch (err) {
    alert(`Run script failed: ${err}`);
  }
}

async function saveScriptPayload(name, steps, overwrite = false) {
  const res = await fetch("/api/scripts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, steps, overwrite }),
  });
  if (res.status === 409 && !overwrite) {
    if (confirm(`Script "${name}" already exists. Overwrite it?`)) {
      return saveScriptPayload(name, steps, true);
    }
    return false;
  }
  if (!res.ok) {
    const err = await res.json();
    alert(`Save script error: ${err.detail || "Failed"}`);
    return false;
  }
  await fetchScripts();
  return true;
}

btnSaveAsScript.addEventListener("click", async () => {
  const ids = Array.from(selectedNotificationIds);
  if (ids.length === 0) return;
  const name = prompt("Enter a name for this Action Script:");
  if (!name || !name.trim()) return;

  async function submitFromNotifications(overwrite) {
    const res = await fetch("/api/scripts/from-notifications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name.trim(),
        notification_ids: ids,
        overwrite,
      }),
    });
    if (res.status === 409 && !overwrite) {
      if (confirm(`Script "${name.trim()}" already exists. Overwrite it?`)) {
        return submitFromNotifications(true);
      }
      return;
    }
    if (!res.ok) {
      const err = await res.json();
      alert(`Save as Script error: ${err.detail || "Failed"}`);
      return;
    }
    await fetchScripts();
    activateTab("scripts");
  }

  await submitFromNotifications(false);
});

// Visual Script Editor
function defaultStepForType(type) {
  if (type === "sendircode") return { type: "sendircode", prot: 8, addr: 0, cmd: 0, flags: 0, delay_ms: 0 };
  if (type === "digital") return { type: "digital", pin: 5, value: 1, delay_ms: 0 };
  if (type === "delay") return { type: "delay", delay_ms: 500 };
  if (type === "run_script") {
    return { type: "run_script", script_name: savedScripts[0]?.name || "", delay_ms: 0 };
  }
  return { type, delay_ms: 0 };
}

function openScriptEditor(name = "", steps = []) {
  activateTab("scripts");
  scriptEditorTitle.textContent = name ? `Edit Script: ${name}` : "New Action Script";
  editorScriptName.value = name;
  editorSteps = steps.length > 0 ? steps.map((s) => ({ ...s })) : [defaultStepForType("sendircode")];
  renderEditorSteps();
  scriptEditorModal.classList.remove("hidden");
}

function closeScriptEditor() {
  scriptEditorModal.classList.add("hidden");
}

function renderEditorSteps() {
  editorStepsContainer.innerHTML = "";
  editorSteps.forEach((step, idx) => {
    const row = document.createElement("div");
    row.className = "step-row";

    let fieldsHtml = "";
    if (step.type === "sendircode") {
      fieldsHtml = `
        <label>prot <input type="number" class="form-control step-small-input" data-field="prot" value="${step.prot ?? 8}"></label>
        <label>addr <input type="number" class="form-control step-small-input" data-field="addr" value="${step.addr ?? 0}"></label>
        <label>cmd <input type="number" class="form-control step-small-input" data-field="cmd" value="${step.cmd ?? 0}"></label>
        <label>flags <input type="number" class="form-control step-small-input" data-field="flags" value="${step.flags ?? 0}"></label>
      `;
    } else if (step.type === "digital") {
      fieldsHtml = `
        <label>pin <input type="number" class="form-control step-small-input" data-field="pin" value="${step.pin ?? 5}"></label>
        <label>val <select class="form-control" data-field="value">
          <option value="0" ${step.value === 0 ? "selected" : ""}>0 (LOW)</option>
          <option value="1" ${step.value !== 0 ? "selected" : ""}>1 (HIGH)</option>
        </select></label>
      `;
    } else if (step.type === "run_script") {
      const scriptNames = savedScripts.map((s) => s.name);
      if (step.script_name && !scriptNames.includes(step.script_name)) {
        scriptNames.unshift(step.script_name);
      }
      const optionsHtml = scriptNames
        .map((n) => `<option value="${n}" ${step.script_name === n ? "selected" : ""}>${n}</option>`)
        .join("");
      fieldsHtml = `
        <label>script <select class="form-control" data-field="script_name">
          <option value="">-- Choose Script --</option>
          ${optionsHtml}
        </select></label>
      `;
    }

    row.innerHTML = `
      <span class="step-num">#${idx + 1}</span>
      <select class="form-control step-type-select">
        <option value="sendircode" ${step.type === "sendircode" ? "selected" : ""}>sendircode</option>
        <option value="digital" ${step.type === "digital" ? "selected" : ""}>digital</option>
        <option value="clearkeydetail" ${step.type === "clearkeydetail" ? "selected" : ""}>clearkeydetail</option>
        <option value="replaylast" ${step.type === "replaylast" ? "selected" : ""}>replaylast</option>
        <option value="delay" ${step.type === "delay" ? "selected" : ""}>delay</option>
        <option value="run_script" ${step.type === "run_script" ? "selected" : ""}>run_script</option>
      </select>
      <div class="step-fields">
        ${fieldsHtml}
        <label>delay_ms <input type="number" min="0" class="form-control step-small-input" data-field="delay_ms" value="${step.delay_ms ?? 0}"></label>
      </div>
      <div class="step-actions">
        <button type="button" class="btn btn-secondary btn-step-up" ${idx === 0 ? "disabled" : ""}>↑</button>
        <button type="button" class="btn btn-secondary btn-step-down" ${idx === editorSteps.length - 1 ? "disabled" : ""}>↓</button>
        <button type="button" class="btn btn-outline-danger btn-step-del">✕</button>
      </div>
    `;

    row.querySelector(".step-type-select").addEventListener("change", (e) => {
      editorSteps[idx] = defaultStepForType(e.target.value);
      renderEditorSteps();
    });
    row.querySelectorAll("[data-field]").forEach((input) => {
      input.addEventListener("change", (e) => {
        const field = e.target.dataset.field;
        if (field === "script_name") {
          editorSteps[idx][field] = e.target.value;
        } else {
          editorSteps[idx][field] = parseInt(e.target.value, 10) || 0;
        }
      });
    });
    row.querySelector(".btn-step-up").addEventListener("click", () => {
      if (idx > 0) {
        [editorSteps[idx - 1], editorSteps[idx]] = [editorSteps[idx], editorSteps[idx - 1]];
        renderEditorSteps();
      }
    });
    row.querySelector(".btn-step-down").addEventListener("click", () => {
      if (idx < editorSteps.length - 1) {
        [editorSteps[idx + 1], editorSteps[idx]] = [editorSteps[idx], editorSteps[idx + 1]];
        renderEditorSteps();
      }
    });
    row.querySelector(".btn-step-del").addEventListener("click", () => {
      editorSteps.splice(idx, 1);
      renderEditorSteps();
    });

    editorStepsContainer.appendChild(row);
  });
}

btnNewScript.addEventListener("click", () => openScriptEditor("", []));
btnRunScriptSequence.addEventListener("click", async () => {
  if (selectedScriptSequence.length === 0) return;
  const targets = Array.from(selectedScriptTargets);
  if (targets.length === 0) {
    alert("Please select at least one target device in the Action Scripts panel.");
    return;
  }
  const speed = parseFloat(inputReplaySpeed.value) || 1.0;
  const interDelay = Math.max(0, parseInt(inputInterScriptDelay.value, 10) || 0);
  try {
    const res = await fetch("/api/scripts/run-sequence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_names: selectedScriptSequence,
        target_device_ids: targets,
        speed,
        inter_script_delay_ms: interDelay,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Run sequence error: ${data.detail || "Failed"}`);
      return;
    }
    setRunProgressState({
      running: true,
      message: `Running ${selectedScriptSequence.length} script(s) on ${targets.length} device(s)...`,
      percent: 0,
    });
  } catch (err) {
    alert(`Run sequence failed: ${err}`);
  }
});
btnCloseScriptEditor.addEventListener("click", closeScriptEditor);
btnCancelScriptEditor.addEventListener("click", closeScriptEditor);
btnAddStep.addEventListener("click", () => {
  editorSteps.push(defaultStepForType("sendircode"));
  renderEditorSteps();
});
btnSaveScriptEditor.addEventListener("click", async () => {
  const name = editorScriptName.value.trim();
  if (!name) {
    alert("Please enter a script name.");
    return;
  }
  const ok = await saveScriptPayload(name, editorSteps, false);
  if (ok) closeScriptEditor();
});

btnImportScript.addEventListener("click", () => inputImportScript.click());
inputImportScript.addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const name = (parsed.name || file.name.replace(/\.json$/i, "")).trim();
    const steps = Array.isArray(parsed.steps) ? parsed.steps : [];
    await saveScriptPayload(name, steps, false);
  } catch (err) {
    alert(`Invalid JSON file: ${err}`);
  } finally {
    inputImportScript.value = "";
  }
});

// ---------------------------------------------------------------------------
// Live Device Recording
// ---------------------------------------------------------------------------

function formatElapsed(ms) {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const mins = String(Math.floor(totalSec / 60)).padStart(2, "0");
  const secs = String(totalSec % 60).padStart(2, "0");
  return `${mins}:${secs}`;
}

function updateRecordingTimer() {
  if (!recordingState.active || !recordingState.started_at) {
    recElapsedTimerEl.textContent = "00:00";
    return;
  }
  const startedMs = Date.parse(recordingState.started_at);
  const elapsed = Number.isNaN(startedMs) ? 0 : Math.max(0, Date.now() - startedMs);
  recElapsedTimerEl.textContent = formatElapsed(elapsed);
}

function applyRecordingStatus(status) {
  recordingState = status || { active: false, device_id: null, started_at: null, step_count: 0, steps: [] };
  if (recordingState.active) {
    recordingBarEl.classList.add("is-recording");
    recordingIdleControls.classList.add("hidden");
    recordingActiveControls.classList.remove("hidden");
    if (tabBadgeRecordingEl) tabBadgeRecordingEl.classList.remove("hidden");
    recDeviceIdEl.textContent = recordingState.device_id || "-";
    recStepCountEl.textContent = `${recordingState.step_count || 0} step(s)`;
    updateRecordingTimer();
    if (!recordingTimerInterval) {
      recordingTimerInterval = setInterval(updateRecordingTimer, 1000);
    }
  } else {
    recordingBarEl.classList.remove("is-recording");
    recordingIdleControls.classList.remove("hidden");
    recordingActiveControls.classList.add("hidden");
    if (tabBadgeRecordingEl) tabBadgeRecordingEl.classList.add("hidden");
    if (recordingTimerInterval) {
      clearInterval(recordingTimerInterval);
      recordingTimerInterval = null;
    }
    recElapsedTimerEl.textContent = "00:00";
  }
}

async function fetchRecordingStatus() {
  try {
    const res = await fetch("/api/recording/status");
    if (!res.ok) return;
    const data = await res.json();
    applyRecordingStatus(data);
  } catch (err) {
    console.error("Failed to fetch recording status:", err);
  }
}

selectRecordDeviceEl.addEventListener("change", () => {
  btnStartRecording.disabled = !selectRecordDeviceEl.value;
});

btnStartRecording.addEventListener("click", async () => {
  const deviceId = selectRecordDeviceEl.value;
  if (!deviceId) {
    alert("Please choose an input device to record.");
    return;
  }
  try {
    const res = await fetch("/api/recording/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        device_id: deviceId,
        include_tx: checkRecordIncludeTxEl.checked,
        hide_noise: hideNoise,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Start recording error: ${data.detail || "Failed"}`);
      return;
    }
    applyRecordingStatus(data);
  } catch (err) {
    alert(`Start recording failed: ${err}`);
  }
});

btnStopSaveRecording.addEventListener("click", async () => {
  const name = prompt("Enter a name for the recorded Action Script:");
  if (!name || !name.trim()) return;

  async function submitStopSave(overwrite) {
    const res = await fetch("/api/recording/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name.trim(),
        overwrite,
        save: true,
      }),
    });
    if (res.status === 409 && !overwrite) {
      if (confirm(`Script "${name.trim()}" already exists. Overwrite it?`)) {
        return submitStopSave(true);
      }
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      alert(`Stop & Save error: ${data.detail || "Failed"}`);
      return;
    }
    await fetchRecordingStatus();
    await fetchScripts();
    activateTab("scripts");
  }

  try {
    await submitStopSave(false);
  } catch (err) {
    alert(`Stop & Save failed: ${err}`);
  }
});

btnStopEditRecording.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/recording/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ save: false }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Stop & Edit error: ${data.detail || "Failed"}`);
      return;
    }
    await fetchRecordingStatus();
    openScriptEditor("", data.steps || []);
  } catch (err) {
    alert(`Stop & Edit failed: ${err}`);
  }
});

btnCancelRecording.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/recording/cancel", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      applyRecordingStatus(data);
    }
  } catch (err) {
    alert(`Cancel recording failed: ${err}`);
  }
});

// Initial bootstrap
activateTab(normalizeTabFromHash(window.location.hash));
fetchDevices();
fetchNotifications();
fetchScripts();
fetchRecordingStatus();
connectWebSocket();
