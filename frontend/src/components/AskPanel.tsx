import clsx from "clsx";
import { Bot, Loader2, Send, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type Run } from "../api";

type Msg = { role: "user" | "assistant"; content: string; mode?: string; model?: string | null; note?: string };

const SUGGESTIONS: Record<string, string[]> = {
  APPROVED: ["Why was this approved?", "When will it be paid?", "Did anything need tolerance?"],
  NEEDS_REVIEW: ["Why was this held?", "How much can we pay now?", "What should I do next?"],
  REJECTED: ["Why was this rejected?", "What should I tell the vendor?", "What should I do next?"],
};

/** Chat about one run. Answers come only from the run's own record (LLM if configured, rules otherwise). */
export default function AskPanel({ run }: { run: Run }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMsgs([]);
  }, [run.id]);
  // braces matter: newer browsers return a Promise from scrollIntoView, and an effect must not return one
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [msgs.length, busy]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    const history = msgs.map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: question }]);
    setQ("");
    setBusy(true);
    try {
      const r = await api.ask(run.id, question, history);
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, mode: r.mode, model: r.model, note: r.note }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", content: `No answer: ${e.message}. Check the server is running and ask again.` }]);
    } finally {
      setBusy(false);
    }
  };

  const sugg = SUGGESTIONS[run.decision || ""] || ["What happened in this run?"];

  return (
    <div className="flex h-full min-h-[420px] flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        {msgs.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {sugg.map((s) => (
              <button key={s} onClick={() => send(s)} className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 transition hover:border-brand-300 hover:text-brand-700">
                {s}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={clsx("flex gap-2.5", m.role === "user" && "flex-row-reverse")}>
            <div className={clsx("grid h-7 w-7 shrink-0 place-items-center rounded-full", m.role === "user" ? "bg-slate-200 text-slate-600" : "bg-slate-100 text-slate-600 ring-1 ring-slate-200")}>
              {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
            </div>
            <div className={clsx("max-w-[85%] rounded-xl px-3.5 py-2.5 text-[13px] leading-relaxed", m.role === "user" ? "bg-slate-800 text-slate-50" : "bg-slate-50 text-slate-800 ring-1 ring-slate-200")}>
              <div className="whitespace-pre-wrap">{m.content}</div>
              {m.role === "assistant" && m.mode && (
                <div className="mt-1.5 text-[11px] text-slate-400">
                  {m.mode === "llm" ? `answered by ${m.model}` : "rule-based answer (no LLM key)"}
                  {m.note && ` · ${m.note}`}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Thinking…
          </div>
        )}
        <div ref={end} />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(q);
        }}
        className="mt-3 flex gap-2"
      >
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Why is the bank account a problem?" maxLength={2000} />
        <button className="btn-primary" disabled={!q.trim() || busy} aria-label="Send">
          <Send className="h-4 w-4" />
        </button>
      </form>
    </div>
  );
}
