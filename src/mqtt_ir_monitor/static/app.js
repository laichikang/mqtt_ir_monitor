// Frontend Dashboard JS for MQTT IR Monitor

let registeredDevices = [];
let selectedNotificationIds = new Set();
let activeFilterDeviceId = "";
let activeFilterEvent = "";
let hideNoise = true;
let isReplaying = false;

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
const btnCancelReplay = document.getElementById("btn-cancel-replay");
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
  selectTargetDeviceEl.innerHTML = '<option value="">-- Choose Target --</option>';
  filterDeviceEl.innerHTML = '<option value="">All Devices</option>';

  devices.forEach(dev => {
    // Replay target selector (show online status)
    const optTarget = document.createElement("option");
    optTarget.value = dev.device_id;
    optTarget.textContent = `${dev.device_id} ${dev.online ? "(Online)" : "(Offline)"}`;
    selectTargetDeviceEl.appendChild(optTarget);

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

  filterDeviceEl.value = activeFilterDeviceId;

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
    isReplaying = true;
    btnCancelReplay.disabled = false;
    replayProgressBox.classList.remove("hidden");
    replayProgressBar.style.width = "0%";
    replayStatusMessage.textContent = "Replay started...";
  } catch (err) {
    alert(`Replay failed: ${err}`);
  }
});

btnCancelReplay.addEventListener("click", async () => {
  await fetch("/api/replay/cancel", { method: "POST" });
  btnCancelReplay.disabled = true;
  replayStatusMessage.textContent = "Cancelling replay...";
});

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
  } else if (msg.type === "replay_status") {
    const data = msg.data;
    replayProgressBox.classList.remove("hidden");
    replayStatusMessage.textContent = data.message || data.status;

    if (data.total && data.total > 0) {
      const pct = Math.round((data.current / data.total) * 100);
      replayProgressBar.style.width = `${pct}%`;
    }

    if (data.status === "completed" || data.status === "cancelled") {
      isReplaying = false;
      btnCancelReplay.disabled = true;
      setTimeout(() => {
        if (!isReplaying) replayProgressBox.classList.add("hidden");
      }, 3000);
    }
  }
}

// Initial bootstrap
fetchDevices();
fetchNotifications();
connectWebSocket();
