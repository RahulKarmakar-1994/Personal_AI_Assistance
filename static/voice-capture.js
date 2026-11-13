(function () {
  "use strict";

  const field = id => document.getElementById(id);

  async function transcribe() {
    const speech = window.Capacitor?.Plugins?.SpeechRecognition;
    if (speech) {
      const permission = await speech.requestPermissions();
      if (permission?.speechRecognition === "denied") throw new Error("Microphone permission was denied.");
      const result = await speech.start({ language: "en-IN", maxResults: 1, partialResults: false, popup: true });
      return (result.matches?.[0] || "").trim();
    }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) throw new Error("Voice recognition is unavailable. Try Chrome or the Android app.");
    if (!window.isSecureContext) throw new Error("Microphone access requires HTTPS or localhost. Open Smriti at http://127.0.0.1:8000 on this Mac.");
    if (navigator.mediaDevices?.getUserMedia) {
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        if (error?.name === "NotAllowedError") throw new Error("Microphone access is blocked. Allow it in Chrome and macOS Settings.");
        if (error?.name === "NotFoundError") throw new Error("No microphone was found.");
        throw new Error("Could not open the microphone (" + (error?.name || "unknown error") + ").");
      } finally {
        stream?.getTracks().forEach(track => track.stop());
      }
    }
    return new Promise((resolve, reject) => {
      const recognition = new Recognition();
      let settled = false;
      recognition.lang = "en-IN";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      recognition.onaudiostart = () => showToast("Microphone active — speak now");
      recognition.onspeechstart = () => showToast("Listening to your voice…");
      recognition.onresult = event => {
        settled = true;
        resolve((event.results[0][0].transcript || "").trim());
      };
      recognition.onnomatch = () => {
        settled = true;
        reject(new Error("I could not understand that. Please try again."));
      };
      recognition.onerror = event => {
        settled = true;
        reject(new Error(
          event.error === "not-allowed" ? "Allow microphone access and try again." :
          event.error === "no-speech" ? "No speech was detected. Tap the microphone and speak immediately." :
          event.error === "network" ? "Chrome's speech service could not connect. Check the internet connection." :
          "Voice recognition failed (" + (event.error || "unknown error") + ")."
        ));
      };
      recognition.onend = () => {
        if (!settled) reject(new Error("No speech was captured. Tap the microphone and speak immediately."));
      };
      recognition.start();
    });
  }

  function detectIntent(text, hint) {
    const value = text.toLowerCase();
    if (/\b(bill|payment)\b/.test(value) && /\b(due|remind|pay|monthly|every month)\b/.test(value)) return "payment";
    if (/\b(spent|expense|bought|purchased|cost me|paid for)\b|₹|\brs\.?\s*\d|\binr\s*\d/.test(value)) return "expense";
    if (/\b(habit|every day|everyday|daily|every week|weekly)\b/.test(value)) return "habit";
    if (/\b(meeting|appointment|event|calendar|schedule)\b/.test(value)) return "event";
    if (/\bremind(?:\s+\w+){0,3}\s+me\b|\b(task|to[- ]?do|need to|must)\b/.test(value)) return "task";
    return ["note", "expense", "habit", "event", "task", "payment"].includes(hint) ? hint : "note";
  }

  function parseCommand(text) {
    const value = text.trim();
    if (!value) return null;
    const lower = value.toLowerCase();
    const isPayment = /\b(bill|payment)\b/.test(lower) && /\b(due|remind|pay|monthly|every month)\b/.test(lower);
    const isExpense = /\b(i\s+)?(spent|paid for|bought|purchased)\b|\brecord (?:an? )?expense\b|₹|\brs\.?\s*\d|\binr\s*\d/.test(lower);
    const isHabit = /\b(add|create|start|track|make)\b.{0,25}\bhabit\b|\bi want to\b.{0,40}\b(every day|daily|every week|weekly)\b/.test(lower);
    const isEvent = /\b(add|create|schedule|put)\b.{0,35}\b(meeting|appointment|event|calendar)\b|\bmeeting with\b/.test(lower);
    const isTask = /\bremind(?:\s+\w+){0,3}\s+me\b|\b(add|create|record)\b.{0,25}\b(task|to[- ]?do)\b/.test(lower);
    const isNote = /^(please\s+)?(note|remember|write down|save (?:this|a note)|add (?:a )?note)\b/i.test(value);
    const type = isPayment ? "payment" : isExpense ? "expense" : isHabit ? "habit" : isEvent ? "event" : isTask ? "task" : isNote ? "note" : "";
    return type ? { type, text: value } : null;
  }

  function extractAmount(text) {
    const match = text.match(/(?:₹|\brs\.?|\binr)\s*([\d,]+(?:\.\d{1,2})?)/i)
      || text.match(/\b(?:spent|paid|cost(?: me)?|expense(?: of)?)\s+(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d{1,2})?)/i);
    return match ? match[1].replaceAll(",", "") : "";
  }

  function localInputValue(date) {
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function extractDateTime(text, fallbackHour) {
    const now = new Date();
    const result = new Date(now);
    result.setSeconds(0, 0);
    let foundDate = false;

    if (/\bday after tomorrow\b/i.test(text)) {
      result.setDate(result.getDate() + 2);
      foundDate = true;
    } else if (/\btomorrow\b/i.test(text)) {
      result.setDate(result.getDate() + 1);
      foundDate = true;
    } else if (/\btoday\b/i.test(text)) {
      foundDate = true;
    }

    const iso = text.match(/\b(20\d{2})-(\d{1,2})-(\d{1,2})\b/);
    if (iso) {
      result.setFullYear(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]));
      foundDate = true;
    }

    const months = {
      jan: 0, january: 0, feb: 1, february: 1, mar: 2, march: 2, apr: 3, april: 3,
      may: 4, jun: 5, june: 5, jul: 6, july: 6, aug: 7, august: 7, sep: 8, sept: 8,
      september: 8, oct: 9, october: 9, nov: 10, november: 10, dec: 11, december: 11
    };
    const dayFirst = text.match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+(20\d{2}))?\b/i);
    const monthFirst = text.match(/\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b/i);
    const named = dayFirst || monthFirst;
    if (named) {
      const startsWithDay = Boolean(dayFirst);
      const day = Number(startsWithDay ? named[1] : named[2]);
      const monthName = (startsWithDay ? named[2] : named[1]).toLowerCase();
      const month = months[monthName];
      let year = Number(named[3] || now.getFullYear());
      if (!named[3] && (month < now.getMonth() || (month === now.getMonth() && day < now.getDate()))) year++;
      result.setFullYear(year, month, day);
      foundDate = true;
    }

    const time = text.match(/\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b/i)
      || text.match(/\bat\s+(\d{1,2}):(\d{2})\b/i);
    if (time) {
      let hour = Number(time[1]);
      const marker = (time[3] || "").toLowerCase();
      if (marker.startsWith("p") && hour < 12) hour += 12;
      if (marker.startsWith("a") && hour === 12) hour = 0;
      result.setHours(hour, Number(time[2] || 0), 0, 0);
    } else if (foundDate) {
      result.setHours(fallbackHour, 0, 0, 0);
    } else {
      return "";
    }
    return localInputValue(result);
  }

  function makeTitle(text, type) {
    let title = text.replace(/^(please\s+)?(add|create|record|make|note|remember|schedule)\s+(a\s+|an\s+|that\s+)?/i, "").trim();
    if (type === "task") title = title.replace(/^remind(?:\s+\w+){0,3}\s+me(?:\s+(?:to|that))?\s+/i, "");
    if (type === "habit") title = title.replace(/^(i want to|my habit is|habit to)\s+/i, "");
    title = title.replace(/[.!?]+$/, "");
    const words = title.split(/\s+/);
    return (words.length > 12 ? words.slice(0, 12).join(" ") + "…" : title) || "Voice " + type;
  }

  function fillForm(text, hint, forceType) {
    const type = forceType ? hint : detectIntent(text, hint);
    const amount = extractAmount(text);
    const dateTime = extractDateTime(text, type === "event" ? Math.min(23, new Date().getHours() + 1) : 9);

    openItemDialog(type);
    field("itemTitle").value = makeTitle(text, type);
    field("itemDetails").value = text;

    if (type === "task") {
      if (dateTime) field("taskDue").value = dateTime;
      field("taskReminder").checked = Boolean(dateTime);
    }
    if (type === "event" && dateTime) {
      field("eventStart").value = dateTime;
      const end = new Date(new Date(dateTime).getTime() + 3600000);
      field("eventEnd").value = localInputValue(end);
    }
    if (type === "expense") {
      if (amount) field("expenseAmount").value = amount;
      const value = text.toLowerCase();
      field("expenseCategory").value =
        /food|grocery|restaurant|lunch|dinner|breakfast/.test(value) ? "Food" :
        /bus|train|taxi|uber|ola|fuel|petrol|travel/.test(value) ? "Transport" :
        /doctor|medicine|hospital|health/.test(value) ? "Health" :
        /course|book|tuition|education/.test(value) ? "Education" :
        /bill|electricity|internet|rent|recharge/.test(value) ? "Bills" :
        /shop|bought|purchase/.test(value) ? "Shopping" : "Other";
    }
    if (type === "payment") {
      if (amount) field("paymentAmount").value = amount;
      if (dateTime) field("paymentDue").value = dateTime;
      field("paymentRepeat").value =
        /\b(monthly|every month)\b/i.test(text) ? "monthly" :
        /\b(weekly|every week)\b/i.test(text) ? "weekly" :
        /\b(yearly|annual|every year)\b/i.test(text) ? "yearly" : "none";
    }
    if (type === "habit") {
      field("habitFrequency").value = /\b(weekly|every week)\b/i.test(text) ? "weekly" : "daily";
      const target = text.match(/\b(\d{1,2})\s*(?:times?|glasses?|minutes?)\b/i);
      if (target) field("habitTarget").value = Math.max(1, Number(target[1]));
    }
    showToast(type.charAt(0).toUpperCase() + type.slice(1) + " form filled — review and save");
  }

  function fillStructured(draft) {
    const type = draft.action || draft.type;
    const values = draft.fields || {};
    openItemDialog(type);
    field("itemTitle").value = values.title || values.name || "New " + type;
    field("itemDetails").value = values.details || values.content || values.description || values.notes || "";
    if (type === "task") {
      field("taskDue").value = values.due_at || "";
      field("taskPriority").value = values.priority || "normal";
      field("taskReminder").checked = values.reminder !== false && Boolean(values.due_at);
    }
    if (type === "event") {
      field("eventStart").value = values.starts_at || "";
      field("eventEnd").value = values.ends_at || "";
      field("eventReminder").value = String(values.reminder_minutes ?? 15);
    }
    if (type === "note") field("notePinned").checked = Boolean(values.pinned);
    if (type === "expense") {
      field("expenseAmount").value = values.amount || "";
      field("expenseCategory").value = values.category || "Other";
      field("expenseDate").value = values.spent_at || new Date().toLocaleDateString("en-CA");
      field("expenseMethod").value = values.payment_method || "Other";
    }
    if (type === "payment") {
      field("paymentAmount").value = values.amount || "";
      field("paymentDue").value = values.due_at || "";
      field("paymentRepeat").value = values.recurrence || "none";
    }
    if (type === "habit") {
      field("habitFrequency").value = values.frequency || "daily";
      field("habitTarget").value = values.target || 1;
    }
    showToast(type.charAt(0).toUpperCase() + type.slice(1) + " form filled — review and save");
  }

  async function capture(hint) {
    showToast("Listening…");
    try {
      const text = await transcribe();
      if (!text) throw new Error("No speech was recognized.");
      fillForm(text, hint);
    } catch (error) {
      showToast(error.message || "Voice capture failed.");
    }
  }

  document.addEventListener("click", event => {
    const button = event.target.closest("[data-voice-capture]");
    if (button) capture(button.dataset.voiceHint || "");
  });

  window.SmritiVoice = {
    detectIntent,
    extractAmount,
    extractDateTime,
    parseCommand,
    fillForm,
    fillAs: (text, type) => fillForm(text, type, true),
    fillStructured
  };
})();
