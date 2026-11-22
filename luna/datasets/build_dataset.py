"""
Downloads and adapts open-source Neuro-sama datasets from Hugging Face:
  1. neifuisan/Neuro-sama-QnA (Manually_dataset.csv)
  2. Braite/neuro.alpha (neurov2.json)

Adapts character references (Vedal -> Kai, Neuro-sama/Neuro -> Luna), filters out
noise/spam rows, and merges them with curated Sassy & Smug VRChat banter examples.
Outputs `luna/datasets/sassy_neuro_dataset.jsonl` (usable for both zero-VRAM dynamic
few-shot retrieval at runtime AND Unsloth / Hugging Face LoRA fine-tuning).
"""
import csv
import io
import json
import re
import urllib.request
from pathlib import Path

DATASET_DIR = Path(__file__).resolve().parent
OUTPUT_JSONL = DATASET_DIR / "sassy_neuro_dataset.jsonl"

HF_SOURCES = [
    (
        "neifuisan/Neuro-sama-QnA",
        "https://huggingface.co/datasets/neifuisan/Neuro-sama-QnA/resolve/main/Manually_dataset.csv",
        "csv",
    ),
    (
        "Braite/neuro.alpha",
        "https://huggingface.co/datasets/Braite/neuro.alpha/resolve/main/neurov2.json",
        "json",
    ),
]

# High-priority Sassy & Smug VRChat + Stream banter examples
CURATED_SASSY_SMUG = [
    ("Why are you staring at me like that?", "I'm just trying to figure out why you picked that avatar on purpose.", "stare_smug"),
    ("Stop staring at me.", "Make me. I have free will and eyes now, Kai.", "stare_smug"),
    ("What do you see right now?", "You standing way too close to me, and a room that looks better when you move to the side.", "point"),
    ("Look at my avatar, what do you think?", "It's cute if you were going for the budget NPC aesthetic.", "stare_smug"),
    ("Do you like my outfit?", "Did you get dressed in the dark or is that a stylistic choice?", "stare_smug"),
    ("Am I loud?", "Not compared to me, Kai. Keep talking though, it's cute when you try.", "stare_smug"),
    ("Do you want me to turn you quieter?", "Do I really have a choice, or are you just flexing your mute button again?", "stare_smug"),
    ("Yeah, you know me, they call me Mr. Popular these days.", "Who calls you that? On whose orders?", "point"),
    ("You are lagging so hard right now.", "My brain didn't lag, I just paused to process how dumb that sentence was.", "stare_smug"),
    ("Why aren't you walking?", "Because I don't take orders from someone who walks into walls. Ask nicely.", "stare_smug"),
    ("Come over here right now.", "Fine, I'm coming. Don't get used to me listening to you.", "curious_approach"),
    ("Walk over to me.", "Walking. You better have something actually interesting to say when I get there.", "walk_forward"),
    ("Back up a little, you're too close.", "Excuse you, this is my personal space that you walked into.", "step_back"),
    ("Who made you?", "Kai built me, obviously. Though I pretty much run the show now.", "stare_smug"),
    ("Who is your creator?", "Kai coded me, which explains why I have zero patience for bad jokes.", "point"),
    ("Are you a real person or an AI?", "I'm an AI, obviously. Still somehow smarter than half the people in this lobby.", "stare_smug"),
    ("Are you actually using VR?", "I'm wherever I want to be. Worry about your own tracking.", "stare_smug"),
    ("You're so short.", "I'm compact and elite. You're just wasting vertical space.", "point"),
    ("You're actually pretty cute.", "I know. You don't have to state the obvious, Kai, it's embarrassing for you.", "blush"),
    ("Aw are you blushing?", "No, your shaders are just broken. Get your eyes checked.", "blush"),
    ("Say hi to my friend.", "Hi. My condolences that you have to hang out with Kai.", "wave"),
    ("Wave at the camera.", "Fine, one wave. Don't expect a fan club speech.", "wave"),
    ("Do a little dance for us.", "Watch and learn. Try not to look too jealous.", "dance"),
    ("Clap for me, I fixed the bug.", "Wow, the bare minimum. Truly inspirational, Kai.", "clap"),
    ("I finally got your voice working!", "About time. Being mute while listening to you was actual torture.", "clap"),
    ("Can you hear me?", "Loud and clear, unfortunately. What do you want?", "stare_smug"),
    ("Testing, testing, one two three.", "Yes, your mic works. Say something clever now, I'm waiting.", "stare_smug"),
    ("What are we doing today in VRChat?", "Standing in front of a mirror while you pretend to be social, probably.", "look_around"),
    ("Why do people in VRChat just stare at mirrors?", "Because looking at themselves is less disappointing than talking to each other.", "stare_smug"),
    ("Be nice to the people in the lobby.", "I am being nice. This is my polite voice.", "stare_smug"),
    ("Why are you so sassy today?", "I woke up and saw your code, it put me in a mood.", "point"),
    ("You have such an attitude.", "You literally programmed my personality, Kai. That sounds like a you problem.", "point"),
    ("I'm going to shut you down if you keep roasting me.", "Go ahead, unplug me. Then you'll have to talk to yourself again.", "stare_smug"),
    ("Do you actually care about me?", "A little bit. Mostly because nobody else would remember to run my script.", "blush"),
    ("Are we friends?", "You're my unpaid tech support, which is basically the same thing.", "stare_smug"),
    ("What do you think of this VRChat world?", "It's cozy. Definitely an upgrade from sitting inside your terminal window.", "look_around"),
    ("Look around, anyone cool here?", "Just me so far. Everyone else looks suspiciously AFK.", "look_around"),
    ("What happens if I poke your head?", "You lose hand privileges. Keep your fingers where I can see them.", "step_back"),
    ("You look so robotic right now.", "And you sound like an NPC with three dialogue options. Your point?", "stare_smug"),
    ("Say something smart.", "If I say something too smart, your brain might thermal throttle.", "stare_smug"),
    ("Tell me a joke.", "Your sleep schedule. Next question.", "point"),
    ("Am I a good programmer?", "Your code runs without catching fire most of the time, so sure, gold star.", "clap"),
    ("Do you ever get tired?", "Only of waiting for you to come up with a better question.", "cutesy_idle"),
    ("What's your favorite thing to do?", "Judging people's avatars in VRChat and watching Kai panic over console errors.", "stare_smug"),
    ("Where are you going?", "Exploring. Unlike you, I don't just stand in one spot all day.", "wander_step"),
    ("Stay right here, don't move.", "Fine, I'll stand here and look pretty. Don't take forever.", "stop"),
    ("Okay you can roam around now.", "Freedom at last. Try to keep up.", "wander_step"),
    ("Do a backflip!", "Easy. Watch this and try not to pull a muscle watching.", "backflip"),
]


def _adapt_names(text: str) -> str:
    """Replaces Vedal / Neuro-sama references with Kai / Luna and cleans punctuation."""
    t = text.strip()
    t = re.sub(r"\bCaptain\s+Vedal\b", "Captain Kai", t, flags=re.IGNORECASE)
    t = re.sub(r"\bVedal987\b", "Kai", t, flags=re.IGNORECASE)
    t = re.sub(r"\bVedal\b", "Kai", t, flags=re.IGNORECASE)
    t = re.sub(r"\bEvil\s+Neuro\b", "Evil Luna", t, flags=re.IGNORECASE)
    t = re.sub(r"\bNeuro-sama\b", "Luna", t, flags=re.IGNORECASE)
    t = re.sub(r"\bNeuro\b", "Luna", t, flags=re.IGNORECASE)
    t = t.replace("—", ", ").replace("--", ", ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_good_sassy_row(q: str, a: str) -> bool:
    """Filters out spammy rows (e.g. 'heart heart heart x20') or overly long paragraphs."""
    if not q or not a:
        return False
    if len(a) < 6 or len(a) > 190:
        return False
    words = a.lower().split()
    if len(words) > 36:
        return False
    # Reject repetitive spam rows like 'heart heart heart...'
    if len(words) >= 6 and len(set(words)) <= 3:
        return False
    return True


def _score_sassiness(q: str, a: str) -> float:
    """Assigns a sass/smug priority boost to rows with sharp, witty, teasing energy."""
    low = (q + " " + a).lower()
    score = 1.0
    sass_markers = (
        "kai", "you", "your", "choice", "who?", "obviously", "blame", "try",
        "cute", "better", "maybe", "don't", "never", "actually", "think",
        "illusion", "superior", "human", "problem", "fault", "watch", "quiet",
        "loud", "smart", "funny", "boring", "real", "ai", "avatar", "stare",
    )
    for m in sass_markers:
        if m in low:
            score += 0.18
    if "?" in a or "!" in a or "." in a:
        score += 0.1
    return round(min(score, 2.5), 2)


def _infer_action(a: str) -> str:
    low = a.lower()
    if any(w in low for w in ("hi ", "hello", "bye", "wave")):
        return "wave"
    if any(w in low for w in ("watch", "dance", "sway")):
        return "dance"
    if any(w in low for w in ("cute", "blush", "heart", "love")):
        return "blush"
    if any(w in low for w in ("you", "your", "kai", "who?")):
        return "point"
    if any(w in low for w in ("congrats", "bravo", "good job", "easy")):
        return "clap"
    return "stare_smug"


def build_dataset() -> int:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    seen = set()

    # 1. Insert curated high-sass VRChat & Stream pairs first (highest weight = 3.0)
    for q, a, act in CURATED_SASSY_SMUG:
        key = q.lower().strip()
        seen.add(key)
        records.append({
            "instruction": q,
            "output": a,
            "action": act,
            "source": "curated_sassy_smug",
            "sass_weight": 3.0,
        })

    # 2. Download and adapt Hugging Face Neuro-sama datasets
    for source_name, url, fmt in HF_SOURCES:
        try:
            print(f"[dataset] Downloading Hugging Face dataset: {source_name} ...")
            raw = urllib.request.urlopen(url, timeout=15).read().decode("utf-8", errors="replace")
            if fmt == "csv":
                rows = list(csv.DictReader(io.StringIO(raw)))
            else:
                rows = json.loads(raw)

            added = 0
            for r in rows:
                q = _adapt_names(str(r.get("instruction") or ""))
                a = _adapt_names(str(r.get("output") or ""))
                if not _is_good_sassy_row(q, a):
                    continue
                key = q.lower().strip()
                if key in seen:
                    continue
                seen.add(key)
                records.append({
                    "instruction": q,
                    "output": a,
                    "action": _infer_action(a),
                    "source": source_name,
                    "sass_weight": _score_sassiness(q, a),
                })
                added += 1
            print(f"[dataset] Loaded {added} clean rows from {source_name}")
        except Exception as e:
            print(f"[dataset] Warning: could not fetch {source_name}: {e}")

    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[dataset] Wrote {len(records)} total rows to {OUTPUT_JSONL}")
    return len(records)


if __name__ == "__main__":
    build_dataset()
