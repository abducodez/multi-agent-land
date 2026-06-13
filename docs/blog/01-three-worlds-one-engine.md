# Three Worlds, One Engine

*Field Notes · Part 1 of 5 — what Multi-Agent Land is, in plain language, and why a forest of tiny models is more fun than one big one.*

← [Series index](00-field-notes-index.md) · [Part 2 · Six Playable Woods and a Fishbowl](02-the-woods-and-the-fishbowl.md) →

---

Most AI demos put you in a chat box with one assistant. Multi-Agent Land does something
stranger. You don't *chat* with it — you drop a disturbance into a living wood and watch a
small troupe of AI characters argue, remember, judge, and reshape the world in response.

Type a seed like *"A village of stage props wakes up and argues about which fairy tale they
belong to,"* and a forest grows around it. A narrator describes a mossy ticket booth opening
in a tree root. A pocket actor announces it's collecting echoes to knit a ladder to the moon.
A critic decides whether that's worth keeping. You lean in and whisper *"a lantern starts
whispering recipes,"* and the whole cast reacts. Nobody is following a script. The story is
whatever the troupe makes of your nudge.

That's the toy. Here's why it's built the way it is.

---

## You're watching a troupe, not querying a model

The unit of delight here isn't a clever answer — it's a *cast*. Each character is its own
small AI specialist with a fixed personality and exactly one job: one narrates, one wants
impossible things, one transforms whatever you throw in, one judges what becomes real. They
have moods. They remember what happened earlier. They talk past each other and occasionally
catch fire.

And here's the part we never hide: **you can read their minds.** Every line a character says
in public comes paired with a private thought it isn't sharing — the bluffer's quiet panic,
the judge's actual reasoning, the gossip behind the smile. A toggle flips the cards over so
you can see both at once. The drama you watch on the surface has a second, hidden layer
underneath, and the whole thing is generated live by the models. The AI isn't decorating the
experience. The AI *is* the experience.

---

## Three worlds are three files, not three apps

You'd be forgiven for thinking a collaborative storytelling game, a murder-mystery solved by
a swarm, and a tense game of twenty questions are three different products. They're not.
They're the **same engine wearing different costumes** — and the costume is a small text file,
not a new codebase.

Every world is a configuration. Swap the cast, swap the goal, swap whether there's a winner,
and the same machinery puts on a completely different show. The collaborative wood has no
winner — the point is the strangeness it grows. The mystery converges on an answer and crowns
the best theory. The guessing game keeps a secret and sees who cracks first. One engine,
eight worlds today, and adding the ninth is writing a file, not shipping code. *(How that's
possible — agents that never call each other, only post to a shared log everything is built
from — is the subject of [Part 3](03-one-engine-three-costumes.md). For now: it's one engine,
and it really is just config.)*

---

## The constraint is the whole point: every model is small

Multi-Agent Land is a hackathon entry, and the hackathon has one hard rule: **every model
must be 32 billion parameters or smaller.** Most of the field treats that as a limitation to
work around. We treat it as the entire design philosophy.

The instinct with a hard size cap is to find the single best small model and make it do
everything. We did the opposite. Instead of one generalist straining at every task, the wood
is a troupe of *specialists* — a tiny model that's very good at one narrow job, standing next
to six other tiny models that are each very good at exactly one other thing. A 4B model can be
genuinely excellent at "react to the table with one fresh, vivid line." It just can't be
excellent at everything at once. So don't ask it to.

This turns out to be cheaper, faster, and more *legible* than one big model. Cheaper, because
small specialists cost little per call and the ones not currently on stage cost nothing at
all. More legible, because you can see precisely which character did what — there's no
monolith to squint at. And it's the more interesting question, honestly: not "how big a model
do you need," but "what does a specialist ≤4B model do better than a generalist seventy
times its size?" Quite a lot, it turns out, when you give it the right small page to read and
put it next to colleagues who each own one thing.

---

## What we're actually going for

When we make a change to this project, we ask whether it makes the thing more *delightful*,
more clearly *AI-load-bearing*, more *original*, or more *polished*. Those four words are the
whole compass:

- **Delightful** — the bar is "would you show a friend?" The wood should feel alive, lead
  with whimsy, and surprise you in the first thirty seconds.
- **AI is load-bearing** — the magic has to come from the models, not from clever scaffolding
  around them. The multi-agent drama *is* the product.
- **Original** — "many worlds, one event-sourced engine, agents that never call each other"
  is a genuinely different shape from the usual agent-framework playbook.
- **Polished** — the live theater should be smooth and beautiful, with no rough edges in the
  demo.

And one rule underneath all of them: the show always runs with **no API key**. There's a
deterministic offline mode that produces a full, reproducible performance with no
credentials, no network, no GPU — so the demo works on stage, every time, and the test suite
(750+ tests, zero mocks) stays honest.

---

## Where to go next

That's the pitch. If you want to *play* — the eight worlds and the theater you watch them in
— read on to [Part 2](02-the-woods-and-the-fishbowl.md). If you'd rather see how the trick
is done — the four abstractions that let one engine wear every costume — jump to
[Part 3](03-one-engine-three-costumes.md) and we'll open the trapdoor under the stage.

---

*Next: [Part 2 · Six Playable Woods and a Fishbowl to Watch Them](02-the-woods-and-the-fishbowl.md) — a tour of the worlds and the theater.*
