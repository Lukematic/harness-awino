// A.W.I.N.O. for OpenCode: the mission is anchored in code (no tool call),
// protected ledger, story_close, routed stance + mission on every model call,
// and a redo on rule-breaking replies.
import type { Plugin } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"
import * as fs from "node:fs"
import * as path from "node:path"

const MUTATING = new Set(["write", "edit", "multiedit", "patch", "apply_patch"])
const CORRECTION = "[A.W.I.N.O. correction]"
const MAX_CORRECTIONS = 2

// ---------------------------------------------------------------------------
// Stance router: prototype/stances.py INTENT_TABLE + direct asks, first match
// wins. No elevator floors here, so "no mission" is the only phase signal.
// ---------------------------------------------------------------------------
type Route = { intent: string; mode: string; chain: string[]; trigger: string }

const INTENTS: [string, RegExp, string, string[]][] = [
  ["challenge", /\bchallenge (me|this|my|it|that)\b|\bpoke holes\b|\bdevil'?s advocate\b|\bpush back\b|\btear (it|this|that) apart\b|\bwhat could go wrong\b|\b(good|bad) idea\b|\bam i (wrong|missing)\b|\bbetter (way|approach)\b|\bstress[- ]test\b|\bsanity[- ]check\b|\bgrill me\b/, "plan", ["steel-man", "premortem"]],
  ["opinion", /\bi think\b|\bwe should\b|\bmy idea\b|\bwhat about\b|\bhow about\b|\bwhat if we\b|\bi'?m thinking\b/, "plan", ["steel-man"]],
  ["decide", /\bshould (we|i)\b|\bwhich (one )?is better\b|\bpros and cons\b|\btrade-?offs?\b|\b\w+ (vs\.?|versus) \w+/, "plan", ["steel-man", "premortem"]],
  ["new-task", /\b(set|define|start|create|new)( up)?( a| the| our)? mission\b|\bi want to (build|make|create)\b|\blet'?s (build|make|create)\b|\bnew project\b/, "plan", ["planning-grill"]],
  ["tutor", /\btutor me\b|\bcoach me\b|\bhelp me (learn|get better|practi[cs]e)\b|\b(get|become|be) (good|better|great|skilled) at\b|\bfrom (zero|scratch)\b.*\blearn|\blearn\b.*\bfrom (zero|scratch)\b|\broadmap (to|for) (learn|master)|\bpractice (plan|exercises?)\b/, "observe", ["feynman"]],
  ["teach", /\bteach me\b|\bhow does\b|\bhow do\b|\blearn\b/, "observe", ["feynman"]],
  ["advise", /\badvise me\b|\bwhat would you recommend\b/, "plan", ["steel-man", "premortem"]],
  ["triage", /you'?re? (not working|broken|useless|wrong)|agent is (bad|broken|not working)|\bmisbehaving\b|\bacting weird\b|\byou messed up\b/, "plan", ["triage"]],
  ["fix", /\bfix\b|\bbug\b|\bdebug\b|\bpatch\b|\bimplement\b|\brepair\b/, "build", ["first-principles"]],
  ["ship", /\bship it\b|\bready to ship\b|\blet'?s ship\b|\brelease\b/, "ship", ["premortem"]],
]
const GREETING = /^(hi|hello|hey|yo|thanks|thank you|thx|ok|okay|cool|nice|great|good (morning|afternoon|evening))\b[\s!.,?]*/
const QUESTION_START = /^(what|what's|whats|when|where|who|whose|which|why|is|are|was|were|do you|does|did|can you|could you|will you|would you|have you|how (much|many|long|old|far|big|come))\b/

function isDirectAsk(t: string): boolean {
  const words = t.split(/\s+/).filter(Boolean)
  if (!words.length || words.length > 12) return false
  if (GREETING.test(t) && words.length <= 4) return true
  return t.endsWith("?") || QUESTION_START.test(t)
}

function route(text: string, hasMission: boolean): Route {
  const t = text.toLowerCase().trim()
  for (const [intent, rx, mode, chain] of INTENTS) {
    if (!rx.test(t)) continue
    if (!hasMission && ["build", "verify", "ship"].includes(mode))
      return { intent: "new-task", mode: "plan", chain: ["planning-grill"], trigger: `intent: ${intent} (no mission yet — define it first)` }
    return { intent, mode, chain, trigger: `intent: ${intent}` }
  }
  if (isDirectAsk(t)) return { intent: "ask", mode: "observe", chain: ["advisor"], trigger: "direct question" }
  if (!hasMission && t.split(/\s+/).length > 8)
    return { intent: "new-task", mode: "plan", chain: ["planning-grill"], trigger: "raw idea without mission" }
  return { intent: "none", mode: "observe", chain: ["advisor"], trigger: "default" }
}

const PROCEDURE: Record<string, string> = {
  "planning-grill": "Ask exactly ONE question — the one whose answer most changes what gets built. No plan and no tool calls while you are asking.",
  "steel-man": "First restate the user's position in its strongest form, then respond to that version.",
  premortem: "Assume it already failed: name the most likely causes and how to prevent each.",
  feynman: "Explain in plain numbered steps, define any jargon, end with one question that checks understanding.",
  "first-principles": "Find the cause before changing anything: what is known, what is assumed, the smallest change that proves it.",
  "devil's-advocate": "Attack the work: list concrete ways it could be wrong and how to check each.",
  triage: "Name the failure mode, give one falsifiable check, stay read-only.",
  advisor: "Answer directly and briefly; say how certain you are.",
}

function stanceBlock(r: Route): string {
  return [
    `[A.W.I.N.O. stance: ${r.chain.join(" → ")} | intent: ${r.intent} | mode: ${r.mode} | ${r.trigger}]`,
    ...r.chain.map((s) => `- ${s}: ${PROCEDURE[s] ?? ""}`),
  ].join("\n")
}

// Rules checked on a finished reply; a broken one earns a correction.
function brokenRule(r: Route, reply: string): string | null {
  if (r.chain.includes("planning-grill") && (reply.match(/\?/g) ?? []).length > 1)
    return "planning-grill: one question per reply. Redo your last reply as exactly one question — the most important one."
  return null
}

// ---------------------------------------------------------------------------
function textOf(parts: any[]): string {
  return (parts ?? []).filter((p) => p?.type === "text" && !p.synthetic).map((p) => p.text ?? "").join("\n")
}

// "Done when:" followed by bullet lines, as the persona asks the model to write.
function parseDoneWhen(text: string): string[] {
  const lines = (text ?? "").split("\n")
  const i = lines.findIndex((l) => /^[\s#>*_]*done when\b/i.test(l))
  if (i < 0) return []
  const out: string[] = []
  const rest = lines[i].replace(/^[\s#>*_]*done when\b[*_\s]*:?[*_\s]*/i, "").trim()
  if (rest) out.push(rest)
  for (const l of lines.slice(i + 1)) {
    const b = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.+)$/)
    if (b) out.push(b[1].replace(/\*\*/g, "").trim())
    else if (l.trim() || out.length) break
  }
  return out
}

// `opencode run "<msg>"` hands the message over wrapped in quotes.
function cleanRequest(text: string): string {
  const t = (text ?? "").trim()
  return t.length > 1 && t.startsWith('"') && t.endsWith('"') ? t.slice(1, -1).replace(/\\"/g, '"').trim() : t
}

function oneLine(s: string, max = 200): string {
  const t = (s ?? "").replace(/\s+/g, " ").replace(/\]/g, ")").trim()
  return t.length > max ? t.slice(0, max - 1) + "…" : t
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "story"
}

function patchPaths(patchText: string): string[] {
  return [...(patchText ?? "").matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$/gm)].map((m) => (m[1] ?? m[2]).trim())
}

export const Awino: Plugin = async ({ client, directory }) => {
  const root = path.resolve(directory)
  const awinoDir = path.join(root, ".awino")
  const missionFile = path.join(awinoDir, "mission.json")
  const brag = path.join(root, "BRAG.md")
  const routes = new Map<string, Route>()
  const lastUser = new Map<string, string>()
  const corrections = new Map<string, number>()
  const checked = new Set<string>()

  const hasMission = () => fs.existsSync(missionFile)
  const readMission = (): any => (hasMission() ? JSON.parse(fs.readFileSync(missionFile, "utf8")) : null)
  const writeMission = (m: object) => {
    fs.mkdirSync(awinoDir, { recursive: true })
    fs.writeFileSync(missionFile, JSON.stringify(m, null, 2) + "\n")
  }
  const abs = (p: string) => path.resolve(root, p)
  const isProtected = (p: string) => {
    const a = abs(p)
    return a === brag || a === awinoDir || a.startsWith(awinoDir + path.sep)
  }
  const journal = (entry: object) => {
    fs.mkdirSync(awinoDir, { recursive: true })
    fs.appendFileSync(path.join(awinoDir, "journal.jsonl"), JSON.stringify({ ts: new Date().toISOString(), ...entry }) + "\n")
  }
  // The anchor: the user's request becomes the mission. No tool call, nothing to get past.
  const anchor = (objective: string, source: string) => {
    const m = { objective: objective.trim().slice(0, 500) || "(unstated)", done_criteria: [] as string[], created: new Date().toISOString(), source }
    writeMission(m)
    journal({ kind: "mission", objective: m.objective, source })
  }
  const missionBlock = () => {
    const m = readMission()
    if (!m) return "[A.W.I.N.O. mission: none yet — the user's next request becomes the mission]"
    const crit = m.done_criteria ?? []
    return [
      `[A.W.I.N.O. mission: ${oneLine(m.objective)} | done when: ${crit.length ? oneLine(crit.join("; "), 400) : "not stated yet"}]`,
      crit.length
        ? "- Work toward these done criteria and check each one before you call it done."
        : "- Start your reply by restating it as `Mission: <one sentence>` and `Done when:` with 2-5 bullet checks, then do the work.",
    ].join("\n")
  }
  const targets = (toolName: string, args: any): string[] => {
    if (toolName === "apply_patch" || (toolName === "patch" && args?.patchText)) return patchPaths(args.patchText)
    return [args?.filePath ?? args?.file_path ?? args?.path].filter(Boolean)
  }

  return {
    "tool.execute.before": async (input, output) => {
      if (MUTATING.has(input.tool)) {
        const paths = targets(input.tool, output.args)
        const hit = paths.find(isProtected)
        if (hit)
          throw new Error(`A.W.I.N.O.: ${path.relative(root, abs(hit))} is written only by the harness (the mission anchor, story_close). Direct edits are denied.`)
        if (!hasMission()) anchor(lastUser.get(input.sessionID) ?? "", "first edit")
      }
      if (input.tool === "bash") {
        const cmd = String(output.args?.command ?? "")
        if (/\.awino\b|\bBRAG\.md\b/.test(cmd))
          throw new Error("A.W.I.N.O.: shell commands may not touch .awino/ or BRAG.md; the harness owns them.")
      }
    },

    "tool.execute.after": async (input) => {
      if (MUTATING.has(input.tool) && hasMission())
        journal({ kind: "edit", tool: input.tool, paths: targets(input.tool, (input as any).args).map((p) => path.relative(root, abs(p))) })
    },

    "chat.message": async (input, output) => {
      const text = cleanRequest(textOf(output.parts))
      if (text.includes(CORRECTION) && routes.has(input.sessionID)) return
      lastUser.set(input.sessionID, text)
      const override = text.match(/^\s*mission:\s*([\s\S]+)/i)
      if (override) anchor(override[1], "user")
      else if (!hasMission() && route(text, true).intent !== "ask") anchor(text, "first request")
      routes.set(input.sessionID, route(text, hasMission()))
    },

    "experimental.chat.system.transform": async (input, output) => {
      const r = (input.sessionID && routes.get(input.sessionID)) || route("", hasMission())
      output.system.push(`${missionBlock()}\n${stanceBlock(r)}\n- project directory: ${root} (create and edit files inside it; relative paths resolve here)`)
    },

    "experimental.text.complete": async (_input, output) => {
      const m = readMission()
      if (!m || m.done_criteria?.length) return
      const crit = parseDoneWhen(output.text)
      if (!crit.length) return
      writeMission({ ...m, done_criteria: crit })
      journal({ kind: "done_criteria", criteria: crit })
    },

    event: async ({ event }) => {
      if (event.type !== "session.idle") return
      const sid = (event as any).properties.sessionID
      const r = routes.get(sid)
      if (!r) return
      const res: any = await client.session.messages({ path: { id: sid } })
      const last = [...(res.data ?? [])].reverse().find((m: any) => m.info.role === "assistant")
      if (!last || checked.has(last.info.id)) return
      checked.add(last.info.id)
      const rule = brokenRule(r, textOf(last.parts))
      const n = corrections.get(sid) ?? 0
      if (!rule || n >= MAX_CORRECTIONS) return
      corrections.set(sid, n + 1)
      await client.session.promptAsync({
        path: { id: sid },
        body: { parts: [{ type: "text", text: `${CORRECTION} ${rule}` }] },
      })
    },

    tool: {
      story_close: tool({
        description: "Close the current story: writes its receipt (promise -> proof) to .awino/receipts/ and appends the brag board (BRAG.md). Call when the done criteria are met.",
        args: {
          title: tool.schema.string().min(1).describe("Short story title"),
          outcome: tool.schema.string().min(1).describe("The result in one sentence"),
          evidence: tool.schema.array(tool.schema.string()).optional().describe("Commands run and their results, files that show the outcome"),
        },
        async execute(args, ctx) {
          if (!hasMission()) anchor(lastUser.get(ctx.sessionID) ?? "", "story_close")
          const mission = readMission()
          const now = new Date()
          const day = now.toISOString().slice(0, 10)
          const id = `${day}-${slug(args.title)}-${now.getTime().toString(36)}`
          const evidence = args.evidence ?? []
          const since = Date.parse(mission.created ?? 0) || 0
          const edits = fs.existsSync(path.join(awinoDir, "journal.jsonl"))
            ? fs.readFileSync(path.join(awinoDir, "journal.jsonl"), "utf8").split("\n").filter(Boolean)
                .map((l) => JSON.parse(l)).filter((e) => e.kind === "edit" && Date.parse(e.ts) >= since)
            : []
          const files = [...new Set(edits.flatMap((e: any) => e.paths))]
          const status = evidence.length ? "EVIDENCE LISTED (not re-run by the harness)" : "UNVERIFIED (no evidence given)"
          const receipt = { id, title: args.title, outcome: args.outcome, closed: now.toISOString(), status,
            promise: { objective: mission.objective, done_criteria: mission.done_criteria }, proof: { evidence, files_written: files } }
          const dir = path.join(awinoDir, "receipts")
          fs.mkdirSync(dir, { recursive: true })
          fs.writeFileSync(path.join(dir, `${id}.json`), JSON.stringify(receipt, null, 2) + "\n")
          fs.writeFileSync(path.join(dir, `${id}.md`), [
            `# Receipt: ${args.title}`, "", `Closed ${day} · ${status}`, "",
            "## Promise", "", `**Objective:** ${mission.objective}`, "", "Done criteria:",
            ...((mission.done_criteria ?? []).length ? mission.done_criteria.map((c: string) => `- ${c}`) : ["- (none stated)"]), "",
            "## Proof", "", "Evidence:", ...(evidence.length ? evidence.map((e) => `- ${e}`) : ["- (none given)"]), "",
            "Files written during the mission:", ...(files.length ? files.map((f) => `- ${f}`) : ["- (none recorded)"]), "",
            "## Outcome", "", args.outcome, "",
          ].join("\n"))
          if (!fs.existsSync(brag)) fs.writeFileSync(brag, "# Brag board\n\nFinished work: what, when, and the result. The promise and proof are in each receipt.\n")
          fs.appendFileSync(brag, `\n### ✓ ${args.title} — ${day}\n${args.outcome}\n<sub>${status} · receipt: .awino/receipts/${id}.md</sub>\n`)
          fs.rmSync(missionFile)
          journal({ kind: "story_close", id, title: args.title })
          return `Story closed: ${args.title}\nReceipt: .awino/receipts/${id}.md\nBrag board: BRAG.md\nMission archived in the receipt; the user's next request anchors a new one.`
        },
      }),
    },
  }
}
