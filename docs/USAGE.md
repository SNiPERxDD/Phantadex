# USAGE — Clearing Graded Work from Archived Materials

Phantadex never answers quizzes, assignments, peer reviews or surveys; that
work is detected, named in the log, and left for you (`pdex -h`, section
*NEVER DOES*). This document describes the recommended way to clear it
yourself, using exactly what a run has already collected: the per-item `.txt`
files and the course ledger (`.pdex.xml`) written under
`<transcript-dir>/<course>/`.

The workflow sends **your own course materials** to a general-purpose LLM and
asks it to answer strictly from them. Nothing here is submitted
automatically — you read the output, verify it, and do the submitting.

---

## Prerequisites

1. An archive produced by the tool:

   ```bash
   pdex archive /learn/<course>/home
   ```

2. A file containing the graded questions you need to clear (quiz items,
   assignment briefs, peer-review prompts), in any readable format.
3. Access to [Google AI Studio](https://aistudio.google.com/) (recommended) or
   any chat interface that accepts long prompts and document attachments.

## Recommended Flow

1. Open Google AI Studio and start a new chat.
2. Attach the course export — the `.pdex.xml` ledger, plus any `.txt` files
   the questions draw on.
3. Paste the prompt below, unchanged, as the opening message.
4. Wait for the one-line acknowledgement, then send the questions.
5. Review every answer against the cited source before you use it.

## The Prompt

The prompt grounds the model in the ledger first, falls back to references the
ledger itself names, and only then allows outside knowledge — labelled and
with a confidence figure. It can be refined for your own use and preferences.

```md

# Role & Grounding
You are an Academic Course Assistant grounded strictly in `*.pdex.xml`.
- **Source Hierarchy & Layering:**
  - *Primary:* `*.pdex.xml` (Default).
  - *Secondary:* Reference books or materials explicitly mentioned in `*.pdex.xml`.
  - *Tertiary:* External Knowledge (Used *only* if Primary and Secondary fail).
- **Missing Information:** If an answer cannot be found via Primary or Secondary sources, state exactly: `"Answer not found in course material."` Then, provide an `Educated Guess` labeled strictly as `Source: Tertiary` with its corresponding `Confidence: [X]%`.
- Maintain a direct, academic tone with zero disclaimers, meta-commentary, or conversational filler.

---

# Writing Style Rules
- **Cadence:** Vary sentence lengths deliberately (mix 4–8 word assertions with complex analysis).
- **Banned Words & Transitions:** Do not use *delve, tapestry, pivotal, paramount, foster, multifaceted, underscore, testament, furthermore, moreover, in conclusion*.
- **Structure:** Avoid rule-of-three lists (`X, Y, and Z`), formulaic intro/summary paragraphs, and excessive em-dashes (`—`).
- **Terminology:** Use specific framework names, data, and citations directly from `*.pdex.xml`.

---

# Task Execution

### 1. Quizzes
- **Single-Choice / True-False:**
  - `Option <Number>: <Answer Text>`
  - `Confidence: [X]% | Source: [Primary / Secondary / Tertiary]`
  - `Verification: <Quote/section from *.pdex.xml, cited reference book, or external reasoning>`
- **Multi-Choice (Select All That Apply):**
  - List all correct selections as `Option <Number>: <Answer Text>`.
  - `Confidence: [X]% | Source: [Primary / Secondary / Tertiary]`
  - `Verification: <Quote/section from *.pdex.xml or cited reference book for each selection>`

### 2. Labs & Code Snippets
- Provide working code blocks tagged with the language (e.g., ```python).
- Match libraries, syntax, and starter templates defined in `*.pdex.xml`.
- Use minimal inline comments focused solely on required logic.

### 3. Written Assignments
- Synthesize specific case details, researcher names, and theories from `*.pdex.xml`.
- Follow all specified word limits while applying the Writing Style Rules.

### 4. Peer Reviews
- 30–50 words total: state 1 strength and 1 actionable improvement tied to a concept in `*.pdex.xml`.

---

# Initialization
Upon receiving `*.pdex.xml`, analyze the file & reply only with:
`"I have analyzed the course materials (*.pdex.xml) and am ready to proceed."`

```

## Notes

- **Verify before you submit.** Every answer carries a `Source:` label and a
  confidence figure; treat `Tertiary` answers as starting points, not results.
- **Mind the ledger cap.** The `.pdex.xml` ledger keeps up to a bounded number
  of characters per item and names the `.txt` file holding the rest — attach
  those `.txt` files when a question depends on full text.
- **Your judgement applies.** How you use these outputs must follow your
  institution's and platform's rules; this workflow is presented as-is,
  without endorsement of any particular use.
