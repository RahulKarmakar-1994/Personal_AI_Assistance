const GROQ_URL = "https://api.groq.com/openai/v1/chat/completions";

function response(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" }
  });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
    if (request.headers.get("authorization") !== `Bearer ${env.NILA_APP_TOKEN}`) {
      return response({ error: "Unauthorized" }, 401);
    }
    let input;
    try { input = await request.json(); } catch { return response({ error: "Invalid JSON" }, 400); }
    if (!Array.isArray(input.messages) || input.messages.length > 30) {
      return response({ error: "Invalid messages" }, 400);
    }
    const messages = input.messages.map(({ role, content }) => ({
      role: ["system", "user", "assistant"].includes(role) ? role : "user",
      content: String(content || "").slice(0, 12000)
    }));
    const upstream = await fetch(GROQ_URL, {
      method: "POST",
      headers: { "content-type": "application/json", "authorization": `Bearer ${env.GROQ_API_KEY}` },
      body: JSON.stringify({ model: env.GROQ_MODEL || "qwen/qwen3.8-27b", messages })
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": "application/json", "cache-control": "no-store" }
    });
  }
};

