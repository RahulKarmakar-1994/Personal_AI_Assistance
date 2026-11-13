const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = { currentId: null, busy: false, conversations: [], view: "dashboard", actionDrafts: new Map(), pendingAction: null, createdActions: new Map(), editingTaskId: null };
const el = { list: $("#conversationList"), messages: $("#messages"), form: $("#chatForm"), input: $("#messageInput"), send: $("#sendButton"), title: $("#chatTitle"), model: $("#modelName"), sidebar: $("#sidebar"), scrim: $("#scrim"), statusDot: $("#statusDot"), statusText: $("#statusText"), settings: $("#settingsDialog"), modelInput: $("#modelInput"), providerInput: $("#providerInput"), groqModelInput: $("#groqModelInput"), groqHelp: $("#groqHelp"), toast: $("#toast"), itemDialog: $("#itemDialog"), itemForm: $("#itemForm") };

async function api(path, options = {}) {
  if (window.MobileStore?.isNative()) {
    const response = await window.MobileStore.route(path, options);
    if (!response.ok) { const error = await response.json(); throw new Error(error.error || `Request failed (${response.status})`); }
    return response.status === 204 ? null : response.json();
  }
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) { let error = { error: `Request failed (${response.status})` }; try { error = await response.json(); } catch (_) {} throw new Error(error.error || "Something went wrong."); }
  return response.status === 204 ? null : response.json();
}
function esc(value = "") { const node = document.createElement("div"); node.textContent = value; return node.innerHTML; }
function showToast(message) { el.toast.textContent = message; el.toast.classList.add("show"); clearTimeout(showToast.timer); showToast.timer = setTimeout(() => el.toast.classList.remove("show"), 2600); }
function closeSidebar() { el.sidebar.classList.remove("open"); el.scrim.classList.remove("show"); }
function localDate() { return new Date().toLocaleDateString("en-CA"); }
function formatDateTime(value) { return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "No due date"; }
function formatTime(value) { return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
function formatInr(value) { return value == null ? "Not available" : `₹${Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }

function setView(view) {
  state.view = view;
  const ids = { dashboard: "dashboardView", chat: "messages", calendar: "calendarView", notes: "notesView", expenses: "expensesView", finance: "financeView", habits: "habitsView", discover: "discoverView" };
  Object.values(ids).forEach(id => $("#" + id).classList.add("hidden")); $("#" + ids[view]).classList.remove("hidden");
  $("#composerWrap").classList.toggle("hidden", view !== "chat");
  $$(".nav-item").forEach(x => x.classList.toggle("active", x.dataset.view === view));
  el.title.textContent = ({ dashboard: "Today", chat: "Assistant", calendar: "Calendar", notes: "Notes", expenses: "Expenses", finance: "Finance", habits: "Habits", discover: "Discover" })[view]; closeSidebar();
  if (view === "dashboard") loadDashboard(); if (view === "calendar") loadEvents(); if (view === "notes") loadNotes(); if (view === "expenses") loadExpenses(); if (view === "finance") loadFinance(); if (view === "habits") loadHabits(); if (view === "discover") loadDiscover();
}
function renderConversations() { el.list.innerHTML = state.conversations.map(item => `<button class="conversation ${item.id === state.currentId ? "active" : ""}" data-id="${item.id}"><span>◫ &nbsp;${esc(item.title)}</span><b class="delete-chat" data-delete="${item.id}" title="Delete">×</b></button>`).join("") || `<p style="opacity:.48;font-size:12px;padding:8px">Your conversations will appear here.</p>`; }
function renderMessage(role, content, thinking = false) { $("#welcome")?.remove(); const item = document.createElement("article"); item.className = `message ${role}`; item.innerHTML = `<div class="avatar">${role === "assistant" ? "N" : "You"}</div><div class="bubble ${thinking ? "thinking" : ""}">${esc(content)}</div>`; el.messages.append(item); el.messages.scrollTop = el.messages.scrollHeight; return item; }
function renderActionDraft(command) {
  $("#welcome")?.remove(); const id = `draft-${Date.now()}-${Math.random().toString(36).slice(2,7)}`; const labels = { task: "Task or reminder", event: "Calendar event", note: "Note", expense: "Expense", payment: "Payment reminder", habit: "Habit" }; const type = command.type || command.action;
  const item = document.createElement("article"); item.className = "message assistant"; item.innerHTML = `<div class="avatar">S</div><div class="bubble action-draft"><span class="draft-label">${esc(labels[type])}</span><strong>I understood this as a ${esc(labels[type].toLowerCase())}.</strong><p>${esc(command.text || command.fields?.title || command.fields?.name || "")}</p><small>Review the detected fields before saving.</small><div class="draft-actions"><button class="primary" data-review-draft="${id}">Review details</button><button class="secondary" data-cancel-draft="${id}">Cancel</button></div></div>`; state.actionDrafts.set(id, { ...command, type, item }); el.messages.append(item); el.messages.scrollTop = el.messages.scrollHeight;
}
function renderCreatedTask(task) {
  $("#welcome")?.remove(); const key = `task-${task.id}`; const item = document.createElement("article"); item.className = "message assistant"; item.innerHTML = `<div class="avatar">S</div><div class="bubble action-draft"><span class="draft-label">Reminder created</span><strong>${esc(task.title)}</strong><p>${esc(formatDateTime(task.due_at))}</p><div class="draft-actions"><button class="secondary" data-edit-created="${key}">Edit</button><button class="secondary" data-undo-created="${key}">Undo</button></div></div>`; state.createdActions.set(key, { type: "task", record: task, item }); el.messages.append(item); el.messages.scrollTop = el.messages.scrollHeight;
}
function welcomeMarkup() { return `<div class="welcome" id="welcome"><div class="welcome-mark">N</div><p class="eyebrow">PERSONAL · USEFUL · YOURS</p><h1>What can I help you with?</h1><p>Plan your day, work through an idea, or draft something clearly.</p><div class="suggestions"><button data-prompt="Help me plan my priorities for today."><b>Plan my day</b><span>Turn a busy list into a clear plan</span></button><button data-prompt="Help me think through an important decision."><b>Think it through</b><span>Compare options and tradeoffs</span></button><button data-prompt="Draft a short, professional message for me."><b>Draft a message</b><span>Start with a clear structure</span></button></div></div>`; }

async function loadList() { state.conversations = await api("/api/conversations"); renderConversations(); }
async function createChat() { const item = await api("/api/conversations", { method: "POST", body: "{}" }); state.currentId = item.id; el.messages.innerHTML = welcomeMarkup(); await loadList(); setView("chat"); el.input.focus(); }
async function openChat(id) { const chat = await api(`/api/conversations/${id}`); state.currentId = id; el.messages.innerHTML = chat.messages.length ? "" : welcomeMarkup(); chat.messages.forEach(m => renderMessage(m.role, m.content)); renderConversations(); setView("chat"); }
async function deleteChat(id) { if (!confirm("Delete this conversation?")) return; await api(`/api/conversations/${id}`, { method: "DELETE" }); if (state.currentId === id) state.currentId = null; await loadList(); }
async function sendMessage(content) {
  if (!content.trim() || state.busy) return; const value = content.trim(); if (!state.currentId) await createChat(); el.input.value = ""; resizeInput(); renderMessage("user", value); if (state.pendingAction && /^(cancel|never\s*mind|stop)$/i.test(value)) { state.pendingAction = null; renderMessage("assistant", "Okay, I cancelled that draft."); return; } state.busy = true; el.send.disabled = true; const thinking = renderMessage("assistant", "Understanding…", true);
  try {
    const now = new Date(); const localNow = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0,19);
    const interpreted = await api("/api/assistant/interpret", { method: "POST", body: JSON.stringify({ content: value, pending: state.pendingAction, local_now: localNow, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }) });
    thinking.remove();
    if (interpreted.kind === "action") {
      if (interpreted.missing_fields?.length) { state.pendingAction = interpreted; renderMessage("assistant", interpreted.question || "Could you provide the missing detail?"); return; }
      state.pendingAction = null;
      if (interpreted.action === "task") {
        const fields = interpreted.fields; await requestNotificationPermission(); const task = await api("/api/tasks", { method: "POST", body: JSON.stringify({ title: fields.title, details: fields.details || "", due_at: fields.due_at, priority: fields.priority || "normal", reminder: fields.reminder !== false }) }); renderCreatedTask(task); loadDashboard(); return;
      }
      renderActionDraft(interpreted); return;
    }
    if (state.pendingAction) { renderMessage("assistant", "I could not understand that detail. " + (state.pendingAction.question || "Please try again.")); return; }
    const fallback = window.SmritiVoice?.parseCommand(value);
    if (interpreted.kind !== "chat" && fallback) { renderActionDraft(fallback); return; }
    const pending = renderMessage("assistant", "Thinking", true); const reply = await api(`/api/conversations/${state.currentId}/messages`, { method: "POST", body: JSON.stringify({ content: value, local_date: localDate() }) }); pending.remove(); renderMessage("assistant", reply.content); await loadList();
  }
  catch (error) { thinking.remove(); renderMessage("assistant", `I couldn't answer: ${error.message}`); }
  finally { state.busy = false; el.send.disabled = false; el.input.focus(); }
}
function resizeInput() { el.input.style.height = "auto"; el.input.style.height = `${Math.min(el.input.scrollHeight, 140)}px`; }

async function loadDashboard() {
  const data = await api(`/api/dashboard?date=${localDate()}`); const hour = new Date().getHours(); $("#greeting").textContent = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening"; $("#todayDate").textContent = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  $("#todayTasks").innerHTML = data.tasks.map(t => `<div class="list-item"><input type="checkbox" data-complete-task="${t.id}" /><div class="item-copy"><strong>${esc(t.title)}</strong><small>${t.due_at ? formatDateTime(t.due_at) : "Any time"}</small>${t.priority === "high" ? '<div class="priority-high">Important</div>' : ""}</div><button class="item-delete" data-delete-task="${t.id}">×</button></div>`).join("") || `<div class="empty-state">Nothing urgent. Add a task when something comes up.</div>`;
  $("#todayEvents").innerHTML = data.events.map(e => `<div class="list-item"><div class="item-copy"><strong>${esc(e.title)}</strong><small>${formatTime(e.starts_at)}${e.details ? " · " + esc(e.details) : ""}</small></div><button class="item-delete" data-delete-event="${e.id}">×</button></div>`).join("") || `<div class="empty-state">No events scheduled today.</div>`;
  $("#pinnedNotes").innerHTML = data.pinned_notes.map(n => `<div class="mini-note"><strong>${esc(n.title)}</strong><p>${esc(n.content)}</p></div>`).join("") || `<div class="empty-state">Pin important notes to see them here.</div>`;
  $("#duePayments").innerHTML = (data.payments || []).map(p => `<div class="list-item"><div class="item-copy"><strong>${esc(p.title)}</strong><small>${p.amount ? `₹${Number(p.amount).toLocaleString("en-IN")} · ` : ""}${formatDateTime(p.due_at)}</small></div><button class="primary mini-action" data-paid-payment="${p.id}">Paid</button></div>`).join("") || `<div class="empty-state">No payments are due.</div>`;
  $("#todayHabits").innerHTML = (data.habits || []).map(h => `<div class="list-item"><div class="item-copy"><strong>${esc(h.name)}</strong><small>${h.today_count}/${h.target} today</small></div><button class="habit-check ${h.today_count >= h.target ? "done" : ""}" data-habit-id="${h.id}" data-habit-count="${h.today_count}">${h.today_count >= h.target ? "✓" : "+"}</button></div>`).join("") || `<div class="empty-state">Add a small habit you want to repeat.</div>`;
}
async function loadEvents() { const data = await api("/api/events?start=0000-01-01&end=9999-12-31"); const native = window.MobileStore?.isNative(); $("#allEvents").innerHTML = data.map(e => { const d = new Date(e.starts_at); return `<article class="collection-card"><div class="date-tile"><strong>${d.getDate()}</strong><span>${d.toLocaleDateString([], { month: "short" }).toUpperCase()}</span></div><div class="collection-copy"><h3>${esc(e.title)}</h3><p>${formatDateTime(e.starts_at)}${e.ends_at ? " – " + formatDateTime(e.ends_at) : ""}</p><p>${esc(e.details)}</p></div>${native ? `<button class="primary mini-action" data-phone-calendar="${e.id}" data-event-title="${esc(e.title)}" data-event-details="${esc(e.details)}" data-event-start="${e.starts_at}" data-event-end="${e.ends_at || ""}">Phone calendar</button>` : ""}<button class="item-delete" data-delete-event="${e.id}">×</button></article>`; }).join("") || `<div class="empty-state">Your calendar is clear.</div>`; }
async function loadNotes() { const data = await api("/api/notes"); $("#allNotes").innerHTML = data.map(n => `<article class="collection-card"><div class="collection-copy"><h3>${n.pinned ? '<span class="pin">◆</span> ' : ""}${esc(n.title)}</h3><p>${esc(n.content)}</p><small>${new Date(n.updated_at).toLocaleDateString()}</small></div><button class="item-delete" data-delete-note="${n.id}">×</button></article>`).join("") || `<div class="empty-state">Capture your first note.</div>`; }
async function loadExpenses() {
  const month = localDate().slice(0,7); const data = await api(`/api/expenses?month=${month}`); const total = data.reduce((sum, x) => sum + Number(x.amount), 0);
  $("#expenseSummary").textContent = `₹${total.toLocaleString("en-IN", { maximumFractionDigits: 2 })} recorded this month`;
  $("#allExpenses").innerHTML = data.map(x => `<article class="collection-card"><div class="date-tile"><strong>₹</strong><span>${esc(x.category).toUpperCase()}</span></div><div class="collection-copy"><h3>₹${Number(x.amount).toLocaleString("en-IN")}</h3><p>${esc(x.description || x.category)} · ${esc(x.payment_method || "")}</p><small>${new Date(x.spent_at + "T00:00:00").toLocaleDateString()}</small></div><button class="item-delete" data-delete-expense="${x.id}">×</button></article>`).join("") || `<div class="empty-state">No expenses recorded this month.</div>`;
}
async function loadHabits() {
  const data = await api(`/api/habits?day=${localDate()}`);
  $("#allHabits").innerHTML = data.map(h => `<article class="collection-card"><div class="habit-ring ${h.today_count >= h.target ? "done" : ""}">${h.today_count >= h.target ? "✓" : h.today_count}</div><div class="collection-copy"><h3>${esc(h.name)}</h3><p>${h.today_count} of ${h.target} · ${esc(h.frequency)}</p></div><button class="habit-check" data-habit-id="${h.id}" data-habit-count="${h.today_count}">＋</button><button class="item-delete" data-delete-habit="${h.id}">×</button></article>`).join("") || `<div class="empty-state">Start with one habit that matters to you.</div>`;
}

async function loadFinance() {
  if (window.MobileStore?.isNative()) {
    $("#financeSummary").innerHTML = '<div class="finance-notice">Finance is currently available in the Mac-hosted version. Android finance storage and synchronization remain disabled until the financial engine passes reconciliation tests.</div>';
    $("#financeHoldings").innerHTML = ""; $("#financeImports").innerHTML = ""; return;
  }
  const [portfolio, imports] = await Promise.all([api(`/api/finance/portfolio?date=${localDate()}`), api("/api/finance/imports")]);
  $("#financeValuation").textContent = `Valuation date ${portfolio.valuation_date} · calculation ${portfolio.calculation_version}`;
  $("#financeSummary").innerHTML = [
    ["Invested cost", formatInr(portfolio.total_cost)], ["Portfolio value", formatInr(portfolio.market_value)],
    ["Unrealized gain", formatInr(portfolio.unrealized_gain)], ["Portfolio XIRR", portfolio.xirr_percent == null ? "Not available" : `${portfolio.xirr_percent}%`]
  ].map(([label, value]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong></article>`).join("");
  $("#financeWarning").textContent = portfolio.warnings.join(" ");
  $("#financeHoldings").innerHTML = portfolio.holdings.map(item => `<tr><td><strong>${esc(item.name)}</strong><small>${esc([item.plan, item.option_name].filter(x => x && x !== "unknown").join(" · "))}${item.plan !== "unknown" ? " · " : ""}AMFI ${esc(item.amfi_code || "unmatched")} · NAV ${esc(item.nav_date || "missing")}</small></td><td>${esc(item.units)}</td><td>${formatInr(item.cost)}</td><td>${item.nav == null ? "—" : esc(item.nav)}</td><td>${item.market_value == null ? "—" : formatInr(item.market_value)}</td><td class="${Number(item.unrealized_gain) < 0 ? "negative" : "positive"}">${item.unrealized_gain == null ? "—" : formatInr(item.unrealized_gain)}</td></tr>`).join("") || '<tr><td colspan="6" class="empty-state">No holdings yet. Add a transaction or import the CSV template.</td></tr>';
  $("#financeImports").innerHTML = imports.map(item => `<div class="list-item"><div class="item-copy"><strong>${esc(item.file_name)}</strong><small>${esc(item.status)} · ${item.accepted_count} imported · ${item.rejected_count} rejected · ${new Date(item.imported_at).toLocaleString()}</small></div></div>`).join("") || '<div class="empty-state">No statement imports yet.</div>';
}

async function importFinanceCsv(file) {
  const form = new FormData(); form.append("file", file);
  const response = await fetch("/api/finance/import", { method: "POST", body: form });
  let result = {}; try { result = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(result.error || `Import failed (${response.status})`);
  if (result.duplicate) showToast("This exact file was already imported");
  else showToast(`${result.accepted_count} transactions imported${result.rejected_count ? `; ${result.rejected_count} rejected` : ""}`);
  await loadFinance();
}

async function saveFinanceTransaction() {
  await api("/api/finance/transactions", { method: "POST", body: JSON.stringify({
    scheme_name: $("#mfSchemeName").value.trim(), amfi_code: $("#mfAmfiCode").value.trim(), folio: $("#mfFolio").value.trim(),
    transaction_date: $("#mfTransactionDate").value, transaction_type: $("#mfTransactionType").value,
    amount: $("#mfAmount").value.trim(), units: $("#mfUnits").value.trim(), nav: $("#mfNav").value.trim(), description: $("#mfDescription").value.trim()
  }) });
  $("#financeTransactionDialog").close(); showToast("Transaction saved"); await loadFinance();
}

function readingDate(value) {
  if (!value) return "Date unavailable"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}
async function loadDiscover() {
  if (window.MobileStore?.isNative()) { $("#discoverItems").innerHTML = '<div class="empty-state">The research reader is currently available through the Mac-hosted version. Phone-local synchronization is planned.</div>'; return; }
  const query = encodeURIComponent($("#discoverQuery").value.trim()); const view = encodeURIComponent($("#discoverViewFilter").value); const payload = await api(`/api/discover?query=${query}&view=${view}`); const data = payload.items || [];
  const unread = data.filter(x => !x.read).length; const refreshed = payload.last_refresh ? ` · refreshed ${new Date(payload.last_refresh).toLocaleString()}` : ""; $("#discoverSummary").textContent = `${data.length} updates · ${unread} unread${refreshed}`;
  $("#discoverItems").innerHTML = data.map(item => `<article class="reading-card ${item.read ? "read" : ""}"><div class="reading-meta"><span>${esc(item.topic)}</span><span>${esc(item.source)}</span><time>${esc(readingDate(item.published_at))}</time></div><h2>${esc(item.title)}</h2><p>${esc(item.summary || "Open the source to read this update.")}</p><div class="reading-actions"><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">Read source ↗</a><button data-read-article="${item.id}" data-read-state="${item.read ? 1 : 0}">${item.read ? "Mark unread" : "Mark read"}</button><button data-save-article="${item.id}" data-save-state="${item.saved ? 1 : 0}">${item.saved ? "★ Saved" : "☆ Save"}</button></div></article>`).join("") || '<div class="empty-state">No updates yet. Select “Refresh sources” to collect the latest AI and science items.</div>';
}
async function refreshDiscover() {
  if (window.MobileStore?.isNative()) return showToast("Source refresh is currently available through the Mac version.");
  $("#refreshDiscover").disabled = true; $("#discoverSummary").textContent = "Refreshing official sources…";
  try { const result = await api("/api/discover/refresh", { method: "POST", body: "{}" }); showToast(result.errors?.length ? `Fetched ${result.fetched} items; some sources failed` : `Fetched ${result.fetched} source items`); await loadDiscover(); }
  catch (error) { showToast(error.message); $("#discoverSummary").textContent = "Refresh failed. Existing saved updates remain available."; }
  finally { $("#refreshDiscover").disabled = false; }
}

function openItemDialog(type) {
  state.editingTaskId = null; el.itemForm.reset(); $("#itemType").value = type; $("#itemTitle").value = ""; $("#itemDetails").value = ""; $$(".conditional-fields").forEach(x => x.classList.add("hidden")); $("#" + type + "Fields").classList.remove("hidden");
  $("#itemHeading").textContent = ({ task: "Add task", event: "Add event", note: "New note", expense: "Record expense", payment: "Payment reminder", habit: "New habit" })[type];
  if (type === "event") $("#eventStart").value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0,16); el.itemDialog.showModal(); setTimeout(() => $("#itemTitle").focus(), 50);
  if (type === "expense") $("#expenseDate").value = localDate();
  if (type === "payment") $("#paymentDue").value = new Date(Date.now() + 86400000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0,16);
}
async function saveItem() {
  const type = $("#itemType").value; const base = { title: $("#itemTitle").value.trim(), details: $("#itemDetails").value.trim() }; if (!base.title) throw new Error("Please enter a title.");
  if (type === "task") {
    const wantsReminder = $("#taskReminder").checked;
    if (wantsReminder && "Notification" in window && Notification.permission === "default") await Notification.requestPermission();
    const taskPayload = { ...base, due_at: $("#taskDue").value || null, priority: $("#taskPriority").value, reminder: wantsReminder };
    if (state.editingTaskId) await api(`/api/tasks/${state.editingTaskId}`, { method: "PUT", body: JSON.stringify(taskPayload) });
    else await api("/api/tasks", { method: "POST", body: JSON.stringify(taskPayload) });
    state.editingTaskId = null;
  }
  if (type === "event") { if (!$("#eventStart").value) throw new Error("Please select a start time."); await api("/api/events", { method: "POST", body: JSON.stringify({ ...base, starts_at: $("#eventStart").value, ends_at: $("#eventEnd").value || null, reminder_minutes: Number($("#eventReminder").value) }) }); }
  if (type === "note") await api("/api/notes", { method: "POST", body: JSON.stringify({ title: base.title, content: base.details, pinned: $("#notePinned").checked }) });
  if (type === "expense") { if (!$("#expenseAmount").value) throw new Error("Please enter the amount."); await api("/api/expenses", { method: "POST", body: JSON.stringify({ amount: Number($("#expenseAmount").value), category: $("#expenseCategory").value, spent_at: $("#expenseDate").value, payment_method: $("#expenseMethod").value, description: base.details || base.title }) }); }
  if (type === "payment") { if (!$("#paymentDue").value) throw new Error("Please select a due time."); await requestNotificationPermission(); await api("/api/payments", { method: "POST", body: JSON.stringify({ title: base.title, notes: base.details, amount: $("#paymentAmount").value || null, due_at: $("#paymentDue").value, recurrence: $("#paymentRepeat").value }) }); }
  if (type === "habit") await api("/api/habits", { method: "POST", body: JSON.stringify({ name: base.title, frequency: $("#habitFrequency").value, target: Number($("#habitTarget").value) }) });
  el.itemDialog.close(); showToast(`${type[0].toUpperCase() + type.slice(1)} saved`); if (state.view === "dashboard") loadDashboard(); if (state.view === "calendar") loadEvents(); if (state.view === "notes") loadNotes(); if (state.view === "expenses") loadExpenses(); if (state.view === "habits") loadHabits();
}

async function requestNotificationPermission() {
  if (window.MobileStore?.isNative()) { try { await window.Capacitor.Plugins.LocalNotifications.requestPermissions(); } catch (_) {} }
  else if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission();
}

async function takeVoiceNote() {
  showToast("Listening…"); let text = "";
  try {
    const speech = window.Capacitor?.Plugins?.SpeechRecognition;
    if (speech) { await speech.requestPermissions(); const result = await speech.start({ language: "en-IN", maxResults: 1, partialResults: false, popup: true }); text = result.matches?.[0] || ""; }
    else {
      const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition; if (!Recognition) throw new Error("Voice recognition is unavailable on this device.");
      text = await new Promise((resolve, reject) => { const recognition = new Recognition(); recognition.lang = "en-IN"; recognition.onresult = e => resolve(e.results[0][0].transcript); recognition.onerror = () => reject(new Error("Voice recognition failed.")); recognition.start(); });
    }
    openItemDialog("note"); $("#itemTitle").value = "Voice note"; $("#itemDetails").value = text;
  } catch (error) { showToast(error.message || "Voice note failed."); }
}

async function addToPhoneCalendar(button) {
  try {
    const calendar = window.Capacitor?.Plugins?.CapacitorCalendar; if (!calendar) throw new Error("Phone calendar integration is unavailable.");
    const start = new Date(button.dataset.eventStart).getTime(); const end = button.dataset.eventEnd ? new Date(button.dataset.eventEnd).getTime() : start + 3600000;
    await calendar.createEventWithPrompt({ title: button.dataset.eventTitle, description: button.dataset.eventDetails || "", startDate: start, endDate: end });
  } catch (error) { showToast(error.message || "Could not open the phone calendar."); }
}

async function checkReminders() {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try {
    const [tasks, events] = await Promise.all([api("/api/tasks"), api("/api/events?start=0000-01-01&end=9999-12-31")]);
    const now = Date.now(); const sent = JSON.parse(localStorage.getItem("smriti-notified") || "{}");
    for (const task of tasks) {
      if (!task.reminder || task.completed || !task.due_at) continue;
      const due = new Date(task.due_at).getTime(); const key = `task-${task.id}-${task.due_at}`;
      if (now >= due && now - due < 3600000 && !sent[key]) { new Notification("Task due", { body: task.title, icon: "/static/icon.svg" }); sent[key] = now; }
    }
    for (const event of events) {
      const start = new Date(event.starts_at).getTime(); const alertAt = start - event.reminder_minutes * 60000; const key = `event-${event.id}-${event.starts_at}`;
      if (now >= alertAt && now < start + 60000 && !sent[key]) { new Notification("Upcoming event", { body: `${event.title} · ${formatTime(event.starts_at)}`, icon: "/static/icon.svg" }); sent[key] = now; }
    }
    localStorage.setItem("smriti-notified", JSON.stringify(sent));
  } catch (_) {}
}

async function checkStatus() { try { const data = await api("/api/status"); el.model.textContent = `${data.provider === "groq" ? "Groq" : "Local"} · ${data.model}`; el.statusDot.classList.toggle("online", data.online); el.statusText.textContent = data.online ? `${data.provider === "groq" ? "Groq" : "Ollama"} connected` : `${data.provider === "groq" ? "Groq not configured" : "Ollama offline"}`; } catch (_) { el.statusText.textContent = "Connection unavailable"; } }
async function openSettings() { const data = await api("/api/settings"); el.modelInput.value = data.model; el.providerInput.value = data.provider; el.groqModelInput.value = data.groq_model; el.groqHelp.innerHTML = data.groq_ready ? "Cloud AI connection is configured." : "Groq requires secure cloud configuration."; $("#mobileCloudSettings").classList.toggle("hidden", !window.MobileStore?.isNative()); $("#cloudEndpointInput").value = data.cloud_endpoint || ""; $("#appTokenInput").value = data.app_token || ""; el.settings.showModal(); }

el.form.addEventListener("submit", e => { e.preventDefault(); sendMessage(el.input.value); }); el.input.addEventListener("input", resizeInput); el.input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); el.form.requestSubmit(); } });
$("#newChat").addEventListener("click", createChat); el.list.addEventListener("click", e => { const del = e.target.dataset.delete; if (del) { e.stopPropagation(); deleteChat(Number(del)); return; } const row = e.target.closest("[data-id]"); if (row) openChat(Number(row.dataset.id)); }); el.messages.addEventListener("click", e => { const prompt = e.target.closest("[data-prompt]")?.dataset.prompt; if (prompt) sendMessage(prompt); });
$$(".nav-item").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
document.body.addEventListener("click", async e => {
  const closeButton = e.target.closest("[data-dialog-close]");
  if (closeButton) { state.editingTaskId = null; closeButton.closest("dialog")?.close(); return; }
  const add = e.target.closest("[data-add]")?.dataset.add; if (add) return openItemDialog(add);
  const reviewDraft = e.target.closest("[data-review-draft]")?.dataset.reviewDraft;
  if (reviewDraft) { const draft = state.actionDrafts.get(reviewDraft); if (draft) draft.fields ? window.SmritiVoice?.fillStructured(draft) : window.SmritiVoice?.fillAs(draft.text, draft.type); return; }
  const cancelDraft = e.target.closest("[data-cancel-draft]")?.dataset.cancelDraft;
  if (cancelDraft) { const draft = state.actionDrafts.get(cancelDraft); if (draft) { draft.item.querySelector(".bubble").innerHTML = "<strong>Draft cancelled.</strong>"; state.actionDrafts.delete(cancelDraft); } return; }
  const readArticle = e.target.closest("[data-read-article]");
  if (readArticle) { await api(`/api/discover/${readArticle.dataset.readArticle}`, { method: "PUT", body: JSON.stringify({ read: readArticle.dataset.readState !== "1", day: localDate() }) }); await loadDiscover(); if (state.view === "habits") loadHabits(); return; }
  const saveArticle = e.target.closest("[data-save-article]");
  if (saveArticle) { await api(`/api/discover/${saveArticle.dataset.saveArticle}`, { method: "PUT", body: JSON.stringify({ saved: saveArticle.dataset.saveState !== "1" }) }); await loadDiscover(); return; }
  const undoCreated = e.target.closest("[data-undo-created]")?.dataset.undoCreated;
  if (undoCreated) { const created = state.createdActions.get(undoCreated); if (created?.type === "task") { await api(`/api/tasks/${created.record.id}`, { method: "DELETE" }); created.item.querySelector(".bubble").innerHTML = "<strong>Reminder removed.</strong>"; state.createdActions.delete(undoCreated); loadDashboard(); } return; }
  const editCreated = e.target.closest("[data-edit-created]")?.dataset.editCreated;
  if (editCreated) { const created = state.createdActions.get(editCreated); if (created?.type === "task") { const task = created.record; window.SmritiVoice?.fillStructured({ action: "task", fields: { title: task.title, details: task.details, due_at: task.due_at, priority: task.priority, reminder: Boolean(task.reminder) } }); state.editingTaskId = task.id; } return; }
  if (e.target.dataset.completeTask) { await api(`/api/tasks/${e.target.dataset.completeTask}`, { method: "PUT", body: JSON.stringify({ completed: true }) }); loadDashboard(); }
  if (e.target.dataset.deleteTask && confirm("Delete this task?")) { await api(`/api/tasks/${e.target.dataset.deleteTask}`, { method: "DELETE" }); loadDashboard(); }
  if (e.target.dataset.deleteEvent && confirm("Delete this event?")) { await api(`/api/events/${e.target.dataset.deleteEvent}`, { method: "DELETE" }); state.view === "calendar" ? loadEvents() : loadDashboard(); }
  if (e.target.dataset.deleteNote && confirm("Delete this note?")) { await api(`/api/notes/${e.target.dataset.deleteNote}`, { method: "DELETE" }); loadNotes(); }
  if (e.target.dataset.deleteExpense && confirm("Delete this expense?")) { await api(`/api/expenses/${e.target.dataset.deleteExpense}`, { method: "DELETE" }); loadExpenses(); }
  if (e.target.dataset.paidPayment) { await api(`/api/payments/${e.target.dataset.paidPayment}`, { method: "PUT", body: JSON.stringify({ paid: true }) }); loadDashboard(); showToast("Payment marked paid"); }
  if (e.target.dataset.habitId) { const count = Number(e.target.dataset.habitCount || 0) + 1; await api(`/api/habits/${e.target.dataset.habitId}`, { method: "PUT", body: JSON.stringify({ day: localDate(), count }) }); state.view === "habits" ? loadHabits() : loadDashboard(); }
  if (e.target.dataset.deleteHabit && confirm("Delete this habit?")) { await api(`/api/habits/${e.target.dataset.deleteHabit}`, { method: "DELETE" }); loadHabits(); }
  if (e.target.dataset.phoneCalendar) await addToPhoneCalendar(e.target);
});
$("#planDay").addEventListener("click", () => { setView("chat"); sendMessage("Help me plan today. Ask me what matters, then make a short prioritized plan."); }); $("#openSidebar").addEventListener("click", () => { el.sidebar.classList.add("open"); el.scrim.classList.add("show"); }); $("#closeSidebar").addEventListener("click", closeSidebar); el.scrim.addEventListener("click", closeSidebar); $("#openSettings").addEventListener("click", openSettings);
$("#settingsForm").addEventListener("submit", async e => { if (e.submitter?.value === "cancel") return; e.preventDefault(); try { await api("/api/settings", { method: "PUT", body: JSON.stringify({ model: el.modelInput.value, provider: el.providerInput.value, groq_model: el.groqModelInput.value, cloud_endpoint: $("#cloudEndpointInput").value, app_token: $("#appTokenInput").value }) }); el.settings.close(); showToast("Settings saved"); checkStatus(); } catch (error) { showToast(error.message); } });
$("#refreshDiscover").addEventListener("click", refreshDiscover);
$("#discoverSearch").addEventListener("submit", e => { e.preventDefault(); loadDiscover(); });
$("#discoverViewFilter").addEventListener("change", loadDiscover);
$("#createReadingHabit").addEventListener("click", async () => { if (window.MobileStore?.isNative()) return showToast("Create this habit from Habits on the phone."); await api("/api/discover/reading-habit", { method: "POST", body: "{}" }); showToast("Daily reading habit is ready"); if (state.view === "habits") loadHabits(); });
$("#financeAddButton").addEventListener("click", () => { if (window.MobileStore?.isNative()) return showToast("Finance entry is currently available through the Mac version."); $("#financeTransactionForm").reset(); $("#mfTransactionDate").value = localDate(); $("#financeTransactionDialog").showModal(); });
$("#financeImportButton").addEventListener("click", () => { if (window.MobileStore?.isNative()) return showToast("Finance import is currently available through the Mac version."); $("#financeImportFile").click(); });
$("#financeImportFile").addEventListener("change", async e => { const file = e.target.files?.[0]; if (!file) return; try { await importFinanceCsv(file); } catch (error) { showToast(error.message); } finally { e.target.value = ""; } });
$("#financeRefreshNav").addEventListener("click", async e => { if (window.MobileStore?.isNative()) return showToast("NAV refresh is currently available through the Mac version."); e.target.disabled = true; try { const result = await api("/api/finance/nav/refresh", { method: "POST", body: "{}" }); showToast(`${result.tracked_updated} tracked schemes updated`); await loadFinance(); } catch (error) { showToast(error.message); } finally { e.target.disabled = false; } });
$("#financeTransactionForm").addEventListener("submit", async e => { if (e.submitter?.value === "cancel") return; e.preventDefault(); try { await saveFinanceTransaction(); } catch (error) { showToast(error.message); } });
el.itemForm.addEventListener("submit", async e => { if (e.submitter?.value === "cancel") return; e.preventDefault(); try { await saveItem(); } catch (error) { showToast(error.message); } });
$("#themeToggle").addEventListener("click", () => { const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = next; localStorage.setItem("theme", next); });
document.documentElement.dataset.theme = localStorage.getItem("theme") || "light"; if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
(async function init() { try { await Promise.all([loadList(), checkStatus(), loadDashboard()]); if (state.conversations.length) state.currentId = state.conversations[0].id; renderConversations(); setView("dashboard"); checkReminders(); setInterval(checkReminders, 60000); } catch (error) { showToast(error.message); } })();
