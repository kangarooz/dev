/**
 * What the agent-builder screen actually says, per episode.
 *
 * The prompts are the real ones from the Socrates guide (Enablement/1099923458),
 * abridged to their opening paragraph — enough that a viewer recognises the prompt
 * they are about to paste without the camera sitting on forty lines of text.
 *
 * The answers are not generic. Each episode teaches something specific, and a canned
 * reply about FAQ workflows playing under narration about retrieval trade-offs is
 * wallpaper. These mirror what each script is saying while it is on screen.
 *
 * Deterministic by construction: no randomness, no clock.
 */
window.CONTENT = {
  bootstrap: `You are onboarding a brand-new Legion Solution Architect.

If local skills are available, use the \`socrates-sa-onboarding\` skill and the
\`legion-agent-builder\` skill for this session.

For every answer in this session:
- use Socrates to ground the answer in the official Solution Architect docs
- explain things in plain English, as if teaching a smart coworker new to Legion
- go one level deeper than a short summary
- do not give me a compressed answer that is mostly labels or file paths

Do not do anything further until prompted to do so.`,

  prompts: {
    '00': null, // episode 00 pastes the bootstrap
    '01': `Teach me the most useful onboarding path for a brand-new Solution Architect learning Legion workflows. Use the official Solution Architect docs, current workflow examples, and Legion terminology correctly, but teach it directly in chat like a supportive teammate.`,
    '02': `Teach me the simple FAQ workflow as if I am brand new to Legion. I want a real teaching walkthrough, not a short summary.`,
    '03': `Explain state management in Legion workflows for a new Solution Architect in a way that makes it feel concrete and usable, not abstract.`,
    '04': `Teach me the difference between depends_on and conditions in Legion workflows like I am new but technical. I want to really understand the difference between when a step runs and whether a step runs.`,
    '05': `Analyze Dev/templated-writing/document-creation/workflow.json for a brand-new Solution Architect. I do not just want a summary. I want to learn how to read this workflow well.`,
    '06': `Compare native:lexical_search and native:semantic_search for a new Solution Architect in a way that helps me actually choose between them.`,
    '07': `Teach me how to read workflow execution logs like a new Solution Architect who wants to debug with evidence instead of guessing.`,
    '08': `Show me two workflows from this repo that use native:chat in meaningfully different ways: one where chat mainly synthesizes or responds, and one where chat is used for parsing, extraction, or structured transformation.`,
    '09': `Analyze Modules/IT HelpDesk/it_helpdesk_tier0_agent.json as a current RAG workflow that uses unified_search for a new Solution Architect.`,
    '10': `Compare the hybrid search pattern, native:full_text_retrieval, and native:unified_search for a new Solution Architect who already understands lexical versus semantic search.`,
    '11': `Analyze On-Prem Jira Assistant with the mindset of teaching a brand-new Solution Architect how to learn from a complex workflow without copying it blindly.`,
    '12': `Review a first-draft FAQ workflow that uses native:semantic_search, native:chat, and native:send-message. Treat this like a structural design review, not a generic summary.`,
  },

  /** Blocks are revealed one at a time so a recording shows the answer arriving. */
  answers: {
    '00': [
      { h: null, p: 'Understood. I will teach in plain English, ground answers in the Solution Architect docs and real workflow JSON, and define Legion vocabulary the first time it comes up.' },
      { h: null, p: 'Ready when you are — send the first onboarding prompt.' },
    ],
    '01': [
      { h: null, p: 'Here is the order I would learn these in, and why the order matters more than the effort.' },
      { h: 'Ordered learning path', p: '1. What a workflow <em>is</em> — steps, and the state they share.<br>2. The simple FAQ shape: input → search → generate → send.<br>3. State: how a step reads what an earlier step wrote.<br>4. <code>depends_on</code> vs <code>conditions</code> — when a step runs vs whether it runs.<br>5. Retrieval choice.<br>6. Reading execution logs.' },
      { h: 'Why this order', p: 'Each step is the prerequisite for the next. You cannot reason about <code>depends_on</code> until state is concrete, because a dependency is a statement about a value existing before someone reads it.' },
      { h: 'What can wait', p: 'Subagents, hybrid retrieval, <code>unified_search</code>, attributions, and anything with the word "orchestration" in it. None of that helps in week one, and reaching for it early is the most common way new SAs stall.' },
      { h: 'Common beginner mistake', p: 'Reading the most impressive workflow in the repo first and concluding that is the standard. It is not — it is an accumulation of edge cases someone hit over a year.' },
    ],
    '02': [
      { h: null, p: 'The FAQ workflow is four moves. Everything else in the repo is a variation on them.' },
      { h: 'How execution flows', p: 'A user asks something. A search step finds material. A chat step turns that material into prose. A send step puts it in front of the user.' },
      { h: 'State and dependencies', p: 'The steps never hand data to each other directly. Each writes to <code>state</code> and the next reads from it. <code>retrieve_docs</code> writes <code>search_results</code>; <code>draft_answer</code> declares <code>depends_on: retrieve_docs</code> so it cannot start before that key exists.' },
      { h: 'Common beginner mistake', p: 'Leaving out the <code>check_hits</code> condition. Without it an empty retrieval still flows into the chat step, which answers fluently from nothing. It does not crash — it produces a confident, unsupported answer, which is far harder to notice.' },
      { h: 'What to inspect in logs', p: 'The <em>inputs</em> of the chat step. If <code>passages</code> is empty there, the bug is upstream in retrieval, not in your prompt.' },
    ],
    '03': [
      { h: null, p: 'State is a shared notebook that lives for the length of one run.' },
      { h: 'The three lifetimes', p: '<code>state</code> is what steps write while the run happens. <code>initial_state</code> is what is already in the notebook before step one. <code>persist_keys</code> is what survives past the end of the run. Different lifetimes — and the bug you will write is assuming something is there at the start when nothing put it there.' },
      { h: 'Reference rules', p: 'A plain key names a value in the current step\'s scope. <code>$input.</code> reaches into what was passed <em>into</em> this step. <code>$state.</code> reads the shared notebook. They look interchangeable; using the wrong one often resolves to nothing rather than erroring, so the step runs with an empty value.' },
      { h: 'State-tracing checklist', p: 'For each step, ask two questions: what must already exist before this can run, and what does it leave behind? Walk them in order and every workflow becomes readable.' },
      { h: 'Beginner mistake', p: 'Assuming a key exists because it existed yesterday — in a run where a step that is skipped today happened to write it.' },
    ],
    '04': [
      { h: null, p: '<code>depends_on</code> controls <em>when</em> a step runs. <code>conditions</code> controls <em>whether</em> it runs at all. Timing versus permission.' },
      { h: 'depends_on', p: 'A data-readiness statement: this step needs something an earlier step produces, so do not start until that step has finished. It is not ordering for neatness — it is a value existing in state before someone reads it.' },
      { h: 'conditions', p: 'A branch. The step is only relevant in some runs. When the condition is not met the step does not run, and that is a normal, healthy outcome.' },
      { h: 'Side by side', p: 'You will see both on one step: wait for the search to finish, <em>and</em> only run if it found something. Two independent gates — passing one tells you nothing about the other.' },
      { h: 'How to debug each', p: 'A dependency problem looks like a step running with something empty. A condition problem looks like a step that never ran. Empty versus absent.' },
    ],
    '05': [
      { h: null, p: 'This workflow drafts a document from source material using a templated writing pass.' },
      { h: 'Architecture in plain English', p: 'Intake → extract facts → build an outline in a subagent → write → attach sources → deliver. The spine is five steps; the subagent is where the messy part lives.' },
      { h: 'Why the subagent matters', p: '<code>outline_subagent</code> takes a self-contained chunk of work out of the main flow. The main flow stays readable; the bounded messy part happens elsewhere and comes back as <code>document_outline</code>.' },
      { h: 'State flow walkthrough', p: '<code>source_docs</code> → <code>extracted_facts</code> → <code>document_outline</code> → <code>document_draft</code> → <code>cited_document</code>. Every step upstream of <code>write_document</code> exists to populate what it reads.' },
      { h: 'Beginner mistake', p: 'Copying the subagent boundary without the reason for it. A subagent that wraps two trivial steps is overhead, not architecture.' },
    ],
    '06': [
      { h: null, p: 'They fail in opposite directions, which is the useful part.' },
      { h: 'Plain-English definitions', p: 'Lexical matches words — exact, literal, predictable. Semantic matches meaning — it finds documents <em>about</em> an idea even with no shared vocabulary.' },
      { h: 'What each misses', p: 'Lexical misses paraphrase: the user says "cannot log in", the doc says "authentication failure", and you get nothing. Semantic misses precision: ask about error code <code>E-4021</code> and you get five documents about errors in general, none containing that code.' },
      { h: 'Effect on the chat step', p: 'Wrong retrieval does not error. It produces a confident answer built on the wrong three documents — so you will blame the prompt for what was a retrieval problem.' },
      { h: 'Decision rule', p: 'Exact identifiers and known terminology → start lexical. Users asking in their own words about prose → start semantic. Reach for hybrid only when you have a symptom that says so.' },
    ],
    '07': [
      { h: null, p: 'Debug with evidence, in a fixed order, so you are not inventing an approach under pressure.' },
      { h: 'What the audit shows', p: 'Start at the run level: which steps ran, which were skipped, which failed, and how long each took. Build the shape of the run before diving into any one part.' },
      { h: 'Inspect a single step', p: 'Inputs, outputs, status, duration — and read the <em>inputs</em> first. Everyone reads outputs first because that is where the wrongness shows. The inputs are where it started.' },
      { h: 'Symptom-to-cause', p: 'Empty input → missing state. Never ran → skipped branch. Ran and returned garbage → tool config. Slow → duration tells you before anything else does.' },
      { h: 'Beginner mistake', p: 'Jumping to the step that produced the bad output. That step is usually the victim; the culprit ran fine two steps earlier with an empty input.' },
    ],
    '08': [
      { h: null, p: 'Two workflows, two very different jobs for the same step type.' },
      { h: 'Chat as synthesizer', p: 'In <code>document-creation</code>, <code>write_document</code> takes an outline and produces prose a human reads. Configuration is tuned for readable output.' },
      { h: 'Chat as transformer', p: 'In the same file, <code>extract_facts</code> takes unstructured source documents and returns a structured list no user ever sees. Its output goes into state for a later step to consume.' },
      { h: 'Rule of thumb', p: 'Ask whether the step produces something a human reads or something another step consumes. Prompt style, model choice and how tightly you constrain the output all follow from that one answer.' },
      { h: 'Beginner mistake', p: 'Concluding that every messy input deserves a language model. Ask "could this have been a regex" before reaching for chat in the middle of a workflow.' },
    ],
    '09': [
      { h: null, p: 'A tier-zero IT helpdesk agent, answering real tickets against real documentation.' },
      { h: 'Retrieval architecture', p: '<code>native:unified_search</code> is one retrieval step covering what you would otherwise wire together yourself. Fewer decisions up front — and less visibility into which part of retrieval actually found the thing.' },
      { h: 'Why attributions matter', p: '<code>native:attributions</code> ties claims back to the sources behind them. That is what makes the quiet failure visible: an answer with no sources looks different from one with three, and the user can see it without reading a log.' },
      { h: 'Lessons worth borrowing', p: 'The retrieval shape and the attributions step. Both transfer to a first project directly.' },
      { h: 'What not to copy', p: 'The accumulated edge-case handling. This workflow earned its complexity against real tickets; yours has not earned any yet.' },
    ],
    '10': [
      { h: null, p: 'Three options, and the useful part is knowing when to move between them.' },
      { h: 'Simple definition of each', p: 'Hybrid runs more than one retrieval approach and combines results — you keep control and carry the wiring. <code>native:full_text_retrieval</code> pulls whole documents rather than fragments. <code>native:unified_search</code> is the managed one: less wiring, less visibility.' },
      { h: 'Escalation signals', p: 'Right about the topic but missing the specific identifier → semantic alone is straining; consider hybrid. Technically sourced but incoherent because the fragment was too small → full-text.' },
      { h: 'Beginner traps', p: 'Hybrid gives you two result sets to reconcile. Full-text eats context window — pull whole documents and you crowd out the room the chat step needs to think. Unified costs visibility.' },
      { h: 'When to use each', p: 'Symptom first, option second. Never pick the most sophisticated retrieval you know and go looking for a justification.' },
    ],
    '11': [
      { h: null, p: 'A genuinely complex integration workflow — and complexity is not the same as exemplary.' },
      { h: 'Critical path walkthrough', p: 'Intake → classify intent → authenticate → build JQL → search → normalise → summarise → attribute → reply. That is the spine. Everything else hangs off it handling cases.' },
      { h: 'Where complexity lives', p: 'At the boundary. Talking to an on-prem Jira means its schema, its auth, its retries and its failure modes — <code>auth_check</code> alone carries a retry policy and two branches.' },
      { h: 'Justified versus optional', p: 'Ask what breaks if you remove it. "Jira returns a different shape when the field is empty" is justified. "It is more robust" is not.' },
      { h: 'Beginner mistake', p: 'Copying the volume instead of the habits. Take how it isolates the integration boundary; leave the accumulated handling where it is.' },
    ],
    '12': [
      { h: null, p: 'Structural review, highest risk first.' },
      { h: '1. State management (highest risk)', p: 'Your <code>native:chat</code> step reads <code>passages</code>, but nothing guarantees the search wrote it. Add a condition on the retrieval result before the chat step runs.' },
      { h: '2. Fallback behaviour', p: 'There is no path for "retrieval found nothing". As written that case produces a fluent answer from an empty context rather than an honest "I could not find that".' },
      { h: '3. What breaks first in logs', p: '<code>native:send-message</code> will look like the failure. It is not — it is downstream of the real problem.' },
      { h: 'Smallest set of changes', p: 'Add the condition, add the no-results reply, and leave everything else alone. Then run it once and read the log before touching anything further.' },
      { h: 'If you revise too aggressively', p: 'A working four-step FAQ beats an elaborate twelve-step branching thing you cannot debug.' },
    ],
  },
};
