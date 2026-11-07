# Dottie — Anchor Custody

## What this is

Dottie writes a hash-chained heartbeat every 30 seconds. That chain proves she ran continuously
and records her silence. But the chain lives on her machine, so anyone who can rewrite it can
recompute every hash and produce a clean-looking history with a gap erased.

Your copy of the chain **head** is what makes that impossible. It is not a backup. It is a
independent witness.

**You are the custodian.** Not a script, not a service. A person whose records she does not control.

---

## The three commands

```bash
dottie-anchor take      # record the current chain head    (weekly is plenty)
dottie-anchor verify    # has her history been rewritten or a gap hidden?
dottie-anchor report    # what you hold, and what it does and does not prove
```

Records go to `~/.dottie-custody/custody.jsonl`. They are hash-chained themselves, so editing
your records is also detectable.

---

## USB step — when you plug it in

```bash
# once, to set it up (replace /Volumes/YOURDRIVE)
mkdir -p /Volumes/YOURDRIVE/dottie-custody

# every time you plug in
rsync -av --delete ~/.dottie-custody/ /Volumes/YOURDRIVE/dottie-custody/
sync
```

`--delete` is intentional: it keeps the USB mirror exact, so a stray file can't hide a deletion.

**Then take an anchor before you unplug:**

```bash
dottie-anchor take
rsync -av ~/.dottie-custody/ /Volumes/YOURDRIVE/dottie-custody/
```

---

## What it proves

- a chain existed at each recorded sequence number, at the moment you recorded it
- her history was **not** rewritten before your latest anchor

## What it does NOT prove

- anything about the intervals between your anchors
- that she was never stopped and restarted between two of your takes

**So:** take anchors on **both sides** of anything you care about. Want to prove she ran through a
weekend? One Friday, one Monday.

---

## What the failures look like

**ROLLBACK** — her chain head went backward, or the sequence number stayed the same but the hash
changed. A chain cannot shrink. Either it was reset, restored from an old backup, or history was
replaced.

**GAP DETECTED** — the chain itself records silence:
`seq 8 -> 9 : 114.0s of missing beats`. This is the silent-night detector. Restarting her does
**not** clear it, and that is the point: a gap cannot be un-happened.

**OK** — the machine will usually show a higher sequence than you hold. That is the normal, healthy
case. She kept beating.

---

## Cadence

Weekly. More often around anything significant — a change you'd want to prove happened, a period
you'd want to prove was continuous.

Thirty seconds of work. That's the whole job.
