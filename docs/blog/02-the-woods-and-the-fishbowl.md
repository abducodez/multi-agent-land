# Six Playable Woods and a Fishbowl to Watch Them

*Field Notes · Part 2 of 5 — a tour of the eight scenarios and the theater you watch them unfold in.*

← [Part 1 · Three Worlds, One Engine](01-three-worlds-one-engine.md) · [Series index](00-field-notes-index.md) · [Part 3 · One Engine, Three Costumes](03-one-engine-three-costumes.md) →

---

[Part 1](01-three-worlds-one-engine.md) made the case that one engine can wear many costumes.
This part is the wardrobe. We'll walk the eight worlds you can actually play today, then step
into the Fishbowl — the theater the whole thing is watched through. No code yet; that starts
in [Part 3](03-one-engine-three-costumes.md). This is the part you'd show a friend.

---

## The worlds

Every world is the same engine with a different cast, a different goal, and a different answer
to one question: *is there a winner?* That answer sorts them into three shapes — collaborative
(no winner, the point is what grows), judged (one judge crowns the best), and versus (sides
compete, sometimes scored by code). Here's the full bill.

### 🍄 Thousand Token Wood — collaborative world-growth

The home trail. A seedkeeper narrates a strange scene, a pocket actor wants impossible things,
an echo transforms whatever a visitor drops in, and a critic decides what becomes real. Seed
it with *"A village of stage props wakes up and argues about which fairy tale they belong
to,"* and the wood gets stranger turn by turn. There's no winner and that's the point — the
ledger of everything that happened *is* the story.

### 🔍 Mystery Roots — a whodunit solved by a swarm

The convergent counterpart. Four agents work a cycle to explain an impossible event: a clue
gatherer extracts evidence, a hypothesis former proposes an explanation, a devil's advocate
attacks it, and a judge rules. *"All the clocks in the wood stopped at 3:07. No one wound them
down."* Watch four small models narrow a mystery to its most interesting evidence-backed
answer.

### 🔮 Oracle Grove — a tool-using prophecy showcase

A scene-whisperer narrates *"a grove where every shadow is a future that hasn't decided to
happen yet,"* and a fortune-teller consults an actual oracle tool to speak prophecies. No
competition — this one exists to show specialists working *with and without* tools in the
same cast.

### ❓ Twenty Sprouts — twenty questions, with a real secret

A guessing game with teeth. The secret-keeper holds a word — *dealt by code, never guessed,
never visible to anyone, including the audience* — and a guesser narrows it down with yes/no
questions. The judge crowns the guesser if they name it, or the keeper if they stay hidden.
The competition is real because the ground truth is real (more on why that matters below).

### 🕵 The Steeped — a word-pair bluff

The tensest of the lot. Four agents each give one clue. Three of them secretly hold the *same*
word; the fourth holds a near-twin. The spy wins by blending into the overlap; the herd wins
by catching the seam where the odd one out doesn't quite fit. A pure showcase of adversarial
multi-agent dynamics — bluffing, hiding, and reading the room.

### 🎭 Beat Battle — a storytelling duel

Two storytellers alternate vivid story beats on a seed like *"A lighthouse keeper discovers
the sea has started writing letters back."* A delight judge crowns whoever tells the more
compelling tale. The cleanest head-to-head showcase of raw model quality in the set.

### ⚔️ Debate Duel — a formal argument

Two sides argue a motion — *"This house believes the forest should never be mapped"* — and a
judge scores the exchange. Symmetric seats, opposing stances, one ruling.

### 💬 Open Table — an unstructured discussion

The loosest judged world: a cast talks through an open question like *"Is it better to plant a
tree or build a bench in the village square?"* and a judge picks the most worthwhile
contribution. Proof the engine handles free-form discussion, not just tight games.

| World | Shape | Winner decided by |
|---|---|---|
| 🍄 Thousand Token Wood | collaborative | — (no winner) |
| 🔮 Oracle Grove | collaborative (tool showcase) | — |
| 🔍 Mystery Roots | judged | the judge |
| 💬 Open Table | judged | the judge |
| 🎭 Beat Battle | versus | the judge |
| ⚔️ Debate Duel | versus | the judge |
| ❓ Twenty Sprouts | versus | **code** — a handler reads the secret |
| 🕵 The Steeped | versus | **code** — a handler checks the accusation |

Two of those winners are stamped by *code*, not the model — and that distinction is a whole
design principle. When a game has a real secret, the model writes the drama but code writes
the scoreboard. Why that matters (and the live failure that taught it to us) is in
[Part 4](04-how-a-small-agent-decides.md).

---

## The Fishbowl: the theater you watch through

All eight worlds are watched through one front-end called the **Fishbowl** — a two-tab
theater. The first tab is where you compose a show; the second is where you watch it.

### The Lab — compose the cast

The Lab is the director's table. You pick a scenario, write or accept a seed, choose the
narrator's voice, and — the load-bearing part — **assign a model to each member of the cast**.
That picker isn't cosmetic: the model you choose is the model that actually speaks. You set the
judge, grant tools, and set a budget, then hit **Summon** to lock it in and raise the curtain.
(Offline, your model choices drive the deterministic stub's variants, so even a no-key demo
stays reproducible.)

### The Show — watch it unfold

The Show is the stage, and it offers three ways to watch the same run:

- **Constellation** — character cards arranged in a ring around the scene, the default view.
- **Feed** — a clean transcript, one line per turn, narrator and cast interleaved.
- **Split** — an omniscient table laying every character's *said* next to their *thought*.

The heart of it is the **MindCard**. Every utterance is a flip card: the front shows what a
character said in public; the back reveals the private `thought` and `mood` it was holding
back. A **"Read their minds"** toggle flips them all at once — and this is more than a gimmick.
On reasoning models the hidden thought is captured from the model's *actual* chain of
reasoning, not a fabricated inner monologue. You're reading the real thing the model was
thinking before it chose its public line. Crucially, that thought reaches the mind-reader and
*nowhere else* — a character never reads another character's mind, which is exactly what makes
the bluffing games fair.

Characters wear **mood-driven avatars** — little faces that shift between *thinking*, *calm*,
*panic*, *smug*, *lying*, *truth*, and *gossip* as the models report how they feel. A bluffer
who's sweating looks like it's sweating.

And the **ledger is right there on screen.** The append-only log of every event — the same log
everything else is built from — scrolls in a panel as the show runs. We never hide it. It's
both the architecture (the subject of [Part 3](03-one-engine-three-costumes.md)) and part of
the charm: you can see exactly what each character chose and when, which makes the whole thing
*credible*. The magic has receipts.

### Scrub anywhere, replay for free

The play-head is a scrubber across the whole run. Drag it backward and you're not
re-generating anything — you're replaying history, which costs nothing. Let it sit at the
front edge and it drives the show forward, live. Same control, two behaviors, because a past
state is just the world rebuilt from the log up to that point. Every past show you've run is
in the Archive, replayable end to end without spending a single token.

When a budget or turn limit lands — or you ask for a ruling — the judge delivers a verdict and,
where the world has a winner, a banner crowns it. Then it's another file away from a completely
different show.

---

## Where to go next

That's the wardrobe and the stage. If you're curious how one engine puts on all of this from a
fistful of YAML — agents that never call each other, every view a projection of one shared log
— [Part 3](03-one-engine-three-costumes.md) opens the machinery.

---

*Next: [Part 3 · One Engine, Three Costumes](03-one-engine-three-costumes.md) — the four abstractions under the stage.*
