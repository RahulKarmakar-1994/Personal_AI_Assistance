(function () {
  const native = () => Boolean(window.Capacitor?.isNativePlatform?.());
  const plugins = () => window.Capacitor?.Plugins || {};
  const blank = () => ({
    seq: 1, tasks: [], notes: [], events: [], expenses: [], payments: [], habits: [], habitLogs: [],
    conversations: [], messages: [], settings: {
      provider: "groq", model: "llama3.2:1b", groq_model: "qwen/qwen3.8-27b",
      cloud_endpoint: "", app_token: ""
    }
  });
  async function load() {
    try {
      const prefs = plugins().Preferences;
      const raw = prefs ? (await prefs.get({ key: "smriti-data" })).value : localStorage.getItem("smriti-data");
      return raw ? { ...blank(), ...JSON.parse(raw) } : blank();
    } catch (_) { return blank(); }
  }
  async function save(data) {
    const value = JSON.stringify(data); const prefs = plugins().Preferences;
    if (prefs) await prefs.set({ key: "smriti-data", value }); else localStorage.setItem("smriti-data", value);
  }
  const stamp = () => new Date().toISOString();
  const next = data => data.seq++;
  const bodyOf = options => { try { return JSON.parse(options?.body || "{}"); } catch (_) { return {}; } };
  function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, json: async () => data }; }
  async function notify(id, title, body, at) {
    const api = plugins().LocalNotifications;
    if (!api || !at || new Date(at).getTime() <= Date.now()) return;
    try {
      await api.schedule({ notifications: [{ id: Number(id) % 2147483647, title, body, schedule: { at: new Date(at), allowWhileIdle: true } }] });
    } catch (_) {}
  }
  async function notifyPayment(item) {
    const api = plugins().LocalNotifications; if (!api || !item.due_at) return;
    const due = new Date(item.due_at); if (due.getTime() <= Date.now()) return;
    let schedule = { at: due, allowWhileIdle: true };
    if (item.recurrence === "weekly") schedule = { on: { weekday: due.getDay() + 1, hour: due.getHours(), minute: due.getMinutes() }, allowWhileIdle: true };
    if (item.recurrence === "monthly") schedule = { on: { day: due.getDate(), hour: due.getHours(), minute: due.getMinutes() }, allowWhileIdle: true };
    if (item.recurrence === "yearly") schedule = { on: { month: due.getMonth() + 1, day: due.getDate(), hour: due.getHours(), minute: due.getMinutes() }, allowWhileIdle: true };
    try { await api.schedule({ notifications: [{ id: (300000 + item.id) % 2147483647, title: "Payment due", body: item.title, schedule }] }); } catch (_) {}
  }
  function advancePayment(item) {
    const nextDue = new Date(item.due_at);
    if (item.recurrence === "weekly") nextDue.setDate(nextDue.getDate() + 7);
    if (item.recurrence === "monthly") nextDue.setMonth(nextDue.getMonth() + 1);
    if (item.recurrence === "yearly") nextDue.setFullYear(nextDue.getFullYear() + 1);
    item.due_at = nextDue.toISOString().slice(0,16); item.paid = 0;
  }
  async function cancelNotification(id) {
    try { await plugins().LocalNotifications?.cancel({ notifications: [{ id: Number(id) % 2147483647 }] }); } catch (_) {}
  }
  async function cloudChat(data, conversationId, content) {
    const endpoint = data.settings.cloud_endpoint?.trim(); const token = data.settings.app_token?.trim();
    if (!endpoint || !token) return response({ error: "Configure the Smriti cloud endpoint and app token in Settings." }, 503);
    const history = data.messages.filter(x => x.conversation_id === conversationId).slice(-16).map(({ role, content }) => ({ role, content }));
    const messages = [{ role: "system", content: "You are Smriti, a concise and helpful personal assistant. Never claim an action succeeded unless the app confirms it." }, ...history, { role: "user", content }];
    let upstream;
    try { upstream = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` }, body: JSON.stringify({ messages }) }); }
    catch (_) { return response({ error: "Cloud AI is unavailable. Notes, reminders and tracking still work offline." }, 503); }
    const result = await upstream.json();
    if (!upstream.ok) return response({ error: result.error || "Cloud AI request failed." }, upstream.status);
    const answer = result.choices?.[0]?.message?.content || result.message?.content;
    if (!answer) return response({ error: "Cloud AI returned no answer." }, 502);
    const now = stamp();
    data.messages.push({ id: next(data), conversation_id: conversationId, role: "user", content, created_at: now });
    const reply = { id: next(data), conversation_id: conversationId, role: "assistant", content: answer, created_at: stamp() }; data.messages.push(reply);
    const chat = data.conversations.find(x => x.id === conversationId); if (chat) { chat.title = content.slice(0, 42); chat.updated_at = stamp(); }
    await save(data); return response(reply);
  }

  function isTodayOverview(content) {
    const text = String(content || "").toLowerCase();
    return /\b(today|this morning|this afternoon|this evening)\b/.test(text) &&
      /\b(what|which|tell|show|list|anything|any|plan|plans|planned|schedule|agenda|task|tasks|reminder|reminders|event|events|due|doing|have)\b/.test(text);
  }
  function todayOverview(data, day) {
    const tasks = data.tasks.filter(x => !x.completed && (!x.due_at || x.due_at.slice(0,10) <= day));
    const events = data.events.filter(x => x.starts_at?.slice(0,10) === day).sort((a,b) => a.starts_at.localeCompare(b.starts_at));
    const payments = data.payments.filter(x => !x.paid && x.due_at?.slice(0,10) <= day);
    const habits = data.habits.filter(x => x.active !== false).map(h => ({ ...h, today_count: data.habitLogs.find(l => l.habit_id === h.id && l.day === day)?.count || 0 }));
    if (![tasks, events, payments, habits].some(x => x.length)) return "Good morning. You don't have anything planned in Smriti for today yet.";
    const lines = ["Here’s what Smriti has for today:"];
    if (events.length) lines.push("Schedule: " + events.map(x => `${x.starts_at.slice(11,16)} — ${x.title}`).join("; "));
    if (tasks.length) lines.push("Tasks: " + tasks.map(x => `${x.title}${x.due_at?.slice(0,10) < day ? " (overdue)" : x.due_at ? ` at ${x.due_at.slice(11,16)}` : ""}`).join("; "));
    if (payments.length) lines.push("Payments due: " + payments.map(x => `${x.title}${x.amount != null ? ` ₹${x.amount}` : ""}${x.due_at.slice(0,10) < day ? " (overdue)" : ""}`).join("; "));
    const remaining = habits.filter(x => x.today_count < x.target);
    if (habits.length) lines.push(remaining.length ? "Habits remaining: " + remaining.map(x => `${x.name} (${x.today_count}/${x.target})`).join("; ") : "Habits: all completed for today.");
    return lines.join("\n");
  }

  async function route(path, options = {}) {
    const url = new URL(path, "https://smriti.local"); const method = (options.method || "GET").toUpperCase(); const body = bodyOf(options); const data = await load();
    const parts = url.pathname.split("/").filter(Boolean); const resource = parts[1]; const id = Number(parts[2]);
    if (url.pathname === "/api/status") return response({ online: Boolean(data.settings.cloud_endpoint && data.settings.app_token), provider: "groq", model: data.settings.groq_model, groq_ready: Boolean(data.settings.cloud_endpoint), ollama_online: false, models: [] });
    if (url.pathname === "/api/assistant/interpret") return response({ kind: "unavailable", source: "offline-fallback" });
    if (url.pathname === "/api/settings") {
      if (method === "GET") return response({ ...data.settings, groq_ready: Boolean(data.settings.cloud_endpoint) });
      data.settings = { ...data.settings, ...body }; await save(data); return response(data.settings);
    }
    if (url.pathname === "/api/dashboard") {
      const day = url.searchParams.get("date");
      return response({
        tasks: data.tasks.filter(x => !x.completed && (!x.due_at || x.due_at.slice(0,10) <= day)),
        events: data.events.filter(x => x.starts_at.slice(0,10) === day),
        pinned_notes: data.notes.filter(x => x.pinned).slice(0,4),
        payments: data.payments.filter(x => !x.paid && x.due_at.slice(0,10) <= day),
        habits: data.habits.filter(x => x.active !== false).map(h => ({ ...h, today_count: data.habitLogs.find(l => l.habit_id === h.id && l.day === day)?.count || 0 }))
      });
    }
    if (resource === "conversations") {
      if (parts.length === 2 && method === "GET") return response([...data.conversations].sort((a,b) => b.updated_at.localeCompare(a.updated_at)));
      if (parts.length === 2 && method === "POST") { const now = stamp(); const item = { id: next(data), title: "New conversation", created_at: now, updated_at: now }; data.conversations.push(item); await save(data); return response(item, 201); }
      if (parts[3] === "messages" && method === "POST") {
        if (!isTodayOverview(body.content)) return cloudChat(data, id, body.content || "");
        const now = stamp(); const day = body.local_date || now.slice(0,10); const answer = todayOverview(data, day);
        data.messages.push({ id: next(data), conversation_id: id, role: "user", content: body.content, created_at: now });
        const reply = { id: next(data), conversation_id: id, role: "assistant", content: answer, created_at: stamp() }; data.messages.push(reply);
        const chat = data.conversations.find(x => x.id === id); if (chat) { chat.title = body.content.slice(0,42); chat.updated_at = stamp(); }
        await save(data); return response(reply);
      }
      const chat = data.conversations.find(x => x.id === id);
      if (method === "GET") return chat ? response({ ...chat, messages: data.messages.filter(x => x.conversation_id === id) }) : response({ error: "Conversation not found." }, 404);
      if (method === "DELETE") { data.conversations = data.conversations.filter(x => x.id !== id); data.messages = data.messages.filter(x => x.conversation_id !== id); await save(data); return response(null, 204); }
    }
    const map = { tasks: "tasks", notes: "notes", events: "events", expenses: "expenses", payments: "payments", habits: "habits" }; const key = map[resource];
    if (!key) return response({ error: "Unknown route." }, 404);
    if (parts.length === 2 && method === "GET") {
      let list = [...data[key]];
      if (resource === "habits") { const day = url.searchParams.get("day"); list = list.filter(x => x.active !== false).map(h => ({ ...h, today_count: data.habitLogs.find(l => l.habit_id === h.id && l.day === day)?.count || 0 })); }
      if (resource === "expenses" && url.searchParams.get("month")) list = list.filter(x => x.spent_at.slice(0,7) === url.searchParams.get("month"));
      return response(list);
    }
    if (parts.length === 2 && method === "POST") {
      const now = stamp(); let item;
      if (resource === "tasks") { item = { id: next(data), title: body.title, details: body.details || "", due_at: body.due_at || null, priority: body.priority || "normal", completed: 0, reminder: body.reminder ? 1 : 0, created_at: now, updated_at: now }; if (item.reminder) await notify(100000 + item.id, "Task due", item.title, item.due_at); }
      if (resource === "notes") item = { id: next(data), title: body.title, content: body.content || "", pinned: body.pinned ? 1 : 0, created_at: now, updated_at: now };
      if (resource === "events") { item = { id: next(data), title: body.title, details: body.details || "", starts_at: body.starts_at, ends_at: body.ends_at || null, reminder_minutes: Number(body.reminder_minutes || 0), created_at: now, updated_at: now }; await notify(200000 + item.id, "Upcoming event", item.title, new Date(new Date(item.starts_at).getTime() - item.reminder_minutes * 60000).toISOString()); }
      if (resource === "expenses") item = { id: next(data), amount: Number(body.amount), currency: body.currency || "INR", category: body.category || "Other", description: body.description || "", spent_at: body.spent_at || now.slice(0,10), payment_method: body.payment_method || "", created_at: now };
      if (resource === "payments") { item = { id: next(data), title: body.title, amount: body.amount ? Number(body.amount) : null, due_at: body.due_at, recurrence: body.recurrence || "none", paid: 0, notes: body.notes || "", created_at: now, updated_at: now }; await notifyPayment(item); }
      if (resource === "habits") item = { id: next(data), name: body.name, frequency: body.frequency || "daily", target: Number(body.target || 1), active: true, created_at: now, today_count: 0 };
      data[key].push(item); await save(data); return response(item, 201);
    }
    const item = data[key].find(x => x.id === id);
    if (!item) return response({ error: "Item not found." }, 404);
    if (method === "DELETE") { data[key] = data[key].filter(x => x.id !== id); if (resource === "habits") data.habitLogs = data.habitLogs.filter(x => x.habit_id !== id); await cancelNotification(({tasks:100000,events:200000,payments:300000}[resource] || 0) + id); await save(data); return response(null, 204); }
    if (method === "PUT") {
      if (resource === "habits") { const day = body.day; data.habitLogs = data.habitLogs.filter(x => !(x.habit_id === id && x.day === day)); if (body.count) data.habitLogs.push({ habit_id: id, day, count: Number(body.count) }); await save(data); return response({ ...item, today_count: Number(body.count || 0) }); }
      if (resource === "payments" && body.paid && item.recurrence !== "none") advancePayment(item);
      else Object.assign(item, body);
      item.updated_at = stamp(); await save(data); return response(item);
    }
    return response({ error: "Unsupported operation." }, 405);
  }
  window.MobileStore = { isNative: native, route };
})();
